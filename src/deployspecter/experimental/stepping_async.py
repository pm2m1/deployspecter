"""EXPERIMENTAL: driver-level stepping of real async Python handlers on psycopg 3's AsyncConnection.

The asynchronous counterpart of ``stepping.py``, with the same evidence model: every supported database operation
stops at a gate, the controller grants exactly one at a time, the real psycopg coroutine runs on the server, and the
handler's own code runs on the result until its next request. Nothing is replayed.

* One asyncio Task per actor, one ``AsyncSteppedConnection`` per actor. The controller is a coroutine on the same
  event loop; actors wait for their grant on an ``asyncio.Condition``, so no thread primitive blocks the loop.
* Intercepted: ``AsyncConnection.execute`` (it builds its cursor through ``cursor_factory``), ``AsyncCursor.execute``,
  ``AsyncCursor.executemany``, ``AsyncConnection.commit`` and ``rollback`` (sent through ``_exec_command``, so gated
  at the connection), and ``AsyncConnection.transaction()`` entry and exit (BEGIN, SAVEPOINT, COMMIT, RELEASE,
  ROLLBACK), including its exit on an exception or cancellation. The implicit BEGIN of a non-autocommit connection is
  part of the first gated execute.
* Refused, never executed: named (server-side) cursors, ``copy``, ``stream``, pipelines, use from another actor's
  task, a second operation on one connection while another is in flight, and any statement outside the handler's
  allowlist. A task the actor's code (or a framework on its behalf, such as SQLAlchemy's shielded session close)
  creates inherits the actor's context and may use its connection; such operations record ``on_behalf_of``.
* Not interceptable here, so left to the server-log corroboration: raw ``pgconn`` calls, connections a handler opens
  itself, and a ``psycopg.AsyncCursor(conn)`` built directly (psycopg's ``TypeInfo.fetch`` does this).
* Lock waits are seen through ``pg_blocking_pids`` on a separate async monitor connection. A halt cancels blocked
  queries with ``pg_cancel_backend``, then cancels any task still running after a grace period, rolls back and closes
  every actor connection.
* A handler that runs CPU-bound code without awaiting blocks the whole loop, the controller included; the run-level
  deadline is then only checked once it yields. Exploring the scheduler's decision points is not exploring every
  possible async interleaving: operations that never reach a gate (pure-Python awaits) aren't scheduled.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from .stepping import (
    BUSY,
    TX,
    Actor,
    Decision,
    Limits,
    OpRecord,
    Refused,
    RunAborted,
    Trace,
    call_sites,
    error_class,
    normal_sql,
    sanitize,
    statement_id,
)


class AsyncSteppedCursor(psycopg.AsyncCursor):
    def _actor(self) -> AsyncActor:
        return self.connection._ds_actor  # type: ignore[attr-defined]

    async def execute(self, query: Any, params: Any = None, *, prepare: bool | None = None,
                      binary: bool | None = None) -> AsyncSteppedCursor:
        text = query if isinstance(query, str) else query.as_string(self.connection)
        actor = self._actor()

        async def run() -> Any:
            await psycopg.AsyncCursor.execute(self, query, params, prepare=prepare, binary=binary)
            return self

        await actor.run.gate(actor, "execute", text, params, run, cursor=self)
        return self

    async def executemany(self, query: Any, params_seq: Any, *, returning: bool = False) -> None:
        text = query if isinstance(query, str) else query.as_string(self.connection)
        params_seq = list(params_seq)
        actor = self._actor()
        await actor.run.gate(actor, "executemany", text, params_seq,
                             lambda: psycopg.AsyncCursor.executemany(self, query, params_seq, returning=returning))

    def copy(self, *_a: Any, **_k: Any) -> Any:
        raise Refused("COPY isn't supported by the stepping prototype")

    def stream(self, *_a: Any, **_k: Any) -> Any:
        raise Refused("streaming cursors aren't supported by the stepping prototype")


class AsyncSteppedRawCursor(psycopg.AsyncRawCursor):
    """``AsyncRawCursor`` (PostgreSQL-native ``$1`` placeholders), gated like ``AsyncSteppedCursor``. psycopg builds
    raw cursors only when asked to, so an adapter must construct this class itself; a plain ``AsyncRawCursor(conn)``
    built elsewhere bypasses the gate and is left to the server-log corroboration."""

    async def execute(self, query: Any, params: Any = None, *, prepare: bool | None = None,
                      binary: bool | None = None) -> AsyncSteppedRawCursor:
        text = query if isinstance(query, str) else query.as_string(self.connection)
        actor = self.connection._ds_actor  # type: ignore[attr-defined]

        async def run() -> Any:
            await psycopg.AsyncRawCursor.execute(self, query, params, prepare=prepare, binary=binary)
            return self

        await actor.run.gate(actor, "execute", text, params, run, cursor=self, raw=True)
        return self

    async def executemany(self, query: Any, params_seq: Any, *, returning: bool = False) -> None:
        text = query if isinstance(query, str) else query.as_string(self.connection)
        params_seq = list(params_seq)
        actor = self.connection._ds_actor  # type: ignore[attr-defined]
        await actor.run.gate(actor, "executemany", text, params_seq,
                             lambda: psycopg.AsyncRawCursor.executemany(self, query, params_seq, returning=returning),
                             raw=True)

    def copy(self, *_a: Any, **_k: Any) -> Any:
        raise Refused("COPY isn't supported by the stepping prototype")

    def stream(self, *_a: Any, **_k: Any) -> Any:
        raise Refused("streaming cursors aren't supported by the stepping prototype")


class AsyncSteppedConnection(psycopg.AsyncConnection):
    """A psycopg 3 AsyncConnection whose every supported database operation passes the actor's gate."""

    _ds_actor: AsyncActor

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        if args or kwargs.get("name"):
            raise Refused("server-side (named) cursors aren't supported by the stepping prototype")
        return super().cursor(**kwargs)

    async def commit(self) -> None:
        a = self._ds_actor
        await a.run.gate(a, "commit", "COMMIT", None, lambda: psycopg.AsyncConnection.commit(self))

    async def rollback(self) -> None:
        a = self._ds_actor
        await a.run.gate(a, "rollback", "ROLLBACK", None, lambda: psycopg.AsyncConnection.rollback(self))

    @contextlib.asynccontextmanager
    async def transaction(self, savepoint_name: str | None = None,
                          force_rollback: bool = False) -> AsyncIterator[Any]:
        a = self._ds_actor
        nested = self.info.transaction_status != TX.IDLE
        label = f"SAVEPOINT {savepoint_name or '<auto>'}" if nested else "BEGIN"
        cm = psycopg.AsyncConnection.transaction(self, savepoint_name, force_rollback)
        tx = await a.run.gate(a, "transaction_enter", label, None, cm.__aenter__)
        try:
            yield tx
        except BaseException as exc:
            exit_label = "ROLLBACK TO SAVEPOINT + RELEASE" if nested else "ROLLBACK"
            err = exc
            await a.run.gate(a, "transaction_exit", exit_label, None,
                             lambda: cm.__aexit__(type(err), err, err.__traceback__),
                             aborting=isinstance(err, (RunAborted, asyncio.CancelledError)))
            raise
        else:
            exit_label = ("RELEASE SAVEPOINT" if nested else "COMMIT") if not force_rollback else "ROLLBACK"
            await a.run.gate(a, "transaction_exit", exit_label, None, lambda: cm.__aexit__(None, None, None))

    @contextlib.asynccontextmanager
    async def pipeline(self) -> AsyncIterator[Any]:
        raise Refused("pipeline mode isn't supported by the stepping prototype")
        yield  # pragma: no cover

    async def notifies(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """Notifications are received, not sent, so they reach no gate; each one is recorded in the trace."""
        a = self._ds_actor
        async for note in psycopg.AsyncConnection.notifies(self, *args, **kwargs):
            a.run.record_notification(a, note)
            yield note


@dataclass
class AsyncActor(Actor):
    task: asyncio.Task[Any] | None = None
    lock: asyncio.Lock | None = None  # serialize_tasks: the actor's tasks take turns on its connection
    holding: bool = False  # inside AsyncRun.quiesced: the actor's pre-existing tasks wait before the gate


# The actor a task acts for. Set in the actor's own task; a task it (or a framework on its behalf) creates inherits a
# copy of the context, so it is recognized as acting for the same actor. Another actor's task carries its own.
_ACTING_FOR: contextvars.ContextVar[AsyncActor | None] = contextvars.ContextVar("ds_acting_for", default=None)
# Set by AsyncRun.quiesced in the task that stops a framework: it, and tasks created from it afterwards (a framework's
# own stop task), keep reaching the gate while the actor's earlier tasks are held.
_QUIESCE_EXEMPT: contextvars.ContextVar[bool] = contextvars.ContextVar("ds_quiesce_exempt", default=False)


class AsyncRun:
    """One execution of async actors under one schedule, on the running event loop. Never retried."""

    def __init__(self, actors: Sequence[Actor], connect: Callable[[], Awaitable[AsyncSteppedConnection]],
                 monitor: Callable[[], Awaitable[psycopg.AsyncConnection]], *, schedule: Sequence[str] = (),
                 strict: bool = False, limits: Limits | None = None, run_id: str = "",
                 migration_phase: str = "") -> None:
        names = [a.name for a in actors]
        if len(set(names)) != len(names):
            raise Refused("actor names must be unique")
        unknown = sorted(set(schedule) - set(names))
        if unknown:
            raise Refused(f"the schedule names unknown actors: {unknown}")
        self.actors = [a if isinstance(a, AsyncActor) else AsyncActor(a.name, a.handler, a.args) for a in actors]
        self.by_name = {a.name: a for a in self.actors}
        self.connect, self.monitor_factory = connect, monitor
        self.monitor: psycopg.AsyncConnection | None = None
        self.monitor_pid: int | None = None
        self.schedule, self.strict = list(schedule), strict
        self.limits = limits or Limits()
        self.run_id, self.migration_phase = run_id, migration_phase
        self.cv: asyncio.Condition | None = None
        self.records: list[OpRecord] = []
        self.decisions: list[Decision] = []
        self.diagnostics: list[str] = []
        self.grants = 0
        self.aborted = False
        self.t0 = time.monotonic()

    def _ms(self) -> float:
        return round((time.monotonic() - self.t0) * 1000, 2)

    def _append(self, op: OpRecord) -> None:
        op.seq = len(self.records)
        self.records.append(op)

    # -- the gate, in the actor's task

    def record_notification(self, actor: AsyncActor, note: Any) -> None:
        current = asyncio.current_task()
        self._append(OpRecord(seq=-1, actor=actor.name, kind="notify_received", sql=f"NOTIFY {note.channel}",
                              statement_id=statement_id(f"NOTIFY {note.channel}"), params=sanitize([note.payload]),
                              provenance={"actor": actor.name, "handler": actor.handler.name,
                                          "app_version": actor.handler.app_version,
                                          "invocation_id": actor.invocation_id,
                                          "task": current.get_name() if current else None,
                                          "sender_pid": note.pid, "call_site": call_sites()[0],
                                          "framework_entry": call_sites()[1], "txn": None},
                              outcome="ok", requested_ms=self._ms(), completed_ms=self._ms()))

    async def gate(self, actor: AsyncActor, kind: str, text: str, params: Any, call: Callable[[], Awaitable[Any]],
                   *, cursor: Any = None, aborting: bool = False, raw: bool = False) -> Any:
        assert self.cv is not None
        current = asyncio.current_task()
        if current is not actor.task and _ACTING_FOR.get() is not actor:
            raise Refused(f"{actor.name}'s connection was used from another task")
        if actor.holding and not aborting and not _QUIESCE_EXEMPT.get() and actor.conn is not None and \
                actor.conn.info.transaction_status == TX.IDLE:
            # only new work is held: a statement inside an open transaction, and that transaction's exit, go on, so
            # a held task never leaves its session idle in a transaction (the server would end it)
            await self._held(actor, kind, text, params, current)
        if actor.handler.serialize_tasks and not aborting:
            assert actor.lock is not None
            async with actor.lock:  # this actor's other tasks wait here, outside the scheduler
                return await self._gate(actor, kind, text, params, call, cursor=cursor, aborting=aborting, raw=raw,
                                        current=current)
        return await self._gate(actor, kind, text, params, call, cursor=cursor, aborting=aborting, raw=raw,
                                current=current)

    async def _held(self, actor: AsyncActor, kind: str, text: str, params: Any,
                    current: asyncio.Task[Any] | None) -> None:
        """While the actor is quiesced, a pre-existing task's new operation (one that would start work on an idle
        connection) waits here, before the actor's lock and the scheduler, so it holds neither. Cancelled here (the
        framework stopping its tasks), it is recorded as never executed. Statements inside an open transaction, its
        exit, and exits on a cancellation (``aborting``) are never held."""
        assert self.cv is not None
        async with self.cv:
            try:
                await self.cv.wait_for(lambda: not actor.holding or self.aborted)
            except asyncio.CancelledError:
                site, entry = call_sites()
                self._append(OpRecord(
                    seq=-1, actor=actor.name, kind=kind, sql=normal_sql(text), statement_id=statement_id(text),
                    params=sanitize(params), outcome="aborted", error_class="cancelled_while_held",
                    provenance={"actor": actor.name, "handler": actor.handler.name,
                                "app_version": actor.handler.app_version, "invocation_id": actor.invocation_id,
                                "task": current.get_name() if current else None, "call_site": site,
                                "framework_entry": entry, "txn": None},
                    requested_ms=self._ms(), completed_ms=self._ms()))
                raise

    @contextlib.asynccontextmanager
    async def quiesced(self, actor: AsyncActor) -> AsyncIterator[None]:
        """Stop a framework's own tasks without cancelling one mid-statement.

        New operations from the actor's pre-existing tasks are held before the gate; the operation already in
        flight (if any) completes; then the body runs, typically a framework's ``stop()``, which cancels the held
        tasks where they wait. The calling task, and tasks created from it inside the body, are exempt. A task that
        is cancelled while a statement is on the wire leaves the session's state unknown to the trace; this avoids
        that, rather than recording it."""
        assert self.cv is not None
        if _ACTING_FOR.get() is not actor:
            raise Refused("quiesced() must be entered by a task acting for the actor")
        token = _QUIESCE_EXEMPT.set(True)
        actor.holding = True
        def settled() -> bool:  # the lock is released only after the gate's last notify, so this also polls
            if self.aborted:
                return True
            lock_free = actor.lock is None or not actor.lock.locked()
            return actor.request is None and lock_free and \
                actor.state not in ("pending", "granted", "executing", "blocked")

        try:
            while not settled():
                async with self.cv:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.cv.wait_for(settled), timeout=0.05)
            yield
        finally:
            actor.holding = False
            _QUIESCE_EXEMPT.reset(token)
            await self._notify()

    async def _gate(self, actor: AsyncActor, kind: str, text: str, params: Any,
                    call: Callable[[], Awaitable[Any]], *, cursor: Any, aborting: bool, raw: bool,
                    current: asyncio.Task[Any] | None) -> Any:
        assert self.cv is not None
        if not aborting and (actor.request is not None or actor.state in ("pending", "granted", "executing",
                                                                          "blocked")):
            raise Refused(f"{actor.name}'s connection got a second operation while one was in flight")
        if self.aborted and not aborting:
            raise RunAborted("the run was halted")
        site, entry = call_sites()
        prov = {"app_version": actor.handler.app_version, "actor": actor.name, "handler": actor.handler.name,
                "invocation_id": actor.invocation_id, "migration_phase": self.migration_phase,
                "logical_op": actor.ops, "session": {"backend_pid": actor.backend_pid},
                "task": current.get_name() if current else None,
                "on_behalf_of": actor.name if current is not actor.task else None,
                "call_site": site, "framework_entry": entry, "txn": None,
                "placeholders": "native" if raw else "psycopg"}
        op = OpRecord(seq=-1, actor=actor.name, kind=kind, sql=normal_sql(text), statement_id=statement_id(text),
                      params=sanitize(params), provenance=prov, requested_ms=self._ms())
        actor.ops += 1
        if kind in ("execute", "executemany") and not actor.handler.allows(text):
            op.outcome, op.error_class = "refused", "unauthorized_sql"
            self._append(op)
            raise Refused(f"{actor.handler.app_version}.{actor.handler.name} may not send: {normal_sql(text)[:80]}")
        if not aborting:
            async with self.cv:
                actor.state, actor.request = "pending", op
                self.cv.notify_all()
                try:
                    await self.cv.wait_for(lambda: actor.granted or self.aborted)
                except asyncio.CancelledError:
                    # the task was cancelled while waiting (a framework stopping its own tasks): the actor is no
                    # longer waiting, and an already issued grant is consumed unused; recorded, never executed
                    actor.granted, actor.request = False, None
                    if actor.state in ("pending", "granted"):
                        actor.state = "running"
                    op.outcome, op.error_class = "aborted", "cancelled_while_waiting"
                    op.completed_ms = self._ms()
                    self._append(op)
                    self.cv.notify_all()
                    raise
                if not actor.granted:
                    actor.request = None
                    op.outcome = "aborted"
                    self._append(op)
                    raise RunAborted("the run was halted before this operation was granted")
                actor.granted, actor.state, actor.request = False, "executing", None
                op.grant, op.decision = self.grants, len(self.decisions) - 1
                self.grants += 1
        conn = actor.conn
        assert conn is not None
        op.txn_before = conn.info.transaction_status.name
        if op.txn_before == "IDLE" and kind in ("execute", "executemany", "transaction_enter"):
            actor.txn += 1
        try:
            out = await call()
            op.outcome = "ok"
            if cursor is not None and cursor.description is not None:
                rows = await cursor.fetchall()
                if rows:
                    await cursor.scroll(0, mode="absolute")  # the handler reads the same rows itself
                op.rows = [sanitize(dict(r)) if isinstance(r, dict) else sanitize(list(r)) for r in rows]
            if cursor is not None:
                op.rowcount = cursor.rowcount
            return out
        except BaseException as exc:
            cancelled = isinstance(exc, (RunAborted, asyncio.CancelledError))
            op.outcome = "aborted" if cancelled else "error"
            op.error_class = "cancelled" if isinstance(exc, asyncio.CancelledError) else error_class(exc)
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
            self._append(op)
            if actor.state in ("executing", "blocked"):
                actor.state = "running"
            await self._notify()

    async def _notify(self) -> None:
        assert self.cv is not None
        async with self.cv:
            self.cv.notify_all()

    # -- the actor tasks

    async def _actor_main(self, actor: AsyncActor) -> None:
        _ACTING_FOR.set(actor)
        try:
            actor.result = await actor.handler.fn(actor.conn, **actor.args)
            actor.state = "finished"
        except (RunAborted, asyncio.CancelledError) as exc:
            actor.state = "aborted"
            actor.exception, actor.exception_class = type(exc).__name__, "aborted"
        except BaseException as exc:  # an application error is a result, recorded, not a crash of the run
            actor.state, actor.exception = "errored", f"{type(exc).__name__}: {str(exc)[:200]}"
            actor.exception_class = error_class(exc)
        finally:
            await self._cleanup(actor)
            await self._notify()

    async def _cleanup(self, actor: AsyncActor) -> None:
        conn = actor.conn
        if conn is None or conn.closed:
            return
        status = conn.info.transaction_status
        if status not in (TX.INTRANS, TX.INERROR):
            return
        op = OpRecord(seq=-1, actor=actor.name, kind="cleanup_rollback", sql="ROLLBACK",
                      statement_id=statement_id("ROLLBACK"), params=None,
                      provenance={"actor": actor.name, "handler": actor.handler.name,
                                  "app_version": actor.handler.app_version, "invocation_id": actor.invocation_id,
                                  "call_site": "<harness cleanup>", "framework_entry": None,
                                  "txn": f"{actor.name}:{actor.txn}"},
                      txn_before=status.name, requested_ms=self._ms())
        try:
            await asyncio.wait_for(psycopg.AsyncConnection.rollback(conn), timeout=self.limits.statement_ms / 1000)
            op.outcome = "ok"
        except BaseException as exc:  # noqa: BLE001 - cleanup must never raise past the actor
            op.outcome, op.error_class = "error", error_class(exc)
        op.txn_after = conn.info.transaction_status.name if not conn.closed else "CLOSED"
        op.completed_ms = self._ms()
        self._append(op)

    # -- the controller

    async def _blockers(self, actor: AsyncActor) -> list[str]:
        assert self.monitor is not None
        cur = await self.monitor.execute("SELECT pg_blocking_pids(%s)", (actor.backend_pid,))
        pids = (await cur.fetchone())[0] or []
        names = {a.backend_pid: a.name for a in self.actors}
        return [names.get(p, f"external:{p}") for p in pids]

    async def _halt(self, outcome: str, detail: str) -> str:
        self.diagnostics.append(detail)
        self.aborted = True
        await self._notify()
        assert self.monitor is not None
        for a in self.actors:
            if a.state in ("executing", "blocked"):
                with contextlib.suppress(Exception):
                    await self.monitor.execute("SELECT pg_cancel_backend(%s)", (a.backend_pid,))
        return outcome

    def _commutes(self, actor: AsyncActor) -> bool:
        req, h = actor.request, actor.handler
        if req is None or not h.setup_sql:
            return False
        if req.kind == "execute":
            return req.sql in h.setup_sql
        if req.kind in ("commit", "rollback"):
            return actor.conn.info.transaction_status == TX.IDLE or actor.setup_only_txn
        return False

    def _choose(self, enabled: list[str], pointer: list[int]) -> tuple[str | None, str, str | None]:
        setup = [n for n in enabled if self._commutes(self.by_name[n])]
        if setup:
            return setup[0], "setup", None
        while pointer[0] < len(self.schedule):
            want = self.schedule[pointer[0]]
            state = self.by_name[want].state
            if want in enabled:
                pointer[0] += 1
                return want, "schedule", want
            if state in ("finished", "errored", "aborted"):
                pointer[0] += 1
                continue
            if self.strict:
                return None, "divergence", want
            return enabled[0], "deferred", want
        return enabled[0], "continuation", None

    async def execute(self) -> Trace:
        self.cv = asyncio.Condition()
        self.monitor = await self.monitor_factory()
        self.monitor_pid = self.monitor.info.backend_pid
        tasks: list[asyncio.Task[Any]] = []
        outcome = "completed"
        try:
            for a in self.actors:
                conn = await self.connect()
                conn._ds_actor = a
                a.conn, a.run, a.backend_pid = conn, self, conn.info.backend_pid
                a.lock = asyncio.Lock()
                a.invocation_id = f"{self.run_id}:{a.name}"
            for a in self.actors:
                a.state = "running"
                a.task = asyncio.create_task(self._actor_main(a), name=f"ds-actor-{a.name}")
                tasks.append(a.task)
            outcome = await self._control()
        finally:
            await self._finish(tasks)
        return Trace(self.run_id, self.schedule, [d.chosen for d in self.decisions], self.decisions, self.records,
                     {a.name: {"handler": f"{a.handler.app_version}.{a.handler.name}", "args": sanitize(a.args),
                               "state": a.state, "result": sanitize(a.result), "exception": a.exception,
                               "exception_class": a.exception_class, "backend_pid": a.backend_pid,
                               "transactions": a.txn} for a in self.actors},
                     outcome, self.diagnostics, self.migration_phase,
                     raw_results={a.name: a.result for a in self.actors})

    async def _control(self) -> str:
        assert self.cv is not None
        lim = self.limits
        deadline = self.t0 + lim.run_s
        pointer = [0]
        running_since: dict[str, float] = {}
        while True:
            if time.monotonic() > deadline:
                return await self._halt("timeout", f"run exceeded {lim.run_s} s")
            async with self.cv:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.cv.wait_for(lambda: all(a.state not in BUSY for a in self.actors)),
                                           timeout=lim.settle_s)
            states = {a.name: a.state for a in self.actors}
            now = time.monotonic()
            for name, st in states.items():
                if st == "running":
                    running_since.setdefault(name, now)
                    if now - running_since[name] > lim.python_step_s:
                        return await self._halt("timeout", f"{name} computed for more than {lim.python_step_s} s")
                else:
                    running_since.pop(name, None)
            blocked: dict[str, list[str]] = {}
            for a in self.actors:
                if a.state in ("executing", "blocked"):
                    who = await self._blockers(a)
                    if who:
                        blocked[a.name] = who
                        if a.state == "executing":
                            a.state = "blocked"
            if any(w.startswith("external:") for ws in blocked.values() for w in ws):
                return await self._halt("external_blocker", f"a session outside the run holds a lock: {blocked}")
            states = {a.name: a.state for a in self.actors}
            if any(st in BUSY for st in states.values()):
                continue
            enabled = [a.name for a in self.actors if states[a.name] == "pending"]
            if not enabled:
                if all(st in ("finished", "errored", "aborted") for st in states.values()):
                    return "completed"
                continue
            chosen, source, wanted = self._choose(enabled, pointer)
            if chosen is None:
                return await self._halt("schedule_divergence", f"schedule wants {wanted}, which is {states[wanted]}")
            if source == "setup":
                enabled = [chosen]
            self.decisions.append(Decision(len(self.decisions), enabled, blocked,
                                           [n for n, s in states.items() if s in ("finished", "errored")],
                                           wanted, chosen, source))
            async with self.cv:  # "granted" is busy until the actor's task takes it, so it can't be granted twice
                self.by_name[chosen].granted, self.by_name[chosen].state = True, "granted"
                self.cv.notify_all()

    async def _finish(self, tasks: list[asyncio.Task[Any]]) -> None:
        """Every task ends and every connection closes, whatever happened."""
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=2.0)
            for t in pending:
                self.diagnostics.append(f"{t.get_name()} didn't stop within the grace period; cancelled")
                t.cancel()
            if pending:
                await asyncio.wait(pending, timeout=self.limits.statement_ms / 1000 + 2.0)
        for a in self.actors:
            if a.conn is not None:
                with contextlib.suppress(Exception):
                    await a.conn.close()
        if self.monitor is not None:
            with contextlib.suppress(Exception):
                await self.monitor.close()


def async_actor_connection_factory(conninfo: dict[str, Any], limits: Limits, *, search_path: str = "public",
                                   autocommit: bool = True) -> Callable[[], Awaitable[AsyncSteppedConnection]]:
    import re
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", search_path):
        raise Refused("search_path must be a plain schema name")
    options = (f"-c statement_timeout={limits.statement_ms} -c lock_timeout={limits.lock_ms} "
               f"-c deadlock_timeout={limits.deadlock_ms} "
               f"-c idle_in_transaction_session_timeout={limits.idle_in_transaction_ms} -c search_path={search_path}")

    async def connect() -> AsyncSteppedConnection:
        return await AsyncSteppedConnection.connect(**conninfo, autocommit=autocommit, prepare_threshold=None,
                                                    cursor_factory=AsyncSteppedCursor, options=options,
                                                    application_name="ds_mvp_actor")

    return connect


__all__ = ["AsyncActor", "AsyncRun", "AsyncSteppedConnection", "AsyncSteppedCursor", "AsyncSteppedRawCursor",
           "async_actor_connection_factory"]
