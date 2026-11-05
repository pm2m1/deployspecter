"""EXPERIMENTAL, live: async psycopg stepping (``stepping_async``) on a disposable PostgreSQL 16.

Skipped unless the disposable MVP PostgreSQL is running. Not Gate A2 evidence. Exploring the scheduler's decision
points is not exploring every possible asyncio interleaving.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psycopg
import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental import stepping as st
from deployspecter.experimental import stepping_async as sa
from deployspecter.experimental import workloads as wl
from deployspecter.experimental.apps import reservations_mixed as sync_app
from deployspecter.experimental.apps import reservations_mixed_async as A

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")
RACE = ["j", "j", "a", "a", "a", "a", "a", "j", "j"]


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


def race_workload():
    return wl.validate(wl._prop([("j", "job", "backfill_v2", {"batch": 10}),
                                 ("a", "v1", "reserve", {"event_id": 1, "holder": "alice", "qty": 2})],
                                [{"id": 1, "capacity": 5, "reserved": 0}]), source="test", label="race")


def run_async(target, w, variant, schedule, **kw):
    return hz.run_app(target, A, w.invocations, w.seed, variant=variant, schedule=schedule, label=w.label, **kw)


def steps(r):
    return [(op.actor, op.kind, op.sql, op.params, op.rows) for op in sorted(r.trace.records, key=lambda o: o.grant)]


class TestAsyncFixture:
    def test_buggy_violates_and_fixed_holds_both_corroborated(self, target):
        w = race_workload()
        buggy = run_async(target, w, "buggy", RACE, corroborate=True)
        fixed = run_async(target, w, "fixed", RACE, corroborate=True)
        assert buggy.initial_digest == fixed.initial_digest
        assert buggy.violations == ["I3 event 1: reserved_v2 0 differs from reserved 2"] and fixed.violations == []
        for r in (buggy, fixed):
            assert r.corroboration["ok"], r.corroboration["problems"]
            assert r.corroboration["matched"] == 9 and r.corroboration["params_unchecked"] == 0
            assert {op.provenance["task"] for op in r.trace.records} == {"ds-actor-j", "ds-actor-a"}
            assert all(op.provenance["call_site"].startswith(
                "src/deployspecter/experimental/apps/reservations_mixed_async.py:") for op in r.trace.records)

    def test_the_async_engine_reproduces_the_sync_engines_trace_step_for_step(self, target):
        w = race_workload()
        for variant in ("buggy", "fixed"):
            a = run_async(target, w, variant, RACE)
            s = hz.run_once(target, w, variant=variant, schedule=RACE)
            assert steps(a) == steps(s) and a.final_state == s.final_state

    def test_a_write_is_computed_from_what_this_schedule_read(self, target):
        w = race_workload()
        race = run_async(target, w, "buggy", RACE)
        serial = run_async(target, w, "buggy", ["a"] * 5 + ["j"] * 4)
        write = [op.params for op in race.trace.records if op.sql.startswith("UPDATE events SET reserved_v2")]
        write_serial = [op.params for op in serial.trace.records if op.sql.startswith("UPDATE events SET reserved_v2")]
        assert write == [[0, 1]] and write_serial == [[2, 1]]

    def test_exploration_matches_the_sync_engine(self, target):
        w = race_workload()
        for variant, want in (("buggy", (61, 46)), ("fixed", (30, 0))):
            results, exhausted = hz.explore_app(target, A, w.invocations, w.seed, variant=variant, max_runs=200,
                                                label="race")
            assert exhausted and (len(results), sum(bool(r.violations) for r in results)) == want


SEED = {"events": [{"id": 1, "capacity": 5, "reserved": 0}, {"id": 2, "capacity": 5, "reserved": 0}]}


def run_handlers(target, *specs, schedule=(), limits=None, corroborate=True):
    """Custom async handlers (name, coroutine fn, allowed statements) on the fixture's schema."""
    admin = target.admin()
    try:
        sync_app.reset(admin, SEED)
        lim = limits or st.Limits()
        actors = [st.Actor(n, st.Handler("test", fn.__name__, fn, frozenset(st.normal_sql(s) for s in sqls), {}), {})
                  for n, fn, sqls in specs]
        connect = sa.async_actor_connection_factory(target.conninfo(), lim, search_path=A.SCHEMA)
        run = sa.AsyncRun(actors, connect, hz.async_monitor(target), schedule=list(schedule), limits=lim,
                          run_id="async")
        if corroborate:
            trace, c = hz.corroborated(admin, run, user=target.user)
        else:
            trace, c = asyncio.run(run.execute()), None
        leftovers = admin.execute("SELECT count(*) FROM pg_stat_activity WHERE pid = ANY(%s)",
                                  ([a["backend_pid"] for a in trace.actors.values()],)).fetchone()[0]
        locks = admin.execute("SELECT count(*) FROM pg_locks WHERE pid = ANY(%s)",
                              ([a["backend_pid"] for a in trace.actors.values()],)).fetchone()[0]
        return trace, c, leftovers, locks
    finally:
        admin.close()


