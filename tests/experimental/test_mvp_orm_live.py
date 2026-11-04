"""EXPERIMENTAL prototype, live: a SQLAlchemy 2.x ORM application on psycopg 3, stepped on a disposable PostgreSQL.

Skipped unless research_tools/mvp_pg.sh has started the disposable instance. Every claim about ORM support here is
backed by a trace the server log corroborates; the refusals show where the hook stops and what the log catches.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from sqlalchemy import URL, create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool, QueuePool  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import stepping as st  # noqa: E402
from deployspecter.experimental.apps import ledger_orm as L  # noqa: E402

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
INTERLEAVE = ["a", "b"] * 4  # both read before either writes


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


def app_ops(r):
    return [op for op in r.trace.records if op.kind == "execute" and op.sql not in L.DIALECT_SETUP]


class TestOrmStepping:
    def test_buggy_loses_an_update_the_fixed_build_holds_both_corroborated(self, target):
        buggy = hz.run_app(target, L, TWO_WITHDRAWALS, SEED, variant="buggy", schedule=INTERLEAVE, label="w",
                           corroborate=True)
        fixed = hz.run_app(target, L, TWO_WITHDRAWALS, SEED, variant="fixed", schedule=INTERLEAVE, label="w",
                           corroborate=True)
        assert buggy.initial_digest == fixed.initial_digest
        assert buggy.violations == ["L1 account 1: balance 40 but opening 100 plus entries -130"]
        assert fixed.violations == []
        for r in (buggy, fixed):
            assert r.trace.outcome == "completed"
            assert r.corroboration["ok"], r.corroboration["problems"]
            assert r.corroboration["params_unchecked"] == 0

    def test_the_orm_update_is_computed_from_what_this_schedule_read(self, target):
        r = hz.run_app(target, L, TWO_WITHDRAWALS, SEED, variant="buggy", schedule=INTERLEAVE, label="w")
        reads = {op.actor: op.rows for op in app_ops(r) if op.sql.startswith("SELECT")}
        updates = {op.actor: op.params for op in app_ops(r) if op.sql.startswith("UPDATE")}
        assert reads == {"a": [[1, {"str": 5, "sha256": reads["a"][0][1]["sha256"]}, 100, 100]],
                         "b": [[1, {"str": 5, "sha256": reads["b"][0][1]["sha256"]}, 100, 100]]}
        assert updates == {"a": {"balance": 30, "accounts_id": 1}, "b": {"balance": 40, "accounts_id": 1}}
        serial = hz.run_app(target, L, TWO_WITHDRAWALS, SEED, variant="buggy", schedule=["a"] * 4 + ["b"] * 4,
                            label="w")
        assert serial.violations == [] and serial.trace.raw_results["b"] == {"status": "insufficient"}

    def test_orm_calls_are_attributed_to_the_application_line_and_the_framework_entry(self, target):
        r = hz.run_app(target, L, TWO_WITHDRAWALS, SEED, variant="buggy", schedule=INTERLEAVE, label="w")
        a = [op for op in r.trace.records if op.actor == "a" and op.decision is not None
             and r.trace.decisions[op.decision].source != "setup"]
        entries = [(op.kind, op.sql.split()[0], op.provenance["framework_entry"]) for op in a]
        assert entries == [("execute", "SELECT", "sqlalchemy/orm/session.py:get"),
                           ("execute", "UPDATE", "sqlalchemy/orm/session.py:flush"),
                           ("execute", "INSERT", "sqlalchemy/orm/session.py:flush"),
                           ("commit", "COMMIT", "sqlalchemy/orm/session.py:commit")]
        assert all(op.provenance["call_site"].startswith("src/deployspecter/experimental/apps/ledger_orm.py:")
                   and op.provenance["call_site"].endswith(":withdraw") for op in a)
        assert a[0].implicit_begin and a[-1].txn_after == "IDLE"

    def test_dialect_setup_is_gated_and_recorded_but_is_not_a_decision_point(self, target):
        r = hz.run_app(target, L, TWO_WITHDRAWALS, SEED, variant="fixed", schedule=INTERLEAVE, label="w")
        setup = [d for d in r.trace.decisions if d.source == "setup"]
        assert setup and all(d.enabled == [d.chosen] for d in setup)
        setup_sql = [op.sql for op in r.trace.records if op.actor == "a" and op.sql in L.DIALECT_SETUP]
        assert setup_sql == ["select pg_catalog.version()", "select current_schema()",
                             "show transaction isolation level", "show standard_conforming_strings"]

    def test_batched_unit_of_work_statements_and_session_begin_are_corroborated(self, target):
        for variant, want in (("buggy", ["L1 account 2: balance 5 but opening 0 plus entries 35"]), ("fixed", [])):
            r = hz.run_app(target, L, TRANSFER_DEPOSIT, SEED, variant=variant, schedule=INTERLEAVE, label="t",
                           corroborate=True)
            assert r.violations == want
            assert r.corroboration["ok"], r.corroboration["problems"]
            kinds = [op.kind for op in r.trace.records if op.actor == "a"]
            assert "executemany" in kinds  # the two balance UPDATEs, batched by the unit of work

    def test_exhaustive_exploration_finds_the_lost_update_only_in_the_buggy_build(self, target):
        counts = {}
        for variant in ("buggy", "fixed"):
            results, exhausted = hz.explore_app(target, L, TWO_WITHDRAWALS, SEED, variant=variant, max_runs=200,
                                                label="w")
            assert exhausted
            counts[variant] = (len(results), sum(bool(r.violations) for r in results))
        assert counts == {"buggy": (24, 22), "fixed": (8, 0)}


def run_handlers(target, *specs, schedule=(), corroborate=False):
    """Custom handlers (name, fn, allowed statements) on the ORM fixture's schema, stepped."""
    admin = target.admin()
    try:
        L.reset(admin, SEED)
        lim = st.Limits()
        actors = [st.Actor(name, st.Handler("test", fn.__name__, fn, frozenset(st.normal_sql(s) for s in sqls), {},
                                            setup_sql=L.DIALECT_SETUP), {}) for name, fn, sqls in specs]
        connect = st.actor_connection_factory(target.conninfo(), lim, search_path=L.SCHEMA, autocommit=False)
        run = st.Run(actors, connect, admin, schedule=list(schedule), limits=lim, run_id="orm")
        if corroborate:
            return hz.corroborated(admin, run, user=target.user)
        return run.execute(), None
    finally:
        admin.close()


