"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): postgres-tq 1.2.1
(``postgrestq.TaskQueue``) under the onb-pilot/1 contract.

* **Seam:** ``TaskQueue`` is synchronous and builds its own ``psycopg_pool.ConnectionPool`` in its public
  ``connect()``, which ``__init__`` calls. A subclass overrides ``connect()`` to set ``self.pool`` to a one-connection
  pool whose only connection forwards every call to the actor's stepped ``AsyncSteppedConnection``. All of the
  library's own SQL (``_create_queue_table``, ``add``, ``get``, ``complete``, ``check_expired_leases``,
  ``__len__``) is unchanged and goes through the gate.
* **Sync-to-async bridge:** the library's synchronous code runs in a worker thread (``asyncio.to_thread``, which
  carries the actor's context); each database call is handed back to the event loop with
  ``run_coroutine_threadsafe`` and waited on, so exactly one operation is in flight and the loop (the controller)
  is never blocked.
* **Transactions:** the stepped connection is switched to ``autocommit=False``, as the library's own pool
  connections are; the library commits explicitly, and the pool's ``connection()`` context commits on exit (rolls
  back on an exception), as ``psycopg_pool`` does.
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``. A queued task is the
  JSON ``{"task": <name>, "key": <job key>, "args": {...}}``.
* **Work** is the library's own consumer, ``for task, id_, _ in queue`` (``get``, then ``complete`` after the body,
  until ``is_empty()``), bounded by ``timeout_s``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"postgrestq/__init__.py": "589bac13f3ae70e1fe55f178e74d6947afd45384078ddf64be3c27e00593a294",
          "postgrestq/task_queue.py": "f7625903a24240f9830e8408b39ecdacd4257d3423e3652c161a34ed4ce9c4f4"}
QUEUE = "ds_q"
LEASE_TIMEOUT_S = 30.0


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


TASKS: dict[str, Callable[..., Any]] = {"ds_task": ds_task}


class _Bridge:
    """Runs one coroutine of the stepped connection on the event loop and waits for it, from the worker thread."""

    def __init__(self, conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        self.conn, self.loop = conn, loop

    def call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


class _Cursor:
    def __init__(self, bridge: _Bridge) -> None:
        async def make() -> Any:
            return bridge.conn.cursor()
        self._b = bridge
        self._c = bridge.call(make())

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._b.call(self._c.close())

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        self._b.call(self._c.execute(query, params))
        return self

    def fetchone(self) -> Any:
        return self._b.call(self._c.fetchone())

    def fetchall(self) -> list[Any]:
        return self._b.call(self._c.fetchall())

    @property
    def rowcount(self) -> int:
        return self._c.rowcount

    @property
    def description(self) -> Any:
        return self._c.description


class _Connection:
    def __init__(self, bridge: _Bridge) -> None:
        self._b = bridge

    def cursor(self) -> _Cursor:
        return _Cursor(self._b)

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        return _Cursor(self._b).execute(query, params)

    def commit(self) -> None:
        self._b.call(self._b.conn.commit())

    def rollback(self) -> None:
        self._b.call(self._b.conn.rollback())


class _Pool:
    """The one-connection stand-in for ``psycopg_pool.ConnectionPool``: ``connection()`` commits on a clean exit and
    rolls back on an exception, as a pooled psycopg connection's context does."""

    def __init__(self, bridge: _Bridge) -> None:
        self._conn = _Connection(bridge)

    @contextlib.contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[_Connection]:
        try:
            yield self._conn
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def wait(self, timeout: float | None = None) -> None:
        return None

    def close(self) -> None:
        return None


def _queue_class(bridge: _Bridge) -> Any:
    from postgrestq import TaskQueue

    class SteppedTaskQueue(TaskQueue):  # the seam: the library's own connect() builds its pool here
        def connect(self) -> None:
            self.pool = _Pool(bridge)

    return SteppedTaskQueue


async def _in_thread(conn: AsyncSteppedConnection, body: Callable[[Any], Any]) -> Any:
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("postgrestq may only be given the actor's stepped connection")
    verify_pinned()
    await conn.set_autocommit(False)  # as the library's own pool connections
    bridge = _Bridge(conn, asyncio.get_running_loop())
    return await asyncio.to_thread(body, _queue_class(bridge))


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    def body(queue_cls: Any) -> dict[str, Any]:
        queue_cls("unused", QUEUE, create_table=True)
        return {"installed": True}
    return await _in_thread(conn, body)


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    def body(queue_cls: Any) -> dict[str, Any]:
        q = queue_cls("unused", QUEUE)
        ids = [q.add({"task": j["task"], "key": j["key"], "args": j["args"]}, LEASE_TIMEOUT_S, ttl=3)
               for j in jobs]
        return {"enqueued": ids}
    return await _in_thread(conn, body)


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    def body(queue_cls: Any) -> dict[str, Any]:
        q = queue_cls("unused", QUEUE)
        deadline = time.monotonic() + timeout_s
        processed = 0
        for task, _id, _queue in q:  # the library's consumer: get(), the body, then complete(); until is_empty()
            TASKS[task["task"]](task["key"], **task["args"])
            processed += 1
            if time.monotonic() > deadline:
                raise TimeoutError(f"work exceeded {timeout_s} s after {processed} task(s)")
        return {"processed": processed, "expect": expect, "handled": list(recorder.HANDLED)}
    return await _in_thread(conn, body)