LOCK = "SELECT reserved FROM events WHERE id = %s FOR UPDATE"


class TestAsyncControl:
    def test_cancellation_during_a_blocked_query_cleans_up_completely(self, target):
        async def holder(conn):
            async with conn.transaction():
                await conn.execute(LOCK, (1,))
                await conn.execute("SELECT 1")  # granted after the waiter has blocked behind this lock
                await asyncio.sleep(30)  # then holds the lock far past the run's deadline

        async def waiter(conn):
            async with conn.transaction():
                await conn.execute(LOCK, (1,))  # blocks behind the holder

        trace, c, leftovers, locks = run_handlers(target, ("h", holder, [LOCK, "SELECT 1"]), ("w", waiter, [LOCK]),
                                                  schedule=["h", "h", "w", "w", "h"],
                                                  limits=st.Limits(run_s=1.5, python_step_s=60, lock_ms=20000))
        assert trace.outcome == "timeout"
        blocked = [op for op in trace.records if op.actor == "w" and op.sql == st.normal_sql(LOCK)][0]
        assert blocked.outcome == "error" and blocked.sqlstate == "57014"  # canceled on the server
        assert trace.actors["h"]["state"] == "aborted"  # its task was cancelled mid-sleep
        assert any("cancelled" in d for d in trace.diagnostics)
        assert leftovers == 0 and locks == 0  # every session closed, no lock left behind
        assert c["ok"], c["problems"]

    def test_borrowing_another_tasks_connection_is_refused(self, target):
        shared = {}

        async def owner(conn):
            shared["conn"] = conn
            await conn.execute("SELECT 1")
            return "own"

        async def borrower(_conn):
            while "conn" not in shared:
                await asyncio.sleep(0.01)
            await shared["conn"].execute("SELECT 1")

        trace, _c, leftovers, _ = run_handlers(target, ("a", owner, ["SELECT 1"]), ("b", borrower, ["SELECT 1"]),
                                               corroborate=False)
        assert trace.actors["b"]["exception_class"] == "refused"
        assert "another task" in trace.actors["b"]["exception"]
        assert trace.raw_results["a"] == "own" and leftovers == 0

    def test_a_deadlock_is_detected_by_postgresql_and_classified(self, target):
        def crossing(first, second):
            async def fn(conn):
                async with conn.transaction():
                    await conn.execute(LOCK, (first,))
                    await conn.execute(LOCK, (second,))
                return "done"
            fn.__name__ = f"lock_{first}_{second}"
            return fn

        trace, c, leftovers, locks = run_handlers(target, ("a", crossing(1, 2), [LOCK]),
                                                  ("b", crossing(2, 1), [LOCK]),
                                                  schedule=["a", "a", "b", "b", "a", "b"])
        classes = sorted(filter(None, (a["exception_class"] for a in trace.actors.values())))
        assert classes == ["deadlock_detected"]
        assert sorted(str(r) for r in trace.raw_results.values()) == ["None", "done"]
        assert leftovers == 0 and locks == 0
        assert c["ok"], c["problems"]  # incl. the 40P01 ERROR row and the transaction's ROLLBACK

    def test_savepoints_executemany_and_rollback_are_gated_and_corroborated(self, target):
        async def nested(conn):
            async with conn.transaction():
                await conn.execute("UPDATE events SET reserved = %s WHERE id = %s", (1, 1))
                try:
                    async with conn.transaction():
                        await conn.execute("UPDATE events SET reserved = %s WHERE id = %s", (9, 2))
                        raise ValueError("undo the inner block")
                except ValueError:
                    pass
                cur = conn.cursor()
                await cur.executemany("UPDATE events SET capacity = %s WHERE id = %s", [(6, 1), (7, 2)])
            await conn.rollback()  # nothing open: psycopg sends nothing, and the trace says so
            cur = await conn.execute("SELECT id, capacity, reserved FROM events ORDER BY id")
            return await cur.fetchall()

        allowed = ["UPDATE events SET reserved = %s WHERE id = %s", "UPDATE events SET capacity = %s WHERE id = %s",
                   "SELECT id, capacity, reserved FROM events ORDER BY id"]
        trace, c, _l, _k = run_handlers(target, ("a", nested, allowed))
        assert trace.raw_results["a"] == [(1, 6, 1), (2, 7, 0)]
        kinds = [op.sql for op in trace.records if op.kind.startswith("transaction")]
        assert kinds == ["BEGIN", "SAVEPOINT <auto>", "ROLLBACK TO SAVEPOINT + RELEASE", "COMMIT"]
        assert c["ok"], c["problems"]

    @pytest.mark.parametrize("bad", ["named", "copy", "stream", "pipeline", "unlisted"])
    def test_unsupported_driver_features_fail_closed(self, target, bad):
        async def fn(conn):
            if bad == "named":
                conn.cursor(name="srv")
            elif bad == "copy":
                async with conn.cursor().copy("COPY events TO STDOUT"):
                    pass
            elif bad == "stream":
                async for _row in conn.cursor().stream("SELECT 1"):
                    pass
            elif bad == "pipeline":
                async with conn.pipeline():
                    pass
            else:
                await conn.execute("DELETE FROM events")

        trace, c, _l, _k = run_handlers(target, ("a", fn, ["SELECT 1"]))
        assert trace.actors["a"]["exception_class"] == "refused"
        assert c["ok"], c["problems"]  # nothing reached the server

    @pytest.mark.parametrize("how", ["raw_pgconn", "direct_cursor", "own_connection"])
    def test_io_that_bypasses_the_hook_is_caught_by_the_server_log(self, target, how):
        info = target.conninfo()

        async def sneak(conn):
            await conn.execute("SELECT 1")
            if how == "raw_pgconn":
                conn.pgconn.exec_(b"SELECT 2")
            elif how == "direct_cursor":
                await psycopg.AsyncCursor(conn).execute("SELECT 3")
            else:
                other = await psycopg.AsyncConnection.connect(**info, application_name="ds_mvp_actor")
                await other.execute("SELECT 4")
                await other.close()

        trace, c, _l, _k = run_handlers(target, ("a", sneak, ["SELECT 1"]))
        assert trace.actors["a"]["exception"] is None
        assert not c["ok"] and c["problems"]


