"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): pgtq 0.5.0's
``AsyncPGTQ`` under the onb-pilot/1 contract.

* **Seam:** ``AsyncPGTQ.__init__`` takes ready-made ``conn`` and ``listen_conn`` connections (``create`` is only a
  convenience that opens two). Both are given the actor's stepped connection; nothing else is opened.
* **Declared deviation (no LISTEN):** ``start_worker``/``listen`` issue ``LISTEN`` and wait on
  ``notifies()``, which needs a dedicated connection. ``work`` instead runs the same loop body ``listen`` and
  ``start_worker`` run, polling: the library's own ``dequeue_one`` (``FOR UPDATE SKIP LOCKED`` claim) and its own
  ``run_task`` (dispatch, then ``complete`` deletes the row). The ``NOTIFY`` sent on enqueue still runs.
* **Args order:** the library sends ``json.dumps(args)`` as text into a jsonb column; the adapter orders the keys
  the way jsonb stores them (by length, then bytewise), so the logged and the stored form agree.
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``.
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
          "pgtq/core.py": "7c0cdb941efb7eb763b4478a9eb8080febbacbc503b9032527b71c95a218b49c",
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
    q = AsyncPGTQ(dsn="postgresql://unused/unused", conn=conn, listen_conn=conn)

    @q.task("ds_task")
    def ds_task(jkey: str, n: int) -> int:
        recorder.handled(jkey)
        return n * 2

    return q


def _jsonb_ordered(args: dict[str, Any]) -> dict[str, Any]:
    return {k: args[k] for k in sorted(args, key=lambda k: (len(k.encode()), k.encode()))}


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    await _queue(conn).install()
    return {"installed": True}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    q = _queue(conn)
    ids = [await q.enqueue(j["task"], args=_jsonb_ordered({"jkey": j["key"], **j["args"]})) for j in jobs]
    return {"enqueued": ids}


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    q = _queue(conn)
    done = 0

    async def drain() -> None:
        nonlocal done
        while done < expect:
            task = await q.dequeue_one(acceptable_tasks=q.registered_task_names)
            if task is None:
                await asyncio.sleep(0.3)
                continue
            await q.run_task(task)
            done += 1

    await asyncio.wait_for(drain(), timeout_s)
    return {"processed": done, "handled": list(recorder.HANDLED)}
