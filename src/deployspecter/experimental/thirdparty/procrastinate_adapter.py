"""EXPERIMENTAL adapter: procrastinate 3.3.0 / 3.4.0 on a stepped async connection (rolling-upgrade case,
docs/experimental_mvp/rolling_upgrade/procrastinate_3.3_3.4/CASE.md). Runs only inside the isolated runner.

* **Seam:** ``App.open_async(pool=...)``, documented ("Procrastinate can use an existing pool"), with a
  one-connection ``psycopg_pool.AsyncConnectionPool`` whose ``connection_class`` hands over the actor's stepped
  connection once (as the Chancy adapter does).
* **Disabled:** LISTEN/NOTIFY (``listen_notify=False``): the listener opens its own standalone connection, which the
  server log would refute. Signal handlers are also disabled.
* **Tasks:** trusted harness tasks, registered through procrastinate's own task API.
* **Migrations:** the operator steps run the migration file's exact content, sha256-checked, from the installed
  package. That is what ``cat …sql | psql`` does, on one stepped session.

Nothing in procrastinate is patched or monkeypatched.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any

import psycopg

from ..stepping import Refused
from ..stepping_async import AsyncSteppedConnection

PINNED_BY_VERSION = {
    "3.3.0": {"procrastinate/sql/queries.sql": "c9b3fb0be4e26bac6612c3a3d4883bdb8e129bac6175362a224e4b7cb643ff9d",
              "procrastinate/sql/schema.sql": "bcc21931d84c4d61d319719a59d31f5322e180ab5ccd04db1875a1a7b6f59b03",
              "procrastinate/app.py": "1147f1a56d55a23c396d364183296dc9ffd66bfc3b16d25e2033752276b35797",
              "procrastinate/worker.py": "c4f6108f93471e145d6ae143d17a7112bc42fd7f9dc12ba102b301847aa421d0",
              "procrastinate/manager.py": "2e41449c71b9b6ed74b5872fec3e103ddb4597646449dffab11e1e3de2107efe",
              "procrastinate/psycopg_connector.py":
                  "d93aaa49a39c1c5822c6fb34ddec6121969f454b253159a7abc891ba7d1c4c9e"},
    "3.4.0": {"procrastinate/sql/queries.sql": "e6b932bcefe689ed2941f5c885b5ebc7f07913203f8363e9f03762cd59314ed3",
              "procrastinate/sql/schema.sql": "c796dca40785a84ee434b930b4bc1bb11cc7a6da84ad6330cf6c49e953c902b9",
              "procrastinate/app.py": "1147f1a56d55a23c396d364183296dc9ffd66bfc3b16d25e2033752276b35797",
              "procrastinate/worker.py": "c4f6108f93471e145d6ae143d17a7112bc42fd7f9dc12ba102b301847aa421d0",
              "procrastinate/manager.py": "2e41449c71b9b6ed74b5872fec3e103ddb4597646449dffab11e1e3de2107efe",
              "procrastinate/psycopg_connector.py":
                  "d93aaa49a39c1c5822c6fb34ddec6121969f454b253159a7abc891ba7d1c4c9e"},
}
MIGRATIONS = {  # 3.4.0's migrations: file name -> sha256 (the operator steps refuse anything else)
    "03.04.00_01_pre_add_retry_failed_job_procedure.sql":
        "6883b7336508620ba856db4a78558158fe15c8e68b2ea05c60b468e73c311255",
    "03.04.00_50_post_add_retry_failed_job_procedure.sql":
        "ac4021eb9d029e08edc9ef7672ae7ea4ef82002858a400cc6b44abd3e79248b2",
}
QUEUE = "ds_q"
HANDLED: list[int] = []
SUCCEEDED: list[int] = []


def _root() -> Path:
    import procrastinate
    return Path(inspect.getsourcefile(procrastinate) or "").resolve().parent.parent


def installed_version() -> str:
    root = _root()
    for version, pins in PINNED_BY_VERSION.items():
        if all(hashlib.sha256((root / rel).read_bytes()).hexdigest() == h for rel, h in pins.items()):
            return version
    raise Refused("procrastinate's sources match no pinned release")


def stepped_pool(conn: AsyncSteppedConnection) -> Any:
    from psycopg_pool import AsyncConnectionPool
    if not isinstance(conn, AsyncSteppedConnection):
        raise Refused("procrastinate may only be given the actor's stepped connection")
    handed: list[Any] = []

    class ActorBoundConnection(AsyncSteppedConnection):
        @classmethod
        async def connect(cls, *_args: Any, **_kwargs: Any) -> Any:
            if handed:
                raise psycopg.OperationalError("refused: the pool asked for a second connection")
            handed.append(conn)
            return conn

    return AsyncConnectionPool("", connection_class=ActorBoundConnection, min_size=1, max_size=1, open=False,
                               timeout=30, reconnect_timeout=10)


def _app() -> Any:
    """A fresh App per actor, with the harness tasks registered through procrastinate's task API."""
    import procrastinate
    app = procrastinate.App(connector=procrastinate.PsycopgConnector())
    version = installed_version()

    @app.task(name="ds_ok", queue=QUEUE)
    async def ds_ok(n: int) -> int:
        HANDLED.append(n)
        SUCCEEDED.append(n)
        return n

    @app.task(name="ds_flaky", queue=QUEUE, retry=1)
    async def ds_flaky(n: int, fail_in: str) -> int:
        HANDLED.append(n)
        if version == fail_in:
            raise RuntimeError(f"ds_flaky: planned failure under {fail_in}")
        SUCCEEDED.append(n)
        return n

    @app.task(name="ds_fail", queue=QUEUE, retry=1)
    async def ds_fail(n: int) -> int:
        HANDLED.append(n)
        raise RuntimeError("ds_fail: always fails")

    return app