class TestTaskIdentity:
    def test_a_child_task_acts_for_its_actor_and_is_attributed(self, target):
        async def spawns(conn):
            await asyncio.create_task(conn.execute("SELECT 1"))  # e.g. a framework's shielded close
            return "ok"

        trace, c, leftovers, _ = run_handlers(target, ("a", spawns, ["SELECT 1"]))
        op = [o for o in trace.records if o.sql == "SELECT 1"][0]
        assert trace.raw_results["a"] == "ok" and op.provenance["on_behalf_of"] == "a"
        assert op.provenance["task"] != "ds-actor-a" and c["ok"], c["problems"]

    def test_two_concurrent_operations_on_one_connection_are_refused(self, target):
        async def concurrent(conn):
            await asyncio.gather(conn.execute("SELECT 1"), conn.execute("SELECT 1"))

        trace, c, leftovers, _ = run_handlers(target, ("a", concurrent, ["SELECT 1"]))
        assert trace.actors["a"]["exception_class"] == "refused"
        assert "in flight" in trace.actors["a"]["exception"] and leftovers == 0


class TestFrameworkSupport:
    """What a job-queue adapter needs: native-placeholder raw cursors, LISTEN/NOTIFY, and several framework tasks
    taking turns on one connection (Handler.serialize_tasks)."""

    def test_raw_cursor_listen_notify_and_serialized_tasks_are_gated_recorded_and_corroborated(self, target):
        async def framework(conn):
            await conn.set_autocommit(True)
            await conn.execute('LISTEN "ds_chan"')
            cur = sa.AsyncSteppedRawCursor(conn)
            await cur.execute("SELECT $1::int, $2::bytea", (5, b"hi"))
            first = await cur.fetchone()
            two = await asyncio.gather(  # two framework tasks on one connection: they take turns, both gated
                asyncio.create_task(sa.AsyncSteppedRawCursor(conn).execute("SELECT pg_notify($1, $2)",
                                                                           ("ds_chan", "job-1"))),
                asyncio.create_task(sa.AsyncSteppedRawCursor(conn).execute("SELECT $1::int", (7,))))
            notes = [n.payload async for n in conn.notifies(timeout=2.0, stop_after=1)]
            return {"first": first, "tasks": len(two), "notes": notes}

        admin = target.admin()
        try:
            sync_app.reset(admin, SEED)
            lim = st.Limits()
            allowed = frozenset(st.normal_sql(s) for s in ('LISTEN "ds_chan"', "SELECT $1::int, $2::bytea",
                                                            "SELECT pg_notify($1, $2)", "SELECT $1::int"))
            actor = st.Actor("q", st.Handler("test", "framework", framework, allowed, {}, serialize_tasks=True), {})
            connect = sa.async_actor_connection_factory(target.conninfo(), lim, search_path=A.SCHEMA)
            run = sa.AsyncRun([actor], connect, hz.async_monitor(target), limits=lim, run_id="framework")
            trace, c = hz.corroborated(admin, run, user=target.user)
        finally:
            admin.close()
        assert trace.actors["q"]["exception"] is None
        assert trace.raw_results["q"] == {"first": (5, b"hi"), "tasks": 2, "notes": ["job-1"]}
        received = [op for op in trace.records if op.kind == "notify_received"]
        assert len(received) == 1 and received[0].sql == "NOTIFY ds_chan"
        raw = [op for op in trace.records if op.provenance.get("placeholders") == "native"]
        assert len(raw) == 3 and {op.provenance["on_behalf_of"] for op in raw[1:]} == {"q"}
        assert c["ok"], c["problems"]
        assert c["params_compared"] == 5 and c["params_unchecked"] == 0  # incl. the bytea, compared by digest


