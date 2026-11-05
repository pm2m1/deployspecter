"""EXPERIMENTAL harness: run a validated workload's real handlers under a schedule, on a disposable PostgreSQL only.

Each run: check the database's identity; reset to the workload's seed; refuse unless an independent read of that state
has the expected digest; run the actors under the schedule; read the final state independently; apply the oracle.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
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
    corroboration: dict[str, Any] | None = None
    workload_sha256: str | None = None
    handler_sources: dict[str, dict[str, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "variant": self.variant, "workload": self.workload,
                "initial_digest": self.initial_digest, "trace": self.trace.to_dict(),
                "final_state": self.final_state, "violations": self.violations, "seconds": self.seconds,
                "corroboration": self.corroboration}

    def bundle(self) -> dict[str, Any]:
        """The machine-readable evidence: what ran, the digests that bind it, the verdicts."""
        trace = self.trace.to_dict()
        return {"kind": "EXPERIMENTAL run evidence (not certified Gate A2 evidence)", "run_id": self.run_id,
                "variant": self.variant, "workload": self.workload, "schedule": self.trace.schedule,
                "chosen": self.trace.chosen, "outcome": self.trace.outcome,
                "trace_sha256": hashlib.sha256(json.dumps(trace, sort_keys=True, default=str).encode()).hexdigest(),
                "trace_fingerprint": self.trace.fingerprint(), "initial_state_digest": self.initial_digest,
                "final_state_digest": oracle.digest(self.final_state), "oracle_violations": self.violations,
                "corroboration": self.corroboration, "handlers": {n: a["handler"] for n, a in
                                                                  self.trace.actors.items()},
                "classification": classify(self), "code_revision": code_revision(),
                "workload_sha256": self.workload_sha256, "handler_sources": self.handler_sources,
                "versions": versions()}


def classify(r: RunResult) -> str:
    """One label per run: what kind of evidence it is, before anyone reads the detail."""
    if r.trace.outcome != "completed":
        return f"run_{r.trace.outcome}"
    if r.corroboration is not None and not r.corroboration.get("ok"):
        return "refuted_by_server_log"
    if r.violations:
        return "oracle_violation"
    errored = [a["exception_class"] for a in r.trace.actors.values() if a["exception_class"]]
    return f"completed_with_actor_errors:{','.join(sorted(set(errored)))}" if errored else "completed_invariants_hold"


def code_revision() -> dict[str, Any]:
    import subprocess
    here = Path(__file__).resolve().parent
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True,
                              timeout=5).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=here.parents[2],
                                    capture_output=True, text=True, timeout=5).stdout.strip())
    except Exception:  # no git: say so rather than guess
        return {"head": None, "dirty": None}
    return {"head": head or None, "dirty": dirty}


def versions() -> dict[str, str]:
    out = {"psycopg": psycopg.__version__}
    try:
        import sqlalchemy
        out["sqlalchemy"] = sqlalchemy.__version__
    except ImportError:
        pass
    return out


def handler_source(fn: Any) -> dict[str, str]:
    import inspect
    target = getattr(fn, "func", fn)  # functools.partial: the real function
    path = inspect.getsourcefile(target) or ""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else ""
    return {"function": f"{target.__module__}.{target.__qualname__}", "file": os.path.relpath(path),
            "sha256": digest}


def workload_digest(w: Workload) -> str:
    return hashlib.sha256(json.dumps({"inv": w.invocations, "seed": w.seed, "phase": w.phase},
                                     sort_keys=True).encode()).hexdigest()[:16]


def _wait_gone(admin: Any, pids: list[int], seconds: float = 5.0) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if admin.execute("SELECT count(*) FROM pg_stat_activity WHERE pid = ANY(%s)", (pids,)).fetchone()[0] == 0:
            return True
        time.sleep(0.02)
    return False


def corroborated(admin: Any, run: Any, *, user: str) -> tuple[Trace, dict[str, Any]]:
    """Run between two markers on the admin session, then reconcile the driver trace with the server's log."""
    from deployspecter.live.serverlog import ServerLog

    from .corroborate import corroborate
    log = ServerLog(lambda q, prm=None: admin.execute(q, prm).fetchone())
    log.position()
    nonce = secrets.token_hex(6)
    start, end = f"ds_mvp_mark_start_{nonce}", f"ds_mvp_mark_end_{nonce}"
    admin.execute(f"SELECT '{start}'")
    trace = run.execute()
    gone = _wait_gone(admin, [a["backend_pid"] for a in trace.actors.values()])
    admin.execute(f"SELECT '{end}'")
    until = time.monotonic() + 5.0
    while True:
        data = log.read()
        if f"SELECT '{end}'".encode() in data and data.endswith(b"\n"):
            break
        if time.monotonic() > until:
            return trace, {"ok": False, "problems": ["the end marker never reached the log"]}
        time.sleep(0.02)
    result = corroborate(data, start=start, end=end, trace=trace, admin_pid=admin.info.backend_pid, user=user,
                         database=DATABASE).to_dict()
    if not gone:
        result["ok"] = False
        result["problems"].append("an actor backend outlived the run")
    return trace, result


