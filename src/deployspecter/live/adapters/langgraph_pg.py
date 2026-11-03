"""A narrow adapter for the pinned ``langgraph-checkpoint-postgres==3.2.0`` PostgresSaver.

It supports exactly two upstream operations, and calls the library's own methods for both:

* ``PostgresSaver.prune(thread_ids, strategy="delete")``
* ``PostgresSaver.get_tuple(config)``

The adapter doesn't issue SQL, patch the library, or change its cursor options (``binary=True``, ``dict_row``) or its
transaction block. Return values and exceptions pass through unchanged. Each adapter wraps one saver on one connection,
so each logical actor gets its own. Everything else the saver offers is refused by name, including the checkpoint
writes (``put``, ``put_writes``, ``delete_thread``), which use pipeline mode, the ``keep_latest`` strategy, and all
asynchronous methods. Pools, pipelines and asynchronous connections are refused at construction.

LangGraph is not a dependency of DeploySpecter. It's imported only when an adapter is built, from the isolated research
environment described in ``docs/research/live_stepping/gate2b/``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

SUPPORTED_VERSION = "3.2.0"
SUPPORTED_OPERATIONS = ("prune_delete", "get_tuple")
REFUSED_OPERATIONS = {
    "put": "checkpoint writes use pipeline mode and executemany, outside this experiment",
    "put_writes": "pipeline mode and executemany, outside this experiment",
    "delete_thread": "pipeline mode, outside this experiment",
    "setup": "DDL and CREATE INDEX CONCURRENTLY; a bootstrap concern, never an actor operation",
    "list": "not in the supported scope",
    "get": "not in the supported scope; use get_tuple",
    "get_delta_channel_history": "not in the supported scope",
    "from_conn_string": "opens its own connection, which the harness didn't register",
}


class UnsupportedLangGraphOperation(Exception):
    """An upstream operation outside this adapter's scope. Raised rather than silently approximated."""


def strict_serde() -> Any:
    """The serializer with no pickle fallback and only its safe built-in types allowed."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None)


def _check_version() -> str:
    from importlib.metadata import version

    found = version("langgraph-checkpoint-postgres")
    if found != SUPPORTED_VERSION:
        raise UnsupportedLangGraphOperation(f"adapter supports {SUPPORTED_VERSION}, found {found}")
    return found


class PostgresSaverAdapter:
    """One PostgresSaver on one registered synchronous connection."""

    def __init__(self, conn: Any) -> None:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool

        self.library_version = _check_version()
        if isinstance(conn, ConnectionPool):
            raise UnsupportedLangGraphOperation("a pool hands out connections the harness didn't register")
        if isinstance(conn, psycopg.AsyncConnection):
            raise UnsupportedLangGraphOperation("asynchronous connections aren't supported")
        if not isinstance(conn, psycopg.Connection):
            raise UnsupportedLangGraphOperation(f"needs a synchronous psycopg connection, got {type(conn).__name__}")
        if conn._pipeline is not None:
            raise UnsupportedLangGraphOperation("the connection is in pipeline mode")
        self.conn = conn
        self.saver = PostgresSaver(conn, serde=strict_serde())  # no pipe: the saver never enters pipeline mode itself

    def prune_delete(self, thread_ids: Sequence[str]) -> None:
        """The upstream ``prune`` with ``strategy="delete"``. Arguments and exceptions are the library's own."""
        return self.saver.prune(thread_ids, strategy="delete")

    def get_tuple(self, config: dict[str, Any]) -> Any:
        """The upstream ``get_tuple``. Returns its ``CheckpointTuple`` or ``None`` unchanged."""
        return self.saver.get_tuple(config)

    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        if strategy != "delete":
            raise UnsupportedLangGraphOperation(f"prune strategy {strategy!r} is outside this experiment")
        return self.prune_delete(thread_ids)

    def __getattr__(self, name: str) -> Any:
        if name in REFUSED_OPERATIONS:
            raise UnsupportedLangGraphOperation(f"{name}: {REFUSED_OPERATIONS[name]}")
        if name.startswith("a") and name[1:] in {*SUPPORTED_OPERATIONS, "prune", *REFUSED_OPERATIONS}:
            raise UnsupportedLangGraphOperation(f"{name}: asynchronous operations aren't supported")
        raise UnsupportedLangGraphOperation(f"{name}: not part of the supported LangGraph scope")


def summarize_tuple(found: Any) -> dict[str, Any] | None:
    """A JSON-safe view of a CheckpointTuple, for crossing the supervised process boundary. Lossless for plain data."""
    if found is None:
        return None
    configurable = found.config["configurable"]
    parent = found.parent_config["configurable"]["checkpoint_id"] if found.parent_config else None
    writes = sorted([str(task), str(channel), value] for task, channel, value in (found.pending_writes or []))
    return {
        "thread_id": configurable["thread_id"],
        "checkpoint_ns": configurable["checkpoint_ns"],
        "checkpoint_id": configurable["checkpoint_id"],
        "parent_checkpoint_id": parent,
        "channel_values": found.checkpoint.get("channel_values", {}),
        "channel_versions": found.checkpoint.get("channel_versions", {}),
        "metadata": dict(found.metadata or {}),
        "pending_writes": writes,
    }


# ------------------------------------------------------------------ registered entrypoints for supervised actors


def actor_prune_delete(conn: Any, thread_ids: list[str]) -> dict[str, Any]:
    """Entry point ``deployspecter.live.adapters.langgraph_pg:actor_prune_delete``."""
    adapter = PostgresSaverAdapter(conn)
    returned = adapter.prune_delete(thread_ids)
    return {"operation": "prune_delete", "library": adapter.library_version, "returned": returned}


def actor_get_tuple(conn: Any, thread_id: str, checkpoint_ns: str = "", checkpoint_id: str | None = None) -> dict:
    """Entry point ``deployspecter.live.adapters.langgraph_pg:actor_get_tuple``."""
    adapter = PostgresSaverAdapter(conn)
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    found = adapter.get_tuple({"configurable": configurable})
    return {"operation": "get_tuple", "library": adapter.library_version, "returned": summarize_tuple(found)}