class TestTypedParameters:
    @pytest.mark.parametrize("zone", ["UTC", "Asia/Kolkata"])
    def test_date_time_interval_uuid_and_arrays_reconcile_exactly_in_any_session_zone(self, target, zone):
        import datetime as dt
        import uuid
        values = (dt.datetime(2026, 11, 5, 8, 30, 59, 357123, tzinfo=dt.UTC),
                  dt.datetime(2026, 11, 5, 8, 30, 59, 357123, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))),
                  dt.datetime(2026, 11, 5, 8, 30, 59, 7), dt.date(2026, 11, 5), dt.time(8, 30, 59, 357000),
                  dt.time(8, 30, tzinfo=dt.timezone(dt.timedelta(hours=-3))), dt.timedelta(seconds=30),
                  dt.timedelta(days=1, seconds=3723, microseconds=5), dt.timedelta(microseconds=-1),
                  uuid.UUID("03fbb261-c406-2a37-a654-f9643fe1ba94"), [dt.timedelta(0), dt.timedelta(days=1)], None)
        query = "SELECT " + ", ".join(f"${i + 1}" for i in range(len(values)))
        zone_sql = f"SET TIME ZONE '{zone}'"

        async def typed(conn):
            await conn.execute(zone_sql)
            cur = sa.AsyncSteppedRawCursor(conn)
            await cur.execute(query, values)
            return await cur.fetchone()

        trace, c, _l, _k = run_handlers(target, ("a", typed, [zone_sql, query]))
        assert trace.actors["a"]["exception"] is None
        assert c["ok"], c["problems"]
        assert (c["params_compared"], c["params_mismatched"], c["params_unchecked"]) == (12, 0, 0)


class TestCancelledWhileWaiting:
    def test_a_task_cancelled_at_the_gate_leaves_the_actor_usable(self, target):
        """Regression: a framework that cancels its own task while it waits for a grant (Chancy's Worker.stop) used
        to leave the actor marked pending, so the actor's next operation was refused as concurrent use."""
        async def framework(conn):
            await conn.execute("SELECT 1")
            waiting = asyncio.create_task(conn.execute("SELECT 1"))  # parks at the gate (serialize_tasks)
            await asyncio.sleep(0.3)
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            await conn.execute("SELECT 1")  # the actor must still work
            return "ok"

        admin = target.admin()
        try:
            sync_app.reset(admin, SEED)
            lim = st.Limits()
            actor = st.Actor("q", st.Handler("test", "framework", framework, frozenset({"SELECT 1"}), {},
                                             serialize_tasks=True), {})
            other = st.Actor("o", st.Handler("test", "idle", idle_long, frozenset({"SELECT 1"}), {}), {})
            connect = sa.async_actor_connection_factory(target.conninfo(), lim, search_path=A.SCHEMA)
            run = sa.AsyncRun([actor, other], connect, hz.async_monitor(target), schedule=["q", "o", "q", "q"],
                              limits=lim, run_id="cancel_wait")
            trace, c = hz.corroborated(admin, run, user=target.user)
        finally:
            admin.close()
        assert trace.actors["q"]["exception"] is None and trace.raw_results["q"] == "ok"
        cancelled = [op for op in trace.records if op.error_class == "cancelled_while_waiting"]
        assert len(cancelled) == 1 and cancelled[0].grant is None
        assert c["ok"], c["problems"]  # the cancelled operation never reached the server


async def idle_long(conn):
    await conn.execute("SELECT 1")
    await asyncio.sleep(0.6)  # keeps the controller busy so q's second request waits at the gate
    return "idle"
