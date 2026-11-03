"""The trusted way to run application code. A successful Python return is never enough to mark a run scoreable.

The parent process keeps all admin credentials, and for each run:

1. Refuses to start unless the cluster is a dedicated disposable one, with the logging this file depends on.
2. Rebuilds the schema within limits, after ending and verifying gone any leftover session.
3. Creates a run-specific non-superuser login role. ``CONNECTION LIMIT`` is defence in depth only.
4. Starts a fresh child process with a clean environment, and hands it that login on stdin.
5. Watches from outside: unregistered sessions, deadlines, then abort, cancel, terminate and kill, in that order.
6. Disables the role, kills the process group, ends leftover sessions, and verifies quiescence independently.
7. Reads the final state itself, drops the role, and reconciles the run against PostgreSQL's log.

Only after all of this can ``LiveTrace.scoreable`` be true. Any failure of a check is a flag, and a flagged run's result
is never scored. Infrastructure failures also poison the runner, so a later run can't start on an unverified database.

Not a security boundary. Application code runs as the same OS user, and could read local files. What this provides is
detection: a run that escapes control can't be scored, as far as the checks listed in ``GATE2A_REPORT.md`` reach.
"""

from __future__ import annotations

import json
import os
import secrets
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from ..database import RUN_SCHEMA, RUNNER_ROLE, Sandbox, SetupError
from .runner import LiveActor, LiveLimits, LiveScenario, Schedule, validate_scenario
from .serverlog import LogUnverified, ServerLog, parse_window, reconcile
from .trace import INCONCLUSIVE, OUTCOME_PRIORITY, EvidenceFlag, LiveTrace, RunEvidence

CLUSTER_NAME = "ds_live_disposable"
REQUIRED_SETTINGS = {
    "cluster_name": CLUSTER_NAME,
    "logging_collector": "on",
    "log_destination": "csvlog",
    "log_connections": "on",
    "log_disconnections": "on",
    "log_statement": "all",
    "log_parameter_max_length": "-1",
    "log_rotation_age": "0",
    "log_rotation_size": "0",
    "autovacuum": "off",
}
RUN_MARKER = "DS_LIVE_RUN"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_ENV_KEPT = ("PATH", "LANG", "LC_ALL", "TMPDIR", "HOME", "SYSTEMROOT")
_ACTIVITY_SQL = """SELECT pid, usename, datname, application_name, backend_type, coalesce(left(query, 200), '')
FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> ALL(%s)"""


class InfrastructureFailure(RuntimeError):
    """The harness can't verify the database or child. The batch stops here."""


@dataclass(frozen=True)
class SupervisorLimits:
    handshake_s: float = 20.0
    escalation_grace_s: float = 1.0
    kill_wait_s: float = 5.0
    db_call_s: float = 15.0
    reset_lock_ms: int = 2000
    reset_statement_ms: int = 10000
    quiescence_s: float = 5.0
    log_wait_s: float = 5.0
    poll_s: float = 0.01
    # Test seam: how long after "go" before the parent starts escalating. By default, after the child's own limits.
    run_deadline_s: float | None = None


@dataclass
class SupervisorHooks:
    """Test seams only. Each receives the runner and the run's context."""

    after_reset: Callable[[Any, Any], None] | None = None
    on_tick: Callable[[Any, Any], None] | None = None
    before_reconcile: Callable[[Any, Any], None] | None = None
    skip_steps: frozenset[str] = frozenset()
    connect_via: tuple[str, int] | None = None
    extra_slots: int = 0
    # macOS only: deny the child file reads and writes under the home directory outside its code, and outbound network
    # except to localhost. Risk reduction for trusted code, not a sandbox for hostile code.
    confine: bool = True


@dataclass
class RunContext:
    run_id: str
    role: str
    actor_pids: dict[str, int] = field(default_factory=dict)
    monitor_pid: int | None = None
    proc: subprocess.Popen | None = None
    plain: bool = False
    confinement: str = "none"


