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
    # historical case chancy-2895c4a (docs/experimental_mvp/historical/chancy_2895c4a): the fix commit and its parent,
    # built from hash-pinned `git archive` trees (research_tools/isolated/run_isolated.py CHANCY_GIT)
    "git-80f570f": {"chancy/app.py": "10418da33d183d8ef0a8bc1c24a2b3078c44edb770b48addeedea72ebee8bdcf",
                    "chancy/worker.py": "fc851ac97b65adb724ae0767c01cf3c4f68d4e3b3515431a877faf43eb67be31",
                    "chancy/migrate.py": "2f19e93779a146ef6b162edf76e2fe174a0405a028d9f38339793ee0213b240e",
                    "chancy/executors/asyncex.py": "356db539d0dabbcbdf20cdd5d5b65dfd792e4fa9e0615d8d0e9cf69c5050c742"},
    "git-2895c4a": {"chancy/app.py": "160de307b11bbdb097eeab2a692f8ade3543c356efe92eb88b35c56f92573265",
                    "chancy/worker.py": "b045bccdfc1ddfe1ec2d7df4d97d6169f454db3cc14eb441cfc2048252c68cdd",
                    "chancy/migrate.py": "2f19e93779a146ef6b162edf76e2fe174a0405a028d9f38339793ee0213b240e",
                    "chancy/executors/asyncex.py": "f6495e78fb80973f1227a9060c9698ef61f984f15180d06f3e4f917aa4eae855"},
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


SUCCEEDED: list[int] = []  # n of every handler execution that returned without error, in this process


@job()
async def ds_job(*, n: int) -> int:  # trusted, deterministic handler; module level so the worker can import it
    from ..stepping_async import _ACTING_FOR
    HANDLED.append(n)
    actor = _ACTING_FOR.get()
    HANDLED_BY.append((n, actor.name if actor is not None else "?"))
    SUCCEEDED.append(n)
    return n * 2


@job()
async def ds_flaky(*, n: int, fail_in: str) -> int:
    """Trusted handler for the retry scenario: fails when run by the release named in ``fail_in``."""
    HANDLED.append(n)
    if installed_version().split("+")[0] == fail_in:
        raise RuntimeError(f"ds_flaky: planned failure under {fail_in}")
    SUCCEEDED.append(n)
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
    """Stop a worker with its own ``stop()``, after queued job updates have been flushed.

    Both releases' ``stop()`` cancel the worker's maintenance tasks. Cancelled while a statement is on the wire, a
    task used to leave its session in a state the trace couldn't vouch for. The server log then (correctly)
    refuted the run: that happened in 0.25.1's heartbeat in ``demo_full_3``. The async gate now defers such a
    cancellation to the statement boundary (``AsyncRun.defer_cancellation``), so nothing is held here. An earlier
    hold (``AsyncRun.quiesced``) blocked 0.26.0's own graceful drain, whose queue loops need their next
    statement to finish (``simultaneous_scenarios_1`` and the ``new_to_old`` diagnosis).
    0.26.0's ``stop`` takes ``timeout``; 0.25.1's takes no arguments."""
    import asyncio
    import time

    def idle() -> bool:
        stats = worker.chancy.pool.get_stats()
        return worker.outgoing.empty() and not getattr(worker, "_pending_updates", None) and \
            stats.get("pool_available", 0) == stats.get("pool_size", 1) and not stats.get("requests_waiting", 0)

    until = time.monotonic() + 10
    while not idle() and time.monotonic() < until:
        await asyncio.sleep(0.02)
    if "timeout" in inspect.signature(worker.stop).parameters:
        return await worker.stop(timeout=max(10, worker.shutdown_timeout))
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


# ------------------------------------------------------- simultaneous mixed-version execution (NONBLIND), ds-coord/1
# Three queues, through the public Queue/Worker tag API: q_old is run only by workers tagged ds=old (0.25.1), q_new
# only by ds=new (0.26.0), q_shared by both. Tags are regular expressions matched against worker tags.
CO_QUEUES = {"q_old": {"^ds=old$"}, "q_new": {"^ds=new$"}, "q_shared": {".*"}}


def worker_tag() -> str:
    return "ds=old" if installed_version().startswith("0.25.1") else "ds=new"


async def co_setup(conn: AsyncSteppedConnection) -> dict[str, Any]:
    """Old code's setup: migrate to its own latest schema, declare the three queues."""
    async with stepped_chancy(conn) as chancy:
        await chancy.migrate()
        for name, tags in CO_QUEUES.items():
            await chancy.declare(Queue(name, concurrency=1, executor=Chancy.Executor.Async, polling_interval=1,
                                       tags=set(tags)))
    return {"version": installed_version(), "queues": sorted(CO_QUEUES)}


async def co_work(conn: AsyncSteppedConnection, *, timeout_s: float = 60) -> dict[str, Any]:
    """A worker of this container's release, tagged by its version. Runs until the coordinator's ``stop`` signal
    (or ``timeout_s``), then stops at a safe point (``stop_worker``: inside ``quiesced``)."""
    import asyncio

    from ..stepping_async import _ACTING_FOR
    me = _ACTING_FOR.get()
    if me is None or not hasattr(me.run, "signal"):
        raise Refused("co_work runs only under a coordinated run")
    stop = me.run.signal("stop")
    async with stepped_chancy(conn) as chancy:
        # under the coordinator every statement is a cross-process round trip, so allow 10 s (below the 15 s
        # idle-in-transaction limit, since other actors wait while a stop drains); a timed-out stop isn't quiescent
        worker = Worker(chancy, register_signal_handlers=False, shutdown_timeout=10, queue_change_poll_interval=1,
                        heartbeat_poll_interval=1, tags={worker_tag()})
        await worker.start()
        try:
            await asyncio.wait_for(stop.wait(), timeout=timeout_s)
            why = "signal"
        except TimeoutError:
            why = "timeout"
        stopped = await stop_worker(worker)
    return {"version": installed_version(), "worker_id": str(worker.worker_id), "tag": worker_tag(),
            "handled": list(HANDLED), "succeeded": list(SUCCEEDED), "worker_stopped": stopped, "stopped_by": why}


async def co_push(conn: AsyncSteppedConnection, *, ns: list[int], queue: str, max_attempts: int = 1,
                  flaky_in: str | None = None, cancel: list[int] | None = None) -> dict[str, Any]:
    """Push jobs (``ds_job``, or ``ds_flaky`` failing under ``flaky_in``) with this release's public ``push``;
    then cancel the listed ones with its public ``cancel_job``. When the cancel runs relative to the worker's claim
    and completion is the coordinator's schedule, not this function's."""
    from ..stepping_async import _ACTING_FOR
    if queue not in CO_QUEUES:
        raise Refused(f"unknown queue {queue!r}")
    version = installed_version()
    pushed, refs = [], {}
    async with stepped_chancy(conn) as chancy:
        for n in ns:
            j = ds_flaky.job.with_kwargs(n=n, fail_in=flaky_in) if flaky_in else ds_job.job.with_kwargs(n=n)
            ref = await chancy.push(j.with_queue(queue).with_max_attempts(max_attempts))
            refs[n] = ref
            pushed.append({"n": n, "id": str(ref.identifier), "queue": queue, "version": version})
        cancelled = []
        me = _ACTING_FOR.get()
        for n in cancel or []:
            await chancy.cancel_job(refs[n])
            ups = [op for op in me.run.records if op.actor == me.name and "SET state = 'failed'" in op.sql]
            cancelled.append({"n": n, "rowcount": ups[-1].rowcount if ups else None})
    return {"version": version, "pushed": pushed, "cancelled": cancelled}
