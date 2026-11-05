"""EXPERIMENTAL engineering smoke test: curated vs systematic vs Muse on the SQLAlchemy ledger and the planner path.

Equal budgets: at most K valid workloads per source and at most S explored schedules per workload per build. Every
valid workload is explored on the buggy build and on the fixed build (the negative control). Two trusted synthetic
fixtures whose defects the developers know: no benchmark winner can be declared from this.

    DS_MVP_PORT=55441 python research_tools/mvp_compare_apps.py --workloads 6 --schedules 60 --muse-calls 2 --out DIR
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from deployspecter.experimental import harness as hz
from deployspecter.experimental import muse_apps as ma
from deployspecter.experimental import planning_adapter as pa
from deployspecter.experimental.apps import ledger_orm as L


def ledger_source(target: hz.Target, source: str, proposals: list[tuple[str, Any]], *, k: int, s: int) -> dict:
    started = time.monotonic()
    verdicts = [(label, ma.validate_ledger(p, source=source, label=label)) for label, p in proposals]
    valid = [v for _, v in verdicts if isinstance(v, ma.LedgerWorkload)][:k]
    rejected = [{"label": lab, "reasons": v} for lab, v in verdicts if isinstance(v, list)]
    handlers, statements, rows, schedules = set(), set(), [], 0
    hyp_tally: Counter[str] = Counter()
    for w in valid:
        runs = {}
        for variant in ("buggy", "fixed"):
            runs[variant] = hz.explore_app(target, L, w.invocations, w.seed, variant=variant, max_runs=s,
                                           label=w.label)
        for results, _ex in runs.values():
            schedules += len(results)
            for r in results:
                for op in r.trace.records:
                    if op.outcome == "ok" and op.kind in ("execute", "executemany") and op.sql not in L.DIALECT_SETUP:
                        handlers.add(op.provenance["handler"])
                        statements.add(op.statement_id)
        buggy, fixed = runs["buggy"][0], runs["fixed"][0]
        initial = L.expected_initial(w.seed)
        hyps = []
        for h in w.hypotheses:
            at_start = ma.evaluate_hypothesis(h, initial, initial)
            b_bad = sum(not ma.evaluate_hypothesis(h, initial, r.final_state) for r in buggy)
            f_bad = sum(not ma.evaluate_hypothesis(h, initial, r.final_state) for r in fixed)
            verdict = ("false_at_start" if not at_start else "false_positive_on_fixed" if f_bad
                       else "detects_buggy_only" if b_bad else "never_violated")
            hyp_tally[verdict] += 1
            hyps.append({"kind": h["kind"], "statement": h["statement"][:160], "verdict": verdict,
                         "buggy_runs_violating": b_bad, "fixed_runs_violating": f_bad})
        first_bad = next((r for r in buggy if r.violations), None)
        rows.append({"label": w.label,
                     "invocations": [f"{i['actor']}={i['handler']}{i['args']}" for i in w.invocations],
                     "seed": w.seed["accounts"], "buggy_runs": len(buggy), "buggy_exhausted": runs["buggy"][1],
                     "buggy_violating_runs": sum(bool(r.violations) for r in buggy),
                     "first_violation": None if first_bad is None else {"schedule": first_bad.trace.chosen,
                                                                       "violations": first_bad.violations},
                     "fixed_runs": len(fixed), "fixed_exhausted": runs["fixed"][1],
                     "fixed_violating_runs": sum(bool(r.violations) for r in fixed), "hypotheses": hyps})
    return {"source": source, "proposals": len(proposals), "valid": len([1 for _, v in verdicts
                                                                         if isinstance(v, ma.LedgerWorkload)]),
            "executed": len(valid), "rejected": len(rejected), "rejections": rejected,
            "handlers_reached": sorted(handlers), "distinct_app_statements_reached": len(statements),
            "workloads_with_verified_violation": sum(r["buggy_violating_runs"] > 0 for r in rows),
            "fixed_build_violations": sum(r["fixed_violating_runs"] > 0 for r in rows),
            "schedules_explored": schedules, "hypotheses": dict(hyp_tally), "workloads": rows,
            "seconds": round(time.monotonic() - started, 2)}


def planner_source(target: hz.Target, source: str, documents: list[tuple[str, Any]], *, k: int, s: int) -> dict:
    started = time.monotonic()
    outcomes: Counter[str] = Counter()
    rows, executed, schedules, handlers = [], 0, 0, set()
    for label, d in documents:
        try:
            doc = ma.to_workload_spec(d) if source == "muse" else d
        except Exception as exc:
            outcomes["invalid@shape"] += 1
            rows.append({"label": label, "outcome": "invalid", "stage": "shape", "detail": str(exc)[:200]})
            continue
        p = pa.plan(doc)
        detail = p.detail[0] if isinstance(p.detail, list) and p.detail else p.detail
        code = detail.get("code") if isinstance(detail, dict) else None
        outcomes[f"{p.outcome}@{p.stage}" + (f":{code}" if code else "")] += 1
        row: dict[str, Any] = {"label": label, "name": doc.get("name"), "outcome": p.outcome, "stage": p.stage,
                               "detail": json.loads(json.dumps(detail, default=str))}
        if p.outcome == "bridged" and executed < k:
            executed += 1
            fx = pa.PlannedReservations(p)
            digest = fx.verify_initial(target)
            invs = pa.invocations(p)
            for variant in ("buggy", "fixed"):
                results, ex = hz.explore_app(target, fx, invs, None, variant=variant, max_runs=s, label=label,
                                             expected_digest=digest)
                schedules += len(results)
                handlers |= {op.provenance["handler"] for r in results for op in r.trace.records if op.outcome == "ok"}
                row[variant] = {"runs": len(results), "exhausted": ex,
                                "violating_runs": sum(bool(r.violations) for r in results),
                                "violations": sorted({v.split(":")[0] for r in results for v in r.violations})}
            row["rows"] = [[t, v] for t, v in pa.typed_rows(p)]
        rows.append(row)
    ran = [r for r in rows if "buggy" in r]
    return {"source": source, "documents": len(documents), "outcomes": dict(outcomes),
            "bridged": sum(r["outcome"] == "bridged" for r in rows), "executed": executed,
            "handlers_reached": sorted(handlers),
            "workloads_with_verified_violation": sum(r["buggy"]["violating_runs"] > 0 for r in ran),
            "fixed_build_violations": sum(r["fixed"]["violating_runs"] > 0 for r in ran),
            "schedules_explored": schedules, "documents_detail": rows, "seconds": round(time.monotonic() - started, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=int, default=6)
    ap.add_argument("--schedules", type=int, default=60)
    ap.add_argument("--muse-calls", type=int, default=0, help="per target")
    ap.add_argument("--muse-n", type=int, default=6)
    ap.add_argument("--muse-usd", type=float, default=2.0)
    ap.add_argument("--muse-timeout", type=float, default=600.0)
    ap.add_argument("--env-file", type=Path, default=Path(os.path.expanduser("~/Desktop/deployspecter/.env")))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    k, s = a.workloads, a.schedules
    muse: dict[str, list[tuple[str, Any]]] = {"ledger": [], "planner": []}
    cost = None
    if a.muse_calls:
        from deployspecter.experimental.muse import client_from_env_file
        client = client_from_env_file(a.env_file, max_requests=2 * a.muse_calls, max_estimated_usd=a.muse_usd,
                                      timeout=a.muse_timeout)
        failed = []
        for t in ("ledger", "planner"):
            for i in range(a.muse_calls):
                try:
                    muse[t] += ma.propose(client, t, n=a.muse_n, ledger=a.out / "muse_ledger.jsonl",
                                          call_label=f"muse_{t}_{i}")
                except Exception as exc:  # recorded in the ledger by propose; the other sources still run
                    failed.append({"call": f"muse_{t}_{i}", "error": type(exc).__name__})
        cost = {"requests": client.requests_sent, "estimated_usd": round(client.estimated_usd, 6),
                "failed_calls": failed,
                "note": "a failed (timed-out) request may still be billed; its tokens are unknown"}
        (a.out / "muse_raw_proposals.json").write_text(json.dumps(muse, indent=1))
    report: dict[str, Any] = {
        "kind": "EXPERIMENTAL engineering smoke test on two trusted synthetic fixtures; not a benchmark",
        "budgets": {"valid_workloads_per_source": k, "schedules_per_workload_per_build": s,
                    "muse_calls_per_target": a.muse_calls, "muse_proposals_per_call": a.muse_n},
        "ledger": [ledger_source(target, "curated", ma.ledger_curated(), k=k, s=s),
                   ledger_source(target, "systematic", ma.ledger_systematic(k), k=k, s=s)],
        "planner": [planner_source(target, "curated", ma.planner_curated(), k=k, s=s),
                    planner_source(target, "systematic", ma.planner_systematic(k), k=k, s=s)],
        "muse_cost": cost}
    if a.muse_calls:
        report["ledger"].append(ledger_source(target, "muse", muse["ledger"], k=k, s=s))
        report["planner"].append(planner_source(target, "muse", muse["planner"], k=k, s=s))
    (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    for t in ("ledger", "planner"):
        for r in report[t]:
            keys = ("proposals", "documents", "valid", "bridged", "executed", "rejected", "handlers_reached",
                    "distinct_app_statements_reached", "workloads_with_verified_violation",
                    "fixed_build_violations", "schedules_explored", "hypotheses", "outcomes", "seconds")
            print(t, r["source"], {x: r[x] for x in keys if x in r})
    print("muse", cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
