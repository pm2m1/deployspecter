"""EXPERIMENTAL onboarding pilot adapter (condition D, written during the measured session): saq 0.26.5's Postgres
queue under the onb-pilot/1 contract.

* **Seam:** ``PostgresQueue(pool=...)`` accepts a caller's ``psycopg_pool.AsyncConnectionPool``. The adapter gives it
  a one-slot pool whose only connection is the actor's stepped connection (as for Chancy). A second connection is
  refused.
* **No LISTEN:** the worker polls (``poll_interval > 0``), so saq's ``ListenMultiplexer`` never starts.
* **The trusted task** ``ds_task(ctx, *, jkey, n)`` calls the harness recorder and returns ``n * 2``. (``key``
  can't be used: saq's ``enqueue`` takes ``key`` as the job's own key, not as a task argument.)
* **Upkeep** timers are set beyond the run (3600 s), so its periodic worker-info and sweep writes don't recur.
* **Declared deviation:** ``write_worker_info`` (worker stats, not job processing) is overridden to pass its two
  JSON values as ``Jsonb`` instead of ``json.dumps`` text. The stored values are identical. The reason is that the
  server logs a text parameter bound to a jsonb column in jsonb-normalized form (keys reordered), which the
  corroborator can't compare with the trace's text digest. That is an instrument limitation, recorded as such in the
  session.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from pathlib import Path
from typing import Any

import psycopg

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection

PINNED = {"saq/queue/postgres.py": "74bea93301231a42826699b263ba7b677cf7957c6266a6d2a715aab8021c7a01",
          "saq/queue/base.py": "f1fcd85fd44e3d010eba756b27ffaa217b33c3b9dd02937931eca81add3e114b",
          "saq/queue/postgres_migrations.py": "1b40e3ea267f264ea2e8296ec2de420b7a9cdef6eb77f59384acc6a1c94bd9a2",
          "saq/worker.py": "347235f7e7fc49cd48b5ea6d6aaf2e6b9a19cfa69e34494fcc64e69332a4f53c",
          "saq/job.py": "0826acfa1a0c2f37299c92469025211aa2f6014fb8d3e4ac38fe26b2e5d080f4"}


def verify_pinned() -> dict[str, str]:
    import saq
    root = Path(inspect.getsourcefile(saq) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"saq sources aren't the pinned 0.26.5 ones: {got}")
    return {"version": "0.26.5", **got}


def stepped_pool(conn: AsyncSteppedConnection) -> Any:
    from psycopg_pool import AsyncConnectionPool
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("saq may only be given the actor's stepped connection")
    handed: list[Any] = []

    class ActorBoundConnection(AsyncSteppedConnection):
        @classmethod
        async def connect(cls, *_a: Any, **_k: Any) -> Any:
            if handed:
                raise psycopg.OperationalError("refused: a second connection; only the stepped connection exists")
            handed.append(conn)
            return conn

    return AsyncConnectionPool("", connection_class=ActorBoundConnection, min_size=1, max_size=1, open=False,
                               timeout=30, reconnect_timeout=10, kwargs={"autocommit": True})


async def ds_task(ctx: Any, *, jkey: str, n: int) -> int:
    recorder.handled(jkey)
    return n * 2


def _queue_class() -> Any:
    from psycopg.sql import SQL
    from psycopg.types.json import Jsonb
    from saq.queue.postgres import PostgresQueue

    class JsonbStatsQueue(PostgresQueue):
        async def write_worker_info(self, worker_id: str, info: Any, ttl: int) -> None:  # the declared deviation
            async with self.pool.connection() as conn:
                await conn.execute(
                    SQL("INSERT INTO {stats_table} (worker_id, stats, queue_key, metadata, expire_at) VALUES "
                        "(%(worker_id)s, %(stats)s, %(queue_key)s, %(metadata)s, EXTRACT(EPOCH FROM NOW()) + %(ttl)s) "
                        "ON CONFLICT (worker_id) DO UPDATE SET stats = %(stats)s, queue_key = %(queue_key)s, "
                        "metadata = %(metadata)s, expire_at = EXTRACT(EPOCH FROM NOW()) + %(ttl)s"
                        ).format(stats_table=self.stats_table),
                    {"worker_id": worker_id, "stats": Jsonb(info["stats"]), "ttl": ttl,
                     "queue_key": info["queue_key"], "metadata": Jsonb(info["metadata"])})

    return JsonbStatsQueue


async def _queue(conn: AsyncSteppedConnection) -> Any:
    verify_pinned()
    pool = stepped_pool(conn)
    await pool.open()
    q = _queue_class()(pool=pool, name="ds_q")
    await q.connect()
    return q, pool


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    q, pool = await _queue(conn)
    try:
        return {"installed": True}
    finally:
        await q.disconnect()
        await pool.close()


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    q, pool = await _queue(conn)
    try:
        out = [await q.enqueue("ds_task", jkey=j["key"], **j["args"]) for j in jobs]
        return {"enqueued": [j.key if j else None for j in out]}
    finally:
        await q.disconnect()
        await pool.close()


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    from saq import Worker
    q, pool = await _queue(conn)
    w = Worker(q, functions=[ds_task], concurrency=1, burst=True, max_burst_jobs=expect, dequeue_timeout=1.0,
               poll_interval=0.5, timers={"schedule": 3600, "worker_info": 3600, "sweep": 3600, "abort": 3600})
    try:
        await asyncio.wait_for(w.start(), timeout_s)
        return {"handled": list(recorder.HANDLED)}
    finally:
        await w.stop()
        await q.disconnect()
        await pool.close()
