"""Amendment 002's confirmation policy, as pure code over attempt records. A research candidate, not frozen.

Amendment 002 requires the server-choice signature's code to be "frozen with the tool". This module is a candidate for
that review. It runs nothing: the caller supplies one attempt at a time, and the policy decides when to stop.

Classes (amendment 002): exact, conditional semantic, not comparable, failed, inconclusive.
Results: reproduced, failed reproduction, rare outcome not encountered, inconclusive reproduction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

ATTEMPT_LIMIT = 125  # amendment 002: the same for every candidate, arm and application; never extended
REQUIRED_COMPARABLE = 3
FAILURE_SQLSTATES = {"40P01": "deadlock", "40001": "serialization_failure"}

AttemptClass = Literal["exact", "conditional_semantic", "not_comparable", "failed", "inconclusive"]
Result = Literal["reproduced", "failed_reproduction", "rare_outcome_not_encountered", "inconclusive_reproduction"]


@dataclass(frozen=True)
class Attempt:
    """What the policy needs from one run. Built by the caller from a trace and the independent oracle."""

    signature: tuple[Any, ...]  # PostgreSQL-selected outcomes only: no rows, requirement ids or state
    fingerprint: str
    outcome_class: str
    requirement_ids: frozenset[str]
    state_digest: str  # committed state, generated ids and masked values excluded by the caller's canonical form
    inconclusive: bool
    followed: bool = True  # whether the saved decisions were followed before any signature difference appeared


def classify(candidate: Attempt, attempt: Attempt) -> AttemptClass:
    if attempt.inconclusive:
        return "inconclusive"
    if not attempt.followed:
        return "failed"
    if attempt.signature != candidate.signature:
        return "not_comparable"
    same = (attempt.outcome_class == candidate.outcome_class and attempt.requirement_ids == candidate.requirement_ids
            and attempt.state_digest == candidate.state_digest)
    if not same:
        return "failed"
    return "exact" if attempt.fingerprint == candidate.fingerprint else "conditional_semantic"


@dataclass
class Confirmation:
    result: Result
    classes: list[AttemptClass] = field(default_factory=list)
    comparable_seen: int = 0
    attempts: int = 0
    unconditional_semantic: int = 0  # same outcome, requirements and state, whatever PostgreSQL selected


def confirm(candidate: Attempt, next_attempt: Callable[[int], Attempt]) -> Confirmation:
    """One attempt at a time; stop at the third comparable attempt or at 125 attempts, whichever comes first."""
    out = Confirmation(result="rare_outcome_not_encountered")
    comparable: list[AttemptClass] = []
    while out.attempts < ATTEMPT_LIMIT and len(comparable) < REQUIRED_COMPARABLE:
        attempt = next_attempt(out.attempts)
        out.attempts += 1
        kind = classify(candidate, attempt)
        out.classes.append(kind)
        if not attempt.inconclusive and (attempt.outcome_class, attempt.requirement_ids, attempt.state_digest) == (
                candidate.outcome_class, candidate.requirement_ids, candidate.state_digest):
            out.unconditional_semantic += 1
        if kind in {"exact", "conditional_semantic", "failed"}:
            comparable.append(kind)
    out.comparable_seen = len(comparable)
    if "failed" in comparable:
        out.result = "failed_reproduction"
    elif len(comparable) == REQUIRED_COMPARABLE:
        out.result = "reproduced"
    elif "inconclusive" in out.classes:
        out.result = "inconclusive_reproduction"
    return out


def server_choice_signature(trace: Any) -> tuple[Any, ...]:
    """Deadlock and serialization failures by actor and step, and lock wakeups where two or more actors waited.

    A candidate reading of amendment 002's definition, over ``LiveTrace``'s records and decisions. A choice the trace
    can't show isn't here, so attempts differing only in it count as comparable and can fail.
    """
    failures = tuple(sorted((r.actor, r.actor_step, FAILURE_SQLSTATES[r.sqlstate]) for r in trace.records
                            if r.sqlstate in FAILURE_SQLSTATES))
    wakeups = []
    decisions = list(trace.decisions)
    for before, after in zip(decisions, decisions[1:], strict=False):
        waiting = set(before.blocked)
        if len(waiting) >= 2:
            woke = sorted(a for a in waiting if a in after.enabled and a not in after.blocked)
            if woke:  # which of the contending waiters PostgreSQL let through first, and against whom
                wakeups.append((before.index, tuple(woke), tuple(sorted(waiting))))
    return ("failures", failures, "wakeups", tuple(wakeups))
