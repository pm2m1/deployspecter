"""Runs real application functions in actor threads and decides which one may touch the database next.

The controller admits one operation at a time, and only once the system is quiescent: every actor is waiting at its
gate, has finished, or has a statement PostgreSQL itself reports as blocked (``pg_blocking_pids``). A blocked statement
stays in flight while other actors continue, which is what lets the holder release the lock. If only blocked actors
remain, the controller waits for PostgreSQL to resolve them (commit, deadlock detector or timeout). It never declares
a deadlock itself.

Transactions overlap freely. Individual statements do not start at the same instant, because that would make the order
irreproducible. This is the same granularity as the frozen ``ScheduleRunner``. PostgreSQL internals are not controlled.
"""

from __future__ import annotations

import importlib
import queue
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg

from ..config import Settings
from ..database import RUN_SCHEMA, RUNNER_ROLE, Sandbox
from ..models import SeedRow
from .driver import (
    ActorChannel,
    OpCompleted,
    OpRefused,
    OpRequested,
    RunAborted,
    SteppedConnection,
    UnsupportedDriverOperation,
    _safe_params,
)
from .trace import INCONCLUSIVE, OUTCOME_PRIORITY, ActorResult, Decision, LiveTrace, OpRecord, Refusal

_ACTOR_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,30}$")
APP_NAME_PREFIX = "ds:live:"
_HARNESS_LIMITS = {"connection_lost", "lock_timeout", "statement_timeout", "idle_in_transaction_timeout"}
_NOTHING_TO_ROLL_BACK = {psycopg.pq.TransactionStatus.IDLE, psycopg.pq.TransactionStatus.UNKNOWN}

_ERROR_CATEGORIES = {
    "40P01": "deadlock",
    "40001": "serialization_failure",
    "55P03": "lock_timeout",
    "57014": "statement_timeout",
    "25P03": "idle_in_transaction_timeout",
}


def error_category(exc: BaseException) -> str:
    state = getattr(exc, "sqlstate", None)
    if state in _ERROR_CATEGORIES:
        return _ERROR_CATEGORIES[state]
    if (state or "").startswith(("08", "57P")) or isinstance(exc, psycopg.OperationalError) and state is None:
        return "connection_lost"
    return "execution_error" if isinstance(exc, psycopg.Error) else "python_exception"


@dataclass(frozen=True)
class LiveLimits:
    """Server limits use the same settings as the frozen sandbox. The others bound what the server cannot see."""

    statement_ms: int = 5000
    lock_ms: int = 3000
    deadlock_ms: int = 50
    idle_in_transaction_ms: int = 15000
    python_step_s: float = 5.0
    run_s: float = 30.0
    poll_s: float = 0.001
    watchdog_margin_s: float = 2.0
    abort_grace_s: float = 3.0

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: Any) -> LiveLimits:
        t = settings.timeouts
        return cls(
            statement_ms=overrides.pop("statement_ms", t.statement_ms),
            lock_ms=overrides.pop("lock_ms", t.lock_ms),
            deadlock_ms=overrides.pop("deadlock_ms", t.deadlock_ms),
            idle_in_transaction_ms=overrides.pop("idle_in_transaction_ms", t.idle_in_transaction_ms),
            **overrides,
        )

    @property
    def statement_watchdog_s(self) -> float:
        return max(self.statement_ms, self.lock_ms) / 1000 + self.watchdog_margin_s


