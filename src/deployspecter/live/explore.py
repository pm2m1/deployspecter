"""Systematic exploration of a live scenario, without any model of what the application will do.

The frozen ``enumerate_schedules`` needs each actor's step count in advance. Real functions branch on what they read,
so here the tree of choices is discovered by running: each run reveals which actors could have gone at each decision,
and each unexplored alternative becomes a new prefix. Prefixes with the fewest preemptions run first, as in the frozen
explorer. There is no partial-order reduction.
"""

from __future__ import annotations

import heapq
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .runner import ExplicitSchedule, LiveRunner, LiveScenario
from .trace import Decision, LiveTrace


@dataclass(frozen=True)
class LiveBudget:
    max_runs: int = 300
    max_seconds: float = 60.0
    max_preemptions: int | None = None


@dataclass
class LiveExploration:
    runs: int = 0
    exhausted: bool = False
    stopped_by: str | None = None
    distinct_paths: int = 0
    distinct_behaviours: int = 0
    distinct_final_states: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    divergences: int = 0
    opaque_runs: int = 0
    complete_schedule_coverage: bool = False
    traces: list[LiveTrace] = field(default_factory=list)
    verdicts: list[Any] = field(default_factory=list)


def _alternatives(decisions: list[Decision], start: int) -> list[tuple[int, list[str]]]:
    """Every prefix that differs from this run at one decision at or after ``start``, with its preemption count."""
    found: list[tuple[int, list[str]]] = []
    used = 0
    last: str | None = None
    prefix: list[str] = []
    for decision in decisions:
        if decision.index >= start:
            for other in decision.enabled:
                if other != decision.chosen:
                    cost = used + (1 if last in decision.enabled and other != last else 0)
                    found.append((cost, [*prefix, other]))
        used += 1 if last in decision.enabled and decision.chosen != last else 0
        last = decision.chosen
        prefix.append(decision.chosen)
    return found


def explore_live(
    runner: LiveRunner, scenario: LiveScenario, budget: LiveBudget | None = None,
    check: Callable[[LiveTrace], Any] | None = None,
) -> LiveExploration:
    budget = budget or LiveBudget()
    result = LiveExploration()
    frontier: list[tuple[int, list[str]]] = [(0, [])]
    queued = {()}
    paths: set[tuple[str, ...]] = set()
    behaviours: set[str] = set()
    states: set[str] = set()
    started = time.perf_counter()
    while frontier:
        if result.runs >= budget.max_runs:
            result.stopped_by = "max_runs"
            break
        if time.perf_counter() - started > budget.max_seconds:
            result.stopped_by = "max_seconds"
            break
        _, prefix = heapq.heappop(frontier)
        trace = runner.run(scenario, ExplicitSchedule(prefix, strict=True))
        result.runs += 1
        result.outcomes[trace.outcome] += 1
        result.traces.append(trace)
        result.opaque_runs += not trace.statement_level_coverage
        if check is not None:
            result.verdicts.append(check(trace))
        if trace.outcome == "schedule_divergence":
            result.divergences += 1
            continue
        paths.add(tuple(trace.chosen_schedule))
        behaviours.add(trace.fingerprint())
        states.add(repr(sorted(trace.final_state.items())))
        for cost, candidate in _alternatives(trace.decisions, len(prefix)):
            key = tuple(candidate)
            if key in queued or (budget.max_preemptions is not None and cost > budget.max_preemptions):
                continue
            queued.add(key)
            heapq.heappush(frontier, (cost, candidate))
    else:
        result.exhausted = budget.max_preemptions is None
    # Exhausting the tree covers every choice the tool could control, and says nothing about inside an opaque batch.
    result.complete_schedule_coverage = result.exhausted and result.opaque_runs == 0
    result.distinct_paths = len(paths)
    result.distinct_behaviours = len(behaviours)
    result.distinct_final_states = len(states)
    return result
