"""Runs INSIDE the isolated runner container of the ds-nbc/1 pgqueuer integrations, as ``/app/smoke_inside.py``.
Never on the host.

The image holds ``research_tools/isolated/smoke_inside.py`` byte-identical as ``/app/ds_inside_base.py`` (its sha256
is checked at build). Every mode is the base's, including ``coordinator`` and the isolation probes, except one added
here: ``pgq_hist_coordinated``, one process of a coordinated run whose actors are the whitelisted functions of
``deployspecter.experimental.thirdparty.pgqueuer_hist_adapter`` (a declared variant of the PGQueuer adapter).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/src")
import ds_inside_base as base  # noqa: E402


async def pgq_hist_coordinated() -> dict:
    import psycopg

    from deployspecter.experimental.coordinated import RemoteAsyncRun
    from deployspecter.experimental.stepping import Actor, Handler, Limits
    from deployspecter.experimental.stepping_async import async_actor_connection_factory
    from deployspecter.experimental.thirdparty import pgqueuer_hist_adapter as ha

    functions = {"install": ha.co_install, "work": ha.co_work, "produce": ha.co_produce}

    class CaptureHandler(Handler):
        def allows(self, text: str) -> bool:  # capture mode: the SQL is PGQueuer's; the server log must explain it
            return True

    info = {"host": os.environ["DS_PG_HOST"], "port": int(os.environ["DS_PG_PORT"]),
            "user": os.environ["DS_PG_USER"], "password": os.environ["DS_PG_PASSWORD"],
            "dbname": os.environ["DS_PG_DB"]}

    async def monitor() -> psycopg.AsyncConnection:
        conn = await psycopg.AsyncConnection.connect(**info, autocommit=True, prepare_threshold=None,
                                                     application_name="ds_mvp_admin")
        cur = await conn.execute("SELECT current_setting('cluster_name'), current_database()")
        if await cur.fetchone() != ("ds_mvp_disposable", "ds_mvp"):
            await conn.close()
            raise SystemExit("not the disposable database")
        return conn

    pinned = ha.verify_pinned()
    plan = json.loads(os.environ["DS_CO_PLAN"])
    version = ha.version_label()
    lim = Limits(statement_ms=20000, run_s=float(plan.get("run_s", 150)), python_step_s=90)
    connect = async_actor_connection_factory(info, lim, search_path=os.environ["DS_SCHEMA"], autocommit=True)
    actors = [Actor(a["actor"], CaptureHandler(f"pgqueuer-{version}", a["fn"], functions[a["fn"]], frozenset(), {},
                                               serialize_tasks=True), dict(a.get("args", {})))
              for a in plan["actors"]]
    host, port = plan["coordinator"]
    r = RemoteAsyncRun(actors, connect, monitor, process=plan["process"], version=version, endpoint=(host, port),
                       token=os.environ["DS_CO_NONCE"], markers=plan.get("markers", {}), report=ha.report,
                       limits=lim, run_id=f"pgq_hist_{plan['process']}")
    trace = await r.execute()
    return {"pinned_sources": pinned, "process": plan["process"], "version": version, "monitor_pid": r.monitor_pid,
            "trace": trace.to_dict(), "raw_results": trace.raw_results, "received": r.received}


def pgq_render_ddl() -> dict:
    """No database: the pinned tree's own install DDL for its default settings, as text (for the documented schema
    that the integration's MUSE_CONTEXT carries). Run with ``--network none``."""
    from pgqueuer.adapters.persistence.schema_ddl import render_install
    from pgqueuer.domain.settings import DBSettings

    from deployspecter.experimental.thirdparty import pgqueuer_hist_adapter as ha
    return {"pinned_sources": ha.verify_pinned(), "ddl": render_install(DBSettings())}


def main() -> int:
    if os.environ.get("DS_MODE") == "pgq_render_ddl":
        print(json.dumps(pgq_render_ddl(), default=str))
        return 0
    if os.environ.get("DS_MODE") == "pgq_hist_coordinated":
        out: dict = {"probes": base.probe()}
        out.update(asyncio.run(pgq_hist_coordinated()))
        print(json.dumps(out, default=str))
        return 0
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