def get_one(engine):
    with Session(engine) as s:
        return s.get(L.Account, 1).balance


class TestOrmRefusals:
    def test_an_unlisted_orm_statement_is_refused_before_it_reaches_the_server(self, target):
        def sweep(conn):
            with Session(L.bind_engine(conn)) as s:
                s.execute(text("DELETE FROM entries"))

        trace, c = run_handlers(target, ("a", sweep, []), corroborate=True)
        assert trace.actors["a"]["exception_class"] == "refused"
        refused = [op for op in trace.records if op.outcome == "refused"]
        assert [op.sql for op in refused] == ["DELETE FROM entries"] and refused[0].grant is None
        assert c["ok"], c["problems"]  # and the server never saw it

    def test_streaming_results_need_a_server_side_cursor_and_are_refused(self, target):
        def stream(conn):
            with Session(L.bind_engine(conn)) as s:
                return list(s.execute(text("SELECT id FROM accounts").execution_options(stream_results=True)))

        trace, _ = run_handlers(target, ("a", stream, ["SELECT id FROM accounts"]))
        assert trace.actors["a"]["exception_class"] == "refused"
        assert "named" in trace.actors["a"]["exception"]

    def test_native_hstore_lookup_bypasses_the_cursor_hook_and_the_server_log_catches_it(self, target):
        def default_dialect(conn):
            return get_one(L.bind_engine(conn, use_native_hstore=True))

        trace, c = run_handlers(target, ("a", default_dialect, [L.GET]), corroborate=True)
        assert trace.actors["a"]["exception"] is None  # the handler itself ran fine
        assert not c["ok"]
        assert any("typname" in p for p in c["problems"])  # psycopg's TypeInfo.fetch, via a plain psycopg.Cursor

    def test_an_engine_the_handler_builds_itself_is_an_unexplained_session(self, target):
        info = target.conninfo()
        url = URL.create("postgresql+psycopg", username=info["user"], password=info["password"], host=info["host"],
                         port=info["port"], database=info["dbname"], query={"application_name": "ds_mvp_actor"})

        def own_engine(conn):
            with Session(L.bind_engine(conn)) as s:
                s.get(L.Account, 1)
            eng = create_engine(url, poolclass=NullPool, connect_args={"options": f"-c search_path={L.SCHEMA}"})
            try:
                return get_one(eng)
            finally:
                eng.dispose()

        trace, c = run_handlers(target, ("a", own_engine, [L.GET]), corroborate=True)
        assert trace.actors["a"]["exception"] is None
        assert not c["ok"] and any("unexplained session" in p for p in c["problems"])


