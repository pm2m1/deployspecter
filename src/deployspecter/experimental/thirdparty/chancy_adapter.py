"""EXPERIMENTAL version-pinned adapter: Chancy 0.26.0's job runtime over one stepped psycopg ``AsyncConnection``.

Runs only inside the isolated runner (it imports Chancy at module level). Never imported on the host.

Where Chancy 0.26.0 gets connections (pinned sources below):

* ``Chancy.pool``: a ``cached_property`` that builds ``psycopg_pool.AsyncConnectionPool(self.dsn, ...)``. There is no
  connection-class or factory option, so ``SteppedChancy`` overrides that one property (subclassing, not patching):
  it returns a one-connection pool whose ``connection_class`` hands over the actor's ``AsyncSteppedConnection``
  exactly once. A second connect (a reconnect, a second slot) raises ``psycopg.OperationalError``: the pool can't
  open an uninstrumented session.
* ``Worker._maintain_notifications``: opens its own ``AsyncConnection.connect(self.chancy.dsn)`` for LISTEN. That is
  an unavoidable extra, uninstrumented connection, so it is turned off with Chancy's public
  ``notifications=False``; the worker then polls (``queue_change_poll_interval``, ``polling_interval``).
* ``Chancy.sync_pool``: a separate synchronous pool, used only by the synchronous APIs (``sync_push`` and friends). The
  DSN given to Chancy points nowhere, so any use of it fails loudly instead of connecting.
* Default plugins are off (``no_default_plugins=True``) and the queue uses the in-process ``AsyncExecutor``: the
  process executor would run jobs in child processes outside the scheduler.

Every statement goes through the pool's single connection, so through the gate; Chancy's worker tasks (queues,
updates, heartbeat, the job) take turns on it (``Handler.serialize_tasks``), as the one-connection pool makes them.
"""

from __future__ import annotations

import hashlib
import inspect
from functools import cached_property
from pathlib import Path
from typing import Any

import psycopg
from chancy import Chancy, Queue, Worker, job

from ..stepping import Refused
from ..stepping_async import AsyncSteppedConnection

PINNED_BY_VERSION = {  # sha256 of the files in each verified sdist
    "0.26.0": {"chancy/app.py": "403e471247c81e0b37560da70e64570b92aa9586ff31826c16316b61deb2cd14",
               "chancy/worker.py": "0f541cbfd5ebce9abe75fd5f6365987333d847aaca352b4e7d8445c56575072a",
               "chancy/migrate.py": "2f19e93779a146ef6b162edf76e2fe174a0405a028d9f38339793ee0213b240e",
               "chancy/executors/asyncex.py": "f6495e78fb80973f1227a9060c9698ef61f984f15180d06f3e4f917aa4eae855"},
    "0.25.1": {"chancy/app.py": "e18bdf44f41b8223a0503df99bfd05afd53c4761b4fffd087790a0ced3d11688",
               "chancy/worker.py": "2742f88da03bcaca2158691e09554dbbf7fa906da4425072f34023ad1b298af3",
               "chancy/migrate.py": "b25d63e03dd13b17c41283415c97533baa11bf8243b17cf5a0ba7a50a8be715b",
               "chancy/executors/asyncex.py": "b3617071805ec538b3bc30c19864af8bcca1bf5654f51644f3e0a7db5ddd9525"},
}
PINNED_SOURCES = PINNED_BY_VERSION["0.26.0"]
NOWHERE_DSN = "postgresql://stepped-pool-only.invalid/none"  # only sync_pool would use it, and must fail
QUEUE = "ds_q"
HANDLED: list[int] = []


def installed_version() -> str:
    """Which pinned release is installed, by source hashes; refuses anything else."""
    import chancy
    root = Path(inspect.getsourcefile(chancy) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED_SOURCES}
    for version, pins in PINNED_BY_VERSION.items():
        if got == pins:
            return version
    # A declared mutant (built from a pinned release by the isolated runner): only the named file may differ, and
    # only to the declared hash.
    import os
    mutant = os.environ.get("DS_CHANCY_MUTANT")  # "<id>:<base version>:<file>:<sha256>"
    if mutant:
        ident, base, rel, sha = mutant.split(":")
        pins = dict(PINNED_BY_VERSION[base])
        pins[rel] = sha
        if got == pins:
            return f"{base}+mutant:{ident}"
    raise Refused(f"chancy's sources match no pinned release: {sorted((k, v[:12]) for k, v in got.items())}")


