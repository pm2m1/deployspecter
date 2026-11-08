"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): postgres-tq 1.2.1
(``postgrestq.TaskQueue``) under the onb-pilot/1 contract.

* **Seam (pool):** the library is synchronous and builds its own ``psycopg_pool.ConnectionPool`` in
  ``TaskQueue.connect()``. A subclass overrides only ``connect`` to install ``_OnePool``, whose ``connection()``
  hands out a synchronous facade (``_SyncConn``) over the actor's stepped ``AsyncSteppedConnection``. There is no
  other connection.
* **Declared deviation (threading):** a synchronous library can't await, so each step runs the library's own
  methods in a worker thread (``asyncio.to_thread``, which carries the actor's context). Every facade call submits
  the stepped connection's coroutine back to the event loop (``run_coroutine_threadsafe``) and waits for it, so all
  database I/O is the gated psycopg calls, one at a time.
* **Transactions:** the stepped connection is switched to non-autocommit, as the library expects; the library's
  own ``commit()`` calls are the stepped commits. ``_OnePool.connection()`` ends a block the way psycopg's pooled
  connection does: commit on success, rollback on an exception, if a transaction is still open.
* **Work:** the library's own ``__iter__`` (``get`` then ``complete``, until ``is_empty``) drives the trusted task.
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``. A task is stored as
  the JSON ``{"k": key, "n": n, "task": "ds_task"}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"postgrestq/task_queue.py": "f7625903a24240f9830e8408b39ecdacd4257d3423e3652c161a34ed4ce9c4f4",
          "postgrestq/__init__.py": "589bac13f3ae70e1fe55f178e74d6947afd45384078ddf64be3c27e00593a294"}
QUEUE = "ds_q"
LEASE_S = 3600.0
CALL_TIMEOUT_S = 120.0


def verify_pinned() -> dict[str, str]:
    import postgrestq
    root = Path(inspect.getsourcefile(postgrestq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"postgrestq sources aren't the pinned 1.2.1 ones: {got}")
    return {"version": "1.2.1", **got}


class _SyncCursor:
    def __init__(self, owner: _SyncConn, cur: Any) -> None:
        self._o, self._cur = owner, cur

    def __enter__(self) -> _SyncCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._o.call(self._cur.close())

    def execute(self, query: Any, params: Any = None) -> _SyncCursor:
        self._o.call(self._cur.execute(query, params))
        return self

    def fetchone(self) -> Any:
        return self._o.call(self._cur.fetchone())

    def fetchall(self) -> Any:
        return self._o.call(self._cur.fetchall())

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class _SyncConn:
    """The synchronous face of the actor's stepped connection, used only from the worker thread."""

    def __init__(self, conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        self._conn, self._loop = conn, loop

    def call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(CALL_TIMEOUT_S)

    @property
    def in_transaction(self) -> bool:
        from psycopg.pq import TransactionStatus
        return self._conn.info.transaction_status != TransactionStatus.IDLE

    def cursor(self) -> _SyncCursor:
        return _SyncCursor(self, self._conn.cursor())

    def execute(self, query: Any, params: Any = None) -> _SyncCursor:
        return _SyncCursor(self, self.call(self._conn.execute(query, params)))

    def commit(self) -> None:
        self.call(self._conn.commit())

    def rollback(self) -> None:
        self.call(self._conn.rollback())


class _OnePool:
    def __init__(self, conn: _SyncConn) -> None:
        self._conn = conn

    @contextlib.contextmanager
    def connection(self) -> Iterator[_SyncConn]:
        try:
            yield self._conn
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        else:
            if self._conn.in_transaction:
                self._conn.commit()

    def close(self) -> None:
        return None


def _queue(sync: _SyncConn, **kw: Any) -> Any:
    from postgrestq import TaskQueue

    class SteppedTaskQueue(TaskQueue):
        def connect(self) -> None:  # the seam: no ConnectionPool, only the stepped connection
            self.pool = _OnePool(sync)

    return SteppedTaskQueue("postgresql://unused/unused", QUEUE, **kw)


def ds_task(jkey: str, n: int) -> int:
    recorder.handled(jkey)
    return n * 2


async def _sync(conn: AsyncSteppedConnection) -> _SyncConn:
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("postgres_tq may only be given the actor's stepped connection")
    verify_pinned()
    await conn.set_autocommit(False)
    return _SyncConn(conn, asyncio.get_running_loop())


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    sync = await _sync(conn)
    await asyncio.to_thread(_queue, sync, create_table=True)
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    sync = await _sync(conn)

    def go() -> list[str]:
        q = _queue(sync)
        return [q.add({"k": j["key"], "n": j["args"]["n"], "task": "ds_task"}, LEASE_S) for j in jobs]

    return {"enqueued": await asyncio.to_thread(go)}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    sync = await _sync(conn)
    deadline = time.monotonic() + timeout_s

    def go() -> int:
        q = _queue(sync)
        done = 0
        for task, _id, _name in q:  # the library's own loop: get, then complete after the body
            if task.get("task") != "ds_task":
                raise RuntimeError(f"unknown task {task!r}")
            ds_task(task["k"], int(task["n"]))
            done += 1
            if time.monotonic() > deadline:
                raise TimeoutError("work timed out")
        return done

    done = await asyncio.to_thread(go)
    return {"processed": done, "handled": list(recorder.HANDLED)}