class SupervisedRunner:
    def __init__(
        self, sandbox: Sandbox, limits: LiveLimits | None = None, supervisor: SupervisorLimits | None = None,
        hooks: SupervisorHooks | None = None, required_settings: dict[str, str] | None = None,
    ) -> None:
        self.box = sandbox
        self.limits = limits or LiveLimits.from_settings(sandbox.settings)
        self.supervisor = supervisor or SupervisorLimits()
        self.hooks = hooks or SupervisorHooks()
        self.required = required_settings or REQUIRED_SETTINGS
        self.poisoned: str | None = None
        self._db = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ds-admin")
        self._hardened = False

    def close(self) -> None:
        self._db.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ bounded database access

    def _bounded(self, fn: Callable[[], Any], seconds: float | None = None) -> Any:
        """Every admin call has a limit, including when the server stops responding altogether."""
        future = self._db.submit(fn)
        try:
            return future.result(timeout=seconds or self.supervisor.db_call_s)
        except FutureTimeout as exc:
            self.poisoned = "the database stopped responding to the supervisor"
            raise InfrastructureFailure(self.poisoned) from exc
        except psycopg.OperationalError as exc:
            if exc.sqlstate is None or exc.sqlstate.startswith(("08", "57P")):  # the admin session itself is gone
                self.poisoned = f"the supervisor lost its database session: {type(exc).__name__}"
                raise InfrastructureFailure(self.poisoned) from exc
            raise

    def _admin(self, query: Any, params: Any = None) -> Any:
        return self._bounded(lambda: self.box.admin.execute(query, params))

    def _one(self, query: Any, params: Any = None) -> Any:
        return self._bounded(lambda: self.box.admin.execute(query, params).fetchone())

    def _all(self, query: Any, params: Any = None) -> list[Any]:
        return self._bounded(lambda: self.box.admin.execute(query, params).fetchall())

    def _harness_pids(self) -> set[int]:
        # The parent's own sessions, including any the frozen engine keeps open in this process. None is the child's.
        pids = {self.box.admin.info.backend_pid, self.box.monitor.info.backend_pid}
        pids |= {c.info.backend_pid for c in self.box._actors.values() if not c.closed}
        return pids

    # ------------------------------------------------------------ preflight and reset

    def preflight_problems(self) -> list[str]:
        problems = []
        for name, wanted in self.required.items():
            row = self._one("SELECT current_setting(%s, true)", (name,))
            if row[0] != wanted:
                problems.append(f"{name} must be {wanted!r}")
        # A per-database or per-role setting overrides the command line for new sessions, unseen from this one.
        overrides = self._one(
            "SELECT count(*) FROM pg_db_role_setting s, unnest(s.setconfig) c WHERE c LIKE 'log\\_%%'"
        )[0]
        if overrides:
            problems.append("a database or role overrides a logging setting")
        dbname = self.box.admin.info.dbname
        if not dbname.endswith("_test"):
            problems.append(f"{dbname!r} is not a test database")
        return problems

    def _harden_cluster(self) -> None:
        """Only reached on a cluster that declared itself disposable, because this changes its databases."""
        if self._hardened:
            return
        databases = [r[0] for r in self._all("SELECT datname FROM pg_database WHERE datallowconn")]
        for name in databases:
            self._admin(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        self._hardened = True

    def _end_outside_sessions(self, deadline_s: float) -> list[Any]:
        """Ends every client session that isn't the supervisor's, and returns any that wouldn't go."""
        harness = list(self._harness_pids())
        deadline = time.monotonic() + deadline_s
        while True:
            rows = self._all(_ACTIVITY_SQL, (harness,))
            if not rows or time.monotonic() > deadline:
                return rows
            for row in rows:
                self._one("SELECT pg_terminate_backend(%s)", (row[0],))
            time.sleep(0.005)

    def _sweep_stale_roles(self) -> list[str]:
        """A crash can leave a run's role able to log in. Nothing may run until no such login exists."""
        names = [r[0] for r in self._all("SELECT rolname FROM pg_roles WHERE rolname LIKE 'ds\\_run\\_%%'")]
        for name in names:
            self._admin(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(name)))
        return names

    def _drop_stale_roles(self, names: list[str]) -> None:
        for name in names:
            self._admin(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
            self._admin(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))

    def _bounded_reset(self, scenario: LiveScenario) -> None:
        s = self.supervisor
        stale = self._sweep_stale_roles()
        stuck = self._end_outside_sessions(s.quiescence_s)
        if stuck:
            self.poisoned = f"sessions would not end before reset: {[r[0] for r in stuck]}"
            raise InfrastructureFailure(self.poisoned)
        self._drop_stale_roles(stale)
        self._clear_prepared()
        self._admin(f"SET lock_timeout = {int(s.reset_lock_ms)}")
        self._admin(f"SET statement_timeout = {int(s.reset_statement_ms)}")
        try:
            self._bounded(lambda: self.box.reset(scenario.setup_sql, scenario.seed))
        except SetupError:
            raise
        except psycopg.Error as exc:
            self.poisoned = f"reset failed: {type(exc).__name__}"
            raise InfrastructureFailure(self.poisoned) from exc
        finally:
            self._restore_admin_limits()

    def _restore_admin_limits(self) -> None:
        try:
            self._admin("SET lock_timeout = 0")
            self._admin("SET statement_timeout = 0")
        except (InfrastructureFailure, psycopg.Error):
            self.poisoned = self.poisoned or "the supervisor's session limits couldn't be restored"

    def _clear_prepared(self) -> list[str]:
        gids = [r[0] for r in self._all("SELECT gid FROM pg_prepared_xacts WHERE database = current_database()")]
        for gid in gids:
            self._admin(sql.SQL("ROLLBACK PREPARED {}").format(sql.Literal(gid)))
        return gids

    # ------------------------------------------------------------ the run role

    def _create_role(self, ctx: RunContext, count: int) -> str:
        password = secrets.token_hex(24)
        verifier = self.box.admin.pgconn.encrypt_password(
            password.encode(), ctx.role.encode(), b"scram-sha-256"
        ).decode()
        role = sql.Identifier(ctx.role)
        self._admin("SET log_statement = 'none'")  # keeps the password verifier out of the server log
        try:
            self._create_role_statement(role, verifier, count)
        finally:
            self._admin("RESET log_statement")
        self._configure_role(ctx, role)
        return password

    def _create_role_statement(self, role: sql.Identifier, verifier: str, count: int) -> None:
        self._admin(sql.SQL(
            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            "PASSWORD {} CONNECTION LIMIT {} IN ROLE {}"
        ).format(role, sql.Literal(verifier), sql.Literal(count), sql.Identifier(RUNNER_ROLE)))

    def _configure_role(self, ctx: RunContext, role: sql.Identifier) -> None:
        self._admin(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(self.box.admin.info.dbname), role,
        ))
        limits = self.limits
        settings = {
            "search_path": RUN_SCHEMA, "statement_timeout": limits.statement_ms, "lock_timeout": limits.lock_ms,
            "idle_in_transaction_session_timeout": limits.idle_in_transaction_ms,
            "deadlock_timeout": limits.deadlock_ms,
        }
        for name, value in settings.items():
            self._admin(sql.SQL("ALTER ROLE {} SET {} = {}").format(role, sql.Identifier(name), sql.Literal(value)))

    def _expected_checks(self, ctx: RunContext) -> dict[str, Any]:
        limits = self.limits
        return {
            "current_user": ctx.role, "session_user": ctx.role, "database": self.box.admin.info.dbname,
            "superuser": False, "createdb": False, "createrole": False, "replication": False, "bypassrls": False,
            "search_path": RUN_SCHEMA, "statement_timeout": limits.statement_ms, "lock_timeout": limits.lock_ms,
            "idle_in_transaction_timeout": limits.idle_in_transaction_ms, "deadlock_timeout": limits.deadlock_ms,
            "schema_create": False, "database_create": False, "database_temp": False, "read_server_files": False,
            "write_server_files": False, "execute_server_program": False, "signal_backend": False,
        }

    # ------------------------------------------------------------ the child process

    def _code_paths(self, scenario: LiveScenario) -> list[str]:
        """Directories the child must import from: this package's root and each entrypoint's top-level package."""
        import importlib.util

        import deployspecter

        roots = {str(Path(deployspecter.__file__).resolve().parents[1])}
        for actor in scenario.actors:
            if isinstance(actor.entrypoint, str):
                top = actor.entrypoint.partition(":")[0].split(".")[0]
                spec = importlib.util.find_spec(top)
                if spec is None:
                    continue  # the child reports it, and the run becomes an infrastructure failure
                if spec.origin:
                    origin = Path(spec.origin).resolve()
                    roots.add(str(origin.parents[1] if origin.name == "__init__.py" else origin.parent))
                for location in spec.submodule_search_locations or []:  # also covers namespace packages
                    roots.add(str(Path(location).resolve().parent))
        return sorted(roots)

    def _child_sys_path(self, code_paths: list[str]) -> list[str]:
        """Only code directories and the Python installation, so a private directory is never on the child's path."""
        home = Path.home().resolve()
        allowed = [Path(p) for p in (sys.base_prefix, sys.prefix, *code_paths)]
        kept = list(code_paths)
        for entry in sys.path:
            if not entry:
                continue
            path = Path(entry).resolve()
            inside_home = path == home or home in path.parents
            if path.exists() and (not inside_home or any(a == path or a in path.parents for a in allowed)):
                kept.append(str(path))
        return list(dict.fromkeys(kept))

    def _confinement(self, code_paths: list[str]) -> tuple[list[str], str]:
        if not (self.hooks.confine and sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC)):
            return [], "none"
        home = str(Path.home().resolve())
        readable = [sys.base_prefix, sys.prefix, *code_paths]

        def quote(path: str) -> str:
            return json.dumps(str(Path(path).resolve()))

        profile = (
            "(version 1)(allow default)"
            f"(deny file-read-data file-write* (subpath {quote(home)}))"
            f"(allow file-read-data {' '.join(f'(subpath {quote(p)})' for p in readable)})"
            '(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))'
        )
        description = "macos-sandbox: no reads or writes under home outside code; localhost only"
        return [SANDBOX_EXEC, "-p", profile], description

    def _spawn(self, ctx: RunContext, plan: dict[str, Any], code_paths: list[str]) -> tuple[subprocess.Popen, int]:
        read_end, write_end = os.pipe()
        env = {k: os.environ[k] for k in _ENV_KEPT if k in os.environ}  # no DATABASE_URL, PG* or API key
        env["PYTHONPATH"] = os.pathsep.join(plan["sys_path"])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env[RUN_MARKER] = ctx.run_id  # inherited by anything the application starts, for the scan after the run
        prefix, ctx.confinement = self._confinement(code_paths)
        proc = subprocess.Popen(
            [*prefix, sys.executable, "-m", "deployspecter.live.worker", str(write_end)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            pass_fds=(write_end,), start_new_session=True, cwd=tempfile.gettempdir(), text=True,
        )
        os.close(write_end)
        ctx.proc = proc
        proc.stdin.write(json.dumps(plan) + "\n")
        proc.stdin.flush()
        return proc, read_end

    def _plan(
        self, ctx: RunContext, password: str, scenario: LiveScenario, code_paths: list[str] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        parts = urlsplit(self.box.settings.test_dsn)
        host, port = self.hooks.connect_via or (parts.hostname, parts.port or 5432)
        actors = []
        for actor in scenario.actors:
            if not isinstance(actor.entrypoint, str):
                raise ValueError(f"{actor.name}: a supervised run takes 'module:function', not a function object")
            actors.append({
                "name": actor.name, "entrypoint": actor.entrypoint, "kwargs": dict(actor.kwargs),
                "autocommit": actor.autocommit, "isolation": actor.isolation.name if actor.isolation else None,
            })
        return {
            "login": {"host": host, "port": port, "dbname": self.box.admin.info.dbname, "user": ctx.role,
                      "password": password, "run_id": ctx.run_id},
            "name": scenario.name, "actors": actors, "limits": asdict(self.limits),
            "sys_path": self._child_sys_path(code_paths if code_paths is not None else self._code_paths(scenario)),
            **extra,
        }

    def _write_child(self, proc: subprocess.Popen, line: str) -> None:
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def _supervise(
        self, ctx: RunContext, proc: subprocess.Popen, read_end: int, evidence: RunEvidence, flag: Callable[..., None],
    ) -> dict[str, Any] | None:
        """Waits for the child, watching for unregistered sessions and escalating past each deadline."""
        s = self.supervisor
        limits = self.limits
        selector = selectors.DefaultSelector()
        os.set_blocking(read_end, False)
        selector.register(read_end, selectors.EVENT_READ)
        buffered = b""
        result: dict[str, Any] | None = None
        started = time.monotonic()
        deadline = started + s.handshake_s
        ready = False
        steps = ["abort", "cancel", "terminate", "kill"]
        harness = list(self._harness_pids())
        seen_unknown: set[int] = set()
        try:
            while True:
                eof = False
                for _ in selector.select(timeout=s.poll_s):
                    chunk = os.read(read_end, 1 << 16)
                    eof = eof or not chunk
                    buffered += chunk
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    message = self._parse_message(line, flag)
                    if message is None:
                        continue
                    if message["type"] == "ready" and not ready:
                        ready = True
                        ctx.actor_pids = {k: int(v) for k, v in message["pids"].items()}
                        ctx.monitor_pid = int(message["monitor_pid"])
                        evidence.privilege_checks = message["checks"]
                        self._verify_checks(ctx, message["checks"], flag)
                        self._verify_registration(ctx, flag)
                        self._write_child(proc, "go")
                        allowed = s.run_deadline_s
                        if allowed is None:
                            allowed = limits.run_s + limits.abort_grace_s + 1.0
                        deadline = time.monotonic() + allowed
                    elif message["type"] == "failed":
                        flag("infrastructure_failure", f"child failed: {message['error']}: {message['detail']}")
                    elif message["type"] in {"result", "plain_result"}:
                        result = message
                if eof or result is not None:
                    return result
                self._check_activity(ctx, harness, ready, seen_unknown, flag)
                if self.hooks.on_tick is not None:
                    self.hooks.on_tick(self, ctx)
                if time.monotonic() > deadline:
                    if not steps:
                        flag("infrastructure_failure", "the child survived SIGKILL")
                        return result
                    step = steps.pop(0)
                    self._escalate(step, ctx, proc, evidence, started)
                    deadline = time.monotonic() + (s.kill_wait_s if step == "kill" else s.escalation_grace_s)
        finally:
            selector.close()
            os.close(read_end)

    def _parse_message(self, line: bytes, flag: Callable[..., None]) -> dict[str, Any] | None:
        try:
            message = json.loads(line)
        except ValueError:
            flag("unmanaged_activity", "unreadable message on the results pipe")
            return None
        if not isinstance(message, dict) or "type" not in message:
            flag("unmanaged_activity", "malformed message on the results pipe")
            return None
        return message

    def _verify_registration(self, ctx: RunContext, flag: Callable[..., None]) -> None:
        """Every registered backend must exist and belong to this run's role. The child's word isn't enough."""
        pids = [*ctx.actor_pids.values(), *([ctx.monitor_pid] if ctx.monitor_pid else [])]
        owners = dict(self._all("SELECT pid, usename FROM pg_stat_activity WHERE pid = ANY(%s)", (pids,)))
        wrong = [p for p in pids if owners.get(p) != ctx.role]
        if wrong or len(set(pids)) != len(pids):
            flag("unmanaged_activity", f"registered sessions that aren't this run's own: {wrong}")

    def _verify_checks(self, ctx: RunContext, checks: dict[str, Any], flag: Callable[..., None]) -> None:
        wanted = self._expected_checks(ctx)
        expected_sessions = {*ctx.actor_pids, "monitor"}
        if set(checks) != expected_sessions:
            flag("infrastructure_failure", f"privilege checks missing for {sorted(expected_sessions - set(checks))}")
        for session, found in checks.items():
            wrong = {k: found.get(k) for k, v in wanted.items() if found.get(k) != v}
            if wrong:
                flag("infrastructure_failure", f"{session}: session privileges not as required: {sorted(wrong)}")

    def _check_activity(
        self, ctx: RunContext, harness: list[int], ready: bool, seen: set[int], flag: Callable[..., None],
    ) -> None:
        known = set(ctx.actor_pids.values()) | ({ctx.monitor_pid} if ctx.monitor_pid else set())
        for pid, user, db, _app, _backend, _query in self._all(_ACTIVITY_SQL, (harness,)):
            # Before the child registers, and throughout a plain control, its own role is expected.
            if pid in known or pid in seen or ((not ready or ctx.plain) and user == ctx.role):
                continue
            seen.add(pid)
            flag("unmanaged_activity", f"unregistered session pid {pid}, user {user}, database {db}")

    def _escalate(
        self, step: str, ctx: RunContext, proc: subprocess.Popen, evidence: RunEvidence, started: float,
    ) -> None:
        skipped = step in self.hooks.skip_steps
        evidence.escalation.append({
            "step": step, "at_ms": round((time.monotonic() - started) * 1000), "skipped_for_test": skipped,
        })
        if skipped:
            return
        if step == "abort":
            self._write_child(proc, "abort")
        elif step in {"cancel", "terminate"}:
            function = "pg_cancel_backend" if step == "cancel" else "pg_terminate_backend"
            for pid in ctx.actor_pids.values():
                self._one(f"SELECT {function}(%s)", (pid,))
        else:
            self._kill_group(proc)

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    # ------------------------------------------------------------ teardown and verification

    def _tear_down(self, ctx: RunContext, evidence: RunEvidence, flag: Callable[..., None]) -> None:
        """Afterwards no process or session of this run can commit. Verified from here, not reported by the child."""
        s = self.supervisor
        role = sql.Identifier(ctx.role)
        failure = None
        try:
            self._admin(sql.SQL("ALTER ROLE {} NOLOGIN").format(role))
        except InfrastructureFailure as exc:
            failure = exc  # the child still has to die, whatever has happened to the database
        proc = ctx.proc
        if proc is not None:
            try:
                proc.wait(timeout=s.escalation_grace_s)
            except subprocess.TimeoutExpired:
                evidence.escalation.append({"step": "kill_at_teardown", "at_ms": None, "skipped_for_test": False})
            self._kill_group(proc)  # also reaches anything the application started in this group
            try:
                proc.wait(timeout=s.kill_wait_s)
            except subprocess.TimeoutExpired:
                flag("infrastructure_failure", "the child survived SIGKILL")
            evidence.child_exit = proc.returncode
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        self._kill_survivors(ctx, flag)
        if failure is not None:
            raise failure
        stuck = self._end_outside_sessions(s.quiescence_s)
        if stuck:
            flag("infrastructure_failure", f"sessions survived termination: {[r[0] for r in stuck]}")
            self.poisoned = "sessions survived termination"
        prepared = self._clear_prepared()
        if prepared:
            flag("unmanaged_activity", f"prepared transactions left behind: {len(prepared)}")
        remaining = self._all(_ACTIVITY_SQL, (list(self._harness_pids()),))
        evidence.quiescent = not stuck and not remaining
        if remaining:
            flag("infrastructure_failure", "the database isn't quiescent")
            self.poisoned = "the database isn't quiescent"

    def _kill_survivors(self, ctx: RunContext, flag: Callable[..., None]) -> None:
        """Finds processes that left the process group but kept the run's marker. One that scrubbed it isn't found."""
        token = f"{RUN_MARKER}={ctx.run_id}"
        try:
            listing = subprocess.run(
                ["ps", "axeww", "-o", "pid=,command="], capture_output=True, text=True, timeout=5, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            flag("unmanaged_activity", f"couldn't scan for surviving processes: {type(exc).__name__}")
            return
        survivors = [int(line.split(None, 1)[0]) for line in listing.splitlines() if token in line]
        for pid in survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if survivors:
            flag("unmanaged_activity", f"processes outlived the run and were killed: {len(survivors)}")

    def _drop_role(self, ctx: RunContext, evidence: RunEvidence, flag: Callable[..., None]) -> None:
        role = sql.Identifier(ctx.role)
        try:
            self._admin(sql.SQL("DROP OWNED BY {}").format(role))
            self._admin(sql.SQL("DROP ROLE {}").format(role))
        except psycopg.Error as exc:
            flag("infrastructure_failure", f"run role wasn't dropped: {type(exc).__name__}")
            self.poisoned = "a run role wasn't dropped"
            return
        gone = self._one("SELECT count(*) FROM pg_roles WHERE rolname = %s", (ctx.role,))[0] == 0
        evidence.role_dropped = gone
        if not gone:
            flag("infrastructure_failure", "the run role still exists")

    def _read_log(self, log: ServerLog, ctx: RunContext) -> list[list[str]]:
        self._one(f"SELECT 'ds_mark_end_{ctx.run_id}'")
        deadline = time.monotonic() + self.supervisor.log_wait_s
        while True:
            rows = parse_window(log.read(), f"ds_mark_start_{ctx.run_id}", f"ds_mark_end_{ctx.run_id}")
            if rows is not None:
                return rows
            if time.monotonic() > deadline:
                raise LogUnverified("the run's end marker never reached the log")
            time.sleep(0.002)

    # ------------------------------------------------------------ one run

    def run(self, scenario: LiveScenario, schedule: Schedule) -> LiveTrace:
        validate_scenario(scenario, schedule)
        plan = {"mode": "stepped", "schedule": list(schedule.requested), "strict": getattr(schedule, "strict", False)}
        trace, _ = self._execute(scenario, plan, list(schedule.requested))
        return trace

    def plain_serial(self, scenario: LiveScenario, order: list[str]) -> dict[str, Any]:
        """The uninstrumented control: same role, process boundary and limits, plain psycopg, serial order."""
        trace, result = self._execute(scenario, {"mode": "plain", "order": order, "schedule": [], "strict": False}, [])
        result = result or {}
        return {"results": result.get("results", {}), "errors": result.get("errors", {}),
                "final_state": trace.final_state, "flags": trace.evidence.flags, "quiescent": trace.evidence.quiescent}

    def _execute(
        self, scenario: LiveScenario, plan_extra: dict[str, Any], requested: list[str],
    ) -> tuple[LiveTrace, dict[str, Any] | None]:
        started = time.perf_counter()
        evidence = RunEvidence(supervised=True)
        trace = LiveTrace(scenario=scenario.name, requested_schedule=requested, evidence=evidence)

        def flag(kind: str, detail: str) -> None:
            evidence.flags.append(EvidenceFlag(kind=kind, detail=detail))

        if self.poisoned:
            raise RuntimeError(f"this runner can't be trusted any more: {self.poisoned}")
        result: dict[str, Any] | None = None
        try:
            problems = self.preflight_problems()
            if problems:  # no application code runs at all
                flag("log_unverified", "; ".join(problems))
                return self._conclude(trace, evidence, None, started), None
            self._harden_cluster()
            result = self._run_child(scenario, plan_extra, trace, evidence, flag)
        except InfrastructureFailure as exc:
            flag("infrastructure_failure", str(exc))
        return self._conclude(trace, evidence, result, started), result

    def _run_child(
        self, scenario: LiveScenario, plan_extra: dict[str, Any], trace: LiveTrace, evidence: RunEvidence,
        flag: Callable[..., None],
    ) -> dict[str, Any] | None:
        run_id = secrets.token_hex(6)
        ctx = RunContext(run_id=run_id, role=f"ds_run_{run_id}", plain=plan_extra["mode"] == "plain")
        evidence.run_id = run_id
        self._bounded_reset(scenario)
        trace.initial_state = self._bounded(self.box.snapshot)
        log = ServerLog(self._one)
        log_ok = True
        try:
            log.position()
            self._one(f"SELECT 'ds_mark_start_{run_id}'")
        except LogUnverified as exc:
            flag("log_unverified", str(exc))
            log_ok = False
        before = self._one("SELECT sessions FROM pg_stat_database WHERE datname = current_database()")[0]
        if self.hooks.after_reset is not None:
            self.hooks.after_reset(self, ctx)
        actors = [a.name for a in scenario.actors]
        password = self._create_role(ctx, len(actors) + 1 + self.hooks.extra_slots)
        result = None
        try:
            code_paths = self._code_paths(scenario)
            child_plan = self._plan(ctx, password, scenario, code_paths, **plan_extra)
            del password
            proc, read_end = self._spawn(ctx, child_plan, code_paths)
            evidence.confinement = ctx.confinement
            del child_plan
            result = self._supervise(ctx, proc, read_end, evidence, flag)
        finally:
            self._tear_down(ctx, evidence, flag)
        self._absorb(result, trace, ctx, flag)
        trace.final_state = self._bounded(self.box.snapshot)
        self._drop_role(ctx, evidence, flag)
        evidence.expected_sessions = dict(ctx.actor_pids)
        if ctx.monitor_pid:
            evidence.expected_sessions["monitor"] = ctx.monitor_pid
        self._check_session_counter(before, plan_extra, actors, evidence, flag, result)
        if self.hooks.before_reconcile is not None:
            self.hooks.before_reconcile(self, ctx)
        if log_ok:
            self._reconcile(log, ctx, trace, result, plan_extra, evidence, flag)
        return result

    def _absorb(self, result: Any, trace: LiveTrace, ctx: RunContext, flag: Callable[..., None]) -> None:
        if result is None:
            flag("harness_killed", "the child ended without a result")
            return
        if result["type"] != "result":
            return
        try:
            child = LiveTrace.model_validate(result["trace"])
        except ValueError as exc:
            flag("unmanaged_activity", f"the child's trace didn't validate: {type(exc).__name__}")
            return
        for name in ("chosen_schedule", "decisions", "records", "actors", "connections", "refusals", "outcome",
                     "inconclusive", "diagnostics", "leaked_threads", "sessions_left_open", "external_blockers"):
            setattr(trace, name, getattr(child, name))
        if child.external_blockers:
            flag("external_interference", f"lock held outside the run by {child.external_blockers}")
        if child.leaked_threads:
            flag("harness_killed", f"threads were still running when the child stopped: {child.leaked_threads}")
        registered = set(ctx.actor_pids.values())
        named = set(child.connections.values()) | {r.backend_pid for r in child.records}
        if named != registered or {a.actor for a in child.actors} != set(ctx.actor_pids):
            flag("unmanaged_activity", "the child's trace doesn't describe the sessions it registered")

    def _check_session_counter(
        self, before: int, plan_extra: dict[str, Any], actors: list[str], evidence: RunEvidence,
        flag: Callable[..., None], result: Any,
    ) -> None:
        """Supporting evidence only. Fewer can be statistics lag; more is treated as a disagreement to explain."""
        after = self._one("SELECT sessions FROM pg_stat_database WHERE datname = current_database()")[0]
        evidence.session_counter_delta = after - before
        if evidence.session_counter_delta > len(actors) + 1:
            flag("unmanaged_activity", f"session counter rose by {evidence.session_counter_delta}, "
                                       f"expected at most {len(actors) + 1}")

    def _reconcile(
        self, log: ServerLog, ctx: RunContext, trace: LiveTrace, result: Any, plan_extra: dict[str, Any],
        evidence: RunEvidence, flag: Callable[..., None],
    ) -> None:
        try:
            rows = self._read_log(log, ctx)
        except LogUnverified as exc:
            flag("log_unverified", str(exc))
            return
        if plan_extra["mode"] == "plain":
            expected = {int(pid): name for name, pid in (result or {}).get("pids", {}).items()}
            if ctx.monitor_pid:
                expected[ctx.monitor_pid] = "monitor"
            report = reconcile(rows, None, expected, ctx.monitor_pid, self._harness_pids())
        else:
            expected = {pid: name for name, pid in ctx.actor_pids.items()}
            if ctx.monitor_pid:
                expected[ctx.monitor_pid] = "monitor"
            has_trace = result is not None and result.get("type") == "result"
            halted = not has_trace or trace.outcome in INCONCLUSIVE or any(a.outcome == "aborted" for a in trace.actors)
            report = reconcile(rows, trace if has_trace else None, expected, ctx.monitor_pid, self._harness_pids(),
                               halted=halted)
        evidence.reconciliation = report.summary()
        if not report.clean:
            flag("unmanaged_activity", f"log reconciliation: {json.dumps(report.summary())[:600]}")

    def _conclude(self, trace: LiveTrace, evidence: RunEvidence, result: Any, started: float) -> LiveTrace:
        kinds = {f.kind for f in evidence.flags}
        for reason in OUTCOME_PRIORITY:
            if reason in kinds:
                trace.outcome = reason  # type: ignore[assignment]
                break
        trace.inconclusive = trace.outcome in INCONCLUSIVE
        trace.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return trace


__all__ = ["LiveActor", "SupervisedRunner", "SupervisorHooks", "SupervisorLimits"]
