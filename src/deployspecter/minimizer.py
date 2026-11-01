"""Greedy schedule and test minimization. Every reduction is verified by re-execution."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from .models import ExecutableSpec, ExecutionTrace, OracleVerdict, Requirement
from .oracle import evaluate
from .scheduler import ScheduleRunner, preemptions


class MinimizationResult(BaseModel):
    spec: ExecutableSpec
    schedule: list[str]
    trace: ExecutionTrace
    verdict: OracleVerdict
    target_requirements: list[str]
    attempts: int
    accepted: int
    budget_exhausted: bool
    before: dict[str, int]
    after: dict[str, int]


def _metrics(spec: ExecutableSpec, schedule: list[str]) -> dict[str, int]:
    return {
        "actors": len(spec.actors),
        "steps": len(schedule),
        "seed_rows": len(spec.seed),
        "preemptions": preemptions(schedule, spec.step_counts()),
    }


def _drop_nth(schedule: list[str], actor: str, n: int) -> list[str]:
    out, seen = [], 0
    for name in schedule:
        if name == actor:
            if seen == n:
                seen += 1
                continue
            seen += 1
        out.append(name)
    return out


def _uses_bind(steps, bind: str) -> bool:
    return any(p.source == "row_ref" and p.bind == bind for s in steps for p in s.params.values())


def _candidates(spec: ExecutableSpec, schedule: list[str]) -> Iterator[tuple[ExecutableSpec, list[str]]]:
    if len(spec.actors) > 1:
        for actor in spec.actors:
            yield (
                spec.model_copy(update={"actors": [a for a in spec.actors if a.name != actor.name]}),
                [n for n in schedule if n != actor.name],
            )
    for i in range(len(spec.seed)):
        yield spec.model_copy(update={"seed": spec.seed[:i] + spec.seed[i + 1:]}), schedule
    for actor in spec.actors:
        for i, step in enumerate(actor.steps):
            # Dropping a statement the source always runs would "minimize" to an impossible execution.
            if step.kind != "sql" or not step.removable:
                continue
            rest = actor.steps[i + 1:]
            if step.bind_as and _uses_bind(rest, step.bind_as):
                continue
            remove = [i]
            # A transaction left with no statements disappears with its boundaries.
            if (
                i > 0 and i + 1 < len(actor.steps)
                and actor.steps[i - 1].kind == "begin" and actor.steps[i + 1].kind == "commit"
            ):
                remove = [i - 1, i, i + 1]
            new_steps = [s for j, s in enumerate(actor.steps) if j not in remove]
            if not new_steps:
                continue
            new_schedule = schedule
            for j in reversed(remove):
                new_schedule = _drop_nth(new_schedule, actor.name, j)
            yield (
                spec.model_copy(update={"actors": [
                    a.model_copy(update={"steps": new_steps}) if a.name == actor.name else a
                    for a in spec.actors
                ]}),
                new_schedule,
            )
    counts = spec.step_counts()
    current = preemptions(schedule, counts)
    for i in range(len(schedule)):
        for j in range(len(schedule)):
            if i == j:
                continue
            moved = list(schedule)
            moved.insert(j, moved.pop(i))
            # Moving an entry across its own actor would just renumber; order is implicit.
            if moved != schedule and preemptions(moved, counts) < current:
                yield spec, moved


def minimize(
    runner: ScheduleRunner,
    spec: ExecutableSpec,
    setup_sql: str,
    requirements: list[Requirement],
    schedule: list[str],
    max_attempts: int = 200,
) -> MinimizationResult:
    trace = runner.run(spec, setup_sql, schedule)
    verdict = evaluate(requirements, trace)
    target = {v.requirement_id for v in verdict.violations}
    if not target:
        raise ValueError("the given schedule does not violate any requirement; nothing to minimize")
    before = _metrics(spec, schedule)
    attempts = accepted = 0
    exhausted = False
    improved = True
    while improved and not exhausted:
        improved = False
        for cand_spec, cand_schedule in _candidates(spec, schedule):
            if attempts >= max_attempts:
                exhausted = True
                break
            attempts += 1
            try:
                cand_trace = runner.run(cand_spec, setup_sql, cand_schedule)
            except Exception:  # a reduction that cannot even be set up is simply not accepted
                continue
            cand_verdict = evaluate(requirements, cand_trace)
            if target <= {v.requirement_id for v in cand_verdict.violations}:
                spec, schedule, trace, verdict = cand_spec, cand_schedule, cand_trace, cand_verdict
                accepted += 1
                improved = True
                break
    return MinimizationResult(
        spec=spec, schedule=schedule, trace=trace, verdict=verdict,
        target_requirements=sorted(target), attempts=attempts, accepted=accepted,
        budget_exhausted=exhausted, before=before, after=_metrics(spec, schedule),
    )