def open_session(
    settings: Settings, name: str, limits: LiveLimits, *, autocommit: bool = False,
    isolation: psycopg.IsolationLevel | None = None, connection_class: type[psycopg.Connection] = SteppedConnection,
) -> psycopg.Connection:
    """The frozen sandbox's session setup, in the same order: restricted role, explicit timeouts, ``ds_run`` only.

    ``connection_class=psycopg.Connection`` gives the uninstrumented control an identical session.
    """
    conn = connection_class.connect(settings.test_dsn, autocommit=True, connect_timeout=5, prepare_threshold=None)
    try:
        dbname = conn.info.dbname
        if not dbname.endswith("_test"):
            raise RuntimeError(f"refusing to run application code on non-test database {dbname!r}")
        conn.execute("SELECT set_config('application_name', %s, false)", (f"{APP_NAME_PREFIX}{name}",))
        conn.execute(f"SET deadlock_timeout = {int(limits.deadlock_ms)}")
        conn.execute(f"SET statement_timeout = {int(limits.statement_ms)}")
        conn.execute(f"SET lock_timeout = {int(limits.lock_ms)}")
        conn.execute(f"SET idle_in_transaction_session_timeout = {int(limits.idle_in_transaction_ms)}")
        conn.execute(f"SET search_path = {RUN_SCHEMA}")
        conn.execute(f"SET ROLE {RUNNER_ROLE}")
        if isolation is not None:
            conn.isolation_level = isolation
        conn.autocommit = autocommit
    except BaseException:
        conn.close()
        raise
    return conn


class Host(Protocol):
    """What the controller needs from wherever it runs, so the same engine works in a test process or a child."""

    def open_actor(self, spec: LiveActor, limits: LiveLimits) -> SteppedConnection: ...

    def blockers(self, pids: list[int]) -> dict[int, list[int]]: ...

    def sessions_remaining(self, pids: list[int]) -> int: ...

    def terminate(self, pid: int) -> None: ...


class SandboxHost:
    """In-process host, for engine tests only. Actors log in as admin, so results here are never scoreable."""

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def open_actor(self, spec: LiveActor, limits: LiveLimits) -> SteppedConnection:
        return open_session(self.sandbox.settings, spec.name, limits, autocommit=spec.autocommit,
                            isolation=spec.isolation)

    def blockers(self, pids: list[int]) -> dict[int, list[int]]:
        return self.sandbox.blockers(pids)

    def sessions_remaining(self, pids: list[int]) -> int:
        return self.sandbox.monitor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE pid = ANY(%s)", (pids,)
        ).fetchone()[0]

    def terminate(self, pid: int) -> None:
        self.sandbox.monitor.execute("SELECT pg_terminate_backend(%s)", (pid,))


@dataclass(frozen=True)
class LiveActor:
    """A registered application function, called as ``entrypoint(conn, **kwargs)`` with an injected connection.

    A supervised run takes ``"module:function"``, because the function must be imported in a fresh child process.
    """

    name: str
    entrypoint: Callable[..., Any] | str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    autocommit: bool = False
    isolation: psycopg.IsolationLevel | None = None


@dataclass(frozen=True)
class LiveScenario:
    name: str
    setup_sql: str
    seed: list[SeedRow]
    actors: list[LiveActor]


class Schedule(Protocol):
    requested: list[str]

    def choose(self, enabled: list[str], finished: list[str], last: str | None) -> tuple[str | None, str | None, str]:
        """Return (chosen, requested, source). ``chosen`` is None if the schedule cannot be followed."""
        ...


class ExplicitSchedule:
    """Actor names to admit, in order. Statements are only known at run time, so the list is a preference.

    Tolerant mode (the default) follows it as far as the application allows. An entry for a blocked actor is deferred
    until it can run, and an entry for a finished actor is dropped. Strict mode, used for replay and exploration,
    refuses any such difference and reports ``schedule_divergence``. Past the end of the list, either mode continues
    without preemption: the last actor if it can run, otherwise the first by name.
    """

    def __init__(self, entries: list[str], *, strict: bool = False) -> None:
        self.requested = list(entries)
        self.pending = list(entries)
        self.strict = strict

    def choose(self, enabled: list[str], finished: list[str], last: str | None) -> tuple[str | None, str | None, str]:
        if self.strict:
            if not self.pending:
                return _continuation(enabled, last), None, "continuation"
            head = self.pending.pop(0)
            return (head if head in enabled else None), head, "schedule"
        self.pending = [e for e in self.pending if e not in finished]
        head = self.pending[0] if self.pending else None
        for index, entry in enumerate(self.pending):
            if entry in enabled:
                del self.pending[index]
                return entry, head, "schedule" if index == 0 else "deferred"
        return _continuation(enabled, last), head, "continuation"


def _continuation(enabled: list[str], last: str | None) -> str:
    return last if last in enabled else enabled[0]


