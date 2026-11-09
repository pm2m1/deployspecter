"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): postgres-tq 1.2.1
(``postgrestq.TaskQueue``) under the onb-pilot/1 contract.

* **The library is synchronous.** ``TaskQueue`` does all its I/O through a ``psycopg_pool.ConnectionPool`` that its
  own ``connect()`` builds (``ConnectionPool(dsn, open=True, min_size=2)`` then ``pool.wait()``), using
  ``with pool.connection() as conn``, ``conn.cursor()``, ``cur.execute``, ``fetchone``/``fetchall``, ``rowcount``,
  ``conn.execute`` and ``conn.commit()``.
* **Seam (pool):** the module global ``postgrestq.task_queue.ConnectionPool`` is replaced, for the duration of each
  step, by ``BridgePool``: its one connection is a synchronous facade over the actor's ``AsyncSteppedConnection``.
  The library's own ``connect()`` still runs; no second connection exists. The library code runs in a worker thread
  (``asyncio.to_thread``, which carries the actor's context), and every facade call is sent back to the actor's
  event loop with ``run_coroutine_threadsafe``, so each statement, commit and rollback passes the actor's gate as the
  library issued it. The SQL is the library's own, unchanged.
* **Install:** ``TaskQueue(..., create_table=True, reset=True)`` (the library's DDL and its queue reset).
* **Enqueue:** ``TaskQueue.add(task, lease_timeout, ttl)`` per job; the task payload is ``{"k": key, "n": n}``
  (keys of equal length, so ``json.dumps(sort_keys=True)`` and jsonb's key order agree).
* **Work:** the library's documented consumer loop, ``for task, id_, _q in task_queue`` (``get``, then ``complete``
  after the body, then ``is_empty``), stopping when the queue is empty, or failing after ``timeout_s``.
* **The trusted task** ``ds_task(key, n)`` calls the harness recorder and returns ``n * 2``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import time
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"postgrestq/__init__.py": "589bac13f3ae70e1fe55f178e74d6947afd45384078ddf64be3c27e00593a294",
          "postgrestq/task_queue.py": "f7625903a24240f9830e8408b39ecdacd4257d3423e3652c161a34ed4ce9c4f4"}
QUEUE = "ds_q"
LEASE_S = 60.0
TTL = 3


def verify_pinned() -> dict[str, str]:
    import postgrestq
    root = Path(inspect.getsourcefile(postgrestq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"postgrestq sources aren't the pinned 1.2.1 ones: {got}")
    return {"version": "1.2.1", **got}


class _Bridge:
    def __init__(self, conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        if not isinstance(conn, AsyncSteppedConnection):
            raise RuntimeError("postgrestq may only be given the actor's stepped connection")
        self.conn, self.loop = conn, loop

    def call(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


class SyncCursor:
    """The synchronous cursor surface ``TaskQueue`` uses, over the stepped connection's gated async cursor."""

    def __init__(self, bridge: _Bridge, cur: Any) -> None:
        self._b, self._cur = bridge, cur

    def __enter__(self) -> SyncCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def execute(self, query: Any, params: Any = None) -> SyncCursor:
        self._b.call(self._cur.execute(query, params))
        return self

    def fetchone(self) -> Any:
        return self._b.call(self._cur.fetchone())

    def fetchall(self) -> Any:
        return self._b.call(self._cur.fetchall())

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def close(self) -> None:
        self._b.call(self._cur.close())


class SyncConnection:
    def __init__(self, bridge: _Bridge) -> None:
        self._b = bridge

    def cursor(self) -> SyncCursor:
        return SyncCursor(self._b, self._b.conn.cursor())

    def execute(self, query: Any, params: Any = None) -> SyncCursor:
        return SyncCursor(self._b, self._b.call(self._b.conn.execute(query, params)))

    def commit(self) -> None:
        self._b.call(self._b.conn.commit())

    def rollback(self) -> None:
        self._b.call(self._b.conn.rollback())


def bridge_pool_class(bridge: _Bridge) -> type:
    class BridgePool:
        """Stands in for ``psycopg_pool.ConnectionPool``: one connection, the actor's own."""

        def __init__(self, _conninfo: str = "", **_kw: Any) -> None:
            self._conn = SyncConnection(bridge)

        def wait(self, timeout: float = 30.0) -> None:
            return None

        @contextlib.contextmanager
        def connection(self, timeout: float | None = None) -> Iterator[SyncConnection]:
            try:
                yield self._conn
            finally:  # as psycopg_pool does on return: never hand back a connection inside a transaction
                if bridge.conn.info.transaction_status.name in ("INTRANS", "INERROR"):
                    self._conn.rollback()

        def close(self, timeout: float = 5.0) -> None:
            return None

    return BridgePool


def ds_task(key: str, n: int) -> int:
    recorder.handled(key)
    return n * 2


def _queue(bridge: _Bridge, **kw: Any) -> Any:
    from postgrestq import task_queue as tq
    verify_pinned()
    tq.ConnectionPool = bridge_pool_class(bridge)  # the seam
    return tq.TaskQueue("", QUEUE, **kw)


async def _in_thread(conn: AsyncSteppedConnection, fn: Any) -> Any:
    bridge = _Bridge(conn, asyncio.get_running_loop())
    return await asyncio.to_thread(fn, bridge)


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    def run(bridge: _Bridge) -> dict[str, Any]:
        q = _queue(bridge, create_table=True, reset=True)
        return {"installed": True, "pending": len(q)}
    return await _in_thread(conn, run)


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    def run(bridge: _Bridge) -> dict[str, Any]:
        q = _queue(bridge)
        return {"enqueued": [q.add({"k": j["key"], "n": j["args"]["n"]}, LEASE_S, ttl=TTL) for j in jobs]}
    return await _in_thread(conn, run)


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    def run(bridge: _Bridge) -> dict[str, Any]:
        q = _queue(bridge)
        deadline = time.monotonic() + timeout_s
        results = []
        for task, _id, _queue_name in q:  # the library's consumer loop: get, (body), complete, is_empty
            results.append(ds_task(task["k"], task["n"]))
            if time.monotonic() > deadline:
                raise TimeoutError(f"work exceeded {timeout_s} s")
        return {"processed": len(results), "expect": expect, "results": results,
                "handled": list(recorder.HANDLED)}
    return await _in_thread(conn, run)