def run_once(target: Target, w: Workload, *, variant: str, schedule: list[str], strict: bool = False,
             limits: Limits | None = None, expected_digest: str | None = None,
             corroborate: bool = False) -> RunResult:
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
        run = Run(actors, connect, admin, schedule=schedule, strict=strict, limits=limits, run_id=run_id,
                  migration_phase=w.phase)
        corroboration = None
        if corroborate:
            trace, corroboration = corroborated(admin, run, user=target.user)
        else:
            trace = run.execute()
        final = oracle.read_state(admin)
        done = any(a["handler"] == "job.backfill_v2" and isinstance(trace.raw_results.get(n), dict)
                   and trace.raw_results[n].get("status") == "done" for n, a in trace.actors.items())
        violations = oracle.check(final, backfill_done=done)
    finally:
        admin.close()
    return RunResult(run_id, variant, w.label, got, trace, final, violations, round(time.monotonic() - started, 3),
                     corroboration)


def run_app(target: Target, app_module: Any, invocations: list[dict[str, Any]], seed: Any, *, variant: str,
            schedule: list[str], label: str, strict: bool = False, limits: Limits | None = None,
            corroborate: bool = False, autocommit: bool | None = None, registry_kw: dict[str, Any] | None = None,
            expected_digest: str | None = None) -> RunResult:
    """``run_once`` for any trusted fixture module with ``SCHEMA``, ``registry``, ``reset``, ``read_state``,
    ``expected_initial`` and ``check``. The same steps: identity check, reset, verified initial digest, stepped run,
    independent final read, oracle."""
    limits = limits or Limits()
    handlers = app_module.registry(variant, **(registry_kw or {}))
    started = time.monotonic()
    # the planned fixtures pass no seed (their state is the plan), so the label and the pinned initial digest are part
    # of the identity too
    key = json.dumps({"inv": invocations, "seed": seed, "label": label, "initial": expected_digest}, sort_keys=True,
                     default=str)
    run_id = hashlib.sha256(f"{key}|{variant}|{','.join(schedule)}".encode()).hexdigest()[:12]
    admin = target.admin()
    try:
        app_module.reset(admin, seed)
        initial = app_module.read_state(admin)
        got = oracle.digest(initial)
        want = expected_digest or oracle.digest(app_module.expected_initial(seed))
        if got != want:
            raise Refused(f"the initial state isn't the verified one (digest {got[:12]}, expected {want[:12]})")
        already = app_module.check(initial)
        if already:  # a violation that predates the run would be a false positive for every schedule
            raise Refused(f"the initial state already violates the oracle: {already}")
        actors = []
        for inv in invocations:
            h = handlers.get((inv["app_version"], inv["handler"]))
            if h is None:
                raise Refused(f"unknown handler {inv['app_version']}.{inv['handler']}")
            actors.append(Actor(inv["actor"], h, dict(inv["args"])))
        if autocommit is None:
            autocommit = getattr(app_module, "AUTOCOMMIT", True)  # the fixture says which DBAPI mode it expects
        connect = actor_connection_factory(target.conninfo(), limits, search_path=app_module.SCHEMA,
                                           autocommit=autocommit)
        run = Run(actors, connect, admin, schedule=schedule, strict=strict, limits=limits, run_id=run_id)
        corroboration = None
        if corroborate:
            trace, corroboration = corroborated(admin, run, user=target.user)
        else:
            trace = run.execute()
        final = app_module.read_state(admin)
        if hasattr(app_module, "check_run"):
            violations = app_module.check_run(initial, final, trace.raw_results)
        else:
            violations = app_module.check(final)
    finally:
        admin.close()
    return RunResult(run_id, variant, label, got, trace, final, violations, round(time.monotonic() - started, 3),
                     corroboration, hashlib.sha256(key.encode()).hexdigest(),
                     {a.name: handler_source(a.handler.fn) for a in actors})


def explore_app(target: Target, app_module: Any, invocations: list[dict[str, Any]], seed: Any, *, variant: str,
                max_runs: int, label: str, **kw: Any) -> tuple[list[RunResult], bool]:
    results: list[RunResult] = []

    def run_with(prefix: list[str]) -> Trace:
        r = run_app(target, app_module, invocations, seed, variant=variant, schedule=prefix, label=label, **kw)
        results.append(r)
        return r.trace

    _traces, exhausted = explore(run_with, max_runs=max_runs)
    return results, exhausted


def explore_workload(target: Target, w: Workload, *, variant: str, max_runs: int,
                     limits: Limits | None = None) -> tuple[list[RunResult], bool]:
    results: list[RunResult] = []

    def run_with(prefix: list[str]) -> Trace:
        r = run_once(target, w, variant=variant, schedule=prefix, limits=limits)
        results.append(r)
        return r.trace

    _traces, exhausted = explore(run_with, max_runs=max_runs)
    return results, exhausted
