"""EXPERIMENTAL, NONBLIND (ds-nbc/1): a declared variant of the PGQueuer Driver-port adapter for commit-pinned
historical trees. Runs only inside the isolated runner (it imports third-party code). Not imported on the host.

It is ``pgqueuer_adapter`` (pinned to the 1.6.0 sdist) re-declared for ``git archive`` trees of later commits:

* **Pinning.** The host integration declares the tree it built into the image (``DS_PGQ_PINNED``: a label, the
  archive sha256 and a digest over every file of ``pgqueuer/``). This module recomputes the digest over the installed
  package and refuses on any difference. The psycopg driver file must also be one of ``REVIEWED_DRIVERS``, whose
  ``fetch``/``execute``/``watch_notifies`` were read before use.
* **The seam** is the same as 1.6.0's: PGQueuer's documented ``Driver`` port. ``SteppedPsycopgDriver`` is the public
  ``PsycopgDriver`` with ``fetch`` and ``execute`` building ``AsyncSteppedRawCursor`` (so every query is gated); the
  SQL, ``$n`` placeholders, ``LISTEN``/``UNLISTEN``, ``pg_notify`` and the notification callbacks are PGQueuer's own.
* **One declared deviation:** ``watch_notifies`` is PGQueuer's loop with a 0.05 s ``notifies()`` timeout instead of
  1.0 s. psycopg's ``notifies()`` holds the connection lock while it waits, so under the lockstep coordinator every
  statement of a listening worker otherwise waits up to 1 s (measured: ~1.0 s per statement after ``LISTEN`` in
  ``results/third_party_pgqueuer_lifecycle_2``), which no run could finish inside the coordinator's fixed stop window.
  Channels, payloads, callbacks and the one-watcher-per-connection rule are unchanged.

Actors (whitelisted functions, JSON arguments only): ``co_install`` (PGQueuer's ``Queries.install`` plus the trusted
handler's table), ``co_work`` (a ``QueueManager`` in continuous mode until the coordinator's ``stop``) and
``co_produce`` (public ``Queries.enqueue``, then ``Queries.mark_job_as_cancelled``). Nothing in PGQueuer is patched.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

from ..stepping import Refused
from ..stepping_async import AsyncSteppedConnection, AsyncSteppedRawCursor

PINNED_ENV = "DS_PGQ_PINNED"
NOTIFY_POLL_S = 0.05
# sha256 of pgqueuer/adapters/drivers/psycopg.py -> what was reviewed (the overridden methods' shape)
REVIEWED_DRIVERS = {
    "006aed381d898f70640f58ff944356ae4de10762b6f466348da05410bad6121a":
        "f258cbc/8dfadf4: fetch/execute build AsyncRawCursor(self._connection); add_listener LISTENs through "
        "self._connection.execute; watch_notifies loops notifies(timeout=1.0, stop_after=1) until shutdown",
}
ENTRYPOINT = "ds_job"
RUNS_DDL = ("CREATE TABLE IF NOT EXISTS ds_pgq_runs (seq bigserial PRIMARY KEY, job_id bigint NOT NULL, "
            "n integer NOT NULL, worker text NOT NULL, exec text NOT NULL, phase text NOT NULL)")
RUNS_INSERT = "INSERT INTO ds_pgq_runs (job_id, n, worker, exec, phase) VALUES ($1, $2, $3, $4, $5)"

# in this process: n of every handler that started, and that returned; n whose cancel call returned
STARTED: list[int] = []
SUCCEEDED: list[int] = []
CANCELLED: list[int] = []


def tree_digest(root: Path) -> str:
    """sha256 over (relative path, NUL, content) of every regular file under ``root``/pgqueuer, sorted by the path
    string (the host computes the same over the archive's members)."""
    h = hashlib.sha256()
    files = {str(p.relative_to(root)): p for p in (root / "pgqueuer").rglob("*") if p.is_file()}
    for rel in sorted(files):
        h.update(rel.encode() + b"\0" + files[rel].read_bytes())
    return h.hexdigest()


def declaration() -> dict[str, Any]:
    raw = os.environ.get(PINNED_ENV)
    if not raw:
        raise Refused(f"no {PINNED_ENV} declaration: this adapter runs only for a declared historical tree")
    d = json.loads(raw)
    if set(d) != {"label", "archive_sha256", "tree_digest"}:
        raise Refused(f"{PINNED_ENV} isn't a declaration")
    return d


def verify_pinned() -> dict[str, str]:
    import pgqueuer
    d = declaration()
    root = Path(inspect.getsourcefile(pgqueuer) or "").resolve().parent.parent
    got = tree_digest(root)
    if got != d["tree_digest"]:
        raise Refused(f"the installed pgqueuer tree ({got[:12]}) isn't the declared one ({d['tree_digest'][:12]})")
    drv = hashlib.sha256((root / "pgqueuer/adapters/drivers/psycopg.py").read_bytes()).hexdigest()
    if drv not in REVIEWED_DRIVERS:
        raise Refused(f"pgqueuer/adapters/drivers/psycopg.py ({drv[:12]}) isn't a reviewed driver")
    return {"label": d["label"], "archive_sha256": d["archive_sha256"], "tree_digest": got, "driver_sha256": drv}


def version_label() -> str:
    return declaration()["label"]


def stepped_driver(connection: Any) -> Any:
    """A PGQueuer ``Driver`` whose every query is gated. Refuses anything but a stepped autocommit connection."""
    verify_pinned()
    from pgqueuer.adapters.drivers.psycopg import PsycopgDriver
    from pgqueuer.core import logconfig
    from psycopg.rows import dict_row

    if not isinstance(connection, AsyncSteppedConnection):
        raise Refused("PGQueuer may only be given the actor's stepped connection")

    class SteppedPsycopgDriver(PsycopgDriver):
        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            cursor = AsyncSteppedRawCursor(self._connection, row_factory=dict_row)
            await cursor.execute(query, args or None)
            return await cursor.fetchall()

        async def execute(self, query: str, *args: object) -> str:
            cursor = AsyncSteppedRawCursor(self._connection)
            await cursor.execute(query, args or None)
            return cursor.statusmessage or ""

        async def watch_notifies(self) -> None:  # PGQueuer's loop; only the timeout differs (module docstring)
            while not self.shutdown.is_set():
                async for note in self._connection.notifies(timeout=NOTIFY_POLL_S, stop_after=1):
                    for callback in list(self._listeners.get(note.channel, [])):
                        try:
                            callback(note.payload)
                        except Exception:
                            logconfig.logger.exception("Unhandled error in NOTIFY callback for channel %s",
                                                       note.channel)

    return SteppedPsycopgDriver(connection)  # PsycopgDriver.__init__ itself refuses a non-autocommit connection


def _me() -> Any:
    from ..stepping_async import _ACTING_FOR
    me = _ACTING_FOR.get()
    if me is None or not hasattr(me.run, "signal"):
        raise Refused("this adapter's actors run only under a coordinated run")
    return me


async def co_install(conn: Any) -> dict[str, Any]:
    from pgqueuer.queries import Queries
    driver = stepped_driver(conn)
    await Queries(driver).install()
    await driver.execute(RUNS_DDL)  # the trusted handler's own table, last: its marker opens the other actors
    return {"installed": True, "version": version_label()}


async def co_work(conn: Any, *, concurrency_limit: int, steps: int, timeout_s: float = 60) -> dict[str, Any]:
    """A worker: one ``QueueManager`` over this actor's stepped connection, entrypoint ``ds_job`` registered with the
    workload's ``concurrency_limit``. The trusted handler records ``start``, ``steps`` × ``step`` and ``end`` rows
    (each its own statement, so the explorer can interleave other actors between them) and ignores cancellation.
    Runs in continuous mode until the coordinator's ``stop`` (or ``timeout_s``), then sets PGQueuer's own
    ``shutdown`` and waits for ``run()`` to return (buffers flushed, tasks gathered)."""
    import asyncio
    from datetime import timedelta

    from pgqueuer.domain.types import QueueExecutionMode
    from pgqueuer.qm import QueueManager
    from pgqueuer.queries import Queries

    me = _me()
    stop = me.run.signal("stop")
    driver = stepped_driver(conn)
    qm = QueueManager(Queries(driver))
    handled: list[int] = []
    count = 0

    @qm.entrypoint(ENTRYPOINT, concurrency_limit=concurrency_limit, accepts_context=False)
    async def handle(job: Any) -> None:  # trusted handler; ignores cancellation, as the docs allow a handler to
        nonlocal count
        count += 1
        n = int(bytes(job.payload).decode())
        token = f"{me.name}:{int(job.id)}:{count}"
        STARTED.append(n)
        handled.append(n)
        await driver.execute(RUNS_INSERT, int(job.id), n, me.name, token, "start")
        for _ in range(steps):
            await driver.execute(RUNS_INSERT, int(job.id), n, me.name, token, "step")
        await driver.execute(RUNS_INSERT, int(job.id), n, me.name, token, "end")
        SUCCEEDED.append(n)

    task = asyncio.create_task(qm.run(dequeue_timeout=timedelta(seconds=0.5), batch_size=1,
                                      mode=QueueExecutionMode.continuous, log_aggregation_interval=timedelta(0)))
    try:
        await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=timeout_s)
        why = "signal"
    except TimeoutError:
        why = "timeout"
    if task.done():  # run() ended by itself: its exception is this actor's result
        task.result()
        return {"worker": me.name, "handled": handled, "worker_stopped": False, "stopped_by": "run_returned",
                "concurrency_limit": concurrency_limit, "queue_manager_id": str(qm.queue_manager_id)}
    qm.shutdown.set()
    await task
    return {"worker": me.name, "version": version_label(), "handled": handled, "succeeded": list(SUCCEEDED),
            "worker_stopped": True, "stopped_by": why, "concurrency_limit": concurrency_limit,
            "queue_manager_id": str(qm.queue_manager_id)}