class TestPooling:
    def test_dedicated_static_pool_checks_out_and_returns_across_sessions(self, target):
        def three_sessions(conn):
            engine = L.bind_engine(conn)
            return [get_one(engine) for _ in range(3)]

        trace, c = run_handlers(target, ("a", three_sessions, [L.GET]), corroborate=True)
        assert trace.raw_results["a"] == [100, 100, 100]
        assert [op.kind for op in trace.records if op.sql == st.normal_sql(L.GET)] == ["execute"] * 3
        assert c["ok"], c["problems"]

    def test_queue_pool_per_actor_works_for_one_connection_and_fails_closed_for_a_second(self, target):
        def one(conn):
            return get_one(L.bind_engine(conn, poolclass=QueuePool))

        def two_at_once(conn):
            engine = L.bind_engine(conn, poolclass=QueuePool)
            with Session(engine) as s1, Session(engine) as s2:
                s1.get(L.Account, 1)
                s2.get(L.Account, 2)  # needs a second pooled connection

        trace, c = run_handlers(target, ("a", one, [L.GET]), corroborate=True)
        assert trace.raw_results["a"] == 100 and c["ok"], c["problems"]
        trace, c = run_handlers(target, ("a", two_at_once, [L.GET]), corroborate=True)
        assert trace.actors["a"]["exception_class"] == "refused"
        assert "second DBAPI connection" in trace.actors["a"]["exception"]
        assert c["ok"], c["problems"]  # what did run is still fully explained

    def test_null_pool_is_the_one_session_no_pooling_mode(self, target):
        def one(conn):
            return get_one(L.bind_engine(conn, poolclass=NullPool))

        def two_sequential(conn):
            engine = L.bind_engine(conn, poolclass=NullPool)
            get_one(engine)
            return get_one(engine)  # NullPool closed the actor's connection on return

        trace, c = run_handlers(target, ("a", one, [L.GET]), corroborate=True)
        assert trace.raw_results["a"] == 100 and c["ok"], c["problems"]
        trace, _ = run_handlers(target, ("a", two_sequential, [L.GET]))
        assert trace.actors["a"]["exception_class"] == "refused"

    @pytest.mark.parametrize("poolclass", [L.StaticPool, QueuePool])
    def test_one_engine_shared_across_actors_fails_closed(self, target, poolclass):
        shared: dict[str, object] = {}
        ready = threading.Event()

        def owner(conn):
            shared["engine"] = L.bind_engine(conn, poolclass=poolclass)
            ready.set()
            return get_one(shared["engine"])

        def borrower(_conn):
            ready.wait(5)
            return get_one(shared["engine"])

        trace, _ = run_handlers(target, ("a", owner, [L.GET]), ("b", borrower, [L.GET]), schedule=["a", "b"])
        assert trace.actors["b"]["exception_class"] == "refused"
        assert "another thread" in trace.actors["b"]["exception"]
        assert trace.raw_results["a"] == 100
