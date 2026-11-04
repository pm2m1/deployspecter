"""Test-only fakes for Gate A2.2: a fake PostgreSQL 16 server and a fake Gate 2A child, under the frozen supervisor.

Simulated, not observed. The server writes a 26-column csvlog in the forms Gate A1 observed on PostgreSQL 16.15
(connection received / authenticated / authorized, statements, disconnection). Queries are rendered with psycopg's own
conversion, and a connection that keeps psycopg's default preparation logs names as psycopg 3.3.6 would choose them.
Frozen Gate 2A code runs unmodified: only the child process spawn, the process-group kill and the ``ps`` scan are
replaced, here, by the test. Nothing connects to a database.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import re
import subprocess
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from deployspecter.live import serverlog
from deployspecter.live.supervisor import REQUIRED_SETTINGS, SupervisedRunner
from deployspecter.live.trace import ActorResult, LiveTrace
from deployspecter.research.track_a import characterization as ch
from deployspecter.research.track_a import gate2a_audit as ga
from deployspecter.research.track_a import gate2a_prepare_model as pm
from deployspecter.research.track_a import gate2a_role_catalog as rc
from deployspecter.research.track_a import parent_reconciliation as pr

LOG_PATH = "log/ds.csv"
ADMIN_USER, TEST_DB, INITIAL_DB = "deployspecter", "deployspecter_test", "deployspecter"
DATABASES = ("postgres", "deployspecter", "deployspecter_test", "template1")
RUNNER_ATTRS = dict(rc.RUNNER_ATTRIBUTES)
ROLE_SQL = dict(rc.ROLE_CATALOG)
ROLE_COLS = rc.ROLE_COLUMNS


class Description:
    def __init__(self, name: str) -> None:
        self.name = name


class Cursor:
    def __init__(self, rows: list[tuple] | None = None, names: list[str] | None = None) -> None:
        self.rows = rows or []
        self.description = [Description(n) for n in names] if names else None

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class Info:
    def __init__(self, session: Session) -> None:
        self.backend_pid, self.dbname, self.user = session.pid, session.db, session.user
        self.host, self.hostaddr, self.port = "127.0.0.1", "127.0.0.1", 55433
        self.transaction_status = psycopg.pq.TransactionStatus.IDLE


class PgConn:
    @staticmethod
    def encrypt_password(password: bytes, user: bytes, algorithm: bytes) -> bytes:
        return b"SCRAM-SHA-256$4096:ZmFrZQ==$fake:verifier"


class Session:
    def __init__(self, server: FakeServer, pid: int, user: str, db: str, app: str) -> None:
        self.server, self.pid, self.user, self.db, self.app = server, pid, user, db, app
        self.id, self.port, self.line, self.alive, self.logging = f"6aeb0000.{pid:x}", 40000 + pid, 0, True, True

    def log(self, message: str, *, detail: str = "", severity: str = "LOG", phase: str = "client") -> None:
        if not self.server.write_allowed(self, message):
            return
        self.line += 1
        cells = [""] * 26
        cells[0] = "2026-11-05 10:00:00.000 UTC"
        cells[pr.PID], cells[pr.SESSION], cells[pr.LINE] = str(self.pid), self.id, str(self.line)
        cells[pr.SEVERITY], cells[pr.MESSAGE], cells[pr.DETAIL] = severity, message, detail
        if phase == "received":
            cells[pr.BACKEND] = "not initialized"
        else:
            cells[pr.USER], cells[pr.DATABASE], cells[pr.BACKEND] = self.user, self.db, "client backend"
            if phase == "client":
                cells[pr.APPLICATION] = self.app
        self.server.append(cells)


class FakeServer:
    """One disposable cluster's state and csvlog. ``faults`` names deliberate deviations a test injects."""

    def __init__(self, *faults: str) -> None:
        self.faults = set(faults)
        self.data = bytearray()
        self.pid = 200
        self.sessions: dict[int, Session] = {}
        self.tables: dict[str, list[dict[str, Any]]] = {}
        # The role catalog, as pg_roles, pg_auth_members, pg_db_role_setting and pg_database.datacl would show it.
        self.roles: dict[str, dict[str, Any]] = {ADMIN_USER: {**RUNNER_ATTRS, "rolsuper": True, "rolcreaterole": True,
                                                              "rolcreatedb": True, "rolcanlogin": True,
                                                              "rolreplication": True, "rolbypassrls": True}}
        if "runner_role_preexists" in faults:
            self.roles["deployspecter_runner"] = dict(RUNNER_ATTRS)
        self.members: set[tuple[str, str]] = set()  # (role, member)
        self.role_settings: dict[str, list[str]] = {}
        self.acls: dict[str, str] = dict.fromkeys(DATABASES, "")  # '' is the default: PUBLIC may connect
        self.connections = 0
        self.inject: dict[str, list[str]] = {}  # after a statement text, an extra admin statement is logged
        self.suppress: set[str] = set()  # statements whose log row is lost
        self.unkillable: set[int] = set()
        self.hooks: list[tuple[str, Any]] = []  # (statement prefix, callback(conn)) run when a statement executes
        self.unlogged_prefixes: list[str] = []  # statements whose log row is lost (by prefix, once each)

    # -- the log

    def append(self, cells: list[str]) -> None:
        out = io.StringIO()
        csv.writer(out).writerow(cells)
        self.data += out.getvalue().encode()

    def write_allowed(self, session: Session, message: str) -> bool:
        text = message.split(": ", 1)[-1]
        if text in self.suppress:
            self.suppress.discard(text)
            return False
        for prefix in list(self.unlogged_prefixes):
            if text.startswith(prefix) and message.startswith(("statement:", "execute")):
                self.unlogged_prefixes.remove(prefix)
                return False
        return True

    def checkpoint(self) -> None:
        cells = [""] * 26
        cells[pr.PID], cells[pr.SESSION], cells[pr.LINE] = "60", "6aeb0000.3c", "1"
        cells[pr.SEVERITY], cells[pr.MESSAGE], cells[pr.BACKEND] = "LOG", "checkpoint starting: time", "checkpointer"
        self.append(cells)

    # -- sessions

    def connect(self, user: str, db: str, app: str, *, threshold: int | None = None) -> FakeConn:
        self.pid += 1
        s = Session(self, self.pid, user, db, app)
        self.sessions[s.pid] = s
        self.connections += 1
        s.log(f"connection received: host=172.17.0.1 port={s.port}", phase="received")
        s.log(f'connection authenticated: identity="{user}" method=scram-sha-256 '
              "(/var/lib/postgresql/data/pg_hba.conf:128)", phase="auth")
        s.log(f"connection authorized: user={user} database={db} application_name={app}", phase="auth")
        return FakeConn(self, s, threshold)

    def disconnect(self, s: Session) -> None:
        if s.alive:
            s.alive = False
            s.logging = True
            s.log(f"disconnection: session time: 0:00:00.010 user={s.user} database={s.db} host=172.17.0.1 "
                  f"port={s.port}")
            self.sessions.pop(s.pid, None)

    def terminate(self, pid: int) -> bool:
        s = self.sessions.get(pid)
        if s is None or pid in self.unkillable:
            return False
        s.log("terminating connection due to administrator command", severity="FATAL")
        self.disconnect(s)
        return True

    def connect_from_dsn(self, dsn: str, **kwargs: Any) -> FakeConn:
        d = conninfo_to_dict(dsn)
        threshold = kwargs.get("prepare_threshold", 5)
        return self.connect(d["user"], d["dbname"], d.get("application_name", ""), threshold=threshold)

    # -- answers, for every query the supervisor, Sandbox, ServerLog and the Gate A2 reader send

    def answer(self, conn: FakeConn, text: str, params: tuple) -> Cursor:
        if text == "SELECT current_setting($1, true)":
            return Cursor([(REQUIRED_SETTINGS.get(params[0]),)])
        if text.startswith("SELECT count(*) FROM pg_db_role_setting"):
            return Cursor([(0,)])
        if text == ga.DATABASES_SQL:
            return Cursor([(d,) for d in DATABASES])
        if text == ga.STALE_ROLES_SQL:
            return Cursor([(r,) for r in sorted(self.roles) if r.startswith("ds_run_")])
        if text.startswith("DO $$ BEGIN") and "deployspecter_runner" in text:
            if "deployspecter_runner" not in self.roles:
                self.roles["deployspecter_runner"] = {**RUNNER_ATTRS, "rolcanlogin": "runner_role_wrong" in self.faults}
            return Cursor([])
        m = re.fullmatch(r'REVOKE ALL ON DATABASE "([a-z0-9_]+)" FROM PUBLIC', text)
        if m:
            self.acls[m.group(1)] = f"{{{ADMIN_USER}=CTc/{ADMIN_USER}}}"
        m = re.fullmatch(r'GRANT CONNECT ON DATABASE "([a-z0-9_]+)" TO "([a-z0-9_]+)"', text)
        if m:
            acl = self.acls[m.group(1)] or f"{{{ADMIN_USER}=CTc/{ADMIN_USER}}}"
            self.acls[m.group(1)] = acl[:-1] + f",{m.group(2)}=c/{ADMIN_USER}}}"
        m = re.fullmatch(r'ALTER ROLE "([a-z0-9_]+)" SET "([a-z_]+)" = (.*)', text)
        if m:
            self.role_settings.setdefault(m.group(1), []).append(f"{m.group(2)}={m.group(3).strip(chr(39))}")
        m = re.fullmatch(r'ALTER ROLE "([a-z0-9_]+)" NOLOGIN', text)
        if m and m.group(1) in self.roles:
            self.roles[m.group(1)]["rolcanlogin"] = False
        m = re.fullmatch(r'DROP OWNED BY "([a-z0-9_]+)"', text)
        if m and "residual_privilege" not in self.faults:
            for db, acl in self.acls.items():
                self.acls[db] = re.sub(rf",{m.group(1)}=[^,}}]*", "", acl)
        if text == ROLE_SQL["roles"]:
            return Cursor([(n, *(a[c] for c in ROLE_COLS)) for n, a in sorted(self.roles.items())])
        if text == ROLE_SQL["memberships"]:
            return Cursor(sorted((r, mem, False) for r, mem in self.members))
        if text == ROLE_SQL["role_settings"]:
            return Cursor(sorted(("", r, "{" + ",".join(v) + "}")
                                 for r, v in self.role_settings.items()))
        if text == ROLE_SQL["database_acls"]:
            return Cursor(sorted(self.acls.items()))
        if text == ch.PREFLIGHT[0]:
            wrong = {"log_statement": "ddl"} if "preflight_bad" in self.faults else {}
            return Cursor(sorted({**ch.EXPECTED_SETTINGS, **wrong}.items()))
        if text == ch.PREFLIGHT[1]:
            return Cursor([(0,)])
        if text == ch.PREFLIGHT[2]:
            return Cursor([(TEST_DB, ADMIN_USER, True, 160015)])
        if text.startswith("SELECT md5("):
            return Cursor([("d",)])
        m = re.fullmatch(r'SELECT count\(\*\) FROM ds_run\."([a-z_]+)"( WHERE .*)?', text, re.S)
        if m:
            return Cursor([(1 if m.group(2) else len(self.tables.get(m.group(1), [])),)])
        if text == ga.ACTIVITY_SQL:
            excluded = set(params[0])
            return Cursor([(s.pid, s.user, s.db, s.app, "client backend", "") for s in list(self.sessions.values())
                           if s.pid not in excluded])
        if text == ga.TERMINATE_SQL:
            return Cursor([(self.terminate(int(params[0])),)])
        if text == ga.CANCEL_SQL:
            return Cursor([(True,)])
        if text == ga.PREPARED_SQL:
            return Cursor([])
        if text == ga.DB_EXISTS_SQL:
            return Cursor([] if "missing_test_database" in self.faults else [(1,)])
        if text.startswith("DROP SCHEMA IF EXISTS ds_run"):
            self.tables = {}
        if "CREATE TABLE events" in text:
            self.tables = {"events": [], "reservations": [], "rejections": []}
        m = re.fullmatch(r'INSERT INTO "([a-z_]+)" \(([^)]*)\) VALUES \(.*\)', text)
        if m:
            if "seed_fails" in self.faults and conn.session.app == ga.HARNESS_APP:  # Gate 2A's reset only
                raise psycopg.errors.UniqueViolation("simulated: duplicate key")
            cols = [c.strip().strip('"') for c in m.group(2).split(",")]
            row = dict(zip(cols, params, strict=True))
            if "parent_seed_differs" in self.faults and conn.session.app.startswith("ds_parent_op0r"):
                row["reserved"] = 1
            self.tables[m.group(1)].append(row)
        if text == ga.TABLES_SQL:
            return Cursor([(t,) for t in sorted(self.tables)])
        m = re.fullmatch(r'SELECT \* FROM "([a-z_]+)" ORDER BY 1', text)
        if m:
            rows = sorted(self.tables[m.group(1)], key=lambda r: r["id"])
            names = sorted(rows[0]) if rows else ["id"]
            if m.group(1) == "events":
                names = ["id", "capacity", "reserved"]
            return Cursor([tuple(r[n] for n in names) for r in rows], names)
        m = re.fullmatch(r'SELECT row_to_json\(t\)::text FROM ds_run\."([a-z_]+)" t ORDER BY 1', text)
        if m:
            return Cursor(sorted((json.dumps(r, sort_keys=True),) for r in self.tables[m.group(1)]))
        if text == ga.LOGFILE_SQL:
            return Cursor([(LOG_PATH,)])
        if text == ga.STAT_SQL:
            return Cursor([(len(self.data),)])
        if text == ga.READ_SQL:
            _path, offset, length = params
            data = bytes(self.data[offset:offset + length])
            if "incomplete_flush" in self.faults and conn.session.app == pr.LOG_READER_APP:
                data = data[:max(0, len(data) - 40)]
            return Cursor([(data,)])
        m = re.fullmatch(r"SELECT '([A-Za-z0-9_]+)'", text)
        if m:
            return Cursor([(m.group(1),)])
        if text == ga.COUNTER_SQL:
            return Cursor([(self.connections,)])
        m = re.match(r'CREATE ROLE "([a-z0-9_]+)" LOGIN .* CONNECTION LIMIT (\d+) IN ROLE "([a-z_]+)"', text)
        if m:
            if "create_role_fails" in self.faults:
                raise psycopg.errors.DuplicateObject("simulated: role already exists")
            self.roles[m.group(1)] = {**RUNNER_ATTRS, "rolcanlogin": True, "rolconnlimit": int(m.group(2))}
            self.members.add((m.group(3), m.group(1)))
        m = re.fullmatch(r'DROP ROLE "([a-z0-9_]+)"', text)
        if m:
            if "role_drop_fails" in self.faults:
                raise psycopg.errors.DependentObjectsStillExist("simulated: role is still referenced")
            if "residual_role" not in self.faults:
                self.roles.pop(m.group(1), None)
                self.members = {(r, mem) for r, mem in self.members if mem != m.group(1)}
                self.role_settings.pop(m.group(1), None)
        if text == ga.ROLE_COUNT_SQL:
            return Cursor([(int(params[0] in self.roles),)])
        if text == ga.REGISTRATION_SQL:
            return Cursor([(s.pid, s.user) for p in params[0] if (s := self.sessions.get(p))])
        if text == pr.READER_POLL:
            return Cursor([(sum(s.app == params[0] for s in self.sessions.values()),)])
        if text == serverlog.SESSION_CHECK_SQL:
            return Cursor([()])
        if text == ga.ADVISORY_LOCK_SQL:
            return Cursor([("",)])
        return Cursor([])


