"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): postgres-tq 1.2.1
(``postgrestq.TaskQueue``) under the onb-pilot/1 contract.

* **Seam:** the library is synchronous. ``TaskQueue.connect()`` builds its own ``psycopg_pool.ConnectionPool(dsn,
  min_size=2)``; every other method only uses ``self.pool.connection()`` (a context manager yielding a connection
  with ``cursor()``, ``execute()`` and ``commit()``). The adapter overrides ``connect()`` (the declared deviation)
  to install a one-connection sync shim pool instead. The library's own methods run unchanged in a worker thread
  (``asyncio.to_thread``, which carries the actor's context); each cursor ``execute``, ``fetch*``, ``commit`` and
  ``rollback`` is sent back to the event loop with ``run_coroutine_threadsafe`` and runs on the actor's
  ``AsyncSteppedConnection``, so all of the library's database I/O passes the gate. No other connection exists.
* **Transactions as the library expects them:** a pool connection is non-autocommit, and leaving
  ``pool.connection()`` commits on success and rolls back on an exception (psycopg's ``Connection.__exit__`` for a
  pooled connection). The stepped connection is switched to non-autocommit (client-side only) for each step, and
  the shim's ``connection()`` exit does the same commit or rollback.
* **Steps:** ``install`` = ``TaskQueue(create_table=True)`` (the library's DDL); ``enqueue`` = ``TaskQueue.add`` per
  job; ``work`` = the library's own consumer loop, ``for task, id_, _ in task_queue`` (``get`` / run /
  ``complete`` / ``is_empty``), which ends when the queue is empty.
* **The task payload** is ``{"a": args, "k": key, "t": task}`` (JSON, as the library stores it); the worker
  dispatches ``t`` to the trusted ``ds_task(jkey, n)``, which calls the harness recorder and returns ``n * 2``.
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
LEASE_TIMEOUT_S = 60.0


def verify_pinned() -> dict[str, str]:
    import postgrestq
    root = Path(inspect.getsourcefile(postgrestq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"postgrestq sources aren't the pinned 1.2.1 ones: {got}")
    return {"version": "1.2.1", **got}


def ds_task(jkey: str, n: int) -> int:
    recorder.handled(jkey)
    return n * 2


TASKS = {"ds_task": ds_task}


class _Bridge:
    """Runs a coroutine on the actor's event loop from the library's worker thread, and waits for it."""

    def __init__(self, conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        if not isinstance(conn, AsyncSteppedConnection):
            raise RuntimeError("postgrestq may only be given the actor's stepped connection")
        self.conn, self.loop = conn, loop

    def call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


class _Cursor:
    def __init__(self, b: _Bridge) -> None:
        self._b = b
        self._cur = b.conn.cursor()  # the connection's cursor_factory: the gated AsyncSteppedCursor

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

    @property
    def description(self) -> Any:
        return self._cur.description

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
        self._b.call(self._b.conn.commit())

    def rollback(self) -> None:
        self._b.call(self._b.conn.rollback())


class _ShimPool:
    """The one-connection stand-in for ``psycopg_pool.ConnectionPool``, as far as ``TaskQueue`` uses it."""

    def __init__(self, b: _Bridge) -> None:
        self._b = b

    @contextlib.contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[_Conn]:
        c = _Conn(self._b)
        try:
            yield c
        except BaseException:
            c.rollback()
            raise
        else:
            c.commit()

    def wait(self, timeout: float = 30.0) -> None:
        return None

    def close(self) -> None:
        return None


def _make_queue(b: _Bridge, **kw: Any) -> Any:
    from postgrestq import TaskQueue

    class SteppedTaskQueue(TaskQueue):
        def connect(self) -> None:  # the declared deviation: no ConnectionPool of its own
            self.pool = _ShimPool(b)

    return SteppedTaskQueue("postgresql://unused/unused", QUEUE, **kw)


async def _bridge(conn: AsyncSteppedConnection) -> _Bridge:
    verify_pinned()
    b = _Bridge(conn, asyncio.get_running_loop())
    await conn.set_autocommit(False)  # as the library's own pool connections are; client-side only
    return b


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    b = await _bridge(conn)
    await asyncio.to_thread(_make_queue, b, create_table=True)
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    b = await _bridge(conn)

    def run() -> list[str]:
        q = _make_queue(b)
        return [q.add({"a": dict(j["args"]), "k": j["key"], "t": j["task"]}, LEASE_TIMEOUT_S, ttl=3) for j in jobs]

    ids = await asyncio.to_thread(run)
    return {"enqueued": len(ids)}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    b = await _bridge(conn)
    deadline = time.monotonic() + timeout_s

    def run() -> int:
        q = _make_queue(b)
        done = 0
        for task, _id, _queue in q:  # the library's consumer loop: get, (body), complete, is_empty
            if time.monotonic() > deadline:
                raise TimeoutError(f"work exceeded {timeout_s} s after {done} task(s)")
            TASKS[task["t"]](task["k"], **task["a"])
            done += 1
        return done

    done = await asyncio.to_thread(run)
    return {"processed": done, "expected": expect, "handled": list(recorder.HANDLED)}