class _Opened:
    def __init__(self, conn: AsyncSteppedConnection) -> None:
        self.app, self.pool = _app(), stepped_pool(conn)

    async def __aenter__(self) -> Any:
        await self.pool.open(wait=True)
        await self.app.open_async(pool=self.pool)
        return self.app

    async def __aexit__(self, *exc: Any) -> None:
        await self.app.close_async()
        await self.pool.close()


async def p_schema(conn: AsyncSteppedConnection) -> dict[str, Any]:
    """This release's own full schema, through its schema manager (``procrastinate schema --apply``)."""
    async with _Opened(conn) as app:
        await app.schema_manager.apply_schema_async()
    return {"version": installed_version(), "schema_applied": True}


async def p_migrate(conn: AsyncSteppedConnection, *, migration: str) -> dict[str, Any]:
    """An operator step: one migration file of the installed package, its exact content, sha256-checked."""
    if migration not in MIGRATIONS:
        raise Refused(f"{migration} isn't a pinned migration")
    text = (_root() / "procrastinate" / "sql" / "migrations" / migration).read_bytes()
    if hashlib.sha256(text).hexdigest() != MIGRATIONS[migration]:
        raise Refused(f"{migration}'s content isn't the pinned one")
    async with conn.transaction():
        await conn.execute(text.decode())
    return {"version": installed_version(), "migration": migration, "sha256": MIGRATIONS[migration]}


async def p_defer(conn: AsyncSteppedConnection, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Defer jobs through the tasks' public ``defer_async``. ``jobs``: {"n", "kind": ok|flaky|fail, "fail_in"}."""
    version = installed_version()
    pushed = []
    async with _Opened(conn) as app:
        for j in jobs:
            kind, n = j["kind"], int(j["n"])
            task = app.tasks[{"ok": "ds_ok", "flaky": "ds_flaky", "fail": "ds_fail"}[kind]]
            kwargs: dict[str, Any] = {"n": n} if kind != "flaky" else {"n": n, "fail_in": j["fail_in"]}
            job_id = await task.defer_async(**kwargs)
            pushed.append({"n": n, "id": job_id, "kind": kind, "version": version,
                           "max_attempts": 0 if kind == "ok" else 1})
    return {"version": version, "pushed": pushed}


async def p_work(conn: AsyncSteppedConnection, *, wait: bool = True, timeout_s: float = 60) -> dict[str, Any]:
    """A worker of this release.

    * ``wait=True``: it runs until the coordinator's ``stop`` signal, then is cancelled. The worker handles that as
      a graceful shutdown (its own ``run`` catches the cancellation).
    * ``wait=False``: procrastinate's own drain mode, which returns as soon as no job is left.
    """
    import asyncio

    from ..stepping_async import _ACTING_FOR
    me = _ACTING_FOR.get()
    if me is None or not hasattr(me.run, "signal"):
        raise Refused("p_work runs only under a coordinated run")
    stop = me.run.signal("stop")
    if not wait:
        async with _Opened(conn) as app:
            await app.run_worker_async(queues=[QUEUE], wait=False, listen_notify=False, install_signal_handlers=False,
                                       concurrency=1, abort_job_polling_interval=30, update_heartbeat_interval=30,
                                       delete_jobs="never")
        return {"version": installed_version(), "handled": list(HANDLED), "succeeded": list(SUCCEEDED),
                "worker_stopped": True, "stopped_by": "drained"}
    async with _Opened(conn) as app:
        task = asyncio.create_task(app.run_worker_async(
            queues=[QUEUE], wait=True, listen_notify=False, install_signal_handlers=False, concurrency=1,
            fetch_job_polling_interval=0.5, abort_job_polling_interval=30, update_heartbeat_interval=30,
            delete_jobs="never", shutdown_graceful_timeout=10))
        try:
            await asyncio.wait_for(stop.wait(), timeout=timeout_s)
            why = "signal"
        except TimeoutError:
            why = "timeout"
        task.cancel()
        stopped = True
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 - a worker error is a result, recorded
            stopped = False
            why = f"{why}; worker raised {type(exc).__name__}: {str(exc)[:120]}"
    return {"version": installed_version(), "handled": list(HANDLED), "succeeded": list(SUCCEEDED),
            "worker_stopped": stopped, "stopped_by": why}


def _job_view(jobs: list[Any]) -> dict[str, Any] | None:
    if not jobs:
        return None
    status = jobs[0].status
    return {"status": str(getattr(status, "value", status)), "attempts": jobs[0].attempts}


async def p_admin_retry(conn: AsyncSteppedConnection, *, targets: list[dict[str, Any]]) -> dict[str, Any]:
    """3.4.0's new feature: retry *failed* jobs (``retry_job_by_id_async``, through ``retry_job_v2``), recording
    each job's status and attempts before and after through ``list_jobs_async``. ``targets``: {"n", "id"}."""
    from procrastinate import utils
    version = installed_version()
    out = []
    async with _Opened(conn) as app:
        jm = app.job_manager
        for t in targets:
            before = list(await jm.list_jobs_async(id=int(t["id"])))
            rec: dict[str, Any] = {"n": t["n"], "id": t["id"], "before": _job_view(before)}
            try:
                await jm.retry_job_by_id_async(int(t["id"]), retry_at=utils.utcnow())
                rec["ok"] = True
            except Exception as exc:  # noqa: BLE001 - the application's refusal is a result
                rec.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:160]}")
            rec["after"] = _job_view(list(await jm.list_jobs_async(id=int(t["id"]))))
            out.append(rec)
    return {"version": version, "retried": out}
