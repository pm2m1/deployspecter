"""EXPERIMENTAL: driver-level stepping of real Python application handlers on psycopg 3.

Each actor runs a real handler function in its own thread, on its own ``SteppedConnection``. Every database operation
the handler makes stops at a gate before it reaches the server:

    A. request   the handler asks to run one operation (the gate records it, with provenance)
    B. grant     the controller, following the schedule, lets exactly one actor proceed
    C. execute   the real psycopg call runs on the server; its result is recorded
    D. compute   the handler's own Python code runs on that result, until its next request

Nothing is replayed: a later write's parameters are whatever the handler computes from what it actually read under
that schedule.

Intercepted call paths: ``Connection.execute`` (it creates its cursor through ``cursor_factory``), ``Cursor.execute``,
``Cursor.executemany``, ``Connection.commit``, ``Connection.rollback``, and ``Connection.transaction`` (whose BEGIN,
SAVEPOINT, COMMIT and ROLLBACK go through psycopg's internal ``_exec_command`` and so bypass any cursor hook: they are
gated at the context manager's entry and exit). Implicit BEGINs in non-autocommit mode are part of the gated execute
and recorded from the transaction status before and after.

Refused, never silently executed: server-side cursors, ``copy``, ``stream``, pipelines, use from another thread, and any
statement outside the handler's allowlist. Not interceptable at this layer: raw ``pgconn`` calls and connections the
handler opens itself; the registry never gives a handler anything but its SteppedConnection, and the PostgreSQL log is
the only corroboration for such bypasses.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
PSYCOPG_DIR = os.path.dirname(os.path.abspath(psycopg.__file__))


def _package_dir(name: str) -> str | None:
    spec = importlib.util.find_spec(name)
    return os.path.dirname(os.path.abspath(spec.origin)) if spec and spec.origin else None


# Frameworks the call site looks through, to the application line that called them. Not imported here.
FRAMEWORK_DIRS = {n: d for n in ("sqlalchemy",) if (d := _package_dir(n))}
TX = psycopg.pq.TransactionStatus
BUSY = ("running", "granted", "executing")  # states in which an actor will act without a new grant


class Refused(RuntimeError):
    """An operation the prototype doesn't support or the handler isn't allowed. It never reaches the server."""


class RunAborted(BaseException):  # noqa: N818 - BaseException so a handler's ``except Exception`` can't swallow it
    """The run was halted (timeout, divergence, external blocker). Raised inside actors at their next gate."""


# ------------------------------------------------------------------ provenance and sanitizing


def normal_sql(text: str) -> str:
    return " ".join(text.split())


def statement_id(text: str) -> str:
    return hashlib.sha256(normal_sql(text).encode()).hexdigest()[:12]


_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
_US = datetime.timedelta(microseconds=1)


def sanitize(value: Any) -> Any:
    """Integers, floats, booleans and NULL are kept exactly (they drive the handlers' arithmetic); strings and bytes
    are reduced to their length and a short digest, so no application text reaches the trace. Date/time values keep
    exactly what PostgreSQL's comparison needs: an aware datetime its instant (microseconds since the epoch), a naive
    one its wall-clock value, a time its wall clock (and offset), a timedelta its days and the rest in microseconds.
    A UUID, like a string, is kept only as a digest of its canonical form."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return {"timestamptz_us": (value - _EPOCH) // _US}
        return {"timestamp": value.isoformat(sep=" ", timespec="microseconds")}
    if isinstance(value, datetime.date):
        return {"date": value.isoformat()}
    if isinstance(value, datetime.time):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return {"timetz": value.replace(tzinfo=None).isoformat(timespec="microseconds"),
                    "offset_s": int(value.utcoffset().total_seconds())}
        return {"time": value.isoformat(timespec="microseconds")}
    if isinstance(value, datetime.timedelta):
        return {"interval_days": value.days, "interval_us": value.seconds * 1_000_000 + value.microseconds}
    if isinstance(value, uuid.UUID):
        return {"uuid_sha256": hashlib.sha256(str(value).encode()).hexdigest()[:12]}
    if isinstance(value, str):
        return {"str": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()[:12]}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes": len(bytes(value)), "sha256": hashlib.sha256(bytes(value)).hexdigest()[:12]}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    return {"type": type(value).__name__}


def call_sites() -> tuple[str, str | None]:
    """The application line that asked for the operation (the first frame outside this module, psycopg and any known
    framework), and the framework entry point it called, if any (``sqlalchemy/orm/session.py:commit``, say)."""
    frame = sys._getframe(1)
    entry: str | None = None
    while frame is not None:
        path = os.path.abspath(frame.f_code.co_filename)
        framework = next((n for n, d in FRAMEWORK_DIRS.items() if path.startswith(d + os.sep)), None)
        if framework is not None:
            entry = f"{framework}/{os.path.relpath(path, FRAMEWORK_DIRS[framework])}:{frame.f_code.co_name}"
        elif entry is not None and frame.f_code.co_filename.startswith("<"):
            pass  # code a framework generated at import time (SQLAlchemy's decorators): still the framework
        elif not (path.startswith(HERE + os.sep + "stepping") or path.startswith(PSYCOPG_DIR)
                  or path.endswith(os.sep + "contextlib.py")):
            return f"{os.path.relpath(path)}:{frame.f_lineno}:{frame.f_code.co_name}", entry
        frame = frame.f_back
    return "<unknown>", entry


def call_site() -> str:
    return call_sites()[0]


SQLSTATE_CLASSES = {"40001": "serialization_failure", "40P01": "deadlock_detected", "55P03": "lock_not_available",
                    "57014": "query_canceled", "23505": "unique_violation", "23503": "foreign_key_violation",
                    "25P02": "in_failed_transaction"}


def error_class(exc: BaseException) -> str:
    """Looks through wrappers (a framework re-raising a driver error or a refusal ``from`` it) to the cause."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, Refused):
            return "refused"
        if isinstance(cur, psycopg.Error):
            return SQLSTATE_CLASSES.get(getattr(cur, "sqlstate", None) or "", "sql_error")
        cur = cur.__cause__ or cur.__context__
    return "application_exception"


@dataclass
class OpRecord:
    seq: int
    actor: str
    kind: str  # execute, executemany, commit, rollback, transaction_enter, transaction_exit
    sql: str
    statement_id: str
    params: Any
    provenance: dict[str, Any]
    grant: int | None = None
    decision: int | None = None
    outcome: str = "requested"  # ok, error, refused, aborted
    error_class: str | None = None
    sqlstate: str | None = None
    rowcount: int | None = None
    rows: list[Any] | None = None
    txn_before: str | None = None
    txn_after: str | None = None
    implicit_begin: bool = False
    blocked_by: list[str] = field(default_factory=list)
    requested_ms: float = 0.0
    completed_ms: float | None = None


@dataclass
class Decision:
    index: int
    enabled: list[str]
    blocked: dict[str, list[str]]
    finished: list[str]
    requested: str | None
    chosen: str
    source: str  # schedule, deferred, continuation, setup


# ------------------------------------------------------------------ the stepped driver objects


class SteppedCursor(psycopg.Cursor):
    def _actor(self) -> Actor:
        return self.connection._ds_actor  # type: ignore[attr-defined]

    def execute(self, query: Any, params: Any = None, *, prepare: bool | None = None, binary: bool | None = None):
        text = query if isinstance(query, str) else query.as_string(self.connection)
        actor = self._actor()

        def run() -> Any:
            psycopg.Cursor.execute(self, query, params, prepare=prepare, binary=binary)
            return self

        actor.run.gate(actor, "execute", text, params, run, cursor=self)
        return self

    def executemany(self, query: Any, params_seq: Any, *, returning: bool = False):
        text = query if isinstance(query, str) else query.as_string(self.connection)
        params_seq = list(params_seq)
        actor = self._actor()
        actor.run.gate(actor, "executemany", text, params_seq,
                       lambda: psycopg.Cursor.executemany(self, query, params_seq, returning=returning))

    def copy(self, *_a: Any, **_k: Any):
        raise Refused("COPY isn't supported by the stepping prototype")

    def stream(self, *_a: Any, **_k: Any):
        raise Refused("streaming cursors aren't supported by the stepping prototype")


class SteppedConnection(psycopg.Connection):
    """A psycopg 3 Connection whose every supported database operation passes the actor's gate."""

    _ds_actor: Actor

    def cursor(self, *args: Any, **kwargs: Any):
        if args or kwargs.get("name"):
            raise Refused("server-side (named) cursors aren't supported by the stepping prototype")
        return super().cursor(**kwargs)

    def commit(self) -> None:
        a = self._ds_actor
        a.run.gate(a, "commit", "COMMIT", None, lambda: psycopg.Connection.commit(self))

    def rollback(self) -> None:
        a = self._ds_actor
        a.run.gate(a, "rollback", "ROLLBACK", None, lambda: psycopg.Connection.rollback(self))

    @contextlib.contextmanager
    def transaction(self, savepoint_name: str | None = None, force_rollback: bool = False) -> Iterator[Any]:
        a = self._ds_actor
        nested = self.info.transaction_status != TX.IDLE
        label = f"SAVEPOINT {savepoint_name or '<auto>'}" if nested else "BEGIN"
        cm = psycopg.Connection.transaction(self, savepoint_name, force_rollback)
        tx = a.run.gate(a, "transaction_enter", label, None, cm.__enter__)
        try:
            yield tx
        except BaseException as exc:
            exit_label = "ROLLBACK TO SAVEPOINT + RELEASE" if nested else "ROLLBACK"
            err = exc
            a.run.gate(a, "transaction_exit", exit_label, None,
                       lambda: cm.__exit__(type(err), err, err.__traceback__), aborting=isinstance(err, RunAborted))
            raise
        else:
            exit_label = ("RELEASE SAVEPOINT" if nested else "COMMIT") if not force_rollback else "ROLLBACK"
            a.run.gate(a, "transaction_exit", exit_label, None, lambda: cm.__exit__(None, None, None))

    @contextlib.contextmanager
    def pipeline(self) -> Iterator[Any]:
        raise Refused("pipeline mode isn't supported by the stepping prototype")
        yield  # pragma: no cover


# ------------------------------------------------------------------ actors and the run


@dataclass
class Handler:
    """A trusted, registered application function and the statements it may send (normalized text)."""
    app_version: str
    name: str
    fn: Callable[..., Any]
    allowed_sql: frozenset[str]
    args: dict[str, type]
    touches_migration: frozenset[str] = frozenset()  # statement ids that read or write migration-added columns
    # Framework setup statements (a dialect's first-connect queries): read-only, lock-free reads of session settings.
    # Opting in also lets the controller grant, without a decision point, these statements, a COMMIT or ROLLBACK that
    # sends nothing (no transaction open), and the end of a transaction that ran only setup statements. All of these
    # commute with every other actor's operations, so interleaving them can't change the outcome. Still gated,
    # recorded and corroborated.
    setup_sql: frozenset[str] = frozenset()
    # Async engine only, opt-in for a framework that runs several tasks on one connection (a job queue's worker,
    # heartbeat and listener): such tasks queue for the actor's connection in arrival order, as psycopg's own
    # connection lock would make them, instead of being refused as concurrent use. Each operation is still gated.
    serialize_tasks: bool = False

    def allows(self, text: str) -> bool:
        return normal_sql(text) in self.allowed_sql or normal_sql(text) in self.setup_sql


@dataclass
class Actor:
    name: str
    handler: Handler
    args: dict[str, Any]
    run: Run = None  # type: ignore[assignment]
    conn: SteppedConnection | None = None
    thread: threading.Thread | None = None
    state: str = "new"  # new, running, pending, executing, blocked, finished, errored, aborted
    request: OpRecord | None = None
    granted: bool = False
    result: Any = None
    exception: str | None = None
    exception_class: str | None = None
    ops: int = 0
    txn: int = 0
    backend_pid: int | None = None
    invocation_id: str = ""
    setup_only_txn: bool = False  # the open transaction has run only the handler's setup statements


@dataclass
class Limits:
    statement_ms: int = 5000
    lock_ms: int = 3000
    deadlock_ms: int = 100
    idle_in_transaction_ms: int = 15000
    settle_s: float = 0.05
    python_step_s: float = 5.0
    run_s: float = 30.0


@dataclass
class Trace:
    run_id: str
    schedule: list[str]
    chosen: list[str]
    decisions: list[Decision]
    records: list[OpRecord]
    actors: dict[str, dict[str, Any]]
    outcome: str  # completed, timeout, schedule_divergence, external_blocker, refused
    diagnostics: list[str]
    migration_phase: str
    # The handlers' own return values, for the harness's logic only; never serialized (they may hold app data).
    raw_results: dict[str, Any] = field(default_factory=dict, repr=False)

    def fingerprint(self) -> str:
        """Observable behaviour without clocks or pids, for comparing repeated runs."""
        drop = {"requested_ms", "completed_ms", "provenance"}
        payload = {"chosen": self.chosen,
                   "records": [{k: v for k, v in asdict(r).items() if k not in drop} for r in self.records],
                   "actors": {n: {k: a[k] for k in ("state", "result", "exception_class")}
                              for n, a in sorted(self.actors.items())}}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "schedule": self.schedule, "chosen": self.chosen,
                "decisions": [asdict(d) for d in self.decisions], "records": [asdict(r) for r in self.records],
                "actors": self.actors, "outcome": self.outcome, "diagnostics": self.diagnostics,
                "migration_phase": self.migration_phase, "fingerprint": self.fingerprint()}


