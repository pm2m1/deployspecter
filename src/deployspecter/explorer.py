"""Bounded exploration: run legal interleavings until a budget runs out."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .models import ExecutableSpec, ExecutionTrace, InvariantProposal, OracleVerdict, Requirement, Violation
from .oracle import evaluate, invariant_holds
from .scheduler import ScheduleRunner, enumerate_schedules, preemptions, schedule_space_size

_MAX_STORED_VIOLATIONS = 50


@dataclass(frozen=True)
class Budget:
    max_schedules: int = 300
    max_seconds: float = 60.0
    seed: int | None = None
    stop_on_first: bool = False


DEFAULT_BUDGET = Budget()


class ViolatingSchedule(BaseModel):
    index: int
    schedule: list[str]
    preemptions: int
    requirement_ids: list[str]


class InvariantReport(BaseModel):
    id: str
    description: str
    screened_out: bool = False
    serial_failures: int = 0
    schedules_failed: int = 0
    failed_matching_oracle_violation: int = 0
    failed_with_unrelated_oracle_violation: int = 0
    failed_without_oracle_violation: int = 0


class ExplorationResult(BaseModel):
    spec_name: str
    generator: str
    spec_digest: str
    space_size: int
    executed: int = 0
    exhausted_space: bool = False
    stopped_by: str | None = None
    seed: int | None = None
    elapsed_seconds: float = 0.0
    classifications: dict[str, int] = Field(default_factory=dict)
    outcomes: dict[str, int] = Field(default_factory=dict)
    distinct_behaviours: int = 0
    deferred_schedules: int = 0
    non_serial_final_states: int = 0
    first_violation_index: int | None = None
    first_violation_seconds: float | None = None
    violates_in_serial_order: bool = False
    violating: list[ViolatingSchedule] = Field(default_factory=list)
    violating_total: int = 0
    first_trace: ExecutionTrace | None = None
    first_verdict: OracleVerdict | None = None
    first_clean_trace: ExecutionTrace | None = None
    invariants: list[InvariantReport] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def _same_target(inv: InvariantProposal, violation: Violation) -> bool:
    return (
        inv.table == violation.table and inv.column == violation.column
        and str(inv.key_value) == str(violation.key)
    )


def _state_key(trace: ExecutionTrace) -> str:
    return repr(sorted((t, repr(rows)) for t, rows in trace.final_state.items()))


def explore(
    runner: ScheduleRunner,
    spec: ExecutableSpec,
    setup_sql: str,
    requirements: list[Requirement],
    budget: Budget = DEFAULT_BUDGET,
) -> ExplorationResult:
    counts = spec.step_counts()
    result = ExplorationResult(
        spec_name=spec.name, generator=spec.generator, spec_digest=spec.digest(),
        space_size=schedule_space_size(counts), seed=budget.seed,
    )
    classes: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    behaviours: set[str] = set()
    serial_states: set[str] = set()
    reports = {i.id: InvariantReport(id=i.id, description=i.description) for i in spec.invariants}
    started = time.perf_counter()

    for index, schedule in enumerate(enumerate_schedules(counts, budget.seed)):
        if index >= budget.max_schedules:
            result.stopped_by = "max_schedules"
            break
        if time.perf_counter() - started > budget.max_seconds:
            result.stopped_by = "max_seconds"
            break
        trace = runner.run(spec, setup_sql, schedule)
        verdict = evaluate(requirements, trace)
        result.executed += 1
        classes[verdict.classification] += 1
        outcomes[trace.outcome] += 1
        behaviours.add(trace.fingerprint())
        if trace.issue_order != trace.requested_schedule:
            result.deferred_schedules += 1

        preempt = preemptions(schedule, counts)
        key = _state_key(trace)
        if preempt == 0:
            serial_states.add(key)
        elif serial_states and key not in serial_states:
            result.non_serial_final_states += 1

        for inv in spec.invariants:
            if invariant_holds(inv, trace.final_state):
                continue
            report = reports[inv.id]
            report.schedules_failed += 1
            if preempt == 0:
                report.serial_failures += 1
                report.screened_out = True
            if any(_same_target(inv, v) for v in verdict.violations):
                report.failed_matching_oracle_violation += 1
            elif verdict.violations:
                report.failed_with_unrelated_oracle_violation += 1
            else:
                report.failed_without_oracle_violation += 1

        if verdict.violations:
            result.violating_total += 1
            result.violates_in_serial_order |= preempt == 0
            if len(result.violating) < _MAX_STORED_VIOLATIONS:
                result.violating.append(ViolatingSchedule(
                    index=index, schedule=schedule, preemptions=preempt,
                    requirement_ids=sorted({v.requirement_id for v in verdict.violations}),
                ))
            if result.first_violation_index is None:
                result.first_violation_index = index
                result.first_violation_seconds = round(time.perf_counter() - started, 4)
                result.first_trace, result.first_verdict = trace, verdict
                if budget.stop_on_first:
                    result.stopped_by = "stop_on_first"
                    break
        elif result.first_clean_trace is None and verdict.classification == "no_violation":
            result.first_clean_trace = trace
    else:
        result.exhausted_space = True

    result.elapsed_seconds = round(time.perf_counter() - started, 4)
    result.classifications = dict(classes)
    result.outcomes = dict(outcomes)
    result.distinct_behaviours = len(behaviours)
    result.invariants = list(reports.values())
    if result.first_verdict:
        result.assumptions = result.first_verdict.assumptions
    return result