def command_tag(text: str) -> str:
    words = text.split()
    if not words or words[0].upper() == "SELECT":
        return "SELECT 1"
    return " ".join(w.upper() for w in words[:2])


class FakeConn:
    """A psycopg-like connection to the fake server."""

    def __init__(self, server: FakeServer, session: Session, threshold: int | None) -> None:
        self.server, self.session, self._threshold = server, session, threshold
        self.prep = pm.PreparedSession(threshold)
        self.info, self.pgconn = Info(session), PgConn()

    @property
    def prepare_threshold(self) -> int | None:
        return self._threshold

    @prepare_threshold.setter
    def prepare_threshold(self, value: int | None) -> None:
        self._threshold = value
        if "prepare_ignored" not in self.server.faults:  # a lying connection: reports None, keeps preparing
            self.prep.manager.prepare_threshold = value

    @property
    def closed(self) -> bool:
        return not self.session.alive

    def close(self) -> None:
        self.server.disconnect(self.session)

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self):
        self.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.execute("ROLLBACK")
            raise
        self.execute("COMMIT")

    def rollback(self) -> None:
        self.execute("ROLLBACK")

    def execute(self, query: Any, params: Any = None) -> Cursor:
        s = self.session
        if not s.alive:
            raise psycopg.OperationalError("the connection is closed")
        client = query if isinstance(query, str) else query.as_string(None)
        values = tuple(params) if params is not None else None
        multi = client.count(";") > 1 and not client.startswith("DO $$")
        entries = self.prep.execute(pm.Op(client, values, results=3 if multi else 1, command=command_tag(client)))
        text = entries[0][1]
        detail = ""
        if values:
            detail = "parameters: " + ", ".join(f"${i} = {ga.literal(v)}" for i, v in enumerate(values, 1))
        if text == "RESET log_statement":
            if "reset_fails" in self.server.faults:
                raise psycopg.errors.InternalError("simulated: RESET failed")
            s.logging = True
            return Cursor([])
        if s.logging:
            for verb, logged in entries[:1]:
                s.log(f"{verb}: {logged}", detail=detail)
        try:
            out = self.server.answer(self, text, values or ())
        except psycopg.Error as exc:
            s.log(str(exc), severity="ERROR")
            raise
        if s.logging:
            for verb, logged in entries[1:]:
                s.log(f"{verb}: {logged}")
        if text == ga.LOG_OFF_SQL:
            s.logging = False
        for extra in self.server.inject.get(text, []):
            s.log(f"statement: {extra}")
        for prefix, callback in list(self.server.hooks):
            if text.startswith(prefix):
                callback(self)
        return out


