"""EXPERIMENTAL planning adapter, live: a planned, verified initial state and Gate 2A's synthetic handlers, stepped.

Skipped unless the disposable MVP PostgreSQL is running. Not Gate 2A's supervised runner and not Gate A2 evidence.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental import planning_adapter as pa
from deployspecter.research.track_a import scenario as sc

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")
BOTH_READ_FIRST = ["a", "b"] * 6


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


def test_the_planned_state_drives_a_lost_update_in_the_buggy_build_only(target):
    results = {}
    for variant in ("buggy", "fixed"):
        p, r, digest = pa.run_planned(target, sc.DOCUMENT, variant=variant, schedule=BOTH_READ_FIRST,
                                      corroborate=True)
        assert p.outcome == "bridged" and r.initial_digest == digest
        assert r.corroboration["ok"], r.corroboration["problems"]
        results[variant] = r
    assert results["buggy"].initial_digest == results["fixed"].initial_digest
    assert results["buggy"].violations == ["R1 event 1: reserved=2 but reservations hold 4",
                                           "R2 event 1: 4 seats reserved of capacity 3"]
    assert results["fixed"].violations == []
    sites = {op.provenance["call_site"].split(":")[0] for op in results["buggy"].trace.records}
    assert sites == {"tests/live_app/reservations.py"}  # the unmodified Gate 2A application ran


def test_planned_rows_with_database_defaults_are_verified_by_type_and_count(target):
    d = copy.deepcopy(sc.DOCUMENT)
    d["state_goals"][0]["constraints"][2]["value"] = {"type": "int", "value": 1}
    d["state_goals"].append({"entity": "reservation", "label": "held", "count": 1, "constraints": [
        {"field": "event_id", "op": "eq", "value": {"type": "int", "value": 1}},
        {"field": "holder", "op": "eq", "value": {"type": "str", "value": "carol"}},
        {"field": "qty", "op": "eq", "value": {"type": "int", "value": 1}}]})
    p = pa.plan(d)
    fx = pa.PlannedReservations(p)
    digest = fx.verify_initial(target)
    admin = target.admin()
    try:
        state = fx.read_state(admin)
    finally:
        admin.close()
    assert state["reservations"] == [{"id": 1, "event_id": 1, "holder": "carol", "qty": 1}]  # id from the serial
    assert fx.check(state) == [] and len(digest) == 64


def test_a_state_that_isnt_the_planned_one_is_refused_before_any_handler_runs(target, monkeypatch):
    p = pa.plan(sc.DOCUMENT)
    fx = pa.PlannedReservations(p)
    real_reset = fx.reset

    def drifted(conn, seed=None):
        real_reset(conn, seed)
        conn.execute("UPDATE ds_run.events SET capacity = 4")

    monkeypatch.setattr(fx, "reset", drifted)
    with pytest.raises(pa.AdapterRefused, match="isn't the planned one"):
        fx.verify_initial(target)


def test_the_largest_int4_is_inserted_and_read_back_exactly(target):
    d = copy.deepcopy(sc.DOCUMENT)
    d["state_goals"][0]["constraints"][1]["value"] = {"type": "int", "value": 2**31 - 1}
    fx = pa.PlannedReservations(pa.plan(d))
    fx.verify_initial(target)
    admin = target.admin()
    try:
        assert fx.read_state(admin)["events"] == [{"id": 1, "capacity": 2147483647, "reserved": 0}]
    finally:
        admin.close()
