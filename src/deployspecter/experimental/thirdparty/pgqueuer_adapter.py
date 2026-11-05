"""EXPERIMENTAL version-pinned adapter: PGQueuer 1.6.0 over a stepped psycopg ``AsyncConnection``.

Runs only inside the isolated runner (it imports third-party code). Not imported on the host.

Why an adapter and not ``PgQueuer.from_psycopg_connection(conn)`` alone: in 1.6.0 that public factory wraps the
connection in ``PsycopgDriver``, whose ``fetch`` and ``execute`` build ``psycopg.AsyncRawCursor(conn)`` directly. A
directly built cursor never goes through ``cursor_factory``, so every queue query would bypass the gate (only the
``LISTEN``, sent through ``conn.execute``, would be seen). PGQueuer's documented extension point is its ``Driver``
port: ``Queries(driver)`` takes any driver, and ``QueueManager(Queries(driver))`` takes those queries.
``SteppedPsycopgDriver`` is the public ``PsycopgDriver`` with exactly those two methods changed to build
``AsyncSteppedRawCursor`` instead; the SQL, its ``$n`` placeholders, the ``LISTEN``/``UNLISTEN``, ``pg_notify`` and the
``notifies()`` watcher are PGQueuer's own.
Nothing in PGQueuer is patched.

Fail-closed checks: PGQueuer must be exactly 1.6.0 and the three source files this adapter relies on must have
their pinned hashes; the connection must be an ``AsyncSteppedConnection`` with autocommit on (PGQueuer's own
requirement, kept rather than overridden). Anything that still bypasses the gate (another directly built cursor, an
extra connection) is caught by the server-log corroboration on the host.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any

from ..stepping import Refused
from ..stepping_async import AsyncSteppedConnection, AsyncSteppedRawCursor

PINNED_VERSION = "1.6.0"
PINNED_SOURCES = {  # sha256 of the files in the verified sdist (pgqueuer-1.6.0.tar.gz, ed130640...)
    "pgqueuer/adapters/drivers/psycopg.py": "df915ea546b27e21e2b725adb48db142ef77536eb83358095032b66a3c14c2d2",
    "pgqueuer/core/qm.py": "dde600273b7aed2e77e5caee78aa935efb6f6c30a1efc2860f2ee80cacd6a4ab",
    "pgqueuer/adapters/persistence/queries.py": "ea9b64fb2182da27eb1d81c8501e24ccfd4e7ab70c31d918aecd1a5740ddfea4",
}


def verify_pinned() -> dict[str, str]:
    import pgqueuer
    if pgqueuer.__version__ != PINNED_VERSION:
        raise Refused(f"pgqueuer {pgqueuer.__version__} isn't the pinned {PINNED_VERSION}")
    root = Path(inspect.getsourcefile(pgqueuer) or "").resolve().parent.parent
    seen = {}
    for rel, want in PINNED_SOURCES.items():
        got = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        if got != want:
            raise Refused(f"{rel} isn't the pinned source (sha256 {got[:12]})")
        seen[rel] = got
    return seen


def stepped_driver(connection: Any) -> Any:
    """A PGQueuer ``Driver`` whose every query is gated. Refuses anything but a stepped autocommit connection."""
    verify_pinned()
    from pgqueuer.adapters.drivers.psycopg import PsycopgDriver
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

    return SteppedPsycopgDriver(connection)  # PsycopgDriver.__init__ itself refuses a non-autocommit connection


# ------------------------------------------------------------------ the trusted lifecycle (one handler per phase)

ENTRYPOINT = "ds_job"
RESULTS_DDL = "CREATE TABLE IF NOT EXISTS ds_pgq_results (job_id bigint PRIMARY KEY, payload bytea NOT NULL)"


async def install(conn: Any) -> dict[str, Any]:
    from pgqueuer.queries import Queries
    driver = stepped_driver(conn)
    await Queries(driver).install()
    await driver.execute(RESULTS_DDL)  # the trusted handler's own table
    return {"installed": True}


async def enqueue(conn: Any, *, payloads: list[str]) -> dict[str, Any]:
    from pgqueuer.queries import Queries
    queries = Queries(stepped_driver(conn))
    ids = []
    for p in payloads:
        ids += await queries.enqueue(ENTRYPOINT, p.encode())
    return {"job_ids": [int(i) for i in ids]}


async def work(conn: Any) -> dict[str, Any]:
    """A worker in drain mode: it claims, runs the trusted handler and finishes every job, then shuts down."""
    from datetime import timedelta

    from pgqueuer.domain.types import QueueExecutionMode
    from pgqueuer.qm import QueueManager
    from pgqueuer.queries import Queries

    driver = stepped_driver(conn)
    qm = QueueManager(Queries(driver))  # as PgQueuer builds it: the manager takes the repository over the driver
    handled: list[int] = []

    @qm.entrypoint(ENTRYPOINT)
    async def handle(job: Any) -> None:  # trusted handler: record the job exactly once, through the same driver
        await driver.execute("INSERT INTO ds_pgq_results (job_id, payload) VALUES ($1, $2)", job.id, job.payload)
        handled.append(int(job.id))

    await qm.run(dequeue_timeout=timedelta(seconds=1), batch_size=1, mode=QueueExecutionMode.drain)
    return {"handled": handled}