async def co_produce(conn: Any, *, jobs: list[dict[str, int]], cancel: list[int]) -> dict[str, Any]:
    """Enqueue each job (payload ``str(n)``, its priority) with the public ``Queries.enqueue``, in order; then cancel
    the listed ones with one public ``Queries.mark_job_as_cancelled`` call. When the cancel runs relative to the
    workers is the coordinator's schedule, not this function's."""
    from pgqueuer.queries import Queries
    me = _me()
    queries = Queries(stepped_driver(conn))
    ids: dict[int, int] = {}
    for j in jobs:
        (jid,) = await queries.enqueue(ENTRYPOINT, str(j["n"]).encode(), j["priority"])
        ids[j["n"]] = int(jid)
    started_before = sorted(set(STARTED))
    if cancel:
        await queries.mark_job_as_cancelled([ids[n] for n in cancel])  # type: ignore[misc]
        CANCELLED.extend(cancel)
    return {"actor": me.name, "version": version_label(), "job_ids": {str(n): i for n, i in ids.items()},
            "cancelled": list(cancel), "started_before_cancel": started_before}


def report() -> dict[str, Any]:
    """The coordinator's stop key ``succeeded``: jobs whose handler returned, and cancelled jobs that never started
    (a cancelled queued job is removed and never runs)."""
    unstarted = [n for n in CANCELLED if n not in STARTED]
    return {"handled": sorted(set(STARTED)), "succeeded": sorted(set(SUCCEEDED) | set(unstarted))}
