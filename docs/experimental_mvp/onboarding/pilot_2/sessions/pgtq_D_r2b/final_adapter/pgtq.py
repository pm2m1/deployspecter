"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): pgtq 0.5.0's
``AsyncPGTQ`` under the onb-pilot contract.

* **Seam:** ``AsyncPGTQ(dsn, conn, listen_conn)`` takes caller-made connections. Both are the actor's stepped
  connection; ``AsyncPGTQ.create`` (which connects by DSN) is never called, and ``close`` is left to the harness.
* **Declared deviation:** the library's worker normally LISTENs on a second connection. Here ``listen_conn`` is the
  same stepped connection, so ``LISTEN`` runs on it. Every job is enqueued before ``work`` starts, so the library's
  ``listen()`` claims each one with its own ``dequeue_one`` (``FOR UPDATE SKIP LOCKED``) without waiting on a
  notification; the loop stops after ``expect`` tasks instead of running forever (``start_worker``).
* **The trusted task** ``ds_task(n, jkey)`` calls the harness recorder and returns ``n * 2``. Args are passed as
  ``{"n": ..., "jkey": ...}`` (jsonb's own key order).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from pathlib import Path
from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"pgtq/__init__.py": "8884110068324d972436c1a9bb2dac836dc5b73fd08cdb42ab931a3482d1eeaf",
          "pgtq/async_core.py": "a452ca64b832125e4e78c625108c8ab3a0a7dac8d57d7775a71f11e9c984c729",
          "pgtq/task.py": "d3778cc85e8a479d82cebfc459baf498042b4666a831393aa43207aeb6aed710"}


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
    q = AsyncPGTQ(dsn="unused", conn=conn, listen_conn=conn)

    @q.task("ds_task")
    async def ds_task(n: int, jkey: str) -> int:
        recorder.handled(jkey)
        return n * 2

    return q


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    await _queue(conn).install()
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    q = _queue(conn)
    ids = [await q.enqueue("ds_task", args={"n": j["args"]["n"], "jkey": j["key"]}) for j in jobs]
    return {"enqueued": ids}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    q = _queue(conn)
    done = 0

    async def drain() -> None:
        nonlocal done
        gen = q.listen(idle_poll_interval=0.5)
        try:
            async for task in gen:
                await q.run_task(task)
                done += 1
                if done >= expect:
                    break
        finally:
            await gen.aclose()

    await asyncio.wait_for(drain(), timeout_s)
    return {"processed": done, "handled": list(recorder.HANDLED)}
