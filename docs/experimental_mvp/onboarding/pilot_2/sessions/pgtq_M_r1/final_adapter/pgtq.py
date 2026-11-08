"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): pgtq 0.5.0's
``AsyncPGTQ`` under the onb-pilot contract.

* **Seam:** ``AsyncPGTQ.__init__`` takes caller-made connections (``conn``, ``listen_conn``); the adapter passes the
  actor's stepped connection as both, so the library opens no connection of its own (``AsyncPGTQ.create`` would).
* **Install / enqueue:** the library's own ``install()`` and ``enqueue()`` (which also sends its ``NOTIFY``).
* **Work:** the library's own ``listen()`` generator (``LISTEN`` on the stepped connection, then ``dequeue_one``
  with ``FOR UPDATE SKIP LOCKED``) and ``run_task()`` (handler, then ``complete`` = ``DELETE``), i.e. the body of
  ``start_worker()``, stopped after ``expect`` tasks instead of running forever.
* **The trusted task** ``ds_task(n, jkey)`` calls the harness recorder and returns ``n * 2``. Its args dict is built
  in jsonb key order (shorter keys first) so the ``json.dumps`` text the library binds equals jsonb's rendering.
* The adapter never calls ``close()``: the connection belongs to the harness.
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
          "pgtq/task.py": "d3778cc85e8a479d82cebfc459baf498042b4666a831393aa43207aeb6aed710",
          "pgtq/core.py": "7c0cdb941efb7eb763b4478a9eb8080febbacbc503b9032527b71c95a218b49c"}
TASK = "ds_task"


def verify_pinned() -> dict[str, str]:
    import pgtq
    root = Path(inspect.getsourcefile(pgtq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"pgtq sources aren't the pinned 0.5.0 ones: {got}")
    return {"version": "0.5.0", **got}


async def ds_task(n: int, jkey: str) -> int:
    recorder.handled(jkey)
    return n * 2


def _queue(conn: AsyncSteppedConnection) -> Any:
    from pgtq import AsyncPGTQ
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("pgtq may only be given the actor's stepped connection")
    verify_pinned()
    q = AsyncPGTQ(dsn="postgresql://unused/unused", conn=conn, listen_conn=conn)
    q.task(TASK)(ds_task)
    return q


def _args(key: str, args: dict[str, Any]) -> dict[str, Any]:
    full = {**args, "jkey": key}
    return {k: full[k] for k in sorted(full, key=lambda k: (len(k), k))}


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    await _queue(conn).install()
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    q = _queue(conn)
    ids = [await q.enqueue(j["task"], args=_args(j["key"], j["args"])) for j in jobs]
    return {"enqueued": ids}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    q = _queue(conn)
    done: list[int] = []

    async def drain() -> None:
        gen = q.listen(acceptable_tasks=q.registered_task_names, idle_poll_interval=1.0)
        try:
            async for task in gen:
                await q.run_task(task)
                done.append(task.id)
                if len(done) >= expect:
                    break
        finally:
            await gen.aclose()

    await asyncio.wait_for(drain(), timeout_s)
    return {"processed": done, "handled": list(recorder.HANDLED)}
