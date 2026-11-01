"""Deterministic step-level scheduler for real PostgreSQL sessions.

Control is limited to explicit operation boundaries: we choose when each
statement, BEGIN or COMMIT is *issued*. We do not and cannot schedule anything
inside PostgreSQL. If an issued statement waits on a lock, the wait is observed
through ``pg_blocking_pids`` and later entries for that actor are deferred; the
trace records the order that actually happened next to the one requested.
"""

from __future__ import annotations

import itertools
import math
import random
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from typing import Any

import psycopg

from .database import Sandbox, normalize
from .models import ExecutableSpec, ExecutionTrace, Outcome, ParamBinding, Step, StepRecord

CONFLICT_STATES = {"40001", "40P01"}  # serialization failure, deadlock detected
TIMEOUT_STATES = {"55P03", "57014", "25P03"}  # lock, statement, idle-in-txn timeouts


class _NoRows(Exception):
    """A row_ref pointed at a row the earlier SELECT did not return."""


def apply_transform(value: Any, binding: ParamBinding) -> Any:
    op = binding.transform
    if op == "none" or value is None:
        return value
    if op in {"strip", "lower", "upper", "title"}:
        return getattr(str(value), op)()
    operand = binding.operand if binding.operand is not None else 0
    number = float(value)
    result = number + operand if op == "add" else number * operand
    return int(result) if float(result).is_integer() else result


