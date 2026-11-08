"""EXPERIMENTAL, NONBLIND (ds-nbc/1) integration ``saq-burst-c570ba4/1``: saq under its single-version contract, shape
``burst`` (``saq_shapes.py``), for the case ``saq-c570ba4``.

Implements the ``chancy-sv/1`` interface (``validate``, ``workloads``, ``prepare``, ``run``) over ``saq_common``:
every run is one restricted container plus the coordinator, on a fresh schema migrated by the tree itself, granted
under the seeded explorer with the given seed, corroborated against the server log, and judged by the approved oracle
only when SOUND.

**RA-DET workloads** (exactly 3, frozen before the case's first live run): curated: a burst worker without a cap
whose ``dequeue_timeout_s`` is shorter than its ``poll_interval_s`` (as ``test_burst``'s 0.1 s timeout is short
against the claim's duration here): W1 one job, timeout 0.5 s; W2 two jobs, timeout 0.5 s; W3 one job, timeout
0.25 s; all with a 1 s poll and the default 1 s schedule timer (see CASE.md).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import saq_common as sc  # noqa: E402
import saq_shapes as sh  # noqa: E402

NAME = "saq-burst-c570ba4/1"
# the trees this integration accepts, by archive sha256 (research_tools/nbc/pin_saq.py), and the label each declares
REVS = {
    "5adf904ddbb9360865e59bf8d2adc06fedd50eb7c438447fdb2622927203dd65": "git-59f32fc",  # R1
    "d5de08ee0daa62500ded34fdf8224d853774c30b4bedc3ce65e71b7e8d850a42": "git-c570ba4",  # R2
    "39fd069884c1840a14a173242bda00058f27423e9c458403e78d3f470e6f5066": "v0.26.5-bringup",  # not a case tree
}
WORKLOAD_JSON_SCHEMA: dict[str, Any] = sh.BURST_SCHEMA
MUSE_CONTEXT: dict[str, Any] = {"application": "saq (Simple Async Queue), Postgres queue",
                                "registry": sh.BURST_REGISTRY, "schema_ddl": sh.SCHEMA_DDL}
RA_DET = [
    {"jobs": 1, "concurrency": 1, "max_burst_jobs": 0, "dequeue_timeout_s": 0.5, "poll_interval_s": 1.0,
     "schedule_s": 1},
    {"jobs": 2, "concurrency": 1, "max_burst_jobs": 0, "dequeue_timeout_s": 0.5, "poll_interval_s": 1.0,
     "schedule_s": 1},
    {"jobs": 1, "concurrency": 1, "max_burst_jobs": 0, "dequeue_timeout_s": 0.25, "poll_interval_s": 1.0,
     "schedule_s": 1},
]


def validate(w: Any) -> list[str]:
    return sh.burst_validate(w)


def workloads(arm: str) -> list[dict[str, Any]]:
    if arm == "RA-DET":
        return [dict(w) for w in RA_DET]
    raise ValueError(f"{arm} isn't an arm this integration serves")


def prepare(archive: Path, sha256: str, workdir: Path) -> dict[str, Any]:
    if sha256 not in REVS:
        raise ValueError(f"{archive.name}: sha256 {sha256[:12]} isn't one of this integration's pinned trees")
    return sc.prepare(archive, sha256, workdir, integration=NAME, rev=REVS[sha256])


def run(handle: dict[str, Any], workload: dict[str, Any], seed: int, out: Path, *, oracle_code: Path,
        oracle_sha256: str) -> dict[str, Any]:
    if p := validate(workload):
        return {"verdict": "REFUSED", "seed": seed, "corroboration": {"ok": False}, "reason": p}
    sim = sc.run_spec(handle, sh.burst_spec(workload), seed, out, label=NAME)
    return sc.judge(sim, seed, oracle_code, oracle_sha256)
