"""EXPERIMENTAL onboarding pilot: runs INSIDE the restricted isolated runner container, never on the host.

It probes the isolation, then runs the adapter's three contract steps, ``install``, ``enqueue`` and ``work``. Each
step is its own stepped actor on its own connection, in capture mode: the SQL is the library's own, so there is no
allowlist, and the host refuses the run unless the server log explains every row. It prints one JSON document:
* probes;
* pinned sources;
* per-step runs (traces);
* the recorder's ``handled`` list;
* per-step exceptions.

Reads DS_PG_HOST, DS_PG_PORT, DS_PG_USER, DS_PG_PASSWORD, DS_PG_DB, DS_SCHEMA, DS_ADAPTER, DS_WORKLOAD and
DS_TIMEOUT_S.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys

sys.path.insert(0, "/app/src")
sys.path.insert(0, "/app")

from smoke_inside import probe  # noqa: E402  (the existing runner's isolation probe, copied into the image)


async def pilot() -> dict:
    import psycopg

    from deployspecter.experimental.onboarding_pilot import recorder
    from deployspecter.experimental.stepping import Actor, Handler, Limits
    from deployspecter.experimental.stepping_async import AsyncRun, async_actor_connection_factory

    class CaptureHandler(Handler):
        def allows(self, text: str) -> bool:
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

    adapter = importlib.import_module(os.environ["DS_ADAPTER"])
    pinned = adapter.verify_pinned()
    workload = json.loads(os.environ["DS_WORKLOAD"])
    timeout_s = float(os.environ.get("DS_TIMEOUT_S", "60"))
    lim = Limits(statement_ms=20000, run_s=180, python_step_s=90)
    connect = async_actor_connection_factory(info, lim, search_path=os.environ["DS_SCHEMA"], autocommit=True)
    steps = [("install", adapter.install, {}), ("enqueue", adapter.enqueue, {"jobs": workload["jobs"]}),
             ("work", adapter.work, {"expect": len(workload["jobs"]), "timeout_s": timeout_s})]
    runs, phases = [], {}
    recorder.reset()
    for name, fn, args in steps:
        actor = Actor(name, CaptureHandler("onb-pilot", name, fn, frozenset(), {}, serialize_tasks=True), args)
        r = AsyncRun([actor], connect, monitor, limits=lim, run_id=f"onb_pilot_{name}")
        trace = await r.execute()
        runs.append({"phase": name, "monitor_pid": r.monitor_pid, "trace": trace.to_dict(),
                     "raw_results": trace.raw_results})
        exc = trace.actors[name]["exception"]
        phases[name] = {"exception": exc, "result": trace.raw_results.get(name)}
        if exc:
            break
    return {"pinned_sources": pinned, "runs": runs, "handled": list(recorder.HANDLED), "phases": phases}


def main() -> int:
    out: dict = {"probes": probe()}
    out.update(asyncio.run(pilot()))
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