# ------------------------------------------------------------------ the fake Gate 2A child


class FakeProc:
    """Stands in for the worker process. ``behaviour``: clean, timeout, linger (a session survives termination)."""

    def __init__(self, server: FakeServer, runner: SupervisedRunner, ctx: Any, plan: dict[str, Any],
                 behaviour: str) -> None:
        self.server, self.runner, self.ctx, self.plan, self.behaviour = server, runner, ctx, plan, behaviour
        self.pid, self.returncode, self.finished = 999_999_999, None, False
        self.read_end, self.write_end = os.pipe()
        login, run = plan["login"], plan["login"]["run_id"]
        self.monitor = server.connect(login["user"], login["dbname"], f"ds:{run}:monitor")
        self.monitor.execute(serverlog.SESSION_CHECK_SQL)
        self.actors = {}
        if plan["mode"] != "plain":
            for actor in plan["actors"]:
                conn = server.connect(login["user"], login["dbname"], f"ds:{run}:{actor['name']}")
                conn.execute(serverlog.SESSION_CHECK_SQL)
                self.actors[actor["name"]] = conn
        if "unregistered_child" in server.faults:
            server.connect(login["user"], login["dbname"], f"ds:{run}:stranger").execute("SELECT 1")
        checks = runner._expected_checks(ctx)  # the honest child's report
        names = [*self.actors, "monitor"]
        self.send({"type": "ready", "pids": {n: c.session.pid for n, c in self.actors.items()},
                   "monitor_pid": self.monitor.session.pid, "checks": {n: dict(checks) for n in names}})
        self.stdin = self

    def send(self, message: dict[str, Any]) -> None:
        os.write(self.write_end, (json.dumps(message) + "\n").encode())

    def write(self, line: str) -> None:
        if line.strip() != "go" or self.behaviour == "timeout" or self.finished:
            return
        self.finished = True
        if self.behaviour == "worker_fails":  # the worker reports its own failure, as worker.main does
            for conn in [*self.actors.values(), self.monitor]:
                conn.close()
            self.send({"type": "failed", "error": "RuntimeError", "detail": "simulated application failure"})
            self.kill()
            return
        for conn in [*self.actors.values(), self.monitor]:
            if conn.session.pid not in self.server.unkillable:  # a lingering session outlives the child
                conn.close()
        trace = LiveTrace(scenario=self.plan["name"], requested_schedule=self.plan["schedule"],
                          chosen_schedule=self.plan["schedule"],
                          connections={n: c.session.pid for n, c in self.actors.items()},
                          actors=[ActorResult(actor=n, outcome="returned") for n in self.actors])
        self.send({"type": "result", "trace": trace.model_dump(mode="json", exclude={"evidence"})})
        self.kill()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = 0 if self.finished else -9
            for conn in [*self.actors.values(), self.monitor]:
                if conn.session.pid not in self.server.unkillable:
                    conn.close()
            os.close(self.write_end)

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("worker", timeout or 0)
        return self.returncode


def install(monkeypatch, server: FakeServer, behaviour: str = "clean") -> list[FakeProc]:
    """Route psycopg.connect to the fake server and replace the three process-level supervisor steps."""
    procs: list[FakeProc] = []
    monkeypatch.setattr(psycopg, "connect", server.connect_from_dsn)

    def spawn(runner, ctx, plan, code_paths):
        proc = FakeProc(server, runner, ctx, plan, behaviour)
        ctx.proc, ctx.confinement = proc, "none (test fake)"
        procs.append(proc)
        if "linger" == behaviour:
            server.unkillable.add(next(iter(proc.actors.values())).session.pid)
        return proc, proc.read_end

    monkeypatch.setattr(SupervisedRunner, "_spawn", spawn)
    monkeypatch.setattr(SupervisedRunner, "_kill_group", staticmethod(lambda proc: proc.kill()))
    monkeypatch.setattr(SupervisedRunner, "_kill_survivors", lambda self, ctx, flag: None)
    return procs


__all__ = ["FakeServer", "FakeConn", "install", "sql"]
