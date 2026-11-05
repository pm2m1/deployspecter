"""EXPERIMENTAL, live: the release_seats defect in Gate 2A's synthetic reservations app, and the corrected reference.

The frozen builds (``buggy``, ``fixed`` = lock_row) are run unmodified; ``corrected`` is the separately named
reference in ``apps/reservations_corrected.py``. The oracle is the frozen R1/R2 plus R5 from the declared release
semantics. Skipped unless the disposable MVP PostgreSQL is running.
"""

from __future__ import annotations

import copy
import os
import time
from pathlib import Path

import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental import planning_adapter as pa
from deployspecter.experimental import stepping as st
from deployspecter.research.track_a import scenario as sc

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")

SERIAL = ["a"] * 6 + ["b"] * 6
BOTH_READ_FIRST = ["a", "b"] + ["a"] * 5 + ["b"] * 5


def I(v):  # noqa: N802, E743
    return {"type": "int", "value": v}


def S(v):  # noqa: N802
    return {"type": "str", "value": v}


def release(name, holder):
    return {"name": name, "entrypoint": "release_seats", "application_version": "v1", "role": "request",
            "arguments": [{"name": "holder", "value": S(holder)}]}


def reserve(name, holder, qty):
    return {"name": name, "entrypoint": "reserve_seats", "application_version": "v1", "role": "request",
            "arguments": [{"name": "event_id", "value": I(1)}, {"name": "holder", "value": S(holder)},
                          {"name": "qty", "value": I(qty)}]}


def document(actors, held):
    """Event 1 with capacity 10; ``held`` maps holder -> list of reservation quantities."""
    d = copy.deepcopy(sc.DOCUMENT)
    d["actors"] = actors
    total = sum(q for qs in held.values() for q in qs)
    d["state_goals"][0]["constraints"][1:] = [{"field": "capacity", "op": "eq", "value": I(10)},
                                              {"field": "reserved", "op": "eq", "value": I(total)}]
    for holder, qtys in held.items():
        for i, q in enumerate(qtys):
            d["state_goals"].append({"entity": "reservation", "label": f"{holder}_{i}", "count": 1, "constraints": [
                {"field": "event_id", "op": "eq", "value": I(1)}, {"field": "holder", "op": "eq", "value": S(holder)},
                {"field": "qty", "op": "eq", "value": I(q)}]})
    return d


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


def run(target, doc, variant, schedule, **kw):
    p, r, _ = pa.run_planned(target, doc, variant=variant, schedule=schedule, **kw)
    assert p.outcome == "bridged", p.detail
    return r


def explore(target, doc, variant, max_runs=200):
    p = pa.plan(doc)
    fx = pa.PlannedReservations(p)
    digest = fx.verify_initial(target)
    return hz.explore_app(target, fx, pa.invocations(p), None, variant=variant, max_runs=max_runs, label="x",
                          expected_digest=digest)


DUP_ONE = document([release("a", "jack"), release("b", "jack")], {"jack": [4]})
DUP_TWO = document([release("a", "jack"), release("b", "jack")], {"jack": [4, 4]})


class TestRootCause:
    @pytest.mark.parametrize("variant", ["buggy", "fixed", "corrected"])
    def test_a_sequential_duplicate_release_is_correct_in_every_build(self, target, variant):
        r = run(target, DUP_ONE, variant, SERIAL)
        assert r.violations == []
        assert r.trace.raw_results == {"a": {"status": "released", "released": 4}, "b": {"status": "nothing_held"}}

    @pytest.mark.parametrize("variant", ["buggy", "fixed"])
    def test_a_concurrent_duplicate_release_double_decrements_in_both_frozen_builds(self, target, variant):
        r = run(target, DUP_ONE, variant, BOTH_READ_FIRST, corroborate=True)
        assert r.corroboration["ok"], r.corroboration["problems"]
        assert r.violations[0] == "R1 event 1: reserved=-4 but reservations hold 0"
        assert any(v.startswith("R5 2 caller(s) told released") for v in r.violations)
        deletes = sorted(op.rowcount for op in r.trace.records if op.sql.startswith("DELETE"))
        assert deletes == [0, 1]  # the second DELETE removed nothing, yet its decrement committed

    def test_the_corrected_build_serializes_the_same_schedule(self, target):
        r = run(target, DUP_ONE, "corrected", BOTH_READ_FIRST, corroborate=True)
        assert r.corroboration["ok"], r.corroboration["problems"]
        assert r.violations == []
        assert sorted(x["status"] for x in r.trace.raw_results.values()) == ["nothing_held", "released"]

    def test_with_two_reservations_the_corrected_build_releases_both(self, target):
        r = run(target, DUP_TWO, "corrected", BOTH_READ_FIRST, corroborate=True)
        assert r.violations == [] and r.final_state["reservations"] == []
        assert r.final_state["events"][0]["reserved"] == 0


