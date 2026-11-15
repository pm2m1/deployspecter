"""NONBLIND, EXPLORATORY, POST-HOC: re-run the two ds-nbc/2 workloads that broke clause B3 (``max_burst_jobs``) on saq's
latest release, with controls that stay within the cap and a small workload grid to find the smallest failing one.

    pg_lock.py -- python research_tools/upstream_leads/saq_burst_latest.py --archive ARCHIVES/saq/bringup/v0.26.5.tar \
        --out DIR

The tree is the v0.26.5 archive pinned by ``research_tools/nbc/pin_saq.py`` (sha256 ``39fd0698…``). Its ``saq/`` is
byte-identical to ``saq/`` in the PyPI sdist ``saq-0.26.5.tar.gz`` (sha256 ``75e8664c…``), checked separately and
recorded in ``SEMANTICS.md``. The integration (``saq_burst_c570ba4.py``) and the oracle (``saq-c570ba4/oracle.py``)
are used unchanged: the API didn't change, so no new adapter is needed. Every run goes through the integration's
``run``: one restricted container plus the coordinator, a fresh schema, the seeded explorer, server-log
corroboration, and the oracle only on a SOUND run. Nothing is retried. Nothing here is a case result or confirmatory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "nbc" / "integrations"))
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))
import saq_burst_c570ba4 as integ  # noqa: E402

LABEL = "NONBLIND EXPLORATORY POST-HOC (upstream-leads/saq-burst/1); not a case result"
ARCHIVE_SHA256 = "39fd069884c1840a14a173242bda00058f27423e9c458403e78d3f470e6f5066"  # git v0.26.5, saq/ only
ORACLE = REPO / "docs/experimental_mvp/nonblind_corpus/saq/cases/saq-c570ba4/oracle.py"
ORACLE_SHA256 = "7a88fd213a2a70d5d2e985122538cd9ee15efe0d6f6156f145950e0b802c1619"

M2 = {"concurrency": 2, "dequeue_timeout_s": 1, "jobs": 4, "max_burst_jobs": 2, "poll_interval_s": 0.25,
      "schedule_s": 1}  # ds-nbc/2 Muse slot 2 (RESULTS_LEDGER seq 8-11)
D2_5 = {"concurrency": 2, "dequeue_timeout_s": 0.5, "jobs": 4, "max_burst_jobs": 3, "poll_interval_s": 0.25,
        "schedule_s": 3600}  # ds-nbc/2 D2 slot 5 (RESULTS_LEDGER seq 68-71)
ORIGINAL_SEEDS = {"M2": [1901810951, 1806303420], "D2_5": [1610254648, 2007160722]}
EXTRA_SEEDS = [0, 1, 2]


def plan() -> list[tuple[str, str, dict[str, Any], int]]:
    """(group, name, workload, seed), fixed before any run."""
    rows: list[tuple[str, str, dict[str, Any], int]] = []
    for name, w in (("M2", M2), ("D2_5", D2_5)):
        rows += [("replicate", name, w, s) for s in ORIGINAL_SEEDS[name] + EXTRA_SEEDS]
    within = {"control_conc1_cap2_jobs4": {**M2, "concurrency": 1},   # same cap and jobs, one slot
              "control_conc2_cap2_jobs2": {**M2, "jobs": 2}}           # no more jobs than the cap
    rows += [("control", n, w, s) for n, w in within.items() for s in EXTRA_SEEDS]
    for cap in (1, 2, 3):
        for jobs in range(cap + 1, 5):
            rows.append(("grid", f"grid_conc2_cap{cap}_jobs{jobs}", {**M2, "max_burst_jobs": cap, "jobs": jobs}, 0))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists")
    if hashlib.sha256(a.archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise SystemExit("refused: not the pinned v0.26.5 tree")
    if hashlib.sha256(ORACLE.read_bytes()).hexdigest() != ORACLE_SHA256:
        raise SystemExit("refused: the oracle isn't the approved one")
    a.out.mkdir(parents=True)
    rows = plan()
    for _, _, w, _ in rows:
        assert not integ.validate(w), w
    (a.out / "PLAN.json").write_text(json.dumps({"label": LABEL, "rows": rows}, indent=1))
    handle = integ.prepare(a.archive, ARCHIVE_SHA256, a.out / "build")
    results = []
    for i, (group, name, w, seed) in enumerate(rows):
        run_dir = a.out / "runs" / f"{i:02d}_{name}_s{seed}"
        t0 = time.monotonic()
        r = integ.run(handle, w, seed, run_dir, oracle_code=ORACLE, oracle_sha256=ORACLE_SHA256)
        rep = json.loads((run_dir / "report.json").read_text()) if (run_dir / "report.json").exists() else {}
        jobs = ((rep.get("final_state") or {}).get("jobs") or {}).get("saq_jobs") or []
        coord = rep.get("coordinator") or {}
        row = {"i": i, "group": group, "name": name, "workload": w, "seed": seed, "verdict": r["verdict"],
               "oracle": r.get("oracle"), "corroboration": r.get("corroboration"), "reason": r.get("reason"),
               "complete": sum(j["status"] == "complete" for j in jobs),
               "statuses": sorted(j["status"] for j in jobs),
               "handled": ((rep.get("raw_results") or {}).get("worker") or {}).get("handled"),
               "grants": len(coord.get("decisions") or []), "wall_s": round(time.monotonic() - t0, 2)}
        results.append(row)
        print(json.dumps(row), flush=True)
    (a.out / "summary.json").write_text(json.dumps({"label": LABEL, "archive_sha256": ARCHIVE_SHA256,
                                                    "image": handle["image"], "oracle_sha256": ORACLE_SHA256,
                                                    "rows": results}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
