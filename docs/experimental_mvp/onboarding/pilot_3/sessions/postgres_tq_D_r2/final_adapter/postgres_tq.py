"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): postgres-tq 1.2.1
(``postgrestq.TaskQueue``) under the onb-pilot/1 contract.

* **Seam:** the library is synchronous and builds its own ``psycopg_pool.ConnectionPool(dsn, min_size=2)`` in
  ``TaskQueue.connect()``. **Declared deviation:** ``connect()`` is overridden to install a one-connection shim pool
  instead. The shim's ``connection()``/``cursor()``/``execute``/``fetch*``/``commit`` forward every call, unchanged, to
  the actor's ``AsyncSteppedConnection`` (each coroutine is run on the actor's event loop from the worker thread, so
  it passes the gate). The library's own methods, SQL and parameters run as written.
* **Threading:** the library's sync calls run in ``asyncio.to_thread`` (the context, so the acting-for actor, is
  copied), and each database call goes back to the loop with ``run_coroutine_threadsafe``.
* **Task payload:** ``{"k": key, "n": n, "t": task}``. One-letter keys in sorted order, so the library's
  ``json.dumps(sort_keys=True)`` text equals jsonb's normalized text.
* **Work:** the library's own ``__iter__`` (get, run, complete, until ``is_empty()``).
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"postgrestq/task_queue.py": "f7625903a24240f9830e8408b39ecdacd4257d3423e3652c161a34ed4ce9c4f4",
          "postgrestq/__init__.py": "589bac13f3ae70e1fe55f178e74d6947afd45384078ddf64be3c27e00593a294"}
QUEUE = "ds_q"
LEASE_S = 30.0


def verify_pinned() -> dict[str, str]:
    import postgrestq
    root = Path(inspect.getsourcefile(postgrestq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"postgrestq sources aren't the pinned 1.2.1 ones: {got}")
    return {"version": "1.2.1", **got}


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._c = conn
        self._cur = conn.aconn.cursor()

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        self._c.run(self._cur.execute(query, params))
        return self

    def fetchone(self) -> Any:
        return self._c.run(self._cur.fetchone())

    def fetchall(self) -> Any:
        return self._c.run(self._cur.fetchall())

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._c.run(self._cur.close())


class _Conn:
    def __init__(self, aconn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        self.aconn, self.loop = aconn, loop

    def run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        return self.cursor().execute(query, params)

    def commit(self) -> None:
        self.run(self.aconn.commit())

    def rollback(self) -> None:
        self.run(self.aconn.rollback())


class _ShimPool:
    def __init__(self, aconn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        if not isinstance(aconn, AsyncSteppedConnection):
            raise RuntimeError("postgrestq may only be given the actor's stepped connection")
        self._conn = _Conn(aconn, loop)

    @contextlib.contextmanager
    def connection(self) -> Iterator[_Conn]:
        yield self._conn

    def close(self) -> None:
        return None


def ds_task(jkey: str, n: int) -> int:
    recorder.handled(jkey)
    return n * 2


TASKS = {"ds_task": ds_task}


def _queue(conn: AsyncSteppedConnection, **kw: Any) -> Any:
    from postgrestq import TaskQueue
    verify_pinned()
    pool = _ShimPool(conn, asyncio.get_running_loop())

    class SteppedTaskQueue(TaskQueue):
        def connect(self) -> None:  # the declared deviation: no own pool
            self.pool = pool

    return lambda: SteppedTaskQueue("postgresql://unused/unused", QUEUE, **kw)


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    make = _queue(conn, create_table=True, reset=True)
    await asyncio.to_thread(make)
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    make = _queue(conn)

    def go() -> list[str]:
        q = make()
        return [q.add({"k": j["key"], "n": j["args"]["n"], "t": j["task"]}, LEASE_S, ttl=3) for j in jobs]

    return {"enqueued": await asyncio.to_thread(go)}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    make = _queue(conn)

    def go() -> int:
        q = make()
        done = 0
        for task, _id, _queue_name in q:  # the library's iterator completes each task after the body
            TASKS[task["t"]](task["k"], task["n"])
            done += 1
        return done

    done = await asyncio.wait_for(asyncio.to_thread(go), timeout_s)
    return {"processed": done, "expect": expect, "handled": list(recorder.HANDLED)}
