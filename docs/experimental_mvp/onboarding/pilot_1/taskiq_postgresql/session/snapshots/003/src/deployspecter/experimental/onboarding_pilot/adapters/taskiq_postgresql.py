"""EXPERIMENTAL onboarding pilot adapter (condition M, written during the measured session): taskiq-postgresql 0.4.0
(with taskiq 0.13.0) under the onb-pilot/1 contract.

* **Seam:** ``PostgresqlBroker(driver="psycopg")``. Its ``PsycopgDriver`` keeps an existing ``pool`` attribute rather
  than creating one, so it is given a one-slot pool whose only connection is the actor's stepped connection; a
  second connection is refused. The library builds its pool with ``cursor_factory=AsyncRawCursor`` (``$1``
  placeholders), so the stepped connection's cursor factory is set to the engine's gated ``AsyncSteppedRawCursor``.
* **Declared deviation:** the broker's ``PsycopgListenDriver`` opens its own connection for ``LISTEN``, and the
  library offers no polling alternative. It is replaced by ``PollingListenDriver``, which yields message ids from
  ``SELECT id FROM taskiq_messages`` on the same stepped connection. The library still claims each message itself
  (``DELETE ... RETURNING``, in ``broker.listen``). The ``NOTIFY`` it sends on enqueue still runs.
* **Declared deviation 2:** ``PsycopgDriver.on_startup`` with ``run_migrations=True`` holds one pool connection in a
  transaction and asks the pool for a second inside it, which the one-connection seam can't serve (the first attempt
  timed out). ``install`` therefore uses ``run_migrations=False`` and calls the driver's own ``create_table()`` and
  ``create_index()`` one after the other: the same library SQL, without the enclosing transaction.
* **Work:** taskiq's ``Receiver.callback`` runs each claimed message, until ``expect`` messages have been processed.
* **The trusted task** ``ds_task(jkey, n)`` calls the harness recorder and returns ``n * 2``.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.stepping_async import AsyncSteppedConnection, AsyncSteppedRawCursor

PINNED = {"taskiq_postgresql/broker.py": "393c33a2bc1bd9f6a3a5802df58d9db292a96d8e43c3cc0cdb202822be854242",
          "taskiq_postgresql/drivers/_psycopg.py": "c5975985060fa204a1b1c3750ad523ca38216e764832d5c6eff409829993322a",
          "taskiq_postgresql/abc/driver.py": "3577d3d72a9188b9eef4f1d6830c9846e6085062b21301df57d68a7dc4a86c9a",
          "taskiq_postgresql/abc/query.py": "e095a0e7ed0c07607b81e43381257d6dcf2152d5000041cdb425bd4a90306e76"}


def verify_pinned() -> dict[str, str]:
    import taskiq_postgresql
    root = Path(inspect.getsourcefile(taskiq_postgresql) or "").resolve().parent.parent
    got = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PINNED}
    if got != PINNED:
        raise RuntimeError(f"taskiq_postgresql sources aren't the pinned 0.4.0 ones: {got}")
    import taskiq
    return {"version": "0.4.0", "taskiq": getattr(taskiq, "__version__", "0.13.0"), **got}


def stepped_pool(conn: AsyncSteppedConnection) -> Any:
    from psycopg_pool import AsyncConnectionPool
    if not isinstance(conn, AsyncSteppedConnection):
        raise RuntimeError("taskiq_postgresql may only be given the actor's stepped connection")
    conn.cursor_factory = AsyncSteppedRawCursor  # what the library's own pool asks for, gated
    handed: list[Any] = []

    class ActorBoundConnection(AsyncSteppedConnection):
        @classmethod
        async def connect(cls, *_a: Any, **_k: Any) -> Any:
            if handed:
                raise psycopg.OperationalError("refused: a second connection; only the stepped connection exists")
            handed.append(conn)
            return conn

    return AsyncConnectionPool("", connection_class=ActorBoundConnection, min_size=1, max_size=1, open=False,
                               timeout=30, reconnect_timeout=10)


def _broker(conn: AsyncSteppedConnection) -> tuple[Any, Any]:
    from taskiq_postgresql import PostgresqlBroker
    from taskiq_postgresql.abc.driver import ListenDriver

    verify_pinned()
    broker = PostgresqlBroker(dsn="postgresql://unused/unused", driver="psycopg", run_migrations=False)
    broker.driver.pool = stepped_pool(conn)

    class PollingListenDriver(ListenDriver):  # the declared deviation: no LISTEN connection
        async def on_startup(self) -> None:
            return None

        async def on_shutdown(self) -> None:
            return None

        async def __aiter__(self) -> AsyncIterator[str]:  # type: ignore[override]
            pk = broker.columns.primary_key.name
            while True:
                rows = await broker.driver.execute(f"SELECT {pk} FROM {broker.table_name} ORDER BY {pk}")
                for r in rows:
                    yield str(r[0])
                await asyncio.sleep(0.3)

    broker.listen_driver = PollingListenDriver(connection_string="unused", channel_name=broker.channel_name)

    async def ds_task(jkey: str, n: int) -> int:
        recorder.handled(jkey)
        return n * 2

    task = broker.task(task_name="ds_task")(ds_task)
    return broker, task


async def install(conn: AsyncSteppedConnection) -> dict[str, Any]:
    broker, _ = _broker(conn)
    await broker.startup()
    try:
        status = await broker.driver.create_table()  # deviation 2: the library's own DDL, sequentially
        await broker.driver.create_index()
    finally:
        await broker.shutdown()
    return {"installed": True, "create_table": status}


async def enqueue(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    broker, task = _broker(conn)
    await broker.startup()
    try:
        sent = [await task.kiq(jkey=j["key"], **j["args"]) for j in jobs]
        return {"enqueued": [s.task_id for s in sent]}
    finally:
        await broker.shutdown()


async def work(conn: AsyncSteppedConnection, *, expect: int, timeout_s: float) -> dict[str, Any]:
    from taskiq.receiver import Receiver
    broker, _ = _broker(conn)
    broker.is_worker_process = True
    await broker.startup()
    receiver = Receiver(broker, run_startup=False, max_async_tasks=1)
    done = 0

    async def drain() -> None:
        nonlocal done
        gen = broker.listen()
        try:
            async for message in gen:
                await receiver.callback(message, raise_err=False)
                done += 1
                if done >= expect:
                    break
        finally:
            await gen.aclose()

    try:
        await asyncio.wait_for(drain(), timeout_s)
        return {"processed": done, "handled": list(recorder.HANDLED)}
    finally:
        await broker.shutdown()
