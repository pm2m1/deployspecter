"""EXPERIMENTAL, NONBLIND (ds-nbc/1) integration ``saq-burst-ad8d1c8/1``: saq under its single-version contract, shape
``burst`` (``saq_shapes.py``), for the case ``saq-ad8d1c8``.

Implements the ``chancy-sv/1`` interface (``validate``, ``workloads``, ``prepare``, ``run``) over ``saq_common``:
every run is one restricted container plus the coordinator, on a fresh schema migrated by the tree itself, granted
under the seeded explorer with the given seed, corroborated against the server log, and judged by the approved oracle
only when SOUND.

**RA-DET workloads** (exactly 3, frozen before the case's first live run): curated from the maintainers'
``tests/test_worker.py`` burst tests, translated to poll mode (the harness has no LISTEN connection). W1 is
``test_max_burst_jobs`` (3 jobs, ``max_burst_jobs=1``); W2 the same with two slots and ``max_burst_jobs=2``; W3
``test_burst_poll_interval`` (2 jobs, no cap). All use ``dequeue_timeout_s`` > ``poll_interval_s`` (see CASE.md).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import saq_common as sc  # noqa: E402
import saq_shapes as sh  # noqa: E402

NAME = "saq-burst-ad8d1c8/1"
# the trees this integration accepts, by archive sha256 (research_tools/nbc/pin_saq.py), and the label each declares
REVS = {
    "a1c757de7ccfe744ebc1dfb770936893179d5dfa63f0ff7092e4ed28855c93d9": "git-525acc9",  # R1
    "047b052dc11eff0946bf8b1463cded16b3f5fc149f4ea0436f2201f833e39acb": "git-ad8d1c8",  # R2
    "39fd069884c1840a14a173242bda00058f27423e9c458403e78d3f470e6f5066": "v0.26.5-bringup",  # not a case tree
}
WORKLOAD_JSON_SCHEMA: dict[str, Any] = sh.BURST_SCHEMA
MUSE_CONTEXT: dict[str, Any] = {"application": "saq (Simple Async Queue), Postgres queue",
                                "registry": sh.BURST_REGISTRY, "schema_ddl": sh.SCHEMA_DDL}
RA_DET = [
    {"jobs": 3, "concurrency": 1, "max_burst_jobs": 1, "dequeue_timeout_s": 3.0, "poll_interval_s": 1.0,
     "schedule_s": 1},
    {"jobs": 3, "concurrency": 2, "max_burst_jobs": 2, "dequeue_timeout_s": 3.0, "poll_interval_s": 1.0,
     "schedule_s": 1},
    {"jobs": 2, "concurrency": 1, "max_burst_jobs": 0, "dequeue_timeout_s": 2.0, "poll_interval_s": 0.5,
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
