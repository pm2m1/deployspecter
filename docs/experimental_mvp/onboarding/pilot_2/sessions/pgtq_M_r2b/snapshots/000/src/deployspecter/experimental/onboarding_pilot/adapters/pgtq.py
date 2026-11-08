"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): pgtq 0.5.0's
``AsyncPGTQ`` under the onb-pilot/1 contract.

* **Seam:** ``AsyncPGTQ(dsn, conn, listen_conn)`` takes caller-supplied async connections. Both are the actor's
  stepped connection; ``AsyncPGTQ.create`` (which opens its own two connections) is not used, nor ``close``.
* **Declared deviation:** ``start_worker`` goes through ``listen``, which LISTENs and waits on ``notifies()``. The
  adapter's ``work`` instead runs the same loop without the LISTEN wait: the library's own ``dequeue_one`` (the
  ``FOR UPDATE SKIP LOCKED`` claim) and ``run_task`` (handler, then ``complete``/``fail``), polling when the queue is
  empty, until ``expect`` tasks have been run. The ``NOTIFY`` that ``enqueue`` sends still runs.
* **The trusted task** ``ds_task(n, jkey)`` calls the harness recorder and returns ``n * 2``. Its args are passed in
  the order ``n``, ``jkey`` (jsonb's own key order), so the jsonb column's normalization doesn't rewrite them.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"pgtq/__init__.py": "8884110068324d972436c1a9bb2dac836dc5b73fd08cdb42ab931a3482d1eeaf",
          "pgtq/async_core.py": "a452ca64b832125e4e78c625108c8ab3a0a7dac8d57d7775a71f11e9c984c729",
          "pgtq/core.py": "7c0cdb941efb7eb763b4478a9eb8080febbacbc503b9032527b71c95a218b49c",
          "pgtq/task.py": "d3778cc85e8a479d82cebfc459baf498042b4666a831393aa43207aeb6aed710"}
TASK = "ds_task"


def verify_pinned() -> dict[str, str]:
    import pgtq
    root = Path(inspect.getsourcefile(pgtq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"pgtq sources aren't the pinned 0.5.0 ones: {got}")
    return {"version": "0.5.0", **got}


def _queue(conn: AsyncSteppedConnection) -> Any:
    from pgtq import AsyncPGTQ
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("pgtq may only be given the actor's stepped connection")
    verify_pinned()
    q = AsyncPGTQ(dsn="postgresql://unused/unused", conn=conn, listen_conn=conn)

    @q.task(TASK)
    async def ds_task(n: int, jkey: str) -> int:
        recorder.handled(jkey)
        return n * 2

    return q


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    await _queue(conn).install()
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    q = _queue(conn)
    ids = [await q.enqueue(j["task"], args={**j["args"], "jkey": j["key"]}) for j in jobs]
    return {"enqueued": ids}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    q = _queue(conn)
    deadline = time.monotonic() + timeout_s
    ran = 0
    while ran < expect:  # the deviation: start_worker's loop, polling instead of waiting on LISTEN
        task = await q.dequeue_one(acceptable_tasks=q.registered_task_names)
        if task is None:
            if time.monotonic() > deadline:
                raise TimeoutError(f"only {ran} of {expect} tasks ran within {timeout_s} s")
            await asyncio.sleep(0.3)
            continue
        await q.run_task(task)
        ran += 1
    return {"ran": ran, "handled": list(recorder.HANDLED)}
