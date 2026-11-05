"""EXPERIMENTAL measurement: curated vs systematic vs Muse workloads on the reservations release path, against the
frozen builds, the corrected reference and four mutants of it.

Equal budgets: at most K bridged workloads per source and at most S explored schedules per workload per build. A
workload is a WorkloadSpec v2 document judged by the unmodified chain; nothing else runs. Muse's invariant hypotheses
(a fixed grammar, evaluated by a pure function) count only after they hold on the initial state and on every explored
run of the corrected reference; only then is it measured which builds they distinguish. The frozen oracle (R1, R2)
plus R5 is the reference measure. The release_seats defect was found earlier by a Muse proposal on this same fixture,
so nothing here is blind.

    DS_MVP_PORT=55441 python research_tools/mvp_compare_release.py --workloads 6 --schedules 60 --muse-calls 2 --out DIR
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mvp_release_evidence as cases  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import muse_apps as ma  # noqa: E402
from deployspecter.experimental import planning_adapter as pa  # noqa: E402
from deployspecter.research.track_a import scenario as sc  # noqa: E402

BUILDS = ["buggy", "fixed", "corrected", "mutant_no_reservation_lock", "mutant_no_guard", "mutant_no_event_lock",
          "mutant_off_by_one"]


def curated() -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    docs = [("curated#0", copy.deepcopy(sc.DOCUMENT))] + [(f"curated#{i + 1}", d)
                                                          for i, d in enumerate(cases.CASES.values())]
    return [(label, d, []) for label, d in docs]


def systematic(limit: int) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    actors = {"reserve_jack": lambda n: cases.reserve(n, "jack", 2), "release_jack": lambda n: cases.release(n, "jack"),
              "release_jill": lambda n: cases.release(n, "jill"), "reserve_jill": lambda n: cases.reserve(n, "jill", 2)}
    seeds = [{"jack": [2]}, {"jack": [2, 2]}, {"jack": [2], "jill": [3]}]
    out = []
    for seed in seeds:
        for x, y in itertools.combinations_with_replacement(sorted(actors), 2):
            doc = cases.document(f"grid_{len(out)}", [actors[x]("a"), actors[y]("b")], seed)
            doc["provenance"] = {"origin": "deterministic_baseline", "author": "mvp_release_grid"}
            out.append((f"systematic#{len(out)}", doc, []))
    return out[:limit]


def measure(target: hz.Target, source: str, items: list[tuple[str, Any, list[dict[str, Any]]]], *, k: int,
            s: int, rejected: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.monotonic()
    outcomes: Counter[str] = Counter()
    rows, executed, schedules = [], 0, 0
    kills: dict[str, int] = dict.fromkeys(BUILDS, 0)
    handlers: set[str] = set()
    hyp_tally: Counter[str] = Counter()
    hyp_kills: dict[str, int] = dict.fromkeys(BUILDS, 0)
    for label, doc, hyps in items:
        p = pa.plan(doc)
        detail = p.detail[0] if isinstance(p.detail, list) and p.detail else p.detail
        code = detail.get("code") if isinstance(detail, dict) else None
        outcomes[f"{p.outcome}@{p.stage}" + (f":{code}" if code else "")] += 1
        row: dict[str, Any] = {"label": label, "name": doc.get("name"), "outcome": p.outcome, "stage": p.stage}
        if p.outcome != "bridged" or executed >= k:
            rows.append(row)
            continue
        executed += 1
        fx = pa.PlannedReservations(p)
        digest = fx.verify_initial(target)
        admin = target.admin()
        try:
            initial = fx.read_state(admin)
        finally:
            admin.close()
        invs = pa.invocations(p)
        handlers |= {i["handler"] for i in invs}
        finals: dict[str, list[dict[str, Any]]] = {}
        for build in BUILDS:
            results, exhausted = hz.explore_app(target, fx, invs, None, variant=build, max_runs=s, label=label,
                                                expected_digest=digest)
            schedules += len(results)
            bad = sum(bool(r.violations) for r in results)
            kills[build] += bad > 0
            finals[build] = [r.final_state for r in results]
            row[build] = {"runs": len(results), "exhausted": exhausted, "violating": bad,
                          "kinds": sorted({v.split(" ")[0] for r in results for v in r.violations})}
        row["hypotheses"] = []
        for h in hyps:
            if not ma.evaluate_res_hypothesis(h, initial, initial):
                verdict, distinguishes = "false_at_start", []
            elif not all(ma.evaluate_res_hypothesis(h, initial, f) for f in finals["corrected"]):
                verdict, distinguishes = "false_on_corrected_reference", []
            else:
                distinguishes = [b for b in BUILDS if b != "corrected"
                                 and any(not ma.evaluate_res_hypothesis(h, initial, f) for f in finals[b])]
                verdict = "accepted"
                for b in distinguishes:
                    hyp_kills[b] += 1
            hyp_tally[verdict] += 1
            row["hypotheses"].append({"kind": h["kind"], "table": h["table"], "column": h["column"],
                                      "statement": h["statement"][:160], "verdict": verdict,
                                      "distinguishes": distinguishes})
        rows.append(row)
    return {"source": source, "documents": len(items), "rejected_before_chain": rejected,
            "outcomes": dict(outcomes), "executed": executed, "handlers_reached": sorted(handlers),
            "schedules_explored": schedules,
            "oracle_workloads_violating_per_build": kills,
            "hypotheses": dict(hyp_tally), "hypothesis_workloads_distinguishing_per_build": hyp_kills,
            "workloads": rows, "seconds": round(time.monotonic() - started, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=int, default=6)
    ap.add_argument("--schedules", type=int, default=60)
    ap.add_argument("--muse-calls", type=int, default=0)
    ap.add_argument("--muse-n", type=int, default=6)
    ap.add_argument("--muse-usd", type=float, default=2.0)
    ap.add_argument("--muse-timeout", type=float, default=900.0)
    ap.add_argument("--env-file", type=Path, default=Path(os.path.expanduser("~/Desktop/deployspecter/.env")))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    k, s = a.workloads, a.schedules
    muse_items, muse_rejected, cost = [], [], None
    if a.muse_calls:
        from deployspecter.experimental.muse import client_from_env_file
        client = client_from_env_file(a.env_file, max_requests=a.muse_calls, max_estimated_usd=a.muse_usd,
                                      timeout=a.muse_timeout)
        failed = []
        for i in range(a.muse_calls):
            try:
                raw = ma.propose(client, "release", n=a.muse_n, ledger=a.out / "muse_ledger.jsonl",
                                 call_label=f"muse_release_{i}")
            except Exception as exc:  # recorded by propose; the deterministic sources still run
                failed.append({"call": f"muse_release_{i}", "error": type(exc).__name__})
                continue
            for label, p in raw:
                doc, hyps, reasons = ma.validate_release(p)
                if doc is None or reasons:
                    muse_rejected.append({"label": label, "reasons": reasons})
                    if doc is None:
                        continue
                muse_items.append((label, doc, hyps))
        cost = {"requests": client.requests_sent, "estimated_usd": round(client.estimated_usd, 6),
                "failed_calls": failed}
    report: dict[str, Any] = {
        "kind": "EXPERIMENTAL measurement on a developer-written fixture whose release defect a Muse proposal found "
                "earlier; not blind, not a benchmark",
        "code_revision": hz.code_revision(), "builds": BUILDS,
        "budgets": {"bridged_workloads_per_source": k, "schedules_per_workload_per_build": s,
                    "muse_calls": a.muse_calls, "muse_proposals_per_call": a.muse_n},
        "sources": [measure(target, "curated", curated(), k=k, s=s, rejected=[]),
                    measure(target, "systematic", systematic(k), k=k, s=s, rejected=[])],
        "muse_cost": cost}
    if a.muse_calls:
        report["sources"].append(measure(target, "muse", muse_items, k=k, s=s, rejected=muse_rejected))
    (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    for r in report["sources"]:
        print(r["source"], {x: r[x] for x in ("documents", "outcomes", "executed", "handlers_reached",
                                              "schedules_explored", "oracle_workloads_violating_per_build",
                                              "hypotheses", "hypothesis_workloads_distinguishing_per_build",
                                              "seconds")}, "rejected", len(r["rejected_before_chain"]))
    print("muse", cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