@dataclass
class _Actor:
    spec: LiveActor
    conn: SteppedConnection
    channel: ActorChannel
    thread: threading.Thread
    result: ActorResult
    state: str = "python"  # python, gate, in_db or finished
    since: float = 0.0
    pending: OpRequested | None = None
    flight: OpRecord | None = None
    flight_releases: bool = False
    steps: int = 0
    frames: list[tuple[str | None, list[OpRecord]]] = field(default_factory=lambda: [(None, [])])


def validate_scenario(scenario: LiveScenario, schedule: Schedule) -> None:
    names = [a.name for a in scenario.actors]
    if len(set(names)) != len(names) or not all(_ACTOR_NAME.match(n) for n in names):
        raise ValueError(f"bad actor names: {names}")
    unknown = set(schedule.requested) - set(names)
    if unknown:
        raise ValueError(f"schedule names unknown actors: {sorted(unknown)}")


class LiveRunner:
    """Engine only: one scenario, one schedule, in this process, with admin login. See ``SupervisedRunner``.

    Its traces are never ``scoreable``. It remains the fast way to test the scheduling engine itself.
    """

    def __init__(self, sandbox: Sandbox, limits: LiveLimits | None = None) -> None:
        self.sandbox = sandbox
        self.limits = limits or LiveLimits.from_settings(sandbox.settings)
        self.poisoned = False

    def run(self, scenario: LiveScenario, schedule: Schedule) -> LiveTrace:
        if self.poisoned:
            raise RuntimeError("an earlier run left a thread that never finished; this runner cannot be trusted")
        validate_scenario(scenario, schedule)
        started = time.perf_counter()
        self.sandbox.reset(scenario.setup_sql, scenario.seed)
        initial = self.sandbox.snapshot()
        run = _Run(SandboxHost(self.sandbox), self.limits, scenario, schedule)
        run.execute()
        self.poisoned = bool(run.trace.leaked_threads)
        run.trace.initial_state = initial
        run.trace.final_state = self.sandbox.snapshot()
        run.trace.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return run.trace


