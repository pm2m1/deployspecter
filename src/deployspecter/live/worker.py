"""The fresh child process that runs application code. It holds no admin connection or credential.

It receives a run-specific login once on stdin, opens its sessions, reports them, and waits for the go-ahead before any
application code starts. Results go out on a dedicated pipe, because application code may write to stdout. The parent
checks everything it can from outside, so nothing this process says is taken as proof of control.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

import psycopg

from .driver import SteppedConnection
from .runner import ExplicitSchedule, LiveActor, LiveLimits, LiveScenario, _jsonable, _resolve, _Run
from .serverlog import BLOCKERS_SQL, SESSION_CHECK_SQL, TERMINATE_SQL

_CHECK_FIELDS = (
    "current_user", "session_user", "database", "superuser", "createdb", "createrole", "replication", "bypassrls",
    "search_path", "statement_timeout", "lock_timeout", "idle_in_transaction_timeout", "deadlock_timeout",
    "schema_create", "database_create", "database_temp", "read_server_files", "write_server_files",
    "execute_server_program", "signal_backend",
)
_ISOLATION = {level.name: level for level in psycopg.IsolationLevel}


class WorkerHost:
    def __init__(self, login: dict[str, Any]) -> None:
        self.login = login
        self.checks: dict[str, dict[str, Any]] = {}
        self.monitor = self._connect("monitor", psycopg.Connection, autocommit=True)

    def _connect(self, name: str, cls: type[psycopg.Connection], *, autocommit: bool, isolation: Any = None):
        conn = cls.connect(
            host=self.login["host"], port=self.login["port"], dbname=self.login["dbname"], user=self.login["user"],
            password=self.login["password"], application_name=f"ds:{self.login['run_id']}:{name}", autocommit=True,
            connect_timeout=5, prepare_threshold=None,
        )
        row = conn.execute(SESSION_CHECK_SQL).fetchone()
        self.checks[name] = dict(zip(_CHECK_FIELDS, row, strict=True))
        if isolation is not None:
            conn.isolation_level = isolation
        conn.autocommit = autocommit
        return conn

    def open_actor(self, spec: LiveActor, limits: LiveLimits) -> SteppedConnection:
        return self._connect(spec.name, SteppedConnection, autocommit=spec.autocommit, isolation=spec.isolation)

    def open_plain(self, spec: LiveActor) -> psycopg.Connection:
        return self._connect(spec.name, psycopg.Connection, autocommit=spec.autocommit, isolation=spec.isolation)

    def blockers(self, pids: list[int]) -> dict[int, list[int]]:
        return {p: list(b) for p, b in self.monitor.execute(BLOCKERS_SQL, (pids,)).fetchall()}

    def sessions_remaining(self, pids: list[int]) -> int:
        return 0  # the supervisor verifies this from outside, and the child's opinion wouldn't count anyway

    def terminate(self, pid: int) -> None:
        self.monitor.execute(TERMINATE_SQL, (pid,))


def _scenario(plan: dict[str, Any]) -> LiveScenario:
    # Resolved before any session opens: an entrypoint that won't import is a harness failure, not an application's.
    actors = [
        LiveActor(
            name=a["name"], entrypoint=_resolve(a["entrypoint"]), kwargs=a["kwargs"], autocommit=a["autocommit"],
            isolation=_ISOLATION[a["isolation"]] if a["isolation"] else None,
        )
        for a in plan["actors"]
    ]
    return LiveScenario(plan["name"], "", [], actors)


def _run_plain(host: WorkerHost, scenario: LiveScenario, order: list[str]) -> dict[str, Any]:
    """The uninstrumented control: same process boundary and login, plain psycopg, one actor after another."""
    by_name = {a.name: a for a in scenario.actors}
    results: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    pids: dict[str, int] = {}
    for name in order:
        actor = by_name[name]
        conn = host.open_plain(actor)
        pids[name] = conn.info.backend_pid
        try:
            results[name] = _jsonable(actor.entrypoint(conn, **actor.kwargs))
        except Exception as exc:
            errors[name] = [type(exc).__name__, getattr(exc, "sqlstate", None)]
        finally:
            conn.close()
    return {"results": results, "errors": errors, "pids": pids}


def main() -> int:
    channel = os.fdopen(int(sys.argv[1]), "w", buffering=1)

    def send(message: dict[str, Any]) -> None:
        channel.write(json.dumps(message, default=str) + "\n")
        channel.flush()

    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 1)  # application output can't reach the results pipe, and isn't kept
    os.dup2(devnull, 2)
    plan = json.loads(sys.stdin.readline())
    login = plan.pop("login")
    sys.path[:0] = plan["sys_path"]
    try:
        scenario = _scenario(plan)
        host = WorkerHost(login)
        del login
        limits = LiveLimits(**plan["limits"])
        if plan["mode"] == "plain":
            send({"type": "ready", "pids": {}, "monitor_pid": host.monitor.info.backend_pid, "checks": host.checks})
            if sys.stdin.readline().strip() != "go":
                return 2
            send({"type": "plain_result", **_run_plain(host, scenario, plan["order"]), "checks": host.checks})
            return 0
        abort = threading.Event()

        def on_ready(pids: dict[str, int]) -> None:
            send({"type": "ready", "pids": pids, "monitor_pid": host.monitor.info.backend_pid, "checks": host.checks})
            if sys.stdin.readline().strip() != "go":
                os._exit(2)

            def watch_for_abort() -> None:
                for line in sys.stdin:
                    if line.strip() == "abort":
                        abort.set()

            threading.Thread(target=watch_for_abort, name="ds-abort-watch", daemon=True).start()

        schedule = ExplicitSchedule(plan["schedule"], strict=plan["strict"])
        run = _Run(host, limits, scenario, schedule, on_ready=on_ready, abort_request=abort)
        run.execute()
        send({"type": "result", "trace": run.trace.model_dump(mode="json", exclude={"evidence"})})
        return 0
    except Exception as exc:
        send({"type": "failed", "error": type(exc).__name__, "detail": str(exc)[:300]})
        return 3


if __name__ == "__main__":
    code = main()
    # Abrupt on purpose: no thread the application left behind may keep running after the result is sent.
    os._exit(code)
