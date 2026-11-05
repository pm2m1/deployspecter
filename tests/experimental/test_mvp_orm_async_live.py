"""EXPERIMENTAL, live: SQLAlchemy 2 asyncio (AsyncEngine, AsyncSession) on psycopg 3's async dialect, stepped.

Every ORM claim here is backed by a gated trace that the server log corroborates; a successful return value alone is
never taken as interception. Skipped unless the disposable MVP PostgreSQL is running.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy.ext.asyncio")
pytest.importorskip("greenlet")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool, StaticPool  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import stepping as st  # noqa: E402
from deployspecter.experimental import stepping_async as sa  # noqa: E402
from deployspecter.experimental.apps import ledger_orm as L  # noqa: E402
from deployspecter.experimental.apps import ledger_orm_async as LA  # noqa: E402

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")

SEED = {"accounts": [{"id": 1, "owner": "alice", "opening": 100, "balance": 100},
                     {"id": 2, "owner": "bob", "opening": 0, "balance": 0}]}
TWO_WITHDRAWALS = [{"actor": "a", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 70}},
                   {"actor": "b", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 60}}]
TRANSFER_DEPOSIT = [
    {"actor": "a", "app_version": "v1", "handler": "transfer", "args": {"src": 1, "dst": 2, "amount": 30}},
    {"actor": "b", "app_version": "v1", "handler": "deposit", "args": {"account_id": 2, "amount": 5}}]
INTERLEAVE = ["a", "b"] * 4


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


def app_steps(r):
    return [(op.actor, op.kind, op.sql, op.params) for op in sorted(r.trace.records, key=lambda o: o.grant)
            if op.sql not in L.DIALECT_SETUP and op.decision is not None
            and r.trace.decisions[op.decision].source != "setup"]


class TestAsyncOrm:
    def test_buggy_loses_an_update_fixed_holds_both_corroborated(self, target):
        buggy = hz.run_app(target, LA, TWO_WITHDRAWALS, SEED, variant="buggy", schedule=INTERLEAVE, label="w",
                           corroborate=True)
        fixed = hz.run_app(target, LA, TWO_WITHDRAWALS, SEED, variant="fixed", schedule=INTERLEAVE, label="w",
                           corroborate=True)
        assert buggy.initial_digest == fixed.initial_digest
        assert buggy.violations == ["L1 account 1: balance 40 but opening 100 plus entries -130"]
        assert fixed.violations == []
        for r in (buggy, fixed):
            assert r.corroboration["ok"], r.corroboration["problems"]
            assert r.corroboration["params_unchecked"] == 0

    def test_the_async_orm_sends_what_the_sync_orm_sends(self, target):
        for inv in (TWO_WITHDRAWALS, TRANSFER_DEPOSIT):
            for variant in ("buggy", "fixed"):
                a = hz.run_app(target, LA, inv, SEED, variant=variant, schedule=INTERLEAVE, label="w")
                s = hz.run_app(target, L, inv, SEED, variant=variant, schedule=INTERLEAVE, label="w")
                assert app_steps(a) == app_steps(s) and a.final_state == s.final_state

    def test_orm_calls_resolve_to_the_application_line_and_the_asyncio_entry_point(self, target):
        r = hz.run_app(target, LA, TWO_WITHDRAWALS, SEED, variant="buggy", schedule=INTERLEAVE, label="w")
        a = [op for op in r.trace.records if op.actor == "a" and op.decision is not None
             and r.trace.decisions[op.decision].source != "setup"]
        assert [(op.kind, op.sql.split()[0], op.provenance["framework_entry"]) for op in a] == [
            ("execute", "SELECT", "sqlalchemy/ext/asyncio/session.py:get"),
            ("execute", "UPDATE", "sqlalchemy/ext/asyncio/session.py:flush"),
            ("execute", "INSERT", "sqlalchemy/ext/asyncio/session.py:flush"),
            ("commit", "COMMIT", "sqlalchemy/ext/asyncio/session.py:commit")]
        assert all(op.provenance["call_site"].startswith("src/deployspecter/experimental/apps/ledger_orm_async.py:")
                   for op in a)
        assert {op.provenance["task"] for op in r.trace.records} == {"ds-actor-a", "ds-actor-b"}

    def test_batched_unit_of_work_and_session_begin_are_corroborated(self, target):
        for variant, want in (("buggy", ["L1 account 2: balance 5 but opening 0 plus entries 35"]), ("fixed", [])):
            r = hz.run_app(target, LA, TRANSFER_DEPOSIT, SEED, variant=variant, schedule=INTERLEAVE, label="t",
                           corroborate=True)
            assert r.violations == want and r.corroboration["ok"], r.corroboration["problems"]
            assert "executemany" in [op.kind for op in r.trace.records if op.actor == "a"]

    def test_exploration_matches_the_sync_orm(self, target):
        for variant, want in (("buggy", (24, 22)), ("fixed", (8, 0))):
            results, exhausted = hz.explore_app(target, LA, TWO_WITHDRAWALS, SEED, variant=variant, max_runs=200,
                                                label="w")
            assert exhausted and (len(results), sum(bool(r.violations) for r in results)) == want


def run_handlers(target, *specs, schedule=(), corroborate=True):
    admin = target.admin()
    try:
        L.reset(admin, SEED)
        lim = st.Limits()
        actors = [st.Actor(n, st.Handler("test", fn.__name__, fn, frozenset(st.normal_sql(s) for s in sqls), {},
                                         setup_sql=L.DIALECT_SETUP), {}) for n, fn, sqls in specs]
        connect = sa.async_actor_connection_factory(target.conninfo(), lim, search_path=L.SCHEMA, autocommit=False)
        run = sa.AsyncRun(actors, connect, hz.async_monitor(target), schedule=list(schedule), limits=lim,
                          run_id="orm_async")
        if corroborate:
            return hz.corroborated(admin, run, user=target.user)
        return asyncio.run(run.execute()), None
    finally:
        admin.close()


async def get_one(engine):
    async with AsyncSession(engine) as s:
        return (await s.get(L.Account, 1)).balance


class TestAsyncOrmBoundary:
    def test_an_unlisted_statement_is_refused_before_it_reaches_the_server(self, target):
        async def sweep(conn):
            async with AsyncSession(LA.bind_async_engine(conn)) as s:
                await s.execute(text("DELETE FROM entries"))

        trace, c = run_handlers(target, ("a", sweep, []))
        assert trace.actors["a"]["exception_class"] == "refused" and c["ok"], c["problems"]

    def test_streaming_results_need_a_server_side_cursor_and_are_refused(self, target):
        async def stream(conn):
            async with AsyncSession(LA.bind_async_engine(conn)) as s:
                result = await s.stream(text("SELECT id FROM accounts"))
                return [row async for row in result]

        trace, _ = run_handlers(target, ("a", stream, ["SELECT id FROM accounts"]), corroborate=False)
        assert trace.actors["a"]["exception_class"] == "refused" and "named" in trace.actors["a"]["exception"]

    def test_native_hstore_lookup_bypasses_the_hook_and_the_server_log_catches_it(self, target):
        async def default_dialect(conn):
            engine = LA.bind_async_engine(conn, use_native_hstore=True)
            try:
                return await get_one(engine)
            finally:
                await engine.dispose(close=False)

        trace, c = run_handlers(target, ("a", default_dialect, [L.GET]))
        assert trace.actors["a"]["exception"] is None
        assert not c["ok"] and any("typname" in p for p in c["problems"])


class TestAsyncPooling:
    def test_dedicated_static_pool_checks_out_and_returns_across_sessions(self, target):
        async def three(conn):
            engine = LA.bind_async_engine(conn)
            try:
                return [await get_one(engine) for _ in range(3)]
            finally:
                await engine.dispose(close=False)

        trace, c = run_handlers(target, ("a", three, [L.GET]))
        assert trace.raw_results["a"] == [100, 100, 100] and c["ok"], c["problems"]

    def test_queue_pool_per_task_fails_closed_for_a_second_connection(self, target):
        async def two_at_once(conn):
            engine = LA.bind_async_engine(conn, poolclass=AsyncAdaptedQueuePool)
            try:
                async with AsyncSession(engine) as s1, AsyncSession(engine) as s2:
                    await s1.get(L.Account, 1)
                    await s2.get(L.Account, 2)
            finally:
                await engine.dispose(close=False)

        trace, c = run_handlers(target, ("a", two_at_once, [L.GET]))
        assert trace.actors["a"]["exception_class"] == "refused"
        assert "second DBAPI connection" in trace.actors["a"]["exception"] and c["ok"], c["problems"]

    def test_null_pool_allows_one_session_and_fails_closed_for_a_second(self, target):
        async def one(conn):
            return await get_one(LA.bind_async_engine(conn, poolclass=NullPool))

        async def two(conn):
            engine = LA.bind_async_engine(conn, poolclass=NullPool)
            await get_one(engine)
            return await get_one(engine)  # NullPool closed the task's connection on return

        trace, c = run_handlers(target, ("a", one, [L.GET]))
        assert trace.raw_results["a"] == 100 and c["ok"], c["problems"]
        trace, _ = run_handlers(target, ("a", two, [L.GET]), corroborate=False)
        assert trace.actors["a"]["exception_class"] == "refused"

    @pytest.mark.parametrize("poolclass", [StaticPool, AsyncAdaptedQueuePool])
    def test_one_engine_shared_across_tasks_fails_closed(self, target, poolclass):
        shared = {}

        async def owner(conn):
            shared["engine"] = LA.bind_async_engine(conn, poolclass=poolclass)
            return await get_one(shared["engine"])

        async def borrower(_conn):
            while "engine" not in shared:
                await asyncio.sleep(0.01)
            return await get_one(shared["engine"])

        trace, _ = run_handlers(target, ("a", owner, [L.GET]), ("b", borrower, [L.GET]), schedule=["a", "b"],
                                corroborate=False)
        assert trace.actors["b"]["exception_class"] == "refused"
        assert "another task" in trace.actors["b"]["exception"]
        assert trace.raw_results["a"] == 100