class _Run:
    def __init__(
        self, host: Host, limits: LiveLimits, scenario: LiveScenario, schedule: Schedule,
        on_ready: Callable[[dict[str, int]], None] | None = None, abort_request: threading.Event | None = None,
    ) -> None:
        self.host, self.limits, self.scenario, self.schedule = host, limits, scenario, schedule
        self.on_ready = on_ready
        self.abort_request = abort_request
        self.events: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self.trace = LiveTrace(scenario=scenario.name, requested_schedule=list(schedule.requested))
        self.actors: dict[str, _Actor] = {}
        self.pid_to_actor: dict[int, str] = {}
        self.taint: Counter[str] = Counter()
        self.commits = 0
        self.epoch_commits: list[tuple[OpRecord, list[OpRecord]]] = []
        self.last: str | None = None
        self.stop = False
        self.start = time.monotonic()

    # ------------------------------------------------------------ lifecycle

    def execute(self) -> None:
        try:
            for spec in self.scenario.actors:
                self._launch(spec)
            if self.on_ready is not None:
                self.on_ready(dict(self.trace.connections))
            self.start = time.monotonic()
            for actor in self.actors.values():
                actor.since = time.monotonic()
                actor.thread.start()
            self._control_loop()
        finally:
            self._shut_down()
            self._conclude()

    def _launch(self, spec: LiveActor) -> None:
        conn = self.host.open_actor(spec, self.limits)
        channel = ActorChannel(spec.name, conn, self.events.put, hard_limit_s=self.limits.run_s + 10)
        conn.attach(channel)
        result = ActorResult(actor=spec.name)
        thread = threading.Thread(
            target=self._actor_main, args=(spec, conn, channel, result), name=f"ds-live-{spec.name}", daemon=True,
        )
        channel.thread = thread
        pid = conn.info.backend_pid
        self.actors[spec.name] = _Actor(spec, conn, channel, thread, result)
        self.pid_to_actor[pid] = spec.name
        self.trace.connections[spec.name] = pid

    def _actor_main(self, spec: LiveActor, conn: SteppedConnection, channel: ActorChannel, result: ActorResult) -> None:
        try:
            result.value = _jsonable(_resolve(spec.entrypoint)(conn, **spec.kwargs))
            result.outcome = "returned"
        except RunAborted:
            result.outcome = "aborted"
        except UnsupportedDriverOperation as exc:
            result.outcome, result.exception_type = "refused", type(exc).__name__
        except BaseException as exc:  # noqa: B036 - the actor thread must always report back
            result.outcome, result.exception_type = "raised", type(exc).__name__
            result.sqlstate = getattr(exc, "sqlstate", None)
            result.error_category = error_category(exc)
        finally:
            self._close_transaction(conn, channel)
            self.events.put(_Finished(spec.name))

    def _close_transaction(self, conn: SteppedConnection, channel: ActorChannel) -> None:
        """A function left its transaction open. Scheduled, because releasing its locks is observable to others."""
        try:
            status = conn.pgconn.transaction_status
            if conn.closed or channel.aborting or status in _NOTHING_TO_ROLL_BACK:
                return
            channel.perform(
                "implicit_rollback", "ROLLBACK", None, lambda: psycopg.Connection.rollback(conn),
            )
        except (RunAborted, psycopg.Error, UnsupportedDriverOperation):
            pass

    # ------------------------------------------------------------ control loop

    def _control_loop(self) -> None:
        while not self.stop:
            self._settle()
            if self.stop:
                return
            enabled = sorted(n for n, a in self.actors.items() if a.state == "gate")
            blocked = {n: a for n, a in self.actors.items() if a.state == "in_db"}
            if not enabled and not blocked:
                return
            if not enabled:
                self._wait_for_event()  # only PostgreSQL can move now
                continue
            self._decide(enabled, blocked)

    def _number_commits(self) -> None:
        """Order this window's commits without the arrival race: the operation just admitted is what woke the others."""
        current = len(self.trace.decisions) - 1
        for record, writes in sorted(self.epoch_commits, key=lambda c: (c[0].admitted_decision != current, c[0].seq)):
            self.commits += 1
            record.commit_seq = self.commits
            for write in writes:
                write.commit_seq = self.commits
        self.epoch_commits.clear()

    def _decide(self, enabled: list[str], blocked: dict[str, _Actor]) -> None:
        self._number_commits()
        finished = sorted(n for n, a in self.actors.items() if a.state == "finished")
        chosen, requested, source = self.schedule.choose(enabled, finished, self.last)
        if chosen is None:
            self.taint["schedule_divergence"] += 1
            self.trace.diagnostics.append(f"schedule wanted {requested}; can run {enabled}")
            self.stop = True
            return
        self.trace.decisions.append(Decision(
            index=len(self.trace.decisions), enabled=enabled, finished=finished, requested=requested, chosen=chosen,
            source=source, blocked={n: sorted(a.flight.blocked_by) for n, a in blocked.items() if a.flight},
        ))
        self.trace.chosen_schedule.append(chosen)
        self.last = chosen
        self._admit(self.actors[chosen])

    def _admit(self, actor: _Actor) -> None:
        op = actor.pending
        record = OpRecord(
            seq=len(self.trace.records), actor=actor.spec.name, actor_step=actor.steps, kind=op.kind, sql=op.sql,
            params=op.params, provenance=op.provenance, backend_pid=self.trace.connections[actor.spec.name],
            admitted_ms=self._now_ms(), admitted_decision=len(self.trace.decisions) - 1,
        )
        self.trace.records.append(record)
        actor.steps += 1
        actor.flight, actor.flight_releases, actor.pending = record, op.releases_savepoint, None
        actor.state, actor.since = "in_db", time.monotonic()
        actor.channel.grant()

    def _settle(self) -> None:
        """Return once every actor is at a gate, finished, or blocked according to PostgreSQL."""
        while not self.stop:
            self._drain()
            if self.stop:
                return
            states = [a.state for a in self.actors.values()]
            if "python" not in states:
                in_db = [a for a in self.actors.values() if a.state == "in_db"]
                if not in_db:
                    return
                graph = self.host.blockers([self.trace.connections[a.spec.name] for a in in_db])
                if self._note_external_blockers(graph):
                    return
                if all(graph.get(self.trace.connections[a.spec.name]) for a in in_db) and self.events.empty():
                    for actor in in_db:
                        pids = graph[self.trace.connections[actor.spec.name]]
                        names = {self.pid_to_actor.get(p, "external") for p in pids}
                        actor.flight.blocked_by = sorted({*actor.flight.blocked_by, *names})
                    return
            self._wait_for_event()

    def _note_external_blockers(self, graph: dict[int, list[int]]) -> bool:
        """A lock held by anything outside the run means the schedule isn't what decided this outcome."""
        strangers = sorted({p for pids in graph.values() for p in pids if p not in self.pid_to_actor})
        if not strangers:
            return False
        self.trace.external_blockers = strangers
        self._halt("external_interference", f"lock held outside the run by backend(s) {strangers}")
        return True

    def _wait_for_event(self) -> None:
        self._check_deadlines()
        if self.stop:
            return
        try:
            self._handle(self.events.get(timeout=self.limits.poll_s))
        except queue.Empty:
            pass

    def _drain(self) -> None:
        while True:
            try:
                self._handle(self.events.get_nowait())
            except queue.Empty:
                return

    def _check_deadlines(self) -> None:
        now = time.monotonic()
        limits = self.limits
        if self.abort_request is not None and self.abort_request.is_set():
            self._halt("run_timeout", "the supervisor asked this run to stop")
            return
        if now - self.start > limits.run_s:
            self._halt("run_timeout", f"run exceeded {limits.run_s}s")
            return
        for name, actor in self.actors.items():
            if actor.state == "python" and now - actor.since > limits.python_step_s:
                self._halt("python_stall", f"{name}: no database call within {limits.python_step_s}s of Python")
            elif actor.state == "in_db" and now - actor.since > limits.statement_watchdog_s:
                self._halt("statement_watchdog", f"{name}: no result after {limits.statement_watchdog_s}s")

    def _halt(self, reason: str, detail: str) -> None:
        self.taint[reason] += 1
        self.trace.diagnostics.append(detail)
        self.stop = True

    # ------------------------------------------------------------ events

    def _handle(self, event: Any) -> None:
        actor = self.actors[event.actor]
        if isinstance(event, OpRequested):
            actor.pending, actor.state = event, "gate"
        elif isinstance(event, OpCompleted):
            self._complete(actor, event)
            actor.state, actor.since = "python", time.monotonic()
        elif isinstance(event, OpRefused):
            self.trace.refusals.append(Refusal(actor=event.actor, operation=event.operation, reason=event.reason))
            self._halt("unsupported", f"{event.actor}: {event.operation}: {event.reason}")
        elif isinstance(event, _Finished):
            actor.state = "finished"

    def _complete(self, actor: _Actor, done: OpCompleted) -> None:
        record = actor.flight
        actor.flight = None
        if record is None:
            return
        record.completed_decision = len(self.trace.decisions) - 1
        record.completed_ms = self._now_ms()
        record.txn_before, record.txn_after = done.txn_before, done.txn_after
        if done.savepoint and record.sql in {"SAVEPOINT", "RELEASE", "ROLLBACK TO + RELEASE"}:
            record.sql = f"{record.sql} {done.savepoint}"
        if done.exception is not None:
            self._record_error(record, done.exception)
        else:
            record.status = "ok"
            record.rowcount, record.command_tag = done.rowcount, done.command_tag
            record.columns, record.rows, record.rows_total = done.columns, done.rows, done.rows_total
        record.batch_count = done.batch_count
        self._apply_transaction_effect(actor, record, done)

    def _record_error(self, record: OpRecord, exc: BaseException) -> None:
        record.status, record.exception_type = "error", type(exc).__name__
        record.sqlstate = getattr(exc, "sqlstate", None)
        record.error_category = error_category(exc)
        text = str(exc).strip()
        record.error = text.splitlines()[0] if text else type(exc).__name__
        # These count even if the application catches them and returns normally. A harness limit or a lost
        # connection changed what the application did, so the result can't be scored.
        if record.error_category in _HARNESS_LIMITS:
            self.taint[record.error_category] += 1

    def _apply_transaction_effect(self, actor: _Actor, record: OpRecord, done: OpCompleted) -> None:
        """Which writes survived. The driver's transaction status decides, not what the application asked for."""
        kind = record.kind
        ended = done.txn_after == "IDLE" and done.txn_before in {"INTRANS", "INERROR"}
        lost = done.txn_after == "UNKNOWN"
        if self._effect_is_unknown(actor, record, done, lost):
            return
        if kind == "savepoint" and record.status == "ok":
            actor.frames.append((done.savepoint, []))
            return
        if kind in {"rollback_to", "release"} and record.status == "ok":
            self._unwind_savepoint(actor, record, done)
            return
        batch_left_writes = kind == "opaque_batch" and record.status == "error" and done.txn_after == "INTRANS"
        if batch_left_writes:  # a client-side error part way through: what was sent stays in the open transaction
            record.effect = "pending"
            actor.frames[-1][1].append(record)
        if kind in {"sql", "opaque_batch"} and record.status == "ok":
            if done.txn_after == "IDLE" and done.txn_before == "IDLE":
                record.effect = "autocommitted"
                self.epoch_commits.append((record, []))
            else:
                record.effect = "pending"
                actor.frames[-1][1].append(record)
        if not (ended or lost):
            return
        # COMMIT of a failed transaction is reported by the server as ROLLBACK, with no exception.
        committed = kind == "commit" and record.status == "ok" and done.txn_before == "INTRANS" and not lost
        effect = "committed" if committed else "rolled_back"
        record.effect = effect if kind in {"commit", "rollback", "implicit_rollback"} else record.effect
        writes = [pending for _, frame in actor.frames for pending in frame]
        for pending in writes:
            pending.effect = effect
        if committed:
            self.epoch_commits.append((record, writes))
        actor.frames[:] = [(None, [])]

    def _effect_is_unknown(self, actor: _Actor, record: OpRecord, done: OpCompleted, lost: bool) -> bool:
        """The client can't know what happened. It doesn't guess from a failed connection or from transaction status."""
        autocommit_write = record.kind in {"sql", "opaque_batch"} and done.txn_before == "IDLE" and done.autocommit
        ambiguous = lost and (record.kind == "commit" or autocommit_write)
        # A client-side error part way through an autocommit batch: earlier parameter sets may have committed.
        client_side_batch = (
            record.kind == "opaque_batch" and record.status == "error" and autocommit_write and not record.sqlstate
        )
        if not (ambiguous or client_side_batch):
            return False
        self._mark_effect_unknown(actor, record)
        return True

    def _mark_effect_unknown(self, actor: _Actor, record: OpRecord) -> None:
        record.effect = "effect_unknown"
        self.taint["effect_unknown"] += 1
        for _, frame in actor.frames:
            for pending in frame:
                pending.effect = "effect_unknown"
        actor.frames[:] = [(None, [])]

    def _unwind_savepoint(self, actor: _Actor, record: OpRecord, done: OpCompleted) -> None:
        names = [name for name, _ in actor.frames]
        if done.savepoint not in names:
            return
        position = len(names) - 1 - names[::-1].index(done.savepoint)
        unwound = actor.frames[position:]
        if record.kind == "rollback_to":
            for _, frame in unwound:
                for pending in frame:
                    pending.effect = "savepoint_rolled_back"
            actor.frames[position:] = [] if actor.flight_releases else [(done.savepoint, [])]
        else:
            merged = [r for _, frame in unwound for r in frame]
            actor.frames[position:] = []
            actor.frames[-1][1].extend(merged)

    # ------------------------------------------------------------ shutdown

    def _shut_down(self) -> None:
        for actor in self.actors.values():
            actor.channel.abort()
        for actor in self.actors.values():
            if actor.state == "in_db" and actor.thread.is_alive():
                try:
                    actor.conn.cancel_safe(timeout=1)
                except psycopg.Error:
                    pass
        deadline = time.monotonic() + self.limits.abort_grace_s
        while any(a.thread.is_alive() for a in self.actors.values()) and time.monotonic() < deadline:
            self._wait_events_quietly()
        self._drain_quietly()
        for name, actor in self.actors.items():
            if actor.flight is not None:
                actor.flight.status = "aborted"
                # Interrupted before any reply: COMMIT, or a write that commits on its own, may or may not have landed.
                may_have_landed = actor.flight.kind == "commit" or (
                    actor.flight.kind in {"sql", "opaque_batch"} and actor.spec.autocommit
                )
                if may_have_landed:
                    self._mark_effect_unknown(actor, actor.flight)
                else:
                    actor.flight.effect = "rolled_back"
            if actor.thread.is_alive():
                # Its session is ended on the server first. Closing a connection another thread is using isn't safe.
                self.trace.leaked_threads.append(name)
                self.trace.diagnostics.append(f"{name}: thread did not stop; its session was terminated on the server")
                try:
                    self.host.terminate(self.trace.connections[name])
                except psycopg.Error as exc:
                    self.trace.diagnostics.append(f"{name}: could not terminate its session: {type(exc).__name__}")
            else:
                actor.conn.detach()
                actor.conn.close()
        self.trace.sessions_left_open = self._await_sessions_gone()
        if self.trace.sessions_left_open:
            self.taint["infrastructure_failure"] += 1

    def _wait_events_quietly(self) -> None:
        try:
            self._handle_during_shutdown(self.events.get(timeout=0.01))
        except queue.Empty:
            pass

    def _drain_quietly(self) -> None:
        while True:
            try:
                self._handle_during_shutdown(self.events.get_nowait())
            except queue.Empty:
                return

    def _handle_during_shutdown(self, event: Any) -> None:
        if isinstance(event, OpRequested):
            return  # never admitted
        self._handle(event)

    def _await_sessions_gone(self) -> int:
        pids = list(self.trace.connections.values())
        if not pids:
            return 0
        deadline = time.monotonic() + 2.0
        while True:
            remaining = self.host.sessions_remaining(pids)
            if remaining == 0 or time.monotonic() > deadline:
                return remaining
            time.sleep(0.005)

    def _conclude(self) -> None:
        trace = self.trace
        self._number_commits()
        for actor in self.actors.values():
            trace.actors.append(actor.result)
            if actor.result.outcome == "raised" and actor.result.error_category:
                self.taint[actor.result.error_category] += 1
        for pending in (r for r in trace.records if r.effect == "pending"):
            pending.effect = "rolled_back"
        for reason in OUTCOME_PRIORITY:
            if self.taint[reason]:
                trace.outcome = reason  # type: ignore[assignment]
                break
        trace.inconclusive = trace.outcome in INCONCLUSIVE

    def _now_ms(self) -> float:
        return round((time.monotonic() - self.start) * 1000, 3)


