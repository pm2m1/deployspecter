"""NONBLIND ds-nbc/1: a harness bring-up run of a pgqueuer case (CASE.md "Bring-up"): R2 only, a workload outside
the RA-DET set with no cancel, one seed. It tests the machinery and carries no verdict for the case.

    research_tools/pg_lock.py -- .venv/bin/python research_tools/nbc/integrations/pgqueuer_bringup.py \
        --spec CASE/case_spec.json --seed 1 --out CASE/bringup_<N>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORKLOAD = {"jobs": [{"n": 0, "priority": 0, "cancel": False}, {"n": 1, "priority": 0, "cancel": False}],
            "workers": 2, "concurrency_limit": 1, "handler_steps": 1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists (evidence is never overwritten)")
    spec = json.loads(a.spec.read_text())
    s = importlib.util.spec_from_file_location("nbc_bringup_integ", REPO / spec["integration"])
    assert s is not None and s.loader is not None
    integ = importlib.util.module_from_spec(s)
    s.loader.exec_module(integ)
    if any(j["cancel"] for j in WORKLOAD["jobs"]) or WORKLOAD in integ.workloads("RA-DET"):
        raise SystemExit("refused: a bring-up workload must be outside the RA-DET set and cancel nothing")
    a.out.mkdir(parents=True)
    t0 = time.monotonic()
    h = integ.prepare(Path(spec["R2"]["archive"]), spec["R2"]["sha256"], a.out / "build")
    r = integ.run(h, WORKLOAD, a.seed, a.out / "run", oracle_code=REPO / spec["oracle"]["path"],
                  oracle_sha256=spec["oracle"]["sha256"])
    rec = {"RA-NONBLIND": True, "bringup": True, "slot": "R2", "workload": WORKLOAD, "seed": a.seed, "result": r,
           "wall_seconds": round(time.monotonic() - t0, 1), "image": list(h["image"])}
    (a.out / "BRINGUP.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
    print(json.dumps(rec, default=str))
    return 0 if r["verdict"] == "SOUND" else 1


if __name__ == "__main__":
    raise SystemExit(main())
