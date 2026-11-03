"""Development-only, exploratory O6 budget sensitivity over the unchanged corpus v1. Not an evaluation arm.

    uv run python tools/o6_budget_sensitivity.py CORPUS DECLARATION OUTPUT

Reuses the calibration runner's loading code unchanged. Refuses unless the declaration names the corpus digest.
"""

from __future__ import annotations

import json
import socket
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import o6_offline_calibration as cal  # noqa: E402

from deployspecter.live import state_planning as sp  # noqa: E402

EXPANSIONS = (2048, 4096, 8192, 16384, 32768, 65536, 131072)
CHECKS = (1024, 2048, 4096, 8192, 16384, 32768, 65536)


def main(corpus_path: Path, declaration_path: Path, output_path: Path) -> int:
    socket.socket = cal._no_network  # type: ignore[assignment]
    digest = cal.sha256_file(corpus_path)
    if digest not in declaration_path.read_text():
        raise SystemExit("the declaration doesn't name this corpus digest; refusing to run")
    corpus, registry, contracts, config, trusted = cal.load(corpus_path)
    probe = sp.PlanningBudget(**cal.PROBE)
    baseline = {c["id"]: cal.plan_once(c, registry, contracts, config, trusted, probe) for c in corpus["cases"]}
    meta = {c["id"]: (c["category"], cal.rows_of(c)) for c in corpus["cases"]}
    grid = [("max_expansions", v, sp.PlanningBudget(max_rows=64, max_expansions=v,
                                                     max_constraint_checks=cal.PROBE["max_constraint_checks"]))
            for v in EXPANSIONS]
    grid += [("max_constraint_checks", v, sp.PlanningBudget(max_rows=64, max_expansions=cal.PROBE["max_expansions"],
                                                            max_constraint_checks=v)) for v in CHECKS]
    grid.append(("defaults", 0, sp.PlanningBudget()))
    rows = []
    for limit, value, budget in grid:
        stopped, changed_other = [], []
        for case in corpus["cases"]:
            got = cal.plan_once(case, registry, contracts, config, trusted, budget)
            base = baseline[case["id"]]
            if got["outcome"] == "Indeterminate":
                stopped.append(case["id"])
            elif (got["outcome"], got["code"], got["request_digest"]) != (base["outcome"], base["code"],
                                                                          base["request_digest"]):
                changed_other.append(case["id"])
        rows.append({"limit": limit, "value": value, "indeterminate": len(stopped), "ids": stopped,
                     "by_category": dict(Counter(meta[i][0] for i in stopped)),
                     "by_rows": dict(Counter(str(meta[i][1]) for i in stopped)), "other_changes": changed_other})
    out = {"status": "development-only exploratory sensitivity; post hoc; selects nothing",
           "corpus_sha256": digest, "declaration_sha256": cal.sha256_file(declaration_path),
           "planner_sha256": cal.sha256_file(cal.ROOT / "src/deployspecter/live/state_planning.py"), "rows": rows}
    output_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    for r in rows:
        print(f"{r['limit']:22} {r['value']:>7} indeterminate={r['indeterminate']:>2} {r['by_category']} "
              f"other_changes={r['other_changes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
