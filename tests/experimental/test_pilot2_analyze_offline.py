"""Offline tests of onb-pilot/2's predeclared lock-wait adjustment (synthetic data only)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research_tools" / "onboarding"))
import pilot2_analyze as pa  # noqa: E402


def test_a_wait_is_subtracted_only_where_it_precedes_the_milestone():
    t0 = 1_000_000.0
    ws = [(t0 + 100, t0 + 160), (t0 + 900, t0 + 1000)]
    assert pa.adjusted(t0, 500, ws) == 440.0
    assert pa.adjusted(t0, 950, ws) == 950 - 60 - 50
    assert pa.adjusted(t0, None, ws) is None


def test_waits_are_parsed_from_the_lock_log(tmp_path):
    log = tmp_path / "pg_lock.log"
    log.write_text("2026-11-08T10:00:30Z pid=1 cwd=/x acquired waited=30.0s cmd=run\n"
                   "2026-11-08T10:01:00Z pid=1 released exit=0\n")
    (w,) = pa.waits(log)
    assert w[1] - w[0] == 30.0


def test_censoring_uses_the_budget():
    assert pa.cens(None) == 5400 and pa.cens(12.5) == 12.5