def resolve_params(step: Step, bound: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name, binding in step.params.items():
        if binding.source == "literal":
            params[name] = binding.value
            continue
        rows = bound.get(binding.bind or "", [])
        # Mirrors the source loop running zero times when a read found nothing.
        if binding.row >= len(rows):
            raise _NoRows(binding.bind)
        params[name] = apply_transform(rows[binding.row].get(binding.column or ""), binding)
    return params


# ------------------------------------------------------------- enumeration


def schedule_space_size(counts: dict[str, int]) -> int:
    total = math.factorial(sum(counts.values()))
    for n in counts.values():
        total //= math.factorial(n)
    return total


def preemptions(schedule: list[str], counts: dict[str, int]) -> int:
    """Times the schedule leaves an actor that still has steps remaining."""
    remaining = Counter(counts)
    switches = 0
    for prev, cur in itertools.pairwise(schedule):
        remaining[prev] -= 1
        if prev != cur and remaining[prev] > 0:
            switches += 1
    return switches


def _with_exact_preemptions(counts: dict[str, int], bound: int) -> Iterator[list[str]]:
    names = sorted(counts)
    total = sum(counts.values())
    remaining = dict(counts)
    path: list[str] = []

    def walk(used: int) -> Iterator[list[str]]:
        if len(path) == total:
            if used == bound:
                yield list(path)
            return
        prev = path[-1] if path else None
        for name in names:
            if remaining[name] == 0:
                continue
            cost = 1 if prev and prev != name and remaining[prev] > 0 else 0
            if used + cost > bound:
                continue
            remaining[name] -= 1
            path.append(name)
            yield from walk(used + cost)
            path.pop()
            remaining[name] += 1

    yield from walk(0)


def enumerate_schedules(counts: dict[str, int], seed: int | None = None) -> Iterator[list[str]]:
    """All legal interleavings, fewest preemptions first, lazily.

    Starts with the serial orders (bound 0). Each schedule appears exactly once.
    With ``seed`` the order is a seeded shuffle, so a budget gives a random
    sample instead; the seed must be recorded by the caller. Shuffling needs the
    full list, so it is refused for spaces too large to materialize.
    """
    counts = {k: v for k, v in counts.items() if v > 0}
    max_bound = sum(counts.values())
    ordered = (
        s for b in range(max_bound + 1) for s in _with_exact_preemptions(counts, b)
    )
    if seed is None:
        yield from ordered
        return
    if schedule_space_size(counts) > 200_000:
        raise ValueError("schedule space too large for seeded enumeration")
    everything = list(ordered)
    random.Random(seed).shuffle(everything)
    yield from everything


# --------------------------------------------------------------- execution


@dataclass
class _ActorRun:
    name: str
    function: str
    steps: list[Step]
    conn: psycopg.Connection
    pool: ThreadPoolExecutor
    next_step: int = 0
    bound: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    in_txn: bool = False
    aborted: bool = False
    uncommitted: list[StepRecord] = field(default_factory=list)


@dataclass
class _Flight:
    future: Future
    record: StepRecord
    step: Step


def _issue(conn: psycopg.Connection, sql_text: str, params: dict[str, Any] | None) -> dict[str, Any]:
    cur = conn.execute(sql_text, params) if params else conn.execute(sql_text)
    rows = None
    if cur.description:
        names = [d.name for d in cur.description]
        rows = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    return {"rowcount": cur.rowcount, "rows": rows, "tag": cur.statusmessage}


class ScheduleRunner:
    """Executes one schedule at a time on a Sandbox. Reuses sessions and threads."""

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self._pools: dict[str, ThreadPoolExecutor] = {}

    def close(self) -> None:
        for pool in self._pools.values():
            pool.shutdown(wait=True)
        self._pools.clear()

    def __enter__(self) -> ScheduleRunner:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _pool(self, name: str) -> ThreadPoolExecutor:
        if name not in self._pools:
            self._pools[name] = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ds-{name}")
        return self._pools[name]

    def run(self, spec: ExecutableSpec, setup_sql: str, schedule: list[str]) -> ExecutionTrace:
        expected = Counter(spec.step_counts())
        if Counter(schedule) != expected:
            raise ValueError(f"schedule {Counter(schedule)} does not match actors {expected}")
        started = time.perf_counter()
        self.sandbox.reset(setup_sql, spec.seed)
        initial_state = self.sandbox.snapshot()
        actors = {
            a.name: _ActorRun(
                a.name, a.function, a.steps, self.sandbox.actor_connection(a.name), self._pool(a.name)
            )
            for a in spec.actors
        }
        session = _Session(self.sandbox, actors)
        session.execute(list(schedule))
        trace = ExecutionTrace(
            requested_schedule=list(schedule),
            issue_order=session.issue_order,
            records=session.records,
            outcome=session.outcome(),
            initial_state=initial_state,
            final_state=self.sandbox.snapshot(),
            diagnostics=session.diagnostics,
        )
        trace.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return trace


class _Session:
    def __init__(self, sandbox: Sandbox, actors: dict[str, _ActorRun]) -> None:
        self.sandbox = sandbox
        self.actors = actors
        self.records: list[StepRecord] = []
        self.issue_order: list[str] = []
        self.flights: dict[str, _Flight] = {}
        self.diagnostics: list[str] = []
        self.commits = 0
        self.saw: Counter[str] = Counter()
        self.pid_to_actor = {a.conn.info.backend_pid: n for n, a in actors.items()}
        t = sandbox.settings.timeouts
        self.wait_limit = (max(t.statement_ms, t.lock_ms) + 2000) / 1000

    def outcome(self) -> Outcome:
        if self.saw["stalled"]:
            return "stalled"
        if self.saw["timeout"]:
            return "timeout"
        if self.saw["error"]:
            return "execution_error"
        if self.saw["conflict"]:
            return "transaction_conflict"
        return "completed"

    def _new_record(self, run: _ActorRun, index: int, step: Step) -> StepRecord:
        rec = StepRecord(
            seq=len(self.records), actor=run.name, step_index=index, kind=step.kind,
            function=run.function, statement_id=step.statement_id,
            sql=step.sql or step.kind.upper(),
        )
        self.records.append(rec)
        return rec

    def execute(self, pending: list[str]) -> None:
        try:
            while pending or self.flights:
                self._settle()
                index = next((i for i, a in enumerate(pending) if a not in self.flights), None)
                if index is None:
                    if self.flights and not self._wait_for_lock_resolution():
                        break
                    continue
                self._dispatch(pending.pop(index))
        finally:
            self._cleanup()

    def _dispatch(self, name: str) -> None:
        run = self.actors[name]
        index = run.next_step
        step = run.steps[index]
        run.next_step += 1
        self.issue_order.append(name)
        rec = self._new_record(run, index, step)
        if run.aborted:
            rec.status = "skipped_after_abort"
            return
        if step.kind == "commit" and not run.in_txn:
            rec.status = "skipped_after_abort"
            return
        params: dict[str, Any] | None = None
        if step.kind == "sql":
            try:
                params = resolve_params(step, run.bound)
            except _NoRows:
                rec.status = "skipped_no_rows"
                return
            rec.params = {k: normalize(v) for k, v in params.items()}
        text = step.sql if step.kind == "sql" else step.kind.upper()
        self.flights[name] = _Flight(run.pool.submit(_issue, run.conn, text, params), rec, step)

    def _settle(self) -> None:
        """Return once every in-flight statement has finished or truly waits on a lock."""
        deadline = time.monotonic() + self.wait_limit
        while self.flights:
            running = {n: f for n, f in self.flights.items() if not f.future.done()}
            if not running:
                self._finish_done()
                return
            wait_futures([f.future for f in running.values()], timeout=0.001)
            still = {n: f for n, f in running.items() if not f.future.done()}
            if still:
                pids = [self.actors[n].conn.info.backend_pid for n in still]
                graph = self.sandbox.blockers(pids)
                blocked = {
                    n: graph.get(self.actors[n].conn.info.backend_pid, []) for n in still
                }
                if all(blocked.values()):
                    for n, pids_blocking in blocked.items():
                        names = sorted({self.pid_to_actor.get(p, f"pid:{p}") for p in pids_blocking})
                        self.flights[n].record.blocked_by = sorted(
                            {*self.flights[n].record.blocked_by, *names}
                        )
                    self._finish_done()
                    return
            if time.monotonic() > deadline:
                self.saw["timeout"] += 1
                self.diagnostics.append(f"settle exceeded {self.wait_limit}s: {sorted(still)}")
                return

    def _finish_done(self) -> None:
        # Sorted so simultaneous completions are recorded identically on replay.
        for name in sorted(n for n, f in self.flights.items() if f.future.done()):
            self._complete(name, self.flights.pop(name))

    def _complete(self, name: str, flight: _Flight) -> None:
        run, rec, step = self.actors[name], flight.record, flight.step
        try:
            result = flight.future.result()
        except psycopg.Error as exc:
            self._record_failure(run, rec, exc)
            return
        rec.rowcount = result["rowcount"]
        rec.command_tag = result["tag"]
        if step.kind == "begin":
            run.in_txn = True
        elif step.kind == "sql":
            rows = result["rows"]
            if rows is not None:
                rec.rows = [{k: normalize(v) for k, v in r.items()} for r in rows]
                if step.bind_as:
                    run.bound[step.bind_as] = rows
            if run.in_txn:
                run.uncommitted.append(rec)
            else:  # autocommit statement: durable the moment it returns
                self.commits += 1
                rec.committed, rec.commit_seq, rec.durable_commit_seq = True, self.commits, self.commits
        elif step.kind in {"commit", "rollback"}:
            ok = step.kind == "commit" and result["tag"] == "COMMIT"
            rec.committed = ok
            if ok:
                self.commits += 1
                rec.commit_seq = self.commits
            for pending in run.uncommitted:
                pending.committed = ok
                pending.durable_commit_seq = rec.commit_seq
            run.uncommitted.clear()
            run.in_txn = False

    def _record_failure(self, run: _ActorRun, rec: StepRecord, exc: psycopg.Error) -> None:
        state = exc.sqlstate
        rec.status, rec.sqlstate = "error", state
        rec.error = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        if state in CONFLICT_STATES:
            self.saw["conflict"] += 1
            self.diagnostics.append(f"{run.name}: transaction conflict {state}: {rec.error}")
        elif state in TIMEOUT_STATES:
            self.saw["timeout"] += 1
            self.diagnostics.append(f"{run.name}: timeout {state}: {rec.error}")
        else:
            self.saw["error"] += 1
            self.diagnostics.append(f"{run.name}: error {state}: {rec.error}")
        run.aborted = True
        # Same behaviour as a real client: the exception rolls the transaction back.
        if run.in_txn:
            self._roll_back(run)

    def _roll_back(self, run: _ActorRun) -> None:
        try:
            run.conn.execute("ROLLBACK")
        except psycopg.Error:  # the server already ended this session, which also rolled it back
            self.diagnostics.append(f"{run.name}: session lost before rollback")
        for pending in run.uncommitted:
            pending.committed = False
        run.uncommitted.clear()
        run.in_txn = False

    def _wait_for_lock_resolution(self) -> bool:
        """Everything runnable is blocked. Only PostgreSQL can resolve this now."""
        futures = [f.future for f in self.flights.values()]
        done, _ = wait_futures(futures, timeout=self.wait_limit, return_when="FIRST_COMPLETED")
        if done:
            return True
        self.saw["stalled"] += 1
        self.diagnostics.append(f"no progress possible; blocked: {sorted(self.flights)}")
        return False

    def _cleanup(self) -> None:
        for name, flight in list(self.flights.items()):
            self.actors[name].conn.cancel_safe(timeout=2)
            wait_futures([flight.future], timeout=self.wait_limit)
            if flight.future.done():
                self._complete(name, self.flights.pop(name))
        for run in self.actors.values():
            if run.in_txn:
                self.diagnostics.append(f"{run.name}: open transaction rolled back at end")
                self._roll_back(run)
