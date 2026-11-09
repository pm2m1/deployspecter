"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): pgtq 0.5.0's
``AsyncPGTQ`` under the onb-pilot/1 contract.

* **Seam:** ``AsyncPGTQ(dsn, conn, listen_conn)`` takes caller-supplied async connections; both are the actor's
  stepped connection (``create()``, which opens its own, is not used).
* **No LISTEN:** ``work`` drives the library's own ``dequeue_one`` (``FOR UPDATE SKIP LOCKED`` claim) and
  ``run_task`` (handler call, then ``complete`` = DELETE), polling instead of ``listen()``/``start_worker()``.
  ``enqueue`` still sends the library's ``NOTIFY``.
* **The trusted task** ``ds_task(*, jkey, n)`` calls the harness recorder and returns ``n * 2``. The job's
  args dict is built in jsonb key order (shorter keys first), so the server log's jsonb rendering of the
  ``json.dumps`` text parameter is byte-identical to it.
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

TASK = "ds_task"
PINNED = {"pgtq/__init__.py": "8884110068324d972436c1a9bb2dac836dc5b73fd08cdb42ab931a3482d1eeaf",
          "pgtq/async_core.py": "a452ca64b832125e4e78c625108c8ab3a0a7dac8d57d7775a71f11e9c984c729",
          "pgtq/core.py": "7c0cdb941efb7eb763b4478a9eb8080febbacbc503b9032527b71c95a218b49c",
          "pgtq/task.py": "d3778cc85e8a479d82cebfc459baf498042b4666a831393aa43207aeb6aed710"}


def verify_pinned() -> dict[str, str]:
    import pgtq
    root = Path(inspect.getsourcefile(pgtq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"pgtq sources aren't the pinned 0.5.0 ones: {got}")
    return {"version": "0.5.0", **got}


def ds_task(*, jkey: str, n: int) -> int:
    recorder.handled(jkey)
    return n * 2


def _q(conn: AsyncSteppedConnection) -> Any:
    from pgtq import AsyncPGTQ
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("pgtq may only be given the actor's stepped connection")
    verify_pinned()
    q = AsyncPGTQ(dsn="unused", conn=conn, listen_conn=conn)
    q.task(TASK)(ds_task)
    return q


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    await _q(conn).install()
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    q = _q(conn)
    ids = []
    for j in jobs:
        args = {"jkey": j["key"], **j["args"]}
        args = {k: args[k] for k in sorted(args, key=lambda k: (len(k), k))}  # jsonb's key order
        ids.append(await q.enqueue(TASK, args=args))
    return {"enqueued": ids}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    q = _q(conn)
    done, deadline = 0, time.monotonic() + timeout_s
    while done < expect:
        task = await q.dequeue_one()
        if task is None:
            if time.monotonic() > deadline:
                raise TimeoutError(f"only {done} of {expect} tasks claimed")
            await asyncio.sleep(0.3)
            continue
        await q.run_task(task)
        done += 1
    return {"processed": done, "handled": list(recorder.HANDLED)}
