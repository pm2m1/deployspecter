"""EXPERIMENTAL engineering smoke test: curated vs systematic vs Muse workloads on the trusted fixture.

Equal budgets: at most K valid workloads per source, and at most S explored schedules per workload per build. Each
valid workload is explored on the buggy build and on the fixed build (the negative control). One synthetic fixture:
no benchmark winner can be declared from this.

    DS_MVP_PORT=55441 python research_tools/mvp_compare.py --workloads 5 --schedules 60 --muse-calls 2 --out DIR
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from deployspecter.experimental import harness as hz
from deployspecter.experimental import muse as mz
from deployspecter.experimental import workloads as wl
from deployspecter.experimental.apps import reservations_mixed as app


def evaluate(target: hz.Target, source: str, proposals: list[tuple[str, dict[str, Any]]], *, k: int, s: int,
             runs_dir: Path) -> dict[str, Any]:
    started = time.monotonic()
    verdicts = [wl.validate(p, source=source, label=label) for label, p in proposals]
    valid = [v for v in verdicts if isinstance(v, wl.Workload)][:k]
    rejected = [v for v in verdicts if isinstance(v, wl.Rejection)]
    handlers, touching, schedules = set(), set(), 0
    rows = []
    for w in valid:
        buggy, b_ex = hz.explore_workload(target, w, variant="buggy", max_runs=s)
        fixed, f_ex = hz.explore_workload(target, w, variant="fixed", max_runs=s)
        for r in buggy + fixed:
            for op in r.trace.records:
                if op.outcome == "ok":
                    handlers.add(f"{op.provenance['app_version']}.{op.provenance['handler']}")
                    if op.statement_id in app.MIGRATION_TOUCHING:
                        touching.add(op.statement_id)
        schedules += len({tuple(r.trace.chosen) for r in buggy}) + len({tuple(r.trace.chosen) for r in fixed})
        first_bad = next((r for r in buggy if r.violations), None)
        rows.append({"label": w.label, "invocations": [f"{i['actor']}={i['app_version']}.{i['handler']}"
                                                       for i in w.invocations],
                     "buggy_runs": len(buggy), "buggy_exhausted": b_ex,
                     "buggy_violating_runs": sum(bool(r.violations) for r in buggy),
                     "first_violation": None if first_bad is None else {"schedule": first_bad.trace.chosen,
                                                                       "violations": first_bad.violations,
                                                                       "run_id": first_bad.run_id},
                     "fixed_runs": len(fixed), "fixed_exhausted": f_ex,
                     "fixed_violating_runs": sum(bool(r.violations) for r in fixed),
                     "fixed_violations": sorted({v for r in fixed for v in r.violations})})
        if first_bad is not None:
            (runs_dir / f"{source}_{first_bad.run_id}.json").write_text(json.dumps(first_bad.to_dict(), indent=1,
                                                                                    default=str))
    return {"source": source, "proposals": len(proposals), "valid": len([v for v in verdicts
                                                                         if isinstance(v, wl.Workload)]),
            "executed": len(valid), "rejected": len(rejected),
            "rejections": [{"label": r.label, "reasons": r.reasons} for r in rejected],
            "distinct_handlers": sorted(handlers), "migration_touching_statements": len(touching),
            "migration_touching_possible": len(app.MIGRATION_TOUCHING),  # both builds run, so both backfill reads
            "unique_schedules": schedules,
            "workloads_with_verified_violation": sum(1 for r in rows if r["buggy_violating_runs"]),
            "fixed_build_violations": sum(r["fixed_violating_runs"] for r in rows),
            "seconds": round(time.monotonic() - started, 2), "workloads": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=int, default=5)
    ap.add_argument("--schedules", type=int, default=60)
    ap.add_argument("--muse-calls", type=int, default=0)
    ap.add_argument("--proposals-per-call", type=int, default=4)
    ap.add_argument("--muse-usd", type=float, default=3.0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    runs = a.out / "violating_runs"
    runs.mkdir()
    results = [evaluate(target, "curated", wl.curated(), k=a.workloads, s=a.schedules, runs_dir=runs),
               evaluate(target, "systematic", wl.systematic(50), k=a.workloads, s=a.schedules, runs_dir=runs)]
    if a.muse_calls:
        client = mz.client_from_env_file(Path.home() / "Desktop/deployspecter/.env", max_requests=a.muse_calls,
                                         max_estimated_usd=a.muse_usd)
        proposals: list[tuple[str, dict[str, Any]]] = []
        for i in range(a.muse_calls):
            try:
                proposals += mz.propose(client, n=a.proposals_per_call, ledger=a.out / "muse_ledger.jsonl",
                                        call_label=f"muse{i + 1}")
            except Exception as exc:  # recorded in the ledger; the remaining sources still report
                print(f"muse call {i + 1} stopped: {type(exc).__name__}")
                break
        res = evaluate(target, "muse", proposals, k=a.workloads, s=a.schedules, runs_dir=runs)
        res["api"] = {"requests": client.requests_sent, "estimated_usd": round(client.estimated_usd, 6),
                      "note": "estimated at published rates; not billing data"}
        (a.out / "muse_proposals.json").write_text(json.dumps(proposals, indent=1))
        results.append(res)
    (a.out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    lines = ["| source | proposals | valid | executed | rejected | handlers reached | migration stmts | unique "
             "schedules | workloads with verified violation | fixed-build violations | seconds |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['source']} | {r['proposals']} | {r['valid']} | {r['executed']} | {r['rejected']} | "
                     f"{len(r['distinct_handlers'])} | {r['migration_touching_statements']}/"
                     f"{r['migration_touching_possible']} | {r['unique_schedules']} | "
                     f"{r['workloads_with_verified_violation']} | {r['fixed_build_violations']} | {r['seconds']} |")
    (a.out / "summary.md").write_text("Engineering smoke test on one trusted synthetic fixture; not a benchmark.\n\n"
                                      + "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