class Run:
    """One execution of a set of actors under one schedule. Never retried."""

    def __init__(self, actors: Sequence[Actor], connect: Callable[[], SteppedConnection], monitor: Any, *,
                 schedule: Sequence[str] = (), strict: bool = False, limits: Limits | None = None,
                 run_id: str = "", migration_phase: str = "") -> None:
        names = [a.name for a in actors]
        if len(set(names)) != len(names):
            raise Refused("actor names must be unique")
        unknown = sorted(set(schedule) - set(names))
        if unknown:
            raise Refused(f"the schedule names unknown actors: {unknown}")
        self.actors = list(actors)
        self.by_name = {a.name: a for a in actors}
        self.connect, self.monitor = connect, monitor
        self.schedule, self.strict = list(schedule), strict
        self.limits = limits or Limits()
        self.run_id, self.migration_phase = run_id, migration_phase
        self.cv = threading.Condition()
        self.records: list[OpRecord] = []
        self.decisions: list[Decision] = []
        self.diagnostics: list[str] = []
        self.grants = 0
        self.aborted = False
        self.t0 = time.monotonic()

    # -- the gate, in the actor's thread

    def gate(self, actor: Actor, kind: str, text: str, params: Any, call: Callable[[], Any], *, cursor: Any = None,
             aborting: bool = False) -> Any:
        if threading.current_thread() is not actor.thread:
            raise Refused(f"{actor.name}'s connection was used from another thread")
        if self.aborted and not aborting:
            raise RunAborted("the run was halted")
        site, entry = call_sites()
        prov = {"app_version": actor.handler.app_version, "actor": actor.name, "handler": actor.handler.name,
                "invocation_id": actor.invocation_id, "migration_phase": self.migration_phase,
                "logical_op": actor.ops, "session": {"backend_pid": actor.backend_pid},
                "thread": threading.current_thread().name, "call_site": site, "framework_entry": entry,
                "txn": None}
        op = OpRecord(seq=-1, actor=actor.name, kind=kind, sql=normal_sql(text), statement_id=statement_id(text),
                      params=sanitize(params), provenance=prov, requested_ms=self._ms())
        actor.ops += 1
        if kind in ("execute", "executemany") and not actor.handler.allows(text):
            op.outcome, op.error_class = "refused", "unauthorized_sql"
            self._append(op)
            raise Refused(f"{actor.handler.app_version}.{actor.handler.name} may not send: {normal_sql(text)[:80]}")
        if not aborting:
            with self.cv:
                actor.state, actor.request = "pending", op
                self.cv.notify_all()
                while not actor.granted and not self.aborted:
                    self.cv.wait(0.05)
                if self.aborted:
                    actor.request = None
                    op.outcome = "aborted"
                    self._append(op)
                    raise RunAborted("the run was halted before this operation was granted")
                actor.granted, actor.state, actor.request = False, "executing", None
                op.grant, op.decision = self.grants, len(self.decisions) - 1
                self.grants += 1
        conn = actor.conn
        op.txn_before = conn.info.transaction_status.name
        if op.txn_before == "IDLE" and kind in ("execute", "executemany", "transaction_enter"):
            actor.txn += 1
        try:
            out = call()
            op.outcome = "ok"
            if cursor is not None and cursor.description is not None:
                rows = cursor.fetchall()
                if rows:  # psycopg refuses to scroll an empty result; an empty one is already at its end
                    cursor.scroll(0, mode="absolute")  # the handler reads the same rows itself
                # a dict_row (or any mapping) row keeps its column names; list(mapping) would give only the keys
                op.rows = [sanitize(dict(r)) if isinstance(r, Mapping) else sanitize(list(r)) for r in rows]
            if cursor is not None:
                op.rowcount = cursor.rowcount
            return out
        except BaseException as exc:
            op.outcome = "aborted" if isinstance(exc, RunAborted) else "error"
            op.error_class = error_class(exc)
            op.sqlstate = getattr(exc, "sqlstate", None)
            raise
        finally:
            op.txn_after = conn.info.transaction_status.name if not conn.closed else "CLOSED"
            op.implicit_begin = op.txn_before == "IDLE" and op.txn_after == "INTRANS" and kind == "execute"
            if op.txn_after != "INTRANS":
                actor.setup_only_txn = False
            elif kind == "execute" and op.outcome == "ok":
                actor.setup_only_txn = op.sql in actor.handler.setup_sql and (
                    op.txn_before == "IDLE" or actor.setup_only_txn)
            else:
                actor.setup_only_txn = False
            if op.txn_before != "IDLE" or op.txn_after != "IDLE":
                prov["txn"] = f"{actor.name}:{actor.txn}"
            op.completed_ms = self._ms()
            with self.cv:
                if actor.state in ("executing", "blocked"):
                    actor.state = "running"
                self._append(op)
                self.cv.notify_all()

    def _append(self, op: OpRecord) -> None:
        op.seq = len(self.records)
        self.records.append(op)

    def _ms(self) -> float:
        return round((time.monotonic() - self.t0) * 1000, 2)

    # -- the actor threads

    def _actor_main(self, actor: Actor) -> None:
        try:
            actor.result = actor.handler.fn(actor.conn, **actor.args)
            with self.cv:
                actor.state = "finished"
        except RunAborted:
            with self.cv:
                actor.state = "aborted"
        except BaseException as exc:  # an application error is a result, recorded, not a crash of the run
            with self.cv:
                actor.state, actor.exception = "errored", f"{type(exc).__name__}: {str(exc)[:200]}"
                actor.exception_class = error_class(exc)
        finally:
            with contextlib.suppress(Exception):
                status = actor.conn.info.transaction_status if actor.conn is not None else None
                if status in (TX.INTRANS, TX.INERROR):
                    # cleanup after the handler, not an application step: recorded (ungranted) so that the server
                    # log's ROLLBACK is explained, never silent
                    op = OpRecord(seq=-1, actor=actor.name, kind="cleanup_rollback", sql="ROLLBACK",
                                  statement_id=statement_id("ROLLBACK"), params=None,
                                  provenance={"actor": actor.name, "handler": actor.handler.name,
                                              "app_version": actor.handler.app_version,
                                              "invocation_id": actor.invocation_id, "call_site": "<harness cleanup>",
                                              "framework_entry": None, "txn": f"{actor.name}:{actor.txn}"},
                                  txn_before=status.name, requested_ms=self._ms())
                    try:
                        psycopg.Connection.rollback(actor.conn)
                        op.outcome = "ok"
                    except Exception as exc:
                        op.outcome, op.error_class = "error", error_class(exc)
                    op.txn_after = actor.conn.info.transaction_status.name if not actor.conn.closed else "CLOSED"
                    op.completed_ms = self._ms()
                    with self.cv:
                        self._append(op)
            with self.cv:
                self.cv.notify_all()

    # -- the controller

    def _blockers(self, actor: Actor) -> list[str]:
        pids = self.monitor.execute("SELECT pg_blocking_pids(%s)", (actor.backend_pid,)).fetchone()[0] or []
        names = {a.backend_pid: a.name for a in self.actors}
        return [names.get(p, f"external:{p}") for p in pids]

    def _halt(self, outcome: str, detail: str) -> str:
        self.diagnostics.append(detail)
        with self.cv:
            self.aborted = True
            executing = [a for a in self.actors if a.state in ("executing", "blocked")]
            self.cv.notify_all()
        for a in executing:
            with contextlib.suppress(Exception):
                self.monitor.execute("SELECT pg_cancel_backend(%s)", (a.backend_pid,))
        return outcome

    def _commutes(self, actor: Actor) -> bool:
        """A pending operation the controller may grant without a decision point (see ``Handler.setup_sql``)."""
        req, h = actor.request, actor.handler
        if req is None or not h.setup_sql:
            return False
        if req.kind == "execute":
            return req.sql in h.setup_sql
        if req.kind in ("commit", "rollback"):  # the actor is parked at its gate, so its connection is idle here
            return actor.conn.info.transaction_status == TX.IDLE or actor.setup_only_txn
        return False

    def _choose(self, enabled: list[str], blocked: dict[str, list[str]], pointer: list[int]) -> tuple[str | None, str,
                                                                                                    str | None]:
        setup = [n for n in enabled if self._commutes(self.by_name[n])]
        if setup:
            return setup[0], "setup", None  # consumes no schedule entry
        while pointer[0] < len(self.schedule):
            want = self.schedule[pointer[0]]
            state = self.by_name[want].state
            if want in enabled:
                pointer[0] += 1
                return want, "schedule", want
            if state in ("finished", "errored", "aborted"):
                pointer[0] += 1  # the scheduled actor has no more operations; skip the entry
                continue
            if self.strict:
                return None, "divergence", want
            return enabled[0], "deferred", want  # the wanted actor is blocked or still computing
        return enabled[0], "continuation", None

    def execute(self) -> Trace:
        lim = self.limits
        for a in self.actors:
            conn = self.connect()
            conn._ds_actor = a
            a.conn, a.run, a.backend_pid = conn, self, conn.info.backend_pid
            a.invocation_id = f"{self.run_id}:{a.name}"
            a.thread = threading.Thread(target=self._actor_main, args=(a,), name=f"ds-actor-{a.name}", daemon=True)
        for a in self.actors:
            a.state = "running"
            a.thread.start()
        deadline = self.t0 + lim.run_s
        pointer = [0]
        outcome = "completed"
        running_since: dict[str, float] = {}
        while True:
            if time.monotonic() > deadline:
                outcome = self._halt("timeout", f"run exceeded {lim.run_s} s")
                break
            with self.cv:
                self.cv.wait_for(lambda: all(a.state not in BUSY for a in self.actors), timeout=lim.settle_s)
                states = {a.name: a.state for a in self.actors}
            now = time.monotonic()
            for name, st in states.items():
                if st == "running":
                    running_since.setdefault(name, now)
                    if now - running_since[name] > lim.python_step_s:
                        outcome = self._halt("timeout", f"{name} computed for more than {lim.python_step_s} s")
                else:
                    running_since.pop(name, None)
            if outcome != "completed":
                break
            blocked: dict[str, list[str]] = {}
            for a in self.actors:
                if states[a.name] in ("executing", "blocked"):
                    who = self._blockers(a)
                    if who:
                        blocked[a.name] = who
                        with self.cv:
                            if a.state == "executing":
                                a.state = "blocked"
            if any(w.startswith("external:") for ws in blocked.values() for w in ws):
                outcome = self._halt("external_blocker", f"a session outside the run holds a lock: {blocked}")
                break
            with self.cv:
                states = {a.name: a.state for a in self.actors}
            if any(st in BUSY for st in states.values()):
                continue  # still settling; an executing op that isn't blocked is just slow
            enabled = [a.name for a in self.actors if states[a.name] == "pending"]
            if not enabled:
                if all(st in ("finished", "errored", "aborted") for st in states.values()):
                    break
                continue  # everyone left is blocked on a lock: PostgreSQL's lock_timeout or deadlock check decides
            chosen, source, wanted = self._choose(enabled, blocked, pointer)
            if chosen is None:
                outcome = self._halt("schedule_divergence", f"schedule wants {wanted}, which is {states[wanted]}")
                break
            if source == "setup":
                enabled = [chosen]  # nothing to explore: the operation commutes with every alternative
            self.decisions.append(Decision(len(self.decisions), enabled, blocked,
                                           [n for n, s in states.items() if s in ("finished", "errored")],
                                           wanted, chosen, source))
            with self.cv:  # "granted" is busy until the actor's thread takes it, so it can't be granted twice
                self.by_name[chosen].granted, self.by_name[chosen].state = True, "granted"
                self.cv.notify_all()
        for a in self.actors:
            a.thread.join(timeout=2.0)
            if a.thread.is_alive():
                self.diagnostics.append(f"{a.name}'s thread didn't stop")
        for a in self.actors:
            with contextlib.suppress(Exception):
                a.conn.close()
        return Trace(self.run_id, self.schedule, [d.chosen for d in self.decisions], self.decisions, self.records,
                     {a.name: {"handler": f"{a.handler.app_version}.{a.handler.name}", "args": sanitize(a.args),
                               "state": a.state, "result": sanitize(a.result), "exception": a.exception,
                               "exception_class": a.exception_class, "backend_pid": a.backend_pid,
                               "transactions": a.txn} for a in self.actors},
                     outcome, self.diagnostics, self.migration_phase,
                     raw_results={a.name: a.result for a in self.actors})


