"""EXPERIMENTAL harness: run a validated workload's real handlers under a schedule, on a disposable PostgreSQL only.

Each run: check the database's identity; reset to the workload's seed; refuse unless an independent read of that state
has the expected digest; run the actors under the schedule; read the final state independently; apply the oracle.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg

from . import oracle
from .apps import reservations_mixed as app
from .stepping import Actor, Limits, Refused, Run, Trace, actor_connection_factory, explore
from .workloads import Workload

CLUSTER_NAME = "ds_mvp_disposable"
DATABASE = "ds_mvp"
FORBIDDEN_PORTS = {55432}  # the protected development database; never a target


class TargetRefused(RuntimeError):
    """The database isn't the disposable one this prototype may touch."""


@dataclass
class Target:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    dbname: str = DATABASE

    def conninfo(self) -> dict[str, Any]:
        if self.host != "127.0.0.1" or self.port in FORBIDDEN_PORTS or self.dbname != DATABASE:
            raise TargetRefused("only a disposable local PostgreSQL on 127.0.0.1, database ds_mvp, other than 55432")
        return {"host": self.host, "hostaddr": self.host, "port": self.port, "user": self.user,
                "password": self.password, "dbname": self.dbname}

    def admin(self) -> psycopg.Connection:
        conn = psycopg.connect(**self.conninfo(), autocommit=True, prepare_threshold=None,
                               application_name="ds_mvp_admin")
        name = conn.execute("SELECT current_setting('cluster_name'), current_database(), "
                            "current_setting('server_version_num')::int").fetchone()
        if name[0] != CLUSTER_NAME or name[1] != DATABASE or not 160000 <= name[2] < 170000:
            conn.close()
            raise TargetRefused(f"unexpected database identity {name[:2]}; stopping")
        return conn


@dataclass
class RunResult:
    run_id: str
    variant: str
    workload: str
    initial_digest: str
    trace: Trace
    final_state: dict[str, list[dict[str, Any]]]
    violations: list[str]
    seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "variant": self.variant, "workload": self.workload,
                "initial_digest": self.initial_digest, "trace": self.trace.to_dict(),
                "final_state": self.final_state, "violations": self.violations, "seconds": self.seconds}


def workload_digest(w: Workload) -> str:
    return hashlib.sha256(json.dumps({"inv": w.invocations, "seed": w.seed, "phase": w.phase},
                                     sort_keys=True).encode()).hexdigest()[:16]


def run_once(target: Target, w: Workload, *, variant: str, schedule: list[str], strict: bool = False,
             limits: Limits | None = None, expected_digest: str | None = None) -> RunResult:
    limits = limits or Limits()
    handlers = app.registry(variant)
    started = time.monotonic()
    run_id = hashlib.sha256(f"{workload_digest(w)}|{variant}|{','.join(schedule)}".encode()).hexdigest()[:12]
    admin = target.admin()
    try:
        app.reset(admin, w.seed)
        initial = oracle.read_state(admin)
        want = expected_digest or oracle.digest(oracle.expected_initial(w.seed))
        got = oracle.digest(initial)
        if got != want:
            raise Refused(f"the initial state isn't the verified one (digest {got[:12]}, expected {want[:12]})")
        actors = []
        for inv in w.invocations:
            h = handlers.get((inv["app_version"], inv["handler"]))
            if h is None:
                raise Refused(f"unknown handler {inv['app_version']}.{inv['handler']}")
            actors.append(Actor(inv["actor"], h, dict(inv["args"])))
        connect = actor_connection_factory(target.conninfo(), limits, search_path=app.SCHEMA)
        trace = Run(actors, connect, admin, schedule=schedule, strict=strict, limits=limits, run_id=run_id,
                    migration_phase=w.phase).execute()
        final = oracle.read_state(admin)
        done = any(a["handler"] == "job.backfill_v2" and isinstance(trace.raw_results.get(n), dict)
                   and trace.raw_results[n].get("status") == "done" for n, a in trace.actors.items())
        violations = oracle.check(final, backfill_done=done)
    finally:
        admin.close()
    return RunResult(run_id, variant, w.label, got, trace, final, violations, round(time.monotonic() - started, 3))


def explore_workload(target: Target, w: Workload, *, variant: str, max_runs: int,
                     limits: Limits | None = None) -> tuple[list[RunResult], bool]:
    results: list[RunResult] = []

    def run_with(prefix: list[str]) -> Trace:
        r = run_once(target, w, variant=variant, schedule=prefix, limits=limits)
        results.append(r)
        return r.trace

    _traces, exhausted = explore(run_with, max_runs=max_runs)
    return results, exhausted
