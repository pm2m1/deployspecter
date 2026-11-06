"""EXPERIMENTAL integration adapter: convert ds-mc-experiment/1 result rows (docs/experimental_mvp/muse_contrib/
RESULTS_SCHEMA.md) into paired_eval run records (research_tools/paired_eval.py).

    python research_tools/paired_eval_from_muse_contrib.py IN.jsonl OUT.jsonl

The two schemas were written in parallel and differ. Each mapping is explicit, and nothing is dropped:
* a muse_contrib row aggregates one case × arm × **replicate**. Replicates are independent, not consecutive runs, so
  each becomes its own arm, ``<arm>.r<replicate>`` (``B3.r0``, ``B3.r1``), and is compared separately.
* ``refused`` (an int count of refused units) is kept as ``refused_units``. paired_eval's boolean ``refused`` (the
  run as a whole can't count) is true only when units were refused **and** nothing was detected. A detection in
  muse_contrib comes from a corroborated, sound unit, so a row with refused units can still detect.
* ``known_defect: false`` becomes ``case_kind: no_known_defect``. Detection analysis skips such a case; it stays in
  the false-positive, invalidity and cost tables.
* ``first_detection_schedule`` is already cumulative over the row's workloads, so the row is one paired_eval run.
* every other field is kept unchanged.
A row missing a muse_contrib field is refused.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REQUIRED = ("case", "arm", "replicate", "run_id", "schedules_used", "detected", "first_detection_schedule",
            "wall_seconds", "invalid_proposals", "refused", "fixed_version_violation", "tokens_in", "tokens_out",
            "est_usd", "known_defect")


def convert(row: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in REQUIRED if k not in row]
    if missing:
        raise SystemExit(f"refused: muse_contrib row {row.get('run_id')} lacks {missing}")
    if not isinstance(row["refused"], int) or isinstance(row["refused"], bool) or row["refused"] < 0:
        raise SystemExit(f"refused: row {row['run_id']}: refused must be a non-negative int count")
    out = dict(row)
    out["arm"] = f"{row['arm']}.r{row['replicate']}"
    out["refused_units"] = row["refused"]
    out["refused"] = row["refused"] > 0 and not row["detected"]
    if not row["known_defect"]:
        out["case_kind"] = "no_known_defect"
    return out


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    rows = [convert(json.loads(x)) for x in src.read_text().splitlines() if x.strip()]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    print(f"{len(rows)} records -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
