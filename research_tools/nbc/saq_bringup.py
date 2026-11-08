"""ds-nbc/1, NONBLIND: bring-up of the saq integrations on the v0.26.5 tree only (the release the onboarding pilot
already exposed; never a case's R1 or R2). One run per shape, with a neutral workload that names no case's
parameters. A bring-up run is plumbing evidence, never a case result.

    pg_lock.py -- python research_tools/nbc/saq_bringup.py --archive ARCHIVES/saq/bringup/v0.26.5.tar --out DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "integrations"))
import saq_burst_ad8d1c8 as burst  # noqa: E402
import saq_two_queues_addee31 as two  # noqa: E402

REPO = HERE.parents[1]
BRINGUP_SHA256 = "39fd069884c1840a14a173242bda00058f27423e9c458403e78d3f470e6f5066"
NEUTRAL = {"burst": {"jobs": 2, "concurrency": 1, "max_burst_jobs": 0, "dequeue_timeout_s": 2.0,
                     "poll_interval_s": 0.5, "schedule_s": 3600},
           "two_queues": {"first": {"tables": "default", "jobs": 1}, "second": {"tables": "default", "jobs": 1},
                          "poll_interval_s": 0.5}}
ORACLE = {"burst": "docs/experimental_mvp/nonblind_corpus/saq/cases/saq-ad8d1c8/oracle.py",
          "two_queues": "docs/experimental_mvp/nonblind_corpus/saq/cases/saq-addee31/oracle.py"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--shapes", nargs="+", default=["burst", "two_queues"])
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists")
    if hashlib.sha256(a.archive.read_bytes()).hexdigest() != BRINGUP_SHA256:
        raise SystemExit("refused: not the bring-up tree")
    a.out.mkdir(parents=True)
    rows = []
    for shape in a.shapes:
        integ = burst if shape == "burst" else two
        h = integ.prepare(a.archive, BRINGUP_SHA256, a.out / "build")
        op = REPO / ORACLE[shape]
        r = integ.run(h, NEUTRAL[shape], a.seed, a.out / shape, oracle_code=op,
                      oracle_sha256=hashlib.sha256(op.read_bytes()).hexdigest())
        rows.append({"shape": shape, "workload": NEUTRAL[shape], "image": h["image"], **r})
        print(json.dumps(rows[-1], default=str), flush=True)
    (a.out / "summary.json").write_text(json.dumps({"label": "NONBLIND development bring-up (not a case result)",
                                                    "rows": rows}, indent=1, default=str))
    return 0 if all(r["verdict"] == "SOUND" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