def actor_connection_factory(conninfo: dict[str, Any], limits: Limits, *, search_path: str = "public",
                             autocommit: bool = True) -> Callable[[], SteppedConnection]:
    """Fresh SteppedConnections with explicit limits. Autocommit by default, so every transaction boundary is a gated
    call; ``autocommit=False`` is the DBAPI default a framework such as SQLAlchemy expects, and its implicit BEGIN is
    part of the first gated execute of each transaction."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", search_path):
        raise Refused("search_path must be a plain schema name")
    options = (f"-c statement_timeout={limits.statement_ms} -c lock_timeout={limits.lock_ms} "
               f"-c deadlock_timeout={limits.deadlock_ms} "
               f"-c idle_in_transaction_session_timeout={limits.idle_in_transaction_ms} -c search_path={search_path}")

    def connect() -> SteppedConnection:
        conn = SteppedConnection.connect(**conninfo, autocommit=autocommit, prepare_threshold=None,
                                         cursor_factory=SteppedCursor, options=options,
                                         application_name="ds_mvp_actor")
        return conn

    return connect


# ------------------------------------------------------------------ bounded exploration


def explore(run_with: Callable[[list[str]], Trace], *, max_runs: int) -> tuple[list[Trace], bool]:
    """Depth-first over the scheduler's decision points, re-executing every run from the verified initial state.
    Returns the traces and whether the frontier was exhausted within the budget (not a completeness proof: it
    assumes each prefix leads to the same decision points when re-executed)."""
    frontier: list[list[str]] = [[]]
    seen: set[tuple[str, ...]] = set()
    traces: list[Trace] = []
    while frontier and len(traces) < max_runs:
        prefix = frontier.pop()
        trace = run_with(prefix)
        # setup grants consume no schedule entry and have no alternative, so they are not schedule positions
        points = [d for d in trace.decisions if d.source != "setup"]
        chosen = [d.chosen for d in points]
        key = tuple(chosen)
        if key in seen:
            continue
        seen.add(key)
        traces.append(trace)
        for i in range(len(prefix), len(points)):
            for alt in reversed(points[i].enabled):
                if alt != points[i].chosen:
                    frontier.append(chosen[:i] + [alt])
    return traces, not frontier


__all__ = ["Actor", "Decision", "Handler", "Limits", "OpRecord", "Refused", "Run", "RunAborted", "SteppedConnection",
           "SteppedCursor", "Trace", "actor_connection_factory", "explore", "normal_sql", "sanitize", "statement_id"]
