"""EXPERIMENTAL: analysis amendment A1 of ``ds-mc-experiment/1`` (written after every run finished; analysis only).

    python research_tools/muse_contrib/analyze_a1.py --manifest M --gen GEN --run RUN --out OUT

**A1:** the frozen ``analyze.py`` emits a (case, arm, replicate) row only when that replicate executed at least one
workload. A Muse replicate whose proposals were *all* invalid therefore had no row, although the frozen rules count
every replicate (Q1/Q2: "every Muse replicate"; ``invalid_proposals``). Here, after the frozen pipeline, each planned
Muse replicate without a row gets one: nothing executed, ``detected`` false and censored at 0 schedules, with its
invalid count and its call's usage. No other rule changes. The frozen output is kept beside this one.

It runs the frozen modules unmodified and refuses unless the manifest and the frozen code files are unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze as an  # noqa: E402
import experiment as ex  # noqa: E402


def missing_rows(m: dict, gen: Path, method: list[dict]) -> list[dict]:
    have = {(r["case"], r["arm"], r["replicate"]) for r in method}
    planned = [("chancy-2895c4a", "B3", rep, None) for rep in (0, 1)]
    planned += [(f"reservations/{t}", "B3", rep, None) for t in an.RES_TARGETS for rep in (0, 1)]
    planned += [(f"reservations-state:{s}/{t}", "B5", rep, s) for s in m["deterministic_inputs"]
                ["reservations_state"]["actor_sets"] for t in an.RES_TARGETS for rep in (0, 1)]
    planned += [("procrastinate-3.3-3.4", "B3", 0, None)]
    out = []
    for case, arm, rep, group in planned:
        if (case, arm, rep) in have:
            continue
        labels = an._call_label(arm, case, rep, group)
        out.append({"case": case, "arm": arm, "replicate": rep, "run_id": an._rid(m["frozen_at"], case, arm, rep),
                    "schedules_used": 0, "detected": False, "first_detection_schedule": None, "wall_seconds": 0.0,
                    "wall_seconds_to_first_detection": None, "refused": 0, "fixed_version_violation": False,
                    "workloads_executed": 0, "invalid_proposals": sum(an._invalid(gen, x) for x in labels),
                    "nonblind": True, "known_defect": not case.startswith("procrastinate"),
                    "amendment": "A1: no proposal of this replicate was valid; nothing executed",
                    **an._usage(gen, labels)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    for k in ("manifest", "gen", "run", "out"):
        ap.add_argument(f"--{k}", type=Path, required=True)
    a = ap.parse_args()
    m = ex.require_frozen(a.manifest)
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists")
    a.out.mkdir(parents=True)
    method, workloads = [], []
    for fn in (an.reservations_rows, an.chancy_rows, an.procrastinate_rows):
        x, y = fn(m, a.gen, a.run)
        method += x
        workloads += y
    added = missing_rows(m, a.gen, method)
    method += added
    for f, rows in (("results.jsonl", method), ("results_workloads.jsonl", workloads)):
        (a.out / f).write_text("".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows))
    inv = an.invariants(m, a.gen, a.run)
    (a.out / "invariants.json").write_text(json.dumps(inv, indent=1, sort_keys=True, default=str))
    cases = sorted({r["case"] for r in method})
    summary = {"amendment": "A1", "rows_added": [(r["case"], r["arm"], r["replicate"]) for r in added],
               "Q1": {c: an.compare([r for r in method if r["case"] == c], "B3", ["B1", "B2"]) for c in cases
                      if not c.startswith("reservations-state")},
               "Q2": {c: an.compare([r for r in method if r["case"] == c], "B5", ["B4"]) for c in cases
                      if c.startswith("reservations-state")},
               "Q3": an.q3(inv),
               "Q4": {"verdict": "not answerable", "why": "onboarding effort is not measured by this experiment"},
               "muse_cost_total": an._usage(a.gen, [e["call"] for e in an.jl(a.gen / "calls.jsonl")
                                                    if e.get("kind") == "call"]),
               "calls": sum(1 for e in an.jl(a.gen / "calls.jsonl") if e.get("kind") == "call"),
               "method_rows": len(method), "workload_rows": len(workloads)}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str))
    print(json.dumps({"rows_added": summary["rows_added"], "method_rows": len(method)}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
