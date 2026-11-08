"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): postgres-tq 1.2.1
(package ``postgrestq``) under the onb-pilot/1 contract.

* **Seam (declared deviation):** ``TaskQueue`` is synchronous and builds its own ``psycopg_pool.ConnectionPool``
  from a DSN in ``connect()``. ``SteppedTaskQueue`` overrides only ``connect()``, installing a one-connection sync
  facade (``pool.connection()``, ``conn.cursor()``, ``cursor.execute/fetchone/fetchall/rowcount``, ``commit``,
  ``rollback``) over the actor's stepped async connection. Every other method, and all of the library's SQL, is the
  library's own.
* **Bridge:** the library's sync code runs in a worker thread (``asyncio.to_thread``, which carries the actor's
  context); each database call is sent back to the actor's event loop with ``run_coroutine_threadsafe``, so it is a
  task acting for the actor and passes the gate. One call is in flight at a time.
* **Transactions:** the connection is switched to non-autocommit, as psycopg's pool connections are. As
  ``ConnectionPool.connection()`` does, leaving ``pool.connection()`` commits an open transaction, or rolls it back on
  an exception.
* **Payload:** a task is ``{"a": args, "k": key, "t": task}``; the worker uses the library's own iterator
  (``get`` / ``complete`` / ``is_empty``) and dispatches to the trusted task.
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"postgrestq/task_queue.py": "f7625903a24240f9830e8408b39ecdacd4257d3423e3652c161a34ed4ce9c4f4",
          "postgrestq/__init__.py": "589bac13f3ae70e1fe55f178e74d6947afd45384078ddf64be3c27e00593a294"}
QUEUE = "ds_q"
LEASE_TIMEOUT_S = 120.0


def verify_pinned() -> dict[str, str]:
    import postgrestq.task_queue as tq
    root = Path(inspect.getsourcefile(tq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"postgrestq sources aren't the pinned 1.2.1 ones: {got}")
    return {"version": "1.2.1", **got}


class _Bridge:
    def __init__(self, conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        if not isinstance(conn, AsyncSteppedConnection):
            raise RuntimeError("postgrestq may only be given the actor's stepped connection")
        self.conn, self.loop, self.stopped = conn, loop, threading.Event()

    def call(self, coro: Any) -> Any:
        if self.stopped.is_set():
            coro.close()
            raise RuntimeError("the step was stopped")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=120)

    def idle(self) -> bool:
        return self.conn.info.transaction_status.name == "IDLE"


class _Cursor:
    def __init__(self, b: _Bridge) -> None:
        self._b, self._cur = b, b.conn.cursor()

    def execute(self, query: Any, params: Any = None) -> _Cursor:
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

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class _Conn:
    def __init__(self, b: _Bridge) -> None:
        self._b = b

    def cursor(self) -> _Cursor:
        return _Cursor(self._b)

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        return _Cursor(self._b).execute(query, params)

    def commit(self) -> None:
        if not self._b.idle():
            self._b.call(self._b.conn.commit())

    def rollback(self) -> None:
        if not self._b.idle():
            self._b.call(self._b.conn.rollback())


class _Pool:
    def __init__(self, b: _Bridge) -> None:
        self._conn = _Conn(b)

    @contextlib.contextmanager
    def connection(self) -> Iterator[_Conn]:
        try:
            yield self._conn
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()


def _queue(b: _Bridge, *, create_table: bool = False) -> Any:
    from postgrestq.task_queue import TaskQueue

    class SteppedTaskQueue(TaskQueue):
        def connect(self) -> None:  # the declared deviation: the seam
            self.pool = _Pool(b)

    return SteppedTaskQueue("unused", QUEUE, create_table=create_table)


def ds_task(jkey: str, n: int) -> int:
    recorder.handled(jkey)
    return n * 2


TASKS = {"ds_task": ds_task}


async def _in_thread(conn: AsyncSteppedConnection, body: Any, timeout_s: float = 120) -> dict[str, Any]:
    verify_pinned()
    await conn.set_autocommit(False)
    b = _Bridge(conn, asyncio.get_running_loop())
    try:
        return await asyncio.wait_for(asyncio.to_thread(body, b), timeout_s)
    finally:
        b.stopped.set()


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    def body(b: _Bridge) -> dict[str, Any]:
        _queue(b, create_table=True)
        return {"installed": True}
    return await _in_thread(conn, body)


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    def body(b: _Bridge) -> dict[str, Any]:
        q = _queue(b)
        ids = q.add_many([{"a": j["args"], "k": j["key"], "t": j["task"]} for j in jobs], LEASE_TIMEOUT_S)
        return {"enqueued": ids}
    return await _in_thread(conn, body)


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    def body(b: _Bridge) -> dict[str, Any]:
        q, done = _queue(b), 0
        for task, _id, _q in q:  # the library's own get / complete / is_empty loop
            TASKS[task["t"]](task["k"], **task["a"])
            done += 1
        return {"processed": done, "expect": expect, "handled": list(recorder.HANDLED)}
    return await _in_thread(conn, body, timeout_s)