class TestCorrectedUnderExploration:
    @pytest.mark.parametrize("doc", [DUP_ONE, DUP_TWO], ids=["one_held", "two_held"])
    def test_frozen_builds_violate_and_corrected_never_does(self, target, doc):
        counts = {}
        for variant in ("buggy", "fixed", "corrected"):
            results, exhausted = explore(target, doc, variant)
            assert exhausted
            counts[variant] = sum(bool(r.violations) for r in results)
            assert all(r.trace.outcome == "completed" for r in results)
        assert counts["buggy"] > 0 and counts["fixed"] > 0 and counts["corrected"] == 0

    def test_two_independent_releases_on_one_event(self, target):
        doc = document([release("a", "jack"), release("b", "jill")], {"jack": [3], "jill": [2]})
        results, exhausted = explore(target, doc, "corrected")
        assert exhausted and not any(r.violations for r in results)
        assert all(r.final_state["events"][0]["reserved"] == 0 for r in results)
        buggy, _ = explore(target, doc, "buggy")
        assert any(r.violations for r in buggy)  # the frozen buggy build loses one of the two decrements

    def test_release_racing_a_reservation_by_the_same_holder(self, target):
        doc = document([reserve("a", "jack", 2), release("b", "jack")], {})
        results, exhausted = explore(target, doc, "corrected")
        assert exhausted and not any(r.violations for r in results)
        outcomes = {r.trace.raw_results["b"]["status"] for r in results}
        assert outcomes == {"released", "nothing_held"}  # both serial orders are legitimate

    def test_an_unknown_holder_releases_nothing(self, target):
        doc = document([release("a", "nobody"), release("b", "jack")], {"jack": [1]})
        r = run(target, doc, "corrected", SERIAL, corroborate=True)
        assert r.corroboration["ok"] and r.violations == []
        assert r.trace.raw_results["a"] == {"status": "nothing_held"}


def hold_event_lock(conn):
    """Test-only actor: holds the event's row lock across a long Python step."""
    conn.execute("SELECT reserved FROM events WHERE id = %s FOR UPDATE", (1,))
    conn.execute("SELECT 1")
    time.sleep(1.0)
    conn.commit()
    return "held"


def test_a_release_that_times_out_rolls_back_and_a_retry_succeeds(target):
    doc = document([release("a", "jack"), release("r", "jack")], {"jack": [4]})
    p = pa.plan(doc)
    fx = pa.PlannedReservations(p)
    fx.verify_initial(target)
    handlers = fx.registry("corrected")
    hold = st.Handler("test", "hold_event_lock", hold_event_lock,
                      frozenset(st.normal_sql(s) for s in ("SELECT reserved FROM events WHERE id = %s FOR UPDATE",
                                                           "SELECT 1")), {})
    lim = st.Limits(lock_ms=300)
    admin = target.admin()
    try:
        initial = fx.read_state(admin)
        actors = [st.Actor("h", hold, {}), st.Actor("a", handlers[("v1", "release_seats")], {"holder": "jack"}),
                  st.Actor("r", handlers[("v1", "release_seats")], {"holder": "jack"})]
        connect = st.actor_connection_factory(target.conninfo(), lim, search_path=fx.SCHEMA, autocommit=False)
        run_ = st.Run(actors, connect, admin, schedule=["h", "a", "a", "h"] + ["r"] * 6, limits=lim, run_id="retry")
        trace, corroboration = hz.corroborated(admin, run_, user=target.user)
        final = fx.read_state(admin)
    finally:
        admin.close()
    assert trace.actors["a"]["exception_class"] == "lock_not_available"
    assert trace.raw_results["r"] == {"status": "released", "released": 4}
    assert [op.kind for op in trace.records if op.actor == "a"][-1] == "cleanup_rollback"
    assert fx.check_run(initial, final, {"r": trace.raw_results["r"]}) == []
    assert final["reservations"] == [] and final["events"][0]["reserved"] == 0
    assert corroboration["ok"], corroboration["problems"]  # incl. the 55P03 ERROR row and the cleanup ROLLBACK


def test_an_initial_state_that_already_violates_the_oracle_is_refused(target):
    doc = document([release("a", "jack"), release("b", "jill")], {"jack": [4]})
    doc["state_goals"][0]["constraints"][2]["value"] = I(1)  # reserved 1, but jack holds 4
    with pytest.raises(st.Refused, match="already violates"):
        run(target, doc, "corrected", SERIAL)


class TestMutants:
    """The frozen oracle's reach over four mutants of the corrected release, under exhaustive exploration."""

    @pytest.mark.parametrize("mutant, dup_one, independent", [
        ("no_reservation_lock", 0, 0),  # the guard turns the double release into an error: safe, not live
        ("no_guard", 0, 0),  # equivalent under the locks
        ("no_event_lock", 0, 170),  # a lost decrement, only between two holders
        ("off_by_one", 10, 36),
    ])
    def test_which_mutants_the_oracle_distinguishes(self, target, mutant, dup_one, independent):
        doc = document([release("a", "jack"), release("b", "jill")], {"jack": [3], "jill": [2]})
        counts = []
        for d in (DUP_ONE, doc):
            results, exhausted = explore(target, d, f"mutant_{mutant}")
            assert exhausted
            counts.append(sum(bool(r.violations) for r in results))
        assert counts == [dup_one, independent]
