"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): postgres-tq 1.2.1
(``postgrestq.TaskQueue``) under the onb-pilot/1 contract.

* **The library is synchronous** (psycopg 3 ``ConnectionPool``); the contract's connection is async. Each step
  therefore runs the library's own methods in one worker thread (``asyncio.to_thread``, which carries the actor's
  context), and every database call the library makes is bridged back onto the event loop with
  ``asyncio.run_coroutine_threadsafe`` against the actor's ``AsyncSteppedConnection``, so it passes the gate.
* **Seam / declared deviation:** ``TaskQueue.connect`` (the library's only connection point, which would build
  ``ConnectionPool(dsn, min_size=2)``) is overridden to install ``BridgePool``: a pool whose ``connection()`` always
  yields a thin synchronous wrapper of the one stepped connection (re-entrant, since ``check_expired_leases`` nests
  acquisitions). The wrapper exposes what the library uses: ``cursor()`` (``execute``, ``fetchone``, ``fetchall``,
  ``rowcount``, context manager), ``execute`` and ``commit``. The stepped connection is in autocommit mode, so the
  library's ``commit()`` calls are gated but send nothing; each of its statements is a single atomic statement.
* **SQL:** all of it is the library's own (``CREATE TABLE/INDEX IF NOT EXISTS``, ``INSERT``, the
  ``UPDATE ... FOR UPDATE SKIP LOCKED ... RETURNING`` claim, ``complete``, the lease check and ``count(*)``).
* **Work:** the library's own ``TaskQueue.__iter__`` loop (get, yield, complete, ``is_empty``) drains the queue.
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``. A task payload is
  ``{"a": args, "k": key, "t": task}`` (equal-length keys, so ``json.dumps(sort_keys=True)`` is already in jsonb
  key order).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"postgrestq/__init__.py": "589bac13f3ae70e1fe55f178e74d6947afd45384078ddf64be3c27e00593a294",
          "postgrestq/task_queue.py": "f7625903a24240f9830e8408b39ecdacd4257d3423e3652c161a34ed4ce9c4f4"}
QUEUE = "ds_q"
LEASE_TIMEOUT_S = 600.5
TTL = 3


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
    def __init__(self, conn: AsyncSteppedConnection, loop: asyncio.AbstractEventLoop) -> None:
        if not isinstance(conn, AsyncSteppedConnection):
            raise RuntimeError("postgrestq may only be given the actor's stepped connection")
        self.conn, self.loop = conn, loop

    def __call__(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


class BridgeCursor:
    def __init__(self, bridge: _Bridge) -> None:
        self._b = bridge
        self._c = bridge.conn.cursor()

    def execute(self, query: Any, params: Any = None) -> BridgeCursor:
        self._b(self._c.execute(query, params))
        return self

    def fetchone(self) -> Any:
        return self._b(self._c.fetchone())

    def fetchall(self) -> Any:
        return self._b(self._c.fetchall())

    @property
    def rowcount(self) -> int:
        return self._c.rowcount

    def close(self) -> None:
        self._b(self._c.close())

    def __enter__(self) -> BridgeCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class BridgeConnection:
    def __init__(self, bridge: _Bridge) -> None:
        self._b = bridge

    def cursor(self) -> BridgeCursor:
        return BridgeCursor(self._b)

    def execute(self, query: Any, params: Any = None) -> BridgeCursor:
        return BridgeCursor(self._b).execute(query, params)

    def commit(self) -> None:
        self._b(self._b.conn.commit())

    def rollback(self) -> None:
        self._b(self._b.conn.rollback())


class BridgePool:
    def __init__(self, bridge: _Bridge) -> None:
        self._conn = BridgeConnection(bridge)

    @contextlib.contextmanager
    def connection(self) -> Iterator[BridgeConnection]:
        yield self._conn

    def wait(self) -> None:
        return None

    def close(self) -> None:
        return None


def _queue(bridge: _Bridge, *, create_table: bool = False) -> Any:
    from postgrestq import TaskQueue

    verify_pinned()

    class SteppedTaskQueue(TaskQueue):
        def connect(self) -> None:  # the declared deviation: the one stepped connection, not a ConnectionPool
            self.pool = BridgePool(bridge)  # type: ignore[assignment]

    return SteppedTaskQueue("", QUEUE, create_table=create_table)


async def _in_thread(conn: AsyncSteppedConnection, fn: Callable[[_Bridge], Any]) -> Any:
    bridge = _Bridge(conn, asyncio.get_running_loop())
    return await asyncio.to_thread(fn, bridge)


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    def go(b: _Bridge) -> dict[str, Any]:
        _queue(b, create_table=True)
        return {"installed": True}
    return await _in_thread(conn, go)


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    def go(b: _Bridge) -> dict[str, Any]:
        tq = _queue(b)
        ids = [tq.add({"a": dict(j["args"]), "k": j["key"], "t": j["task"]}, LEASE_TIMEOUT_S, ttl=TTL) for j in jobs]
        return {"enqueued": ids}
    return await _in_thread(conn, go)


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    def go(b: _Bridge) -> dict[str, Any]:
        tq = _queue(b)
        done = 0
        for task, _id, _q in tq:  # the library's own loop: get, (task), complete, is_empty
            TASKS[task["t"]](task["k"], **task["a"])
            done += 1
        return {"processed": done, "expect": expect, "handled": list(recorder.HANDLED)}
    return await asyncio.wait_for(_in_thread(conn, go), timeout_s)
