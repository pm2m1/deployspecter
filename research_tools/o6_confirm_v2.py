"""Development-only O6 confirmation on corpus v2 at the declared candidate budgets. Not an evaluation arm.

    uv run python research_tools/o6_confirm_v2.py CORPUS DECLARATION OUTPUT

Reuses the calibration runner's loading and planning code unchanged. Refuses unless the declaration names the corpus
digest. The candidate budgets and the pass rule are constants here and in the declaration, never arguments.
"""

from __future__ import annotations

import json
import socket
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import o6_offline_calibration as cal  # noqa: E402

from deployspecter.live import state_planning as sp  # noqa: E402

CANDIDATE = {"max_rows": 64, "max_expansions": 65_536, "max_constraint_checks": 32_768}
HALF = {"max_rows": 64, "max_expansions": 32_768, "max_constraint_checks": 16_384}
DOUBLE = {"max_rows": 64, "max_expansions": 131_072, "max_constraint_checks": 65_536}
STRATA = (1, 2, 4, 8, 10, 11, 16, 32, 64)


def kind(outcome: str) -> str:
    return {"CandidatePlan": "completed", "UnsupportedGoal": "rejected", "InfeasibleUnderAssumptions": "rejected",
            "Indeterminate": "indeterminate"}[outcome]


def main(corpus_path: Path, declaration_path: Path, output_path: Path) -> int:
    socket.socket = cal._no_network  # type: ignore[assignment]
    digest = cal.sha256_file(corpus_path)
    if digest not in declaration_path.read_text():
        raise SystemExit("the declaration doesn't name this corpus digest; refusing to run")
    corpus, registry, contracts, config, trusted = cal.load(corpus_path)
    budgets = {"candidate": CANDIDATE, "half": HALF, "double": DOUBLE, "probe": cal.PROBE}
    records = []
    for case in corpus["cases"]:
        row = {"id": case["id"], "category": case["category"], "rows": cal.rows_of(case)}
        for name, values in budgets.items():
            row[name] = cal.plan_once(case, registry, contracts, config, trusted, sp.PlanningBudget(**values))
        again = cal.plan_once(case, registry, contracts, config, trusted, sp.PlanningBudget(**CANDIDATE))
        row["repeat_identical"] = ({k: v for k, v in again.items() if k != "seconds"} ==
                                   {k: v for k, v in row["candidate"].items() if k != "seconds"})
        records.append(row)
    at = {name: Counter(kind(r[name]["outcome"]) for r in records) for name in budgets}
    exhausted = [r["id"] for r in records if r["candidate"]["outcome"] == "Indeterminate"]
    probe_reached = [r["id"] for r in records if r["probe"]["expansions"] >= cal.PROBE["max_expansions"]
                     or r["probe"]["constraint_checks"] >= cal.PROBE["max_constraint_checks"]]
    covered = {s for s in STRATA for r in records if r["rows"] == s and r["candidate"]["outcome"] == "CandidatePlan"}
    deterministic = all(r["repeat_identical"] for r in records)
    confirmed = not exhausted and not probe_reached and deterministic and covered == set(STRATA)
    summary = {
        "confirmed": confirmed, "deterministic": deterministic, "exhausted_at_candidate": exhausted,
        "probe_reached": probe_reached, "strata_with_a_completed_case": sorted(covered),
        "counts": {name: dict(c) for name, c in at.items()},
        "peak_at_probe": {"expansions": max(r["probe"]["expansions"] for r in records),
                          "constraint_checks": max(r["probe"]["constraint_checks"] for r in records)},
        "budgets": budgets,
    }
    out = {"status": "development-only confirmation of candidate planning budgets; not an evaluation arm",
           "corpus_sha256": digest, "declaration_sha256": cal.sha256_file(declaration_path),
           "planner_sha256": cal.sha256_file(cal.ROOT / "src/deployspecter/live/state_planning.py"),
           "records": records, "summary": summary}
    output_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