@dataclass
class _Finished:
    actor: str


def _resolve(entrypoint: Callable[..., Any] | str) -> Callable[..., Any]:
    if callable(entrypoint):
        return entrypoint
    module, _, name = entrypoint.partition(":")
    return getattr(importlib.import_module(module), name)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return _safe_params([value])[0]


def run_uninstrumented_serial(
    sandbox: Sandbox, scenario: LiveScenario, order: list[str], limits: LiveLimits | None = None,
) -> dict[str, Any]:
    """The control: the same functions on plain psycopg connections, one after another, with no controller."""
    limits = limits or LiveLimits.from_settings(sandbox.settings)
    by_name = {a.name: a for a in scenario.actors}
    sandbox.reset(scenario.setup_sql, scenario.seed)
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in order:
        actor = by_name[name]
        conn = open_session(sandbox.settings, name, limits, autocommit=actor.autocommit, isolation=actor.isolation,
                            connection_class=psycopg.Connection)
        try:
            results[name] = _jsonable(_resolve(actor.entrypoint)(conn, **actor.kwargs))
        except psycopg.Error as exc:
            errors[name] = exc.sqlstate or type(exc).__name__
        finally:
            conn.close()  # An open transaction rolls back here, like the instrumented implicit rollback.
    return {"results": results, "errors": errors, "final_state": sandbox.snapshot()}