def verify_pinned() -> dict[str, str]:
    version = installed_version()
    return {"version": version, **PINNED_BY_VERSION[version.split("+")[0]]}


def stepped_pool(conn: AsyncSteppedConnection) -> Any:
    from psycopg_pool import AsyncConnectionPool
    if not isinstance(conn, AsyncSteppedConnection):
        raise Refused("Chancy may only be given the actor's stepped connection")
    handed: list[Any] = []

    class ActorBoundConnection(AsyncSteppedConnection):
        @classmethod
        async def connect(cls, *_args: Any, **_kwargs: Any) -> Any:  # what the pool calls to open a slot
            if handed:
                raise psycopg.OperationalError("refused: the pool asked for a second connection; only the actor's "
                                               "stepped connection exists")
            handed.append(conn)
            return conn

    return AsyncConnectionPool("", connection_class=ActorBoundConnection, min_size=1, max_size=1, open=False,
                               timeout=30, reconnect_timeout=10)


def stepped_chancy(conn: AsyncSteppedConnection, *, notifications: bool = False, dsn: str = NOWHERE_DSN) -> Any:
    """``notifications=True`` with a real ``dsn`` is the negative control: the worker then opens its own LISTEN
    connection, which the server-log corroboration must refuse as an unexplained session."""
    verify_pinned()

    class SteppedChancy(Chancy):
        @cached_property
        def pool(self) -> Any:  # the one overridden member: Chancy's pool, over the stepped connection
            return stepped_pool(conn)

    return SteppedChancy(dsn, notifications=notifications, no_default_plugins=True, min_connection_pool_size=1,
                         max_connection_pool_size=1)


HANDLED_BY: list[tuple[int, str]] = []  # (n, the actor whose worker ran it), for the concurrent mode


@job()
async def ds_job(*, n: int) -> int:  # trusted, deterministic handler; module level so the worker can import it
    from ..stepping_async import _ACTING_FOR
    HANDLED.append(n)
    actor = _ACTING_FOR.get()
    HANDLED_BY.append((n, actor.name if actor is not None else "?"))
    return n * 2


async def produce(conn: AsyncSteppedConnection, *, count: int) -> dict[str, Any]:
    """Concurrent mode, phase 1 (its own actor and connection): migrate, declare the queue, push ``count`` jobs."""
    verify_pinned()
    async with stepped_chancy(conn) as chancy:
        await chancy.migrate()
        await chancy.declare(Queue(QUEUE, concurrency=1, executor=Chancy.Executor.Async, polling_interval=1))
        refs = [await chancy.push(ds_job.job.with_kwargs(n=i).with_queue(QUEUE)) for i in range(count)]
    return {"job_ids": [str(r.identifier) for r in refs]}


async def stop_worker(worker: Any) -> bool:
    """0.26.0's ``Worker.stop`` takes ``timeout``; 0.25.1's takes no arguments."""
    if "timeout" in inspect.signature(worker.stop).parameters:
        return await worker.stop(timeout=10)
    return await worker.stop()


# ------------------------------------------------------------------ the mixed-version pilot (NONBLIND), one phase each


async def phase_setup(conn: AsyncSteppedConnection, *, ns: list[int]) -> dict[str, Any]:
    """Old code: migrate to its own latest schema, declare the queue, push jobs."""
    async with stepped_chancy(conn) as chancy:
        await chancy.migrate()
        await chancy.declare(Queue(QUEUE, concurrency=1, executor=Chancy.Executor.Async, polling_interval=1))
        refs = [await chancy.push(ds_job.job.with_kwargs(n=n).with_queue(QUEUE)) for n in ns]
    return {"version": installed_version(), "job_ids": [str(r.identifier) for r in refs]}


