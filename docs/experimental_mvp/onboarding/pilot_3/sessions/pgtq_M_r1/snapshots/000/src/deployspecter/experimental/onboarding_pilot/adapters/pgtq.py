"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): pgtq 0.5.0's
``AsyncPGTQ`` under the onb-pilot/1 contract.

* **Seam:** ``AsyncPGTQ(dsn, conn, listen_conn)`` takes caller-made async connections directly (``create()`` is only
  a convenience that connects). Both are the actor's stepped connection; the adapter never opens one and never
  calls ``close()`` (the harness owns the connection). Tables are unqualified, so they land in the run's schema
  through the stepped connection's ``search_path``.
* **No LISTEN:** ``start_worker()`` runs forever on ``listen()`` (``LISTEN`` + ``notifies()``). ``work`` instead
  drives the library's own ``dequeue_one()`` (``FOR UPDATE SKIP LOCKED`` claim) and ``run_task()`` (handler call,
  then ``complete``/``fail``) until ``expect`` tasks have run, polling every 0.5 s when the queue is empty. The
  ``NOTIFY`` that ``enqueue`` sends still runs.
* **The trusted task** ``add_numbers(a, b, jkey)`` calls the harness recorder and returns ``a + b``. The job key
  travels in the task's JSON ``args`` as ``jkey``. The args dict is built in jsonb's canonical key order (shorter
  keys first, then bytewise) so the ``json.dumps`` text the library binds equals the server's jsonb rendering.
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

TASK = "add_numbers"
PINNED = {"pgtq/__init__.py": "8884110068324d972436c1a9bb2dac836dc5b73fd08cdb42ab931a3482d1eeaf",
          "pgtq/async_core.py": "a452ca64b832125e4e78c625108c8ab3a0a7dac8d57d7775a71f11e9c984c729",
          "pgtq/task.py": "d3778cc85e8a479d82cebfc459baf498042b4666a831393aa43207aeb6aed710",
          "pgtq/core.py": "7c0cdb941efb7eb763b4478a9eb8080febbacbc503b9032527b71c95a218b49c"}


def verify_pinned() -> dict[str, str]:
    import pgtq
    root = Path(inspect.getsourcefile(pgtq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"pgtq sources aren't the pinned 0.5.0 ones: {got}")
    return {"version": "0.5.0", **got}


def add_numbers(a: int, b: int, jkey: str) -> int:
    recorder.handled(jkey)
    return a + b


def _queue(conn: AsyncSteppedConnection) -> Any:
    from pgtq import AsyncPGTQ
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("pgtq may only be given the actor's stepped connection")
    q = AsyncPGTQ(dsn="", conn=conn, listen_conn=conn)
    q.task(TASK)(add_numbers)
    return q


def _canonical(args: dict[str, Any], key: str) -> dict[str, Any]:
    d = {**args, "jkey": key}
    return {k: d[k] for k in sorted(d, key=lambda k: (len(k.encode()), k.encode()))}


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    verify_pinned()
    q = _queue(conn)
    await q.install()
    return {"installed": True, "table": q.table_name}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    verify_pinned()
    q = _queue(conn)
    ids = []
    for j in jobs:
        if j["task"] != TASK:
            raise ValueError(f"undeclared task {j['task']!r}")
        ids.append(await q.enqueue(TASK, args=_canonical(j["args"], j["key"])))
    return {"enqueued": [j["key"] for j in jobs], "task_ids": ids}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    verify_pinned()
    q = _queue(conn)
    ran, deadline = 0, time.monotonic() + timeout_s
    while ran < expect:
        if time.monotonic() > deadline:
            raise TimeoutError(f"only {ran} of {expect} tasks ran within {timeout_s} s")
        task = await q.dequeue_one(acceptable_tasks=q.registered_task_names)
        if task is None:
            await asyncio.sleep(0.5)
            continue
        await q.run_task(task)
        ran += 1
    return {"ran": ran, "handled": list(recorder.HANDLED)}
