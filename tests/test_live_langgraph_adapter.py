"""Offline checks of the LangGraph adapter. No database: the real upstream methods run against a recording double.

Needs the isolated Gate 2B environment, where the pinned library is installed. Anywhere else this module skips.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

pytest.importorskip("langgraph.checkpoint.postgres")

from langgraph.checkpoint.base import CheckpointTuple  # noqa: E402
from langgraph.checkpoint.postgres import PostgresSaver  # noqa: E402
from langgraph.checkpoint.postgres.base import PRUNE_DELETE_THREADS_SQL  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402

from deployspecter.live.adapters import langgraph_pg as lg  # noqa: E402
from deployspecter.live.driver import SteppedConnection  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class RecordingCursor:
    def __init__(self, conn, options):
        self.connection = conn
        self.options = options
        conn.cursors.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.connection.statements.append((query, params))
        if self.connection.fail_on is not None and len(self.connection.statements) == self.connection.fail_on:
            raise self.connection.failure
        return self

    def fetchone(self):
        return self.connection.rows.pop(0) if self.connection.rows else None


class RecordingConnection(psycopg.Connection):
    """A real psycopg.Connection subclass that never connects, so upstream type checks see the genuine class."""

    def __del__(self):
        pass

    @classmethod
    def make(cls, base=psycopg.Connection):
        conn = object.__new__(type("Recording", (cls, base), {}) if base is not psycopg.Connection else cls)
        conn._pipeline = None
        conn._channel = None
        conn.cursors, conn.statements, conn.transactions, conn.rows = [], [], [], []
        conn.fail_on, conn.failure = None, None
        return conn

    def cursor(self, *args, **kwargs):
        return RecordingCursor(self, kwargs)

    @contextmanager
    def transaction(self, *args, **kwargs):
        record = {"entered": True, "exception": None}
        self.transactions.append(record)
        try:
            yield
        except BaseException as exc:
            record["exception"] = exc
            raise


@pytest.fixture
def conn():
    return RecordingConnection.make()


# ------------------------------------------------------------------ 1. construction


class TestConstruction:
    def test_wraps_the_real_saver_on_this_connection(self, conn):
        adapter = lg.PostgresSaverAdapter(conn)
        assert type(adapter.saver) is PostgresSaver
        assert adapter.saver.conn is conn and adapter.saver.pipe is None
        assert adapter.library_version == "3.2.0"

    def test_serializer_is_strict(self, conn):
        serde = lg.PostgresSaverAdapter(conn).saver.serde
        inner = getattr(serde, "serde", serde)
        assert inner.pickle_fallback is False
        assert inner._allowed_msgpack_modules is None and inner._allowed_json_modules is None

    def test_each_adapter_has_its_own_saver_and_lock(self):
        a, b = RecordingConnection.make(), RecordingConnection.make()
        first, second = lg.PostgresSaverAdapter(a), lg.PostgresSaverAdapter(b)
        assert first.saver is not second.saver and first.saver.lock is not second.saver.lock

    def test_library_methods_are_not_replaced(self, conn):
        saver = lg.PostgresSaverAdapter(conn).saver
        for name in ("prune", "get_tuple", "_cursor"):
            assert getattr(saver, name).__func__ is getattr(PostgresSaver, name)

    def test_wrong_library_version_is_refused(self, conn, monkeypatch):
        import importlib.metadata

        monkeypatch.setattr(importlib.metadata, "version", lambda name: "3.2.1")
        with pytest.raises(lg.UnsupportedLangGraphOperation, match="3.2.1"):
            lg.PostgresSaverAdapter(conn)


# ------------------------------------------------------------------ 2-4. forwarding, return values, exceptions


class TestForwarding:
    def test_prune_delete_forwards_arguments(self, conn, monkeypatch):
        adapter = lg.PostgresSaverAdapter(conn)
        calls = []
        monkeypatch.setattr(adapter.saver, "prune", lambda *a, **k: calls.append((a, k)))
        ids = ["t1", "t2"]
        adapter.prune_delete(ids)
        assert calls == [((ids,), {"strategy": "delete"})] and calls[0][0][0] is ids

    def test_get_tuple_forwards_the_same_config_object(self, conn, monkeypatch):
        adapter = lg.PostgresSaverAdapter(conn)
        seen = []
        monkeypatch.setattr(adapter.saver, "get_tuple", lambda config: seen.append(config))
        config = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
        adapter.get_tuple(config)
        assert seen == [config] and seen[0] is config

    def test_return_values_pass_through_unchanged(self, conn, monkeypatch):
        adapter = lg.PostgresSaverAdapter(conn)
        sentinel = object()
        monkeypatch.setattr(adapter.saver, "get_tuple", lambda config: sentinel)
        monkeypatch.setattr(adapter.saver, "prune", lambda *a, **k: sentinel)
        assert adapter.get_tuple({"configurable": {"thread_id": "t"}}) is sentinel
        assert adapter.prune_delete(["t"]) is sentinel

    def test_exceptions_pass_through_unchanged(self, conn, monkeypatch):
        adapter = lg.PostgresSaverAdapter(conn)
        failure = psycopg.errors.DeadlockDetected("from the library")

        def raise_it(*a, **k):
            raise failure

        monkeypatch.setattr(adapter.saver, "prune", raise_it)
        with pytest.raises(psycopg.errors.DeadlockDetected) as caught:
            adapter.prune_delete(["t1"])
        assert caught.value is failure

    def test_upstream_argument_errors_are_the_librarys(self, conn):
        adapter = lg.PostgresSaverAdapter(conn)
        with pytest.raises(TypeError, match="not a str"):
            adapter.prune_delete("t1")
        with pytest.raises(KeyError):
            adapter.get_tuple({"configurable": {}})
        assert conn.statements == []


# ------------------------------------------------------------------ 5. cursor and transaction configuration


class TestUpstreamControlFlow:
    def test_prune_delete_uses_one_transaction_and_the_upstream_statements(self, conn):
        assert lg.PostgresSaverAdapter(conn).prune_delete(("t1", "t2")) is None
        assert [c.options for c in conn.cursors] == [{"binary": True, "row_factory": dict_row}]
        assert conn.transactions == [{"entered": True, "exception": None}]
        assert [q for q, _ in conn.statements] == list(PRUNE_DELETE_THREADS_SQL)
        assert all(p == (["t1", "t2"],) for _, p in conn.statements)

    def test_empty_prune_touches_nothing(self, conn):
        lg.PostgresSaverAdapter(conn).prune_delete([])
        assert conn.cursors == [] and conn.transactions == [] and conn.statements == []

    def test_failure_in_the_second_delete_leaves_the_transaction_block_with_that_error(self, conn):
        conn.fail_on = 2
        conn.failure = psycopg.errors.RaiseException("injected")
        with pytest.raises(psycopg.errors.RaiseException) as caught:
            lg.PostgresSaverAdapter(conn).prune_delete(["t1"])
        assert caught.value is conn.failure
        assert len(conn.statements) == 2, "the third DELETE never ran"
        assert conn.transactions[0]["exception"] is conn.failure, "so the real block would roll back"

    def test_get_tuple_latest_uses_the_upstream_query(self, conn):
        assert lg.PostgresSaverAdapter(conn).get_tuple({"configurable": {"thread_id": "t1"}}) is None
        (query, params), = conn.statements
        assert query == PostgresSaver.SELECT_SQL + (
            "WHERE thread_id = %s AND checkpoint_ns = %s ORDER BY checkpoint_id DESC LIMIT 1"
        )
        assert params == ("t1", "")
        assert conn.cursors[0].options == {"binary": True, "row_factory": dict_row}
        assert conn.transactions == [], "a read with no transaction block, as upstream"

    def test_get_tuple_by_id_uses_the_upstream_query(self, conn):
        config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "ns", "checkpoint_id": "c1"}}
        lg.PostgresSaverAdapter(conn).get_tuple(config)
        (query, params), = conn.statements
        assert query.endswith("WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s")
        assert params == ("t1", "ns", "c1")

    def test_get_tuple_builds_the_librarys_tuple(self, conn):
        conn.rows.append({
            "thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "c2", "parent_checkpoint_id": "c1",
            "checkpoint": {"v": 4, "id": "c2", "ts": "2026-01-01T00:00:00+00:00", "channel_values": {"count": 3},
                           "channel_versions": {"count": "2"}, "versions_seen": {}, "pending_sends": []},
            "metadata": {"step": 2}, "channel_values": None, "pending_writes": None,
        })
        found = lg.PostgresSaverAdapter(conn).get_tuple({"configurable": {"thread_id": "t1"}})
        assert isinstance(found, CheckpointTuple)
        assert len(conn.statements) == 1, "a v4 checkpoint needs no legacy second query"
        summary = lg.summarize_tuple(found)
        assert summary["checkpoint_id"] == "c2" and summary["parent_checkpoint_id"] == "c1"
        assert summary["channel_values"] == {"count": 3} and summary["pending_writes"] == []

    def test_registered_entrypoints_return_json_safe_results(self, conn):
        assert lg.actor_prune_delete(conn, ["t1"]) == {"operation": "prune_delete", "library": "3.2.0",
                                                       "returned": None}
        other = RecordingConnection.make()
        assert lg.actor_get_tuple(other, "t1")["returned"] is None


# ------------------------------------------------------------------ 6. refusals


class TestRefusals:
    @pytest.mark.parametrize("name", ["put", "put_writes", "delete_thread", "setup", "list", "get",
                                      "from_conn_string", "get_delta_channel_history", "anything_else"])
    def test_out_of_scope_operations(self, conn, name):
        adapter = lg.PostgresSaverAdapter(conn)
        with pytest.raises(lg.UnsupportedLangGraphOperation):
            getattr(adapter, name)
        assert conn.statements == []

    @pytest.mark.parametrize("name,reason", [("put", "pipeline"), ("put_writes", "pipeline"),
                                             ("delete_thread", "pipeline"), ("setup", "bootstrap"),
                                             ("from_conn_string", "own connection")])
    def test_refusals_name_their_reason(self, conn, name, reason):
        with pytest.raises(lg.UnsupportedLangGraphOperation, match=reason):
            getattr(lg.PostgresSaverAdapter(conn), name)

    @pytest.mark.parametrize("name", ["aget_tuple", "aprune", "aput", "aput_writes", "aprune_delete"])
    def test_asynchronous_operations(self, conn, name):
        with pytest.raises(lg.UnsupportedLangGraphOperation, match="asynchronous"):
            getattr(lg.PostgresSaverAdapter(conn), name)

    def test_keep_latest_strategy(self, conn):
        with pytest.raises(lg.UnsupportedLangGraphOperation, match="keep_latest"):
            lg.PostgresSaverAdapter(conn).prune(["t1"])
        assert conn.statements == []

    def test_pool(self):
        pool = ConnectionPool("", open=False)
        with pytest.raises(lg.UnsupportedLangGraphOperation, match="pool"):
            lg.PostgresSaverAdapter(pool)

    def test_async_connection(self):
        with pytest.raises(lg.UnsupportedLangGraphOperation, match="asynchronous"):
            lg.PostgresSaverAdapter(object.__new__(psycopg.AsyncConnection))

    def test_not_a_connection(self):
        with pytest.raises(lg.UnsupportedLangGraphOperation, match="synchronous psycopg"):
            lg.PostgresSaverAdapter(object())

    def test_connection_in_pipeline_mode(self, conn):
        conn._pipeline = object()
        with pytest.raises(lg.UnsupportedLangGraphOperation, match="pipeline"):
            lg.PostgresSaverAdapter(conn)


# ------------------------------------------------------------------ 7. registered connection interface


class TestRegisteredConnections:
    def test_stepped_connection_is_accepted_as_is(self):
        conn = object.__new__(type("SteppedDouble", (SteppedConnection,), {"__del__": lambda self: None}))
        conn._pipeline, conn._channel = None, None
        adapter = lg.PostgresSaverAdapter(conn)
        from langgraph.checkpoint.postgres import _internal

        with _internal.get_connection(adapter.saver.conn) as given:
            assert given is conn, "the library uses the registered connection itself, unwrapped"

    def test_recording_double_is_a_genuine_connection_subclass(self, conn):
        assert isinstance(conn, psycopg.Connection)


# ------------------------------------------------------------------ 8. nothing frozen changed


PROTOCOL = {
    "EVALUATION_PROTOCOL.md": "31d82afd7bed1c38aa4870c064ca5b0827e72211ca6804a4b98370c54aef1725",
    "EVALUATION_PROTOCOL_AMENDMENT_001.md": "28add0e8f69aaa3dd96be205b2c62dcf1b7a7b27d1da963256b9e818a6b06480",
    "EVALUATION_PROTOCOL_AMENDMENT_002.md": "c3e939bd87fe89ae2e04c43d23aa1156e441e2c20be12a44c8f5939909c47075",
}


class TestNothingFrozenChanged:
    @pytest.mark.parametrize("name,digest", sorted(PROTOCOL.items()))
    def test_protocol_files(self, name, digest):
        data = (ROOT / "docs/research/live_stepping" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest

    def test_tracked_gate_2a_files_are_unchanged(self):
        """The frozen release engine, the Gate 2A supervision code, its tests and tools, and historical results.

        Not the whole of src: later offline research steps legitimately change their own contract modules.
        """
        live = ROOT / "src/deployspecter/live"
        gate_2a = [str(live / f"{name}.py") for name in (
            "driver", "runner", "trace", "explore", "supervisor", "worker", "serverlog", "workload")]
        release = [str(p) for p in (ROOT / "src/deployspecter").glob("*.py")]
        paths = [*release, *gate_2a, "tests/test_live_stepping.py", "tests/test_live_supervised.py",
                 "tests/conftest.py", "tools", "pyproject.toml", "uv.lock", "results", "scenarios",
                 "scenarios_external"]
        changed = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *paths], cwd=ROOT)
        assert changed.returncode == 0, "a tracked frozen file differs from HEAD"
        untracked = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", *paths],
                                   cwd=ROOT, capture_output=True, text=True, check=True)
        assert untracked.stdout == "", f"new or changed files in frozen paths: {untracked.stdout[:300]}"

    def test_engine_and_supervisor_never_import_langgraph(self):
        code = ("import sys, deployspecter.live.supervisor, deployspecter.live.worker, deployspecter.live.runner;"
                "print(any(m.startswith('langgraph') for m in sys.modules))")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "False"
