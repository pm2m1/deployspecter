"""EXPERIMENTAL, NONBLIND (ds-nbc/1): a declared variant of the onboarding pilot's saq adapter
(``onboarding_pilot/adapters/saq.py``, unchanged) for **coordinated** runs of commit-pinned saq trees.

Runs only inside the isolated runner (``smoke_inside.py`` mode ``saq_coordinated``). Never imported on the host.

What it keeps from the pilot adapter (same seam, same reasons):
* ``PostgresQueue(pool=...)`` gets a one-slot ``psycopg_pool.AsyncConnectionPool`` whose only connection is the
  actor's stepped connection. A second connection is refused, so no session can bypass the gate.
* Workers poll (``poll_interval > 0``): saq's ``ListenMultiplexer`` (LISTEN on a pooled connection) never starts.
* The trusted task ``ds_task(ctx, *, n)`` records ``(tables, n)`` and returns ``n * 2``.

What differs:
* **No declared deviation.** ``write_worker_info`` is saq's own: the corroborator now compares server-normalized
  jsonb text (``jsonb_text.py``), so the pilot's ``Jsonb`` override isn't used.
* **Pins.** The tree is a commit-pinned ``git archive`` (not the 0.26.5 sdist). The host derives the sha256 of the
  exercised files from the hash-pinned archive and declares them in ``DS_SAQ_PINNED`` (with ``DS_SAQ_REV``); this
  adapter refuses to run unless the installed sources are exactly those.
* **Table sets.** A queue uses saq's default tables (``"default"``) or the documented constructor overrides
  ``jobs_table``/``stats_table``/``versions_table`` (``"alt"``: ``saq_alt_*``), in the same schema.
* Database errors raised by saq's public calls are **results** (class and message), not actor exceptions, so a run
  in which the application fails is still a complete, corroborated run. Harness refusals still propagate.

Whitelisted actor functions (``FUNCTIONS``): ``enqueue``, ``burst_work``, ``run_worker``.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

import psycopg

from ..stepping import Refused
from ..stepping_async import AsyncSteppedConnection

EXERCISED = ("saq/__init__.py", "saq/job.py", "saq/utils.py", "saq/worker.py", "saq/queue/__init__.py",
             "saq/queue/base.py", "saq/queue/postgres.py", "saq/queue/postgres_migrations.py", "saq/multiplexer.py")
TABLES = {"default": {"name": "ds_q", "jobs_table": "saq_jobs", "stats_table": "saq_stats",
                      "versions_table": "saq_versions"},
          "alt": {"name": "ds_q_alt", "jobs_table": "saq_alt_jobs", "stats_table": "saq_alt_stats",
                  "versions_table": "saq_alt_versions"}}
HANDLED: list[list[Any]] = []  # [tables, n] per handler execution, in this process
DONE: list[str] = []  # actor names whose function returned


def saq_root() -> Path:
    import saq
    return Path(inspect.getsourcefile(saq) or "").resolve().parent.parent


def source_hashes(root: Path) -> dict[str, str]:
    return {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in EXERCISED}


def verify_pinned() -> dict[str, Any]:
    want = json.loads(os.environ.get("DS_SAQ_PINNED") or "{}")
    got = source_hashes(saq_root())
    if not want or got != want:
        short = sorted((k, v[:12]) for k, v in got.items())
        raise Refused(f"saq's sources aren't the host-declared pinned tree: {short}")
    return {"version": installed_version(), **got}


def installed_version() -> str:
    rev = os.environ.get("DS_SAQ_REV", "")
    if not rev:
        raise Refused("no declared saq revision")
    return rev


def stepped_pool(conn: AsyncSteppedConnection) -> Any:
    from psycopg_pool import AsyncConnectionPool
    if not isinstance(conn, AsyncSteppedConnection):
        raise Refused("saq may only be given the actor's stepped connection")
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


def _me() -> Any:
    from ..stepping_async import _ACTING_FOR
    me = _ACTING_FOR.get()
    if me is None or not hasattr(me.run, "signal"):
        raise Refused("saq_coord_adapter functions run only under a coordinated run")
    return me


def _db_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"


async def ds_task(ctx: Any, *, n: int, tables: str) -> int:
    HANDLED.append([tables, n])
    return n * 2


class _Queue:
    """A saq PostgresQueue on the stepped pool: ``async with _Queue(conn, tables) as (q, errors)``."""

    def __init__(self, conn: AsyncSteppedConnection, tables: str) -> None:
        if tables not in TABLES:
            raise Refused(f"unknown table set {tables!r}")
        self.conn, self.tables = conn, tables

    async def __aenter__(self) -> Any:
        from saq.queue.postgres import PostgresQueue
        verify_pinned()
        self.pool = stepped_pool(self.conn)
        await self.pool.open()
        self.q = PostgresQueue(pool=self.pool, **TABLES[self.tables])
        await self.q.connect()  # saq's own init_db: its migrations, by this revision
        return self.q

    async def __aexit__(self, *exc: Any) -> None:
        try:
            await self.q.disconnect()
        finally:
            await self.pool.close()


async def enqueue(conn: AsyncSteppedConnection, *, tables: str, ns: list[int]) -> dict[str, Any]:
    """Start a queue on ``tables`` (its ``connect``), enqueue ``ds_task(n)`` for each ``n`` (key ``<tables>-<n>``)."""
    me = _me()
    out: dict[str, Any] = {"tables": tables, "ns": list(ns), "connected": False, "enqueued": [], "db_error": None}
    try:
        async with _Queue(conn, tables) as q:
            out["connected"] = True
            for n in ns:
                job = await q.enqueue("ds_task", key=f"{tables}-{n}", n=n, tables=tables)
                out["enqueued"].append(job.key if job else None)
    except psycopg.DatabaseError as exc:  # the application's failure is a result
        out["db_error"] = _db_error(exc)
    DONE.append(me.name)
    return out


async def burst_work(conn: AsyncSteppedConnection, *, tables: str, concurrency: int, max_burst_jobs: int | None,
                     dequeue_timeout_s: float, poll_interval_s: float, schedule_s: float,
                     timeout_s: float = 90) -> dict[str, Any]:
    """saq's own burst worker (``burst=True``) on ``tables``: it stops by itself once a dequeue times out, or after
    ``max_burst_jobs`` jobs. ``timeout_s`` is only a guard; a guarded stop is reported (``stopped_by``)."""
    from saq import Worker
    me = _me()
    out: dict[str, Any] = {"tables": tables, "max_burst_jobs": max_burst_jobs, "stopped_by": None, "db_error": None,
                           "handled": []}
    try:
        async with _Queue(conn, tables) as q:
            w = Worker(q, functions=[ds_task], concurrency=concurrency, burst=True, max_burst_jobs=max_burst_jobs,
                       dequeue_timeout=dequeue_timeout_s, poll_interval=poll_interval_s,
                       timers={"schedule": schedule_s, "worker_info": 3600, "sweep": 3600, "abort": 3600})
            try:
                await asyncio.wait_for(w.start(), timeout_s)
                out["stopped_by"] = "burst"
            except TimeoutError:
                out["stopped_by"] = "guard_timeout"
            out["worker_id"] = str(w.id)
    except psycopg.DatabaseError as exc:
        out["db_error"] = _db_error(exc)
    out["handled"] = [h for h in HANDLED if h[0] == tables]
    DONE.append(me.name)
    return out


async def run_worker(conn: AsyncSteppedConnection, *, tables: str, ns: list[int], poll_interval_s: float,
                     timeout_s: float = 90) -> dict[str, Any]:
    """Start a queue on ``tables``, enqueue ``ds_task(n)`` for each ``n``, and run saq's worker (not burst) until
    the coordinator's ``stop`` signal; then stop it (saq's own ``Worker.stop`` via its stop event)."""
    from saq import Worker
    me = _me()
    stop = me.run.signal("stop")
    out: dict[str, Any] = {"tables": tables, "ns": list(ns), "connected": False, "enqueued": [], "stopped_by": None,
                           "db_error": None}
    try:
        async with _Queue(conn, tables) as q:
            out["connected"] = True
            for n in ns:
                job = await q.enqueue("ds_task", key=f"{tables}-{n}", n=n, tables=tables)
                out["enqueued"].append(job.key if job else None)
            w = Worker(q, functions=[ds_task], concurrency=1, dequeue_timeout=1.0, poll_interval=poll_interval_s,
                       timers={"schedule": 3600, "worker_info": 3600, "sweep": 3600, "abort": 3600})
            task = asyncio.create_task(w.start())
            try:
                await asyncio.wait_for(stop.wait(), timeout=timeout_s)
                out["stopped_by"] = "signal"
            except TimeoutError:
                out["stopped_by"] = "guard_timeout"
            w.event.set()
            await asyncio.wait_for(task, timeout=30)
    except psycopg.DatabaseError as exc:
        out["db_error"] = _db_error(exc)
    out["handled"] = [h for h in HANDLED if h[0] == tables]
    DONE.append(me.name)
    return out


FUNCTIONS = {"enqueue": enqueue, "burst_work": burst_work, "run_worker": run_worker}


async def coordinated_process() -> dict[str, Any]:
    """One process of a coordinated saq run: this image's pinned tree, its actors (whitelisted functions above),
    their own stepped connections and monitor; every statement grant comes from the coordinator."""
    from ..coordinated import RemoteAsyncRun
    from ..stepping import Actor, Handler, Limits
    from ..stepping_async import async_actor_connection_factory

    class CaptureHandler(Handler):
        def allows(self, text: str) -> bool:
            return True

    info = {"host": os.environ["DS_PG_HOST"], "port": int(os.environ["DS_PG_PORT"]),
            "user": os.environ["DS_PG_USER"], "password": os.environ["DS_PG_PASSWORD"],
            "dbname": os.environ["DS_PG_DB"]}

    async def monitor() -> psycopg.AsyncConnection:
        c = await psycopg.AsyncConnection.connect(**info, autocommit=True, prepare_threshold=None,
                                                  application_name="ds_mvp_admin")
        cur = await c.execute("SELECT current_setting('cluster_name'), current_database()")
        if await cur.fetchone() != ("ds_mvp_disposable", "ds_mvp"):
            await c.close()
            raise SystemExit("not the disposable database")
        return c

    pinned = verify_pinned()
    plan = json.loads(os.environ["DS_CO_PLAN"])
    version = installed_version()
    lim = Limits(statement_ms=20000, run_s=float(plan.get("run_s", 150)), python_step_s=90)
    connect = async_actor_connection_factory(info, lim, search_path=os.environ["DS_SCHEMA"], autocommit=True)
    actors = [Actor(a["actor"], CaptureHandler(f"saq-{version}", a["fn"], FUNCTIONS[a["fn"]], frozenset(), {},
                                               serialize_tasks=True), dict(a.get("args", {})))
              for a in plan["actors"]]
    host, port = plan["coordinator"]
    r = RemoteAsyncRun(actors, connect, monitor, process=plan["process"], version=version, endpoint=(host, port),
                       token=os.environ["DS_CO_NONCE"], markers=plan.get("markers", {}),
                       # "succeeded" is the coordinator's stop key: here, the actors whose function has returned
                       report=lambda: {"succeeded": list(DONE), "handled": [f"{t}-{n}" for t, n in HANDLED]},
                       limits=lim, run_id=f"saq_{plan['process']}")
    trace = await r.execute()
    return {"pinned_sources": pinned, "process": plan["process"], "version": version, "monitor_pid": r.monitor_pid,
            "trace": trace.to_dict(), "raw_results": trace.raw_results, "received": r.received}
