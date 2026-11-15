"""Offline checks of the upstream-leads saq re-run plan (no database, no containers)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "research_tools" / "upstream_leads" / "saq_burst_latest.py"


def _load():
    spec = importlib.util.spec_from_file_location("saq_burst_latest_t", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_plan_is_fixed_valid_and_includes_the_original_workloads_and_seeds() -> None:
    m = _load()
    rows = m.plan()
    assert len(rows) == 22
    assert all(not m.integ.validate(w) for _, _, w, _ in rows)
    replicate = [(n, s) for g, n, _, s in rows if g == "replicate"]
    assert ("M2", 1901810951) in replicate and ("D2_5", 1610254648) in replicate
    assert m.M2 == {"concurrency": 2, "dequeue_timeout_s": 1, "jobs": 4, "max_burst_jobs": 2,
                    "poll_interval_s": 0.25, "schedule_s": 1}
    assert m.D2_5["max_burst_jobs"] == 3 and m.D2_5["jobs"] == 4


def test_controls_cannot_exceed_the_cap_by_construction() -> None:
    m = _load()
    for g, _, w, _ in m.plan():
        if g == "control":
            assert w["concurrency"] == 1 or w["jobs"] <= w["max_burst_jobs"]


def test_oracle_pin_matches_the_unchanged_case_oracle() -> None:
    m = _load()
    assert hashlib.sha256(m.ORACLE.read_bytes()).hexdigest() == m.ORACLE_SHA256
