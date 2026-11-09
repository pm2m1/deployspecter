"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): postgres-tq 1.2.1
(``postgrestq.TaskQueue``) under the onb-pilot/1 contract.

* **Seam:** ``TaskQueue`` is synchronous and builds its own ``psycopg_pool.ConnectionPool(dsn)`` in ``connect()``.
  **Declared deviation:** a subclass overrides only ``connect()`` to set ``self.pool`` to a facade whose
  ``connection()`` yields a synchronous view of the actor's stepped ``AsyncSteppedConnection``. Every other method,
  and all of its SQL, is the library's own. The library's sync calls run in a worker thread (``asyncio.to_thread``,
  which carries the actor's context) and each database call is submitted back to the actor's event loop with
  ``run_coroutine_threadsafe``, so it reaches the actor's gate as a task acting for the actor.
* The facade mirrors the pool's ``connection()`` context: commit on a clean exit, rollback on an exception. The
  stepped connection is autocommit (the harness's), so the library's explicit commits find no open transaction.
* **Task payload** ``{"k": key, "n": n, "t": "ds_task"}``: single-letter keys, so ``json.dumps(sort_keys=True)``
  (the library's serializer) and jsonb's normalized key order coincide.
* **Work:** the README's consumer loop (``get``; run; ``complete``; stop when ``is_empty()``), with a deadline.
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``.
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

PINNED = {"postgrestq/__init__.py": "589bac13f3ae70e1fe55f178e74d6947afd45384078ddf64be3c27e00593a294",
          "postgrestq/task_queue.py": "f7625903a24240f9830e8408b39ecdacd4257d3423e3652c161a34ed4ce9c4f4"}
QUEUE = "ds_onb"
LEASE_S = 120.0
CALL_TIMEOUT_S = 120.0


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
    def __init__(self, conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        if not isinstance(conn, AsyncSteppedConnection):
            raise RuntimeError("postgrestq may only be given the actor's stepped connection")
        self.conn, self.loop = conn, loop

    def call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(CALL_TIMEOUT_S)


class _Cursor:
    def __init__(self, b: _Bridge) -> None:
        self._b, self._c = b, b.conn.cursor()

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._b.call(self._c.close())

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        self._b.call(self._c.execute(query, params))
        return self

    def fetchone(self) -> Any:
        return self._b.call(self._c.fetchone())

    def fetchall(self) -> Any:
        return self._b.call(self._c.fetchall())

    @property
    def rowcount(self) -> int:
        return self._c.rowcount


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


class _Pool:
    def __init__(self, b: _Bridge) -> None:
        self._b = b

    def wait(self, timeout: float = 30.0) -> None:
        return None

    @contextlib.contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[_Conn]:
        conn = _Conn(self._b)
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()

    def close(self) -> None:
        return None


def _queue(conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop, **kw: Any) -> Any:
    from postgrestq import TaskQueue
    verify_pinned()
    pool = _Pool(_Bridge(conn, loop))

    class SteppedTaskQueue(TaskQueue):
        def connect(self) -> None:  # the declared deviation: the stepped facade instead of ConnectionPool(dsn)
            self.pool = pool  # type: ignore[assignment]

    return SteppedTaskQueue("postgresql://unused/unused", QUEUE, **kw)


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    loop = asyncio.get_running_loop()

    def go() -> dict[str, Any]:
        _queue(conn, loop, create_table=True)
        return {"installed": True}

    return await asyncio.to_thread(go)


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    for j in jobs:
        if j["task"] not in TASKS:
            raise ValueError(f"undeclared task {j['task']!r}")

    def go() -> dict[str, Any]:
        q = _queue(conn, loop)
        ids = [q.add({"k": j["key"], "n": int(j["args"]["n"]), "t": j["task"]}, lease_timeout=LEASE_S, ttl=3)
               for j in jobs]
        return {"enqueued": ids}

    return await asyncio.to_thread(go)


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    loop = asyncio.get_running_loop()

    def go() -> dict[str, Any]:
        q = _queue(conn, loop)
        deadline = time.monotonic() + timeout_s
        done, results = 0, []
        while time.monotonic() < deadline:
            task, task_id, _queue_name = q.get()
            if task is not None:
                results.append(TASKS[task["t"]](task["k"], task["n"]))
                q.complete(task_id)
                done += 1
            if q.is_empty():
                break
            if task is None:
                time.sleep(0.2)
        return {"processed": done, "expect": expect, "results": results, "handled": list(recorder.HANDLED)}

    return await asyncio.to_thread(go)
