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
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
PSYCOPG_DIR = os.path.dirname(os.path.abspath(psycopg.__file__))
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


def sanitize(value: Any) -> Any:
    """Integers, floats, booleans and NULL are kept exactly (they drive the handlers' arithmetic); strings and bytes
    are reduced to their length and a short digest, so no application text reaches the trace."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return {"str": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()[:12]}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes": len(bytes(value)), "sha256": hashlib.sha256(bytes(value)).hexdigest()[:12]}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    return {"type": type(value).__name__}


def call_site() -> str:
    """The first frame outside this module and psycopg: the application line that asked for the operation."""
    frame = sys._getframe(1)
    while frame is not None:
        path = os.path.abspath(frame.f_code.co_filename)
        if not (path.startswith(HERE + os.sep + "stepping") or path.startswith(PSYCOPG_DIR)
                or path.endswith(os.sep + "contextlib.py")):
            return f"{os.path.relpath(path)}:{frame.f_lineno}:{frame.f_code.co_name}"
        frame = frame.f_back
    return "<unknown>"


SQLSTATE_CLASSES = {"40001": "serialization_failure", "40P01": "deadlock_detected", "55P03": "lock_not_available",
                    "57014": "query_canceled", "23505": "unique_violation", "23503": "foreign_key_violation",
                    "25P02": "in_failed_transaction"}


def error_class(exc: BaseException) -> str:
    if isinstance(exc, Refused):
        return "refused"
    if isinstance(exc, psycopg.Error):
        return SQLSTATE_CLASSES.get(getattr(exc, "sqlstate", None) or "", "sql_error")
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
    source: str  # schedule, deferred, continuation


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

    def allows(self, text: str) -> bool:
        return normal_sql(text) in self.allowed_sql


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
        prov = {"app_version": actor.handler.app_version, "actor": actor.name, "handler": actor.handler.name,
                "invocation_id": actor.invocation_id, "migration_phase": self.migration_phase,
                "logical_op": actor.ops, "session": {"backend_pid": actor.backend_pid},
                "thread": threading.current_thread().name, "call_site": call_site(),
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
                cursor.scroll(0, mode="absolute")  # the handler reads the same rows itself
                op.rows = [sanitize(list(r)) for r in rows]
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
                if actor.conn is not None and actor.conn.info.transaction_status != TX.IDLE:
                    psycopg.Connection.rollback(actor.conn)  # cleanup after the handler, not an application step
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

    def _choose(self, enabled: list[str], blocked: dict[str, list[str]], pointer: list[int]) -> tuple[str | None, str,
                                                                                                    str | None]:
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


def actor_connection_factory(conninfo: dict[str, Any], limits: Limits, *,
                             search_path: str = "public") -> Callable[[], SteppedConnection]:
    """Fresh SteppedConnections with explicit limits. Autocommit, so every transaction boundary is a gated call."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", search_path):
        raise Refused("search_path must be a plain schema name")
    options = (f"-c statement_timeout={limits.statement_ms} -c lock_timeout={limits.lock_ms} "
               f"-c deadlock_timeout={limits.deadlock_ms} "
               f"-c idle_in_transaction_session_timeout={limits.idle_in_transaction_ms} -c search_path={search_path}")

    def connect() -> SteppedConnection:
        conn = SteppedConnection.connect(**conninfo, autocommit=True, prepare_threshold=None,
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
        key = tuple(trace.chosen)
        if key in seen:
            continue
        seen.add(key)
        traces.append(trace)
        for d in trace.decisions[len(prefix):]:
            for alt in reversed(d.enabled):
                if alt != d.chosen:
                    frontier.append(list(trace.chosen[:d.index]) + [alt])
    return traces, not frontier


__all__ = ["Actor", "Decision", "Handler", "Limits", "OpRecord", "Refused", "Run", "RunAborted", "SteppedConnection",
           "SteppedCursor", "Trace", "actor_connection_factory", "explore", "normal_sql", "sanitize", "statement_id"]
