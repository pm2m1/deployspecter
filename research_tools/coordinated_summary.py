"""EXPERIMENTAL: summarize a directory of simultaneous-pilot runs (``run_coordinated.py`` outputs).

    python research_tools/coordinated_summary.py DIR [DIR ...]

For each run directory (holding ``report.json``), it gives:
* the verdict;
* the window evidence;
* the canonical final state: per job n, its state, attempts, claim presence, error count, and the versions that
  executed it. Job ids and timestamps are random per run, so the raw final-state digest is unique per run by
  construction and is not used to compare runs.
* the transaction-order digest;
* the property violations;
* the corroboration totals.

Across runs it counts candidate, executed, refused and incomplete runs, distinct transaction orders, distinct
canonical final states, and the time and grant of the first violation.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def canonical_final(report: dict[str, Any]) -> list[Any]:
    sim = report.get("simultaneous") or {}
    jobs = (sim.get("final_state") or {}).get("jobs_detail", [])
    execs = ((sim.get("properties") or {}).get("executions")) or {}
    return [[j["n"], j["state"], j["attempts"], j["has_claim_id"], j["errors"], sorted(execs.get(str(j["n"]), []))]
            for j in sorted(jobs, key=lambda j: j["n"])]


def first_violation(report: dict[str, Any]) -> dict[str, Any] | None:
    """The run's violations are judged on its final state; the earliest point is when the run ended."""
    sim = report.get("simultaneous") or {}
    if not (sim.get("properties") or {}).get("violations"):
        return None
    coord = sim.get("coordinator") or {}
    return {"grants": coord.get("grants"), "elapsed_ms": coord.get("elapsed_ms")}


def row(run_dir: Path) -> dict[str, Any]:
    r = json.loads((run_dir / "report.json").read_text())
    s = r.get("summary") or {}
    canon = canonical_final(r)
    return {"run": run_dir.name, "scenario": r.get("scenario"), "mutant": r.get("mutant"),
            "policy": ((r.get("simultaneous") or {}).get("policy")), "verdict": r.get("verdict"),
            "grants": s.get("grants"), "window": s.get("window"), "txn_order": s.get("txn_order_sha256"),
            "canonical_final": canon,
            "canonical_final_sha256": hashlib.sha256(json.dumps(canon).encode()).hexdigest()[:16],
            "violations": s.get("violations") or {}, "first_violation": first_violation(r),
            "corroboration": s.get("corroboration"), "sessions": s.get("sessions"),
            "elapsed_ms": ((r.get("simultaneous") or {}).get("coordinator") or {}).get("elapsed_ms")}


def aggregate(rows: list[dict[str, Any]], candidates: int | None = None) -> dict[str, Any]:
    executed = [x for x in rows if x["verdict"] in ("SOUND", "INCOMPLETE")]
    sound = [x for x in rows if x["verdict"] == "SOUND"]
    violating = [x for x in sound if x["violations"]]
    return {"candidate_schedules": candidates if candidates is not None else len(rows),
            "executed": len(executed), "sound": len(sound),
            "refused": sum(1 for x in rows if x["verdict"] == "REFUSED"),
            "incomplete": sum(1 for x in rows if x["verdict"] == "INCOMPLETE"),
            "distinct_txn_orders": len({x["txn_order"] for x in sound}),
            "distinct_canonical_final_states": len({x["canonical_final_sha256"] for x in sound}),
            "runs_with_violations": len(violating),
            "violated_properties": sorted({p for x in violating for p in x["violations"]}),
            "first_violation": next(({"run": x["run"], **x["first_violation"]} for x in violating), None),
            "statements_corroborated": sum((x["corroboration"] or {}).get("matched") or 0 for x in sound),
            "params_mismatched": sum((x["corroboration"] or {}).get("params_mismatched") or 0 for x in sound),
            "params_unchecked": sum((x["corroboration"] or {}).get("params_unchecked") or 0 for x in sound)}


def main() -> int:
    rows = []
    for d in sys.argv[1:]:
        p = Path(d)
        runs = [p] if (p / "report.json").exists() else sorted(x for x in p.iterdir() if (x / "report.json").exists())
        rows += [row(x) for x in runs]
    print(json.dumps({"runs": rows, "aggregate": aggregate(rows)}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
