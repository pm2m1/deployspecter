"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): pgtq 0.5.0 under the
onb-pilot contract.

* **Seam:** ``AsyncPGTQ(dsn, conn, listen_conn)`` takes caller-supplied connections, so both are the actor's stepped
  connection (autocommit, search_path = the run's schema). ``AsyncPGTQ.create`` (which opens its own) isn't used.
* **No LISTEN:** ``work`` doesn't use ``listen``/``start_worker`` (they block on ``NOTIFY`` with no stop). It loops the
  library's own ``dequeue_one`` (``FOR UPDATE SKIP LOCKED`` claim) and ``run_task`` (handler call, then ``complete``
  which deletes the row) until ``expect`` tasks have run, polling every 0.3 s when the queue is empty.
* **The trusted task** ``add_numbers(a, b, jkey)`` calls the harness recorder and returns ``a + b``. The args dict is
  built in jsonb's key order (shorter keys first), so ``json.dumps`` text equals the server's normalized jsonb form.
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
          "pgtq/task.py": "d3778cc85e8a479d82cebfc459baf498042b4666a831393aa43207aeb6aed710"}
TASK = "add_numbers"


def verify_pinned() -> dict[str, str]:
    import pgtq
    root = Path(inspect.getsourcefile(pgtq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"pgtq sources aren't the pinned 0.5.0 ones: {got}")
    return {"version": "0.5.0", **got}


def _queue(conn: AsyncSteppedConnection) -> Any:
    from pgtq.async_core import AsyncPGTQ
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("pgtq may only be given the actor's stepped connection")
    verify_pinned()
    q = AsyncPGTQ(dsn="postgresql://unused/unused", conn=conn, listen_conn=conn)

    @q.task(TASK)
    async def add_numbers(a: int, b: int, jkey: str) -> int:
        recorder.handled(jkey)
        return a + b

    return q


def _args(job: dict[str, Any]) -> dict[str, Any]:
    args = {**job["args"], "jkey": job["key"]}
    return {k: args[k] for k in sorted(args, key=lambda k: (len(k.encode()), k.encode()))}


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    await _queue(conn).install()
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    q = _queue(conn)
    ids = [await q.enqueue(j["task"], args=_args(j)) for j in jobs]
    return {"enqueued": ids}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    q = _queue(conn)
    deadline = time.monotonic() + timeout_s
    done, results = 0, []
    while done < expect:
        task = await q.dequeue_one(acceptable_tasks=q.registered_task_names)
        if task is None:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"only {done} of {expect} tasks ran within {timeout_s} s")
            await asyncio.sleep(0.3)
            continue
        results.append(await q.run_task(task))
        done += 1
    return {"processed": done, "results": results, "handled": list(recorder.HANDLED)}