async def phase_migrate(conn: AsyncSteppedConnection) -> dict[str, Any]:
    """New code's deploy step: apply its migrations to the live schema."""
    async with stepped_chancy(conn) as chancy:
        await chancy.migrate()
    return {"version": installed_version(), "migrated": True}


async def phase_push(conn: AsyncSteppedConnection, *, ns: list[int]) -> dict[str, Any]:
    async with stepped_chancy(conn) as chancy:
        refs = [await chancy.push(ds_job.job.with_kwargs(n=n).with_queue(QUEUE)) for n in ns]
    return {"version": installed_version(), "job_ids": [str(r.identifier) for r in refs]}


async def phase_work(conn: AsyncSteppedConnection, *, handle_at_least: int, wait_for: list[str] | None = None,
                     timeout_s: float = 60) -> dict[str, Any]:
    """A worker of this version. Stops (gracefully) once it has handled ``handle_at_least`` jobs, or, with
    ``wait_for``, once every listed job has reached a final state."""
    import asyncio
    import time

    from chancy.job import Reference
    async with stepped_chancy(conn) as chancy:
        worker = Worker(chancy, register_signal_handlers=False, shutdown_timeout=5, queue_change_poll_interval=1,
                        heartbeat_poll_interval=1)
        await worker.start()
        until = time.monotonic() + timeout_s
        final: list[str] = []
        while time.monotonic() < until:
            if wait_for is not None:
                jobs = [await chancy.get_job(Reference(i)) for i in wait_for]
                final = [j.state.value if j else "missing" for j in jobs]
                if all(st in ("succeeded", "failed") for st in final):
                    break
            elif len(HANDLED) >= handle_at_least:
                break
            await asyncio.sleep(0.5)
        stopped = await stop_worker(worker)
    return {"version": installed_version(), "handled": list(HANDLED), "worker_stopped": stopped,
            "waited_states": final}


async def work_until(conn: AsyncSteppedConnection, *, count: int, timeout_s: float = 60) -> dict[str, Any]:
    """Concurrent mode, phase 2: one of several workers, each its own actor with its own Chancy instance and its own
    stepped connection. Runs until ``count`` jobs have been handled in this process, by any worker, then stops."""
    import asyncio
    import time
    verify_pinned()
    async with stepped_chancy(conn) as chancy:
        worker = Worker(chancy, register_signal_handlers=False, shutdown_timeout=5, queue_change_poll_interval=1,
                        heartbeat_poll_interval=1)
        await worker.start()
        until = time.monotonic() + timeout_s
        while len(HANDLED) < count and time.monotonic() < until:
            await asyncio.sleep(0.2)
        stopped = await stop_worker(worker)
    from ..stepping_async import _ACTING_FOR
    me = _ACTING_FOR.get()
    return {"handled_here": sorted(n for n, who in HANDLED_BY if me is not None and who == me.name),
            "handled_total": len(HANDLED), "worker_stopped": stopped}


async def lifecycle(conn: AsyncSteppedConnection, *, notifications: bool = False,
                    dsn: str = NOWHERE_DSN) -> dict[str, Any]:
    """Migrate, declare a queue, push one job, run a worker until the job completes, stop the worker, close."""
    pinned = verify_pinned()
    async with stepped_chancy(conn, notifications=notifications, dsn=dsn) as chancy:
        await chancy.migrate()
        await chancy.declare(Queue(QUEUE, concurrency=1, executor=Chancy.Executor.Async, polling_interval=1))
        ref = await chancy.push(ds_job.job.with_kwargs(n=21).with_queue(QUEUE))
        worker = Worker(chancy, register_signal_handlers=False, shutdown_timeout=5, queue_change_poll_interval=1,
                        heartbeat_poll_interval=1)
        await worker.start()
        done = await chancy.wait_for_job(ref, interval=1, timeout=60)
        stopped = await stop_worker(worker)
    return {"job_id": str(ref.identifier), "state": done.state.value if done else None, "handled": list(HANDLED),
            "worker_stopped": stopped, "pinned_sources": pinned}
