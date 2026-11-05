"""EXPERIMENTAL prototype, live: real handlers, real psycopg 3, a real disposable PostgreSQL 16.

Skipped unless research_tools/mvp_pg.sh has started the disposable instance (DS_MVP_PORT, password file). Never the
protected 55432. These tests show what the prototype does on PostgreSQL; they are not certified Gate A2 evidence.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental import stepping as st
from deployspecter.experimental import workloads as wl
from deployspecter.experimental.apps import reservations_mixed as app

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")

# The backfill reads, an old-version reservation commits, then the backfill writes.
RACE = ["j", "j", "a", "a", "a", "a", "a", "j", "j"]


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


def backfill_and_reserve():
    return wl.validate(wl._prop([("j", "job", "backfill_v2", {"batch": 10}),
                                 ("a", "v1", "reserve", {"event_id": 1, "holder": "alice", "qty": 2})],
                                [{"id": 1, "capacity": 5, "reserved": 0}]), source="test", label="backfill race")


def writes(result, actor, sql_prefix):
    return [r.params for r in result.trace.records if r.actor == actor and r.sql.startswith(sql_prefix)]


class TestRealStepping:
    def test_a_later_write_is_computed_from_this_schedules_read_not_replayed(self, target):
        w = backfill_and_reserve()
        race = hz.run_once(target, w, variant="buggy", schedule=RACE)
        serial = hz.run_once(target, w, variant="buggy", schedule=["a"] * 5 + ["j"] * 4)
        # the backfill read 0 in one schedule and 2 in the other, and wrote what it had read each time
        assert [r.rows for r in race.trace.records if r.actor == "j" and r.sql.startswith("SELECT")][0] == [[1, 0]]
        assert [r.rows for r in serial.trace.records if r.actor == "j" and r.sql.startswith("SELECT")][0] == [[1, 2]]
        assert writes(race, "j", "UPDATE events SET reserved_v2")[0] == [0, 1]
        assert writes(serial, "j", "UPDATE events SET reserved_v2")[0] == [2, 1]
        assert race.violations and not serial.violations

    def test_the_buggy_build_violates_and_the_fixed_build_holds_on_the_same_schedule(self, target):
        w = backfill_and_reserve()
        buggy = hz.run_once(target, w, variant="buggy", schedule=RACE)
        fixed = hz.run_once(target, w, variant="fixed", schedule=RACE)
        assert buggy.violations == ["I3 event 1: reserved_v2 0 differs from reserved 2"]
        assert fixed.violations == [] and fixed.final_state["events"][0]["reserved_v2"] == 2
        assert any(d.blocked == {"a": ["j"]} and d.source == "deferred" for d in fixed.trace.decisions)

    def test_the_same_schedule_reproduces_from_the_verified_initial_state(self, target):
        w = backfill_and_reserve()
        runs = [hz.run_once(target, w, variant="buggy", schedule=RACE) for _ in range(3)]
        assert len({r.trace.fingerprint() for r in runs}) == 1 and len({r.initial_digest for r in runs}) == 1
        assert all(r.violations == runs[0].violations for r in runs)

    def test_bounded_exploration_finds_the_race_and_the_fixed_build_never_violates(self, target):
        w = backfill_and_reserve()
        buggy, b_exhausted = hz.explore_workload(target, w, variant="buggy", max_runs=200)
        fixed, f_exhausted = hz.explore_workload(target, w, variant="fixed", max_runs=200)
        # Row locks prune interleavings in both builds (the backfill's UPDATE and the reservation's FOR UPDATE each
        # hold the row until commit); the fixed build's FOR UPDATE read prunes more. Observed: 61 and 30.
        assert b_exhausted and len({tuple(r.trace.chosen) for r in buggy}) == len(buggy)
        assert f_exhausted and len(fixed) < len(buggy)
        assert any(r.violations for r in buggy) and not any(r.violations for r in fixed)
        _, small = hz.explore_workload(target, w, variant="buggy", max_runs=40)
        assert small is False  # a budget that runs out is reported as such, never as completeness

    def test_a_wrong_initial_state_digest_refuses_before_any_handler_runs(self, target):
        with pytest.raises(st.Refused, match="isn't the verified one"):
            hz.run_once(target, backfill_and_reserve(), variant="buggy", schedule=RACE, expected_digest="0" * 64)

    def test_every_operation_carries_its_provenance(self, target):
        r = hz.run_once(target, backfill_and_reserve(), variant="buggy", schedule=RACE)
        for op in r.trace.records:
            p = op.provenance
            assert p["app_version"] in ("v1", "job") and p["handler"] in ("reserve", "backfill_v2")
            assert p["invocation_id"] == f"{r.run_id}:{op.actor}" and p["migration_phase"] == "expand"
            assert p["call_site"].startswith("src/deployspecter/experimental/apps/reservations_mixed.py:")
            assert p["thread"] == f"ds-actor-{op.actor}" and p["session"]["backend_pid"] > 0
            assert p["txn"] == f"{op.actor}:1" and op.grant is not None
        assert [op.grant for op in sorted(r.trace.records, key=lambda o: o.grant)] == list(range(9))
        boundaries = [(op.actor, op.kind, op.sql) for op in r.trace.records if op.kind.startswith("transaction")]
        assert ("a", "transaction_exit", "COMMIT") in boundaries and ("j", "transaction_enter", "BEGIN") in boundaries


# ------------------------------------------------------------------ adversarial cases with trusted test handlers


def run_handlers(target, *specs, schedule=(), limits=None, seed=None):
    """Run test-only trusted handlers against the fixture schema."""
    admin = target.admin()
    try:
        app.reset(admin, seed or {"events": [{"id": 1, "capacity": 5, "reserved": 0}, {"id": 2, "capacity": 5,
                                                                                      "reserved": 0}]})
        lim = limits or st.Limits()
        actors = [st.Actor(name, st.Handler("test", fn.__name__, fn, frozenset(st.normal_sql(s) for s in sqls), {}),
                           {}) for name, fn, sqls in specs]
        connect = st.actor_connection_factory(target.conninfo(), lim, search_path=app.SCHEMA)
        return st.Run(actors, connect, admin, schedule=list(schedule), limits=lim, run_id="adv").execute()
    finally:
        admin.close()


LOCK1 = "SELECT reserved FROM events WHERE id = 1 FOR UPDATE"
LOCK2 = "SELECT reserved FROM events WHERE id = 2 FOR UPDATE"


class TestAdversarial:
    def test_a_rollback_is_recorded_and_leaves_no_change(self, target):
        def raises(conn):
            with conn.transaction():
                conn.execute("UPDATE events SET reserved = 1 WHERE id = 1")
                raise ValueError("application error after a write")
        t = run_handlers(target, ("a", raises, ["UPDATE events SET reserved = 1 WHERE id = 1"]))
        assert t.actors["a"]["state"] == "errored" and t.actors["a"]["exception_class"] == "application_exception"
        assert [(o.kind, o.sql) for o in t.records][-1] == ("transaction_exit", "ROLLBACK")
        admin = target.admin()
        assert admin.execute(f"SELECT reserved FROM {app.SCHEMA}.events WHERE id = 1").fetchone()[0] == 0
        admin.close()

    def test_a_handler_exception_is_a_result_not_a_crash(self, target):
        def boom(conn):
            conn.execute("SELECT 1")
            raise RuntimeError("worker failed")
        t = run_handlers(target, ("a", boom, ["SELECT 1"]))
        assert t.outcome == "completed" and t.actors["a"]["exception"].startswith("RuntimeError")

    def test_a_real_deadlock_is_left_to_postgresql_and_classified(self, target):
        def ab(conn):
            with conn.transaction():
                conn.execute(LOCK1)
                conn.execute(LOCK2)

        def ba(conn):
            with conn.transaction():
                conn.execute(LOCK2)
                conn.execute(LOCK1)
        t = run_handlers(target, ("a", ab, [LOCK1, LOCK2]), ("b", ba, [LOCK1, LOCK2]), schedule=["a", "b", "a", "b"])
        classes = {a["exception_class"] for a in t.actors.values()}
        assert t.outcome == "completed" and "deadlock_detected" in classes
        assert any(d.blocked for d in t.decisions)

    def test_unauthorized_sql_is_refused_before_the_server(self, target):
        def sneaky(conn):
            conn.execute("DELETE FROM events")
        t = run_handlers(target, ("a", sneaky, ["SELECT 1"]))
        assert t.actors["a"]["exception_class"] == "refused" and t.records[0].error_class == "unauthorized_sql"
        assert t.records[0].grant is None  # never granted, never executed
        admin = target.admin()
        assert admin.execute(f"SELECT count(*) FROM {app.SCHEMA}.events").fetchone()[0] == 2
        admin.close()

    def test_bypasses_are_refused(self, target):
        def named(conn):
            conn.cursor(name="srv")

        def pipe(conn):
            with conn.pipeline():
                pass

        def copy(conn):
            conn.cursor().copy("COPY events TO STDOUT")
        t = run_handlers(target, ("a", named, []), ("b", pipe, []), ("c", copy, []))
        assert {a["exception_class"] for a in t.actors.values()} == {"refused"}

    def test_use_from_another_thread_is_refused(self, target):
        seen = []

        def spawns(conn):
            def other():
                try:
                    conn.execute("SELECT 1")
                except st.Refused as exc:
                    seen.append(str(exc))
            th = threading.Thread(target=other)
            th.start()
            th.join()
            conn.execute("SELECT 1")
        t = run_handlers(target, ("a", spawns, ["SELECT 1"]))
        assert seen and "another thread" in seen[0] and t.actors["a"]["state"] == "finished"
        assert [r.provenance["thread"] for r in t.records] == ["ds-actor-a"]

    def test_a_handler_that_never_returns_times_out_and_halts(self, target):
        def stalls(conn):
            conn.execute("SELECT 1")
            time.sleep(3)
            conn.execute("SELECT 1")
        t = run_handlers(target, ("a", stalls, ["SELECT 1"]), limits=st.Limits(python_step_s=0.5, run_s=10))
        assert t.outcome == "timeout" and any("computed for more than" in d for d in t.diagnostics)

    def test_savepoints_and_executemany_are_gated(self, target):
        INS = "INSERT INTO reservations (event_id, holder, qty) VALUES (%s, %s, %s)"

        def nested(conn):
            with conn.transaction():
                with conn.transaction():
                    conn.cursor().executemany(INS, [(1, "x", 1), (1, "y", 1)])
        t = run_handlers(target, ("a", nested, [INS]))
        kinds = [(o.kind, o.sql[:20]) for o in t.records]
        assert kinds == [("transaction_enter", "BEGIN"), ("transaction_enter", "SAVEPOINT <auto>"),
                         ("executemany", INS[:20]), ("transaction_exit", "RELEASE SAVEPOINT"),
                         ("transaction_exit", "COMMIT")]
        assert t.records[2].params == [[1, {"str": 1, "sha256": "2d711642b726"}, 1],
                                       [1, {"str": 1, "sha256": "a1fce4363854"}, 1]]


# ------------------------------------------------------------------ P0: the server log corroborates the driver trace


def corroborated_handlers(target, *specs, schedule=()):
    admin = target.admin()
    try:
        app.reset(admin, {"events": [{"id": 1, "capacity": 5, "reserved": 0}, {"id": 2, "capacity": 5,
                                                                              "reserved": 0}]})
        lim = st.Limits()
        actors = [st.Actor(name, st.Handler("test", fn.__name__, fn, frozenset(st.normal_sql(s) for s in sqls), {}),
                           {}) for name, fn, sqls in specs]
        connect = st.actor_connection_factory(target.conninfo(), lim, search_path=app.SCHEMA)
        run = st.Run(actors, connect, admin, schedule=list(schedule), limits=lim, run_id="corr")
        return hz.corroborated(admin, run, user=target.user)
    finally:
        admin.close()


class TestServerLogCorroboration:
    def test_buggy_and_fixed_traces_match_the_server_log(self, target):
        for variant in ("buggy", "fixed"):
            r = hz.run_once(target, backfill_and_reserve(), variant=variant, schedule=RACE, corroborate=True)
            c = r.corroboration
            assert c["ok"], c["problems"]
            assert c["matched"] == 9 and c["params_compared"] == 9 and c["params_unchecked"] == 0
            assert sorted(c["sessions"].values()) == ["a", "j"]

    def test_the_evidence_bundle_binds_digests_and_verdicts(self, target):
        r = hz.run_once(target, backfill_and_reserve(), variant="buggy", schedule=RACE, corroborate=True)
        b = r.bundle()
        assert b["initial_state_digest"] == r.initial_digest and len(b["trace_sha256"]) == 64
        assert b["oracle_violations"] == r.violations and b["corroboration"]["ok"] is True
        assert b["final_state_digest"] != b["initial_state_digest"]

    def test_a_raw_pgconn_bypass_is_caught_by_the_server_log(self, target):
        def bypass(conn):
            conn.execute("SELECT 1")
            conn.pgconn.exec_(b"UPDATE events SET reserved = 9 WHERE id = 2")  # invisible to the driver hook
        _trace, c = corroborated_handlers(target, ("a", bypass, ["SELECT 1"]))
        assert not c["ok"] and any("a statement the trace doesn't record" in p for p in c["problems"])

    def test_a_connection_the_handler_opens_itself_is_unexplained(self, target):
        def sneaky(conn):
            import psycopg
            with psycopg.connect(**target.conninfo(), autocommit=True) as other:
                other.execute("SELECT 2")
            conn.execute("SELECT 1")
        _trace, c = corroborated_handlers(target, ("a", sneaky, ["SELECT 1"]))
        assert not c["ok"] and any("unexplained session" in p for p in c["problems"])

    def test_a_tampered_trace_value_is_refuted(self, target):
        from deployspecter.experimental.corroborate import corroborate
        admin = target.admin()
        try:
            app.reset(admin, {"events": [{"id": 1, "capacity": 5, "reserved": 0}]})
            from deployspecter.live.serverlog import ServerLog
            log = ServerLog(lambda q, prm=None: admin.execute(q, prm).fetchone())
            log.position()
            admin.execute("SELECT 'ds_mvp_mark_start_aaaaaaaaaaaa'")
            h = app.registry("buggy")[("v1", "reserve")]
            run = st.Run([st.Actor("a", h, {"event_id": 1, "holder": "alice", "qty": 2})],
                         st.actor_connection_factory(target.conninfo(), st.Limits(), search_path=app.SCHEMA),
                         admin, run_id="tamper")
            trace = run.execute()
            hz._wait_gone(admin, [trace.actors["a"]["backend_pid"]])
            admin.execute("SELECT 'ds_mvp_mark_end_aaaaaaaaaaaa'")
            time.sleep(0.2)
            data = log.read()
        finally:
            admin.close()
        ok = corroborate(data, start="ds_mvp_mark_start_aaaaaaaaaaaa", end="ds_mvp_mark_end_aaaaaaaaaaaa",
                         trace=trace, admin_pid=-1, user=target.user, database="ds_mvp")
        assert not ok.ok  # the admin pid is wrong on purpose: its markers' session is then unexplained
        update = next(r for r in trace.records if r.sql.startswith("UPDATE"))
        update.params = [7, 1]  # claim the handler wrote 7, not 2
        bad = corroborate(data, start="ds_mvp_mark_start_aaaaaaaaaaaa", end="ds_mvp_mark_end_aaaaaaaaaaaa",
                          trace=trace, admin_pid=0, user=target.user, database="ds_mvp")
        assert any("isn't the value the trace records" in p for p in bad.problems)

    def test_deadlock_errors_savepoints_and_executemany_corroborate(self, target):
        def ab(conn):
            with conn.transaction():
                conn.execute(LOCK1)
                conn.execute(LOCK2)

        def ba(conn):
            with conn.transaction():
                conn.execute(LOCK2)
                conn.execute(LOCK1)
        _t, c = corroborated_handlers(target, ("a", ab, [LOCK1, LOCK2]), ("b", ba, [LOCK1, LOCK2]),
                                      schedule=["a", "b", "a", "b"])
        assert c["ok"], c["problems"]
        INS = "INSERT INTO reservations (event_id, holder, qty) VALUES (%s, %s, %s)"

        def nested(conn):
            with conn.transaction():
                with conn.transaction():
                    conn.cursor().executemany(INS, [(1, "x", 1), (1, "y", 1)])
                try:
                    with conn.transaction():
                        conn.execute("SELECT 1")
                        raise ValueError("roll back the savepoint")
                except ValueError:
                    pass
        _t, c = corroborated_handlers(target, ("a", nested, [INS, "SELECT 1"]))
        assert c["ok"], c["problems"]


class TestEmptyResults:
    def test_a_select_that_returns_no_rows_reaches_the_handler_as_empty(self, target):
        """Regression: the gate rewound every prefetched result with scroll(0), which psycopg refuses on an empty
        one, so a zero-row SELECT used to fail the handler with IndexError."""
        def none_found(conn):
            row = conn.execute("SELECT id FROM events WHERE id = %s", (999,)).fetchone()
            rows = conn.execute("SELECT id FROM events WHERE id = %s", (998,)).fetchall()
            return {"row": row, "rows": rows}

        trace, c = corroborated_handlers(target, ("a", none_found, ["SELECT id FROM events WHERE id = %s"]))
        assert trace.actors["a"]["exception"] is None
        assert trace.raw_results["a"] == {"row": None, "rows": []}
        assert [op.rows for op in trace.records if op.kind == "execute"] == [[], []]
        assert c["ok"], c["problems"]
