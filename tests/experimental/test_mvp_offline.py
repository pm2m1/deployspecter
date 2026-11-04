"""EXPERIMENTAL prototype, offline: validator, sanitizer, exploration logic, oracle, target rules. No database."""

from __future__ import annotations

import copy

import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental import oracle
from deployspecter.experimental import stepping as st
from deployspecter.experimental import workloads as wl


def good():
    return wl._prop([("j", "job", "backfill_v2", {"batch": 10}),
                     ("a", "v1", "reserve", {"event_id": 1, "holder": "alice", "qty": 2})],
                    [{"id": 1, "capacity": 5, "reserved": 0}])


class TestValidator:
    def test_a_good_proposal_becomes_registry_invocations(self):
        w = wl.validate(good(), source="t", label="x")
        assert isinstance(w, wl.Workload)
        assert w.invocations[1] == {"actor": "a", "app_version": "v1", "handler": "reserve",
                                    "args": {"event_id": 1, "holder": "alice", "qty": 2}}

    @pytest.mark.parametrize("change, reason", [
        (lambda p: p["invocations"][0].update(handler="drop_table"), "unknown handler job.drop_table"),
        (lambda p: p["invocations"][1].update(app_version="v3"), "unknown handler v3.reserve"),
        (lambda p: p["invocations"][1]["args"][2].update(kind="str", int_value=None, str_value="2"),
         "qty must be an integer"),
        (lambda p: p["invocations"][1]["args"][2].update(int_value=1000), "qty must be in"),
        (lambda p: p["invocations"][1]["args"].pop(), "takes exactly"),
        (lambda p: p["invocations"][1]["args"][1].update(str_value="x'); DROP TABLE events; --"),
         "holder must be an identifier"),
        (lambda p: p["invocations"][1]["args"][0].update(int_value=7), "event 7 isn't seeded"),
        (lambda p: p.update(migration_phase="contract"), "unsupported migration phase"),
        (lambda p: p["seed_events"][0].update(reserved=9), "0 <= reserved <= capacity"),
        (lambda p: p["seed_events"][0].update(reserved=2), "must equal its seeded reservations"),
        (lambda p: p["seed_events"][0].update(reserved_v2=3), "must equal reserved"),
        (lambda p: p["invocations"][1].update(actor="j"), "actor names must be unique"),
        (lambda p: p.update(schema_version="ds-mvp-workload/0"), "schema_version"),
        (lambda p: p["invocations"].extend(copy.deepcopy(p["invocations"]) * 2), "1 to 4 invocations"),
        (lambda p: p.update(sql="DELETE FROM events"), "schema:"),
        (lambda p: p["invocations"][0].update(code="import os"), "schema:"),
    ])
    def test_every_unsafe_or_impossible_proposal_is_rejected_with_a_reason(self, change, reason):
        p = good()
        change(p)
        out = wl.validate(p, source="t", label="x")
        assert isinstance(out, wl.Rejection) and any(reason in r for r in out.reasons), out

    def test_a_release_must_name_a_seeded_reservation(self):
        p = wl._prop([("a", "v1", "release", {"reservation_id": 5})], [{"id": 1, "capacity": 5, "reserved": 0}])
        out = wl.validate(p, source="t", label="x")
        assert isinstance(out, wl.Rejection) and "reservation 5 isn't seeded" in out.reasons[0]

    def test_the_deterministic_sources_are_all_valid_and_stable(self):
        for label, prop in wl.curated() + wl.systematic(50):
            assert isinstance(wl.validate(prop, source="d", label=label), wl.Workload), label
        assert [x for x, _ in wl.systematic(50)] == [x for x, _ in wl.systematic(50)]
        assert len(wl.systematic(50)) == 10  # C(5, 2) pairs of the five registry handlers


class TestSanitizerAndIds:
    def test_numbers_are_kept_and_text_is_digested(self):
        out = st.sanitize([1, 2**63, -0.0, None, True, "alice", b"x"])
        assert out[:5] == [1, 2**63, -0.0, None, True]
        assert out[5] == {"str": 5, "sha256": "2bd806c97f0e"} and out[6]["bytes"] == 1
        assert "alice" not in repr(out)

    def test_statement_ids_ignore_whitespace_only(self):
        assert st.statement_id("SELECT  1\n") == st.statement_id("SELECT 1")
        assert st.statement_id("SELECT 1") != st.statement_id("SELECT 2")


class TestExplore:
    def test_the_frontier_branches_at_every_decision_and_dedupes(self):
        """A fake two-actor program with two operations each: exploration reaches all 6 interleavings."""
        def run_with(prefix):
            chosen, left = [], {"a": 2, "b": 2}
            decisions = []
            for i in range(4):
                enabled = [n for n in ("a", "b") if left[n]]
                c = prefix[i] if i < len(prefix) and prefix[i] in enabled else enabled[0]
                decisions.append(st.Decision(i, enabled, {}, [], None, c, "schedule"))
                chosen.append(c)
                left[c] -= 1
            return st.Trace("r", list(prefix), chosen, decisions, [], {}, "completed", [], "expand")
        traces, exhausted = st.explore(run_with, max_runs=20)
        assert exhausted and len({tuple(t.chosen) for t in traces}) == 6
        traces, exhausted = st.explore(run_with, max_runs=3)
        assert not exhausted and len(traces) == 3  # a budget is reported as a budget, never as completeness


class TestOracle:
    def state(self, reserved=2, v2=2, qty=2):
        return {"events": [{"id": 1, "capacity": 5, "reserved": reserved, "reserved_v2": v2}],
                "reservations": [{"id": 1, "event_id": 1, "holder": "a", "qty": qty}]}

    def test_each_invariant(self):
        assert oracle.check(self.state(), backfill_done=True) == []
        assert oracle.check(self.state(v2=0), backfill_done=True)[0].startswith("I3")
        assert oracle.check(self.state(qty=1), backfill_done=False)[0].startswith("I1")
        assert oracle.check(self.state(reserved=6, v2=None, qty=6), backfill_done=False)[0].startswith("I2")
        assert oracle.check(self.state(v2=None), backfill_done=True)[0].startswith("I4")
        assert oracle.check(self.state(v2=None), backfill_done=False) == []

    def test_the_digest_is_typed(self):
        a = {"events": [{"id": 1, "capacity": 5, "reserved": 0, "reserved_v2": None}], "reservations": []}
        b = copy.deepcopy(a)
        b["events"][0]["reserved_v2"] = 0
        c = copy.deepcopy(a)
        c["events"][0]["reserved"] = "0"
        assert len({oracle.digest(a), oracle.digest(b), oracle.digest(c)}) == 3


class TestTarget:
    @pytest.mark.parametrize("host, port, db", [("127.0.0.1", 55432, "ds_mvp"), ("10.0.0.5", 55441, "ds_mvp"),
                                                 ("127.0.0.1", 55441, "deployspecter_test")])
    def test_only_the_disposable_local_target_is_accepted(self, host, port, db):
        with pytest.raises(hz.TargetRefused):
            hz.Target(host, port, "ds_mvp", "x", db).conninfo()

    def test_unknown_schedule_actors_are_refused(self):
        a = st.Actor("a", st.Handler("v1", "h", lambda conn: None, frozenset(), {}), {})
        with pytest.raises(st.Refused, match="unknown actors"):
            st.Run([a], lambda: None, None, schedule=["a", "zz"])
