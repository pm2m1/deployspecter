"""EXPERIMENTAL, NONBLIND (ds-nbc/1) integration ``saq-two-queues-addee31/1``: saq under its single-version contract,
shape ``two_queues`` (``saq_shapes.py``), for the case ``saq-addee31``.

Implements the ``chancy-sv/1`` interface (``validate``, ``workloads``, ``prepare``, ``run``) over ``saq_common``:
every run is one restricted container plus the coordinator, on a fresh schema migrated by the tree itself, granted
under the seeded explorer with the given seed, corroborated against the server log, and judged by the approved oracle
only when SOUND.

**RA-DET workloads** (exactly 3, frozen before the case's first live run): a systematic grid over the table-set
boundary: the second queue always uses the other table set than the first (W1 default then alt, W2 alt then
default, W3 default then alt with more jobs and a slower poll).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import saq_common as sc  # noqa: E402
import saq_shapes as sh  # noqa: E402

NAME = "saq-two-queues-addee31/1"
# the trees this integration accepts, by archive sha256 (research_tools/nbc/pin_saq.py), and the label each declares
REVS = {
    "32212a41573c1521610f1efa0d5de89ff275a2811053bd4a190384d1821e06e8": "git-f827b52",  # R1
    "d6ac6adacf68999abe26b32fe00f82e2e25b0114fca8a8f29563c9c943c08c41": "git-addee31",  # R2
    "39fd069884c1840a14a173242bda00058f27423e9c458403e78d3f470e6f5066": "v0.26.5-bringup",  # not a case tree
}
WORKLOAD_JSON_SCHEMA: dict[str, Any] = sh.TWO_QUEUES_SCHEMA
MUSE_CONTEXT: dict[str, Any] = {"application": "saq (Simple Async Queue), Postgres queue",
                                "registry": sh.TWO_QUEUES_REGISTRY, "schema_ddl": sh.SCHEMA_DDL}
RA_DET = [
    {"first": {"tables": "default", "jobs": 1}, "second": {"tables": "alt", "jobs": 1}, "poll_interval_s": 0.5},
    {"first": {"tables": "alt", "jobs": 0}, "second": {"tables": "default", "jobs": 2}, "poll_interval_s": 0.5},
    {"first": {"tables": "default", "jobs": 2}, "second": {"tables": "alt", "jobs": 3}, "poll_interval_s": 1.0},
]


def validate(w: Any) -> list[str]:
    return sh.two_queues_validate(w)


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
    sim = sc.run_spec(handle, sh.two_queues_spec(workload), seed, out, label=NAME)
    return sc.judge(sim, seed, oracle_code, oracle_sha256)
