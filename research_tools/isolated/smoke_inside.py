"""Runs INSIDE the isolated runner container. Never on the host.

1. Probes the isolation it was given and reports what it observed (it doesn't trust the flags it was started with).
2. Runs the trusted async fixture (``reservations_mixed_async``) on the async stepping engine against the disposable
   PostgreSQL, on the schedule given in argv, and prints one JSON document: probes, the monitor's pid and the trace.
   Reset, initial-state verification, the oracle and the server-log corroboration happen on the host.

Reads only DS_PG_HOST, DS_PG_PORT, DS_PG_USER, DS_PG_PASSWORD, DS_PG_DB, DS_SCHEDULE and DS_VARIANT.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys

sys.path.insert(0, "/app/src")


def probe() -> dict:
    def tcp(host: str, port: int) -> str:
        try:
            with socket.create_connection((host, port), timeout=2):
                return "connected"
        except OSError as exc:
            return f"refused:{type(exc).__name__}"

    def dns(name: str) -> str:
        try:
            return f"resolved:{socket.gethostbyname(name)}"
        except OSError as exc:
            return f"unresolved:{type(exc).__name__}"

    def writable(path: str) -> bool:
        try:
            with open(os.path.join(path, ".ds_probe"), "w") as f:
                f.write("x")
            os.remove(os.path.join(path, ".ds_probe"))
            return True
        except OSError:
            return False

    # No raw probe of the disposable database: an unauthenticated TCP connection inside the log window is an
    # unexplained session, and the corroboration rightly refuses it. The stepped run itself shows reachability.
    home = os.path.expanduser("~")
    return {
        "uid": os.getuid(), "gid": os.getgid(),
        "root_writable": writable("/"), "app_writable": writable("/app"), "tmp_writable": writable("/tmp"),
        "ssh_dir_present": os.path.exists(os.path.join(home, ".ssh")) or os.path.exists("/root/.ssh"),
        "env_keys": sorted(os.environ),
        "secret_like_env": sorted(k for k in os.environ if any(s in k.upper() for s in ("MODEL", "API", "TOKEN",
                                                                                      "SSH", "AWS", "OPENAI"))
                                  and k != "DS_PG_PASSWORD"),
        "host_55432": tcp("host.docker.internal", 55432),
        "host_gateway_55441": tcp("host.docker.internal", 55441),
        "internet_tcp": tcp("1.1.1.1", 443),
        "internet_dns": dns("pypi.org"),
        "nproc_limit": open("/sys/fs/cgroup/pids.max").read().strip() if os.path.exists("/sys/fs/cgroup/pids.max")
        else None,
        "memory_limit": open("/sys/fs/cgroup/memory.max").read().strip()
        if os.path.exists("/sys/fs/cgroup/memory.max") else None,
    }


async def run(schedule: list[str], variant: str) -> dict:
    import psycopg

    from deployspecter.experimental.apps import reservations_mixed_async as app
    from deployspecter.experimental.stepping import Actor, Limits
    from deployspecter.experimental.stepping_async import AsyncRun, async_actor_connection_factory

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

    handlers = app.registry(variant)
    actors = [Actor("j", handlers[("job", "backfill_v2")], {"batch": 10}),
              Actor("a", handlers[("v1", "reserve")], {"event_id": 1, "holder": "alice", "qty": 2})]
    lim = Limits()
    r = AsyncRun(actors, async_actor_connection_factory(info, lim, search_path=app.SCHEMA), monitor,
                 schedule=schedule, limits=lim, run_id=f"isolated_{variant}")
    trace = await r.execute()
    return {"monitor_pid": r.monitor_pid, "trace": trace.to_dict(), "raw_results": trace.raw_results}


def main() -> int:
    out: dict = {"probes": probe()}
    out.update(asyncio.run(run(json.loads(os.environ["DS_SCHEDULE"]), os.environ["DS_VARIANT"])))
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
