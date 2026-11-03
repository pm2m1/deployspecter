"""What a live run produced. Separate from ``ExecutionTrace``: statements are discovered at run time, not planned."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

LiveOutcome = Literal[
    "completed",
    "python_exception",
    "execution_error",
    "deadlock",
    "serialization_failure",
    "lock_timeout",
    "statement_timeout",
    "idle_in_transaction_timeout",
    "connection_lost",
    "unsupported",
    "schedule_divergence",
    "python_stall",
    "statement_watchdog",
    "run_timeout",
    "effect_unknown",
    "external_interference",
    "unmanaged_activity",
    "log_unverified",
    "harness_killed",
    "infrastructure_failure",
]

# No conclusion about the application can be drawn from a run that ended in one of these.
INCONCLUSIVE: frozenset[str] = frozenset({
    "lock_timeout", "statement_timeout", "idle_in_transaction_timeout", "connection_lost", "unsupported",
    "schedule_divergence", "python_stall", "statement_watchdog", "run_timeout", "effect_unknown",
    "external_interference", "unmanaged_activity", "log_unverified", "harness_killed", "infrastructure_failure",
})

# Most to least severe for the run as a whole. Harness conditions outrank anything the application did.
OUTCOME_PRIORITY: tuple[str, ...] = (
    "infrastructure_failure", "harness_killed", "log_unverified", "unmanaged_activity", "external_interference",
    "effect_unknown", "unsupported", "schedule_divergence", "run_timeout", "python_stall", "statement_watchdog",
    "connection_lost",
    "idle_in_transaction_timeout", "statement_timeout", "lock_timeout", "execution_error", "python_exception",
    "deadlock", "serialization_failure",
)


class OpRecord(BaseModel):
    seq: int  # admission order
    actor: str
    actor_step: int
    kind: str
    sql: str
    params: Any = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    status: Literal["in_flight", "ok", "error", "aborted"] = "in_flight"
    sqlstate: str | None = None
    error_category: str | None = None
    error: str | None = None
    exception_type: str | None = None
    rowcount: int | None = None
    command_tag: str | None = None
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    rows_total: int | None = None
    txn_before: str | None = None
    txn_after: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    # The decision that admitted this operation, and the one in force when it finished. Operations finishing under the
    # same decision have no recorded order: a COMMIT and the statement it wakes race to report back.
    admitted_decision: int = 0
    completed_decision: int | None = None
    commit_seq: int | None = None
    # Whether a write survived: committed, autocommitted, rolled_back, savepoint_rolled_back, effect_unknown, none.
    effect: str = "none"
    # For an opaque batch: how many parameter sets psycopg took. No statement in it was individually scheduled.
    batch_count: int | None = None
    # Excluded from the fingerprint, which must not depend on the clock.
    admitted_ms: float = 0.0
    completed_ms: float | None = None
    backend_pid: int = 0


class Decision(BaseModel):
    index: int
    enabled: list[str]
    blocked: dict[str, list[str]] = Field(default_factory=dict)
    finished: list[str] = Field(default_factory=list)
    requested: str | None = None
    chosen: str
    source: Literal["schedule", "deferred", "continuation"]


class ActorResult(BaseModel):
    actor: str
    outcome: Literal["returned", "raised", "aborted", "refused", "never_finished"] = "never_finished"
    value: Any = None
    exception_type: str | None = None
    sqlstate: str | None = None
    error_category: str | None = None


class Refusal(BaseModel):
    actor: str
    operation: str
    reason: str


class EvidenceFlag(BaseModel):
    kind: str
    detail: str


class RunEvidence(BaseModel):
    """What the supervisor verified from outside the application's process. Empty for an unsupervised run."""

    supervised: bool = False
    flags: list[EvidenceFlag] = Field(default_factory=list)
    run_id: str = ""
    expected_sessions: dict[str, int] = Field(default_factory=dict)
    session_counter_delta: int | None = None
    reconciliation: dict[str, Any] = Field(default_factory=dict)
    escalation: list[dict[str, Any]] = Field(default_factory=list)
    privilege_checks: dict[str, Any] = Field(default_factory=dict)
    confinement: str = "none"
    quiescent: bool = False
    role_dropped: bool = False
    child_exit: int | None = None


class LiveTrace(BaseModel):
    scenario: str
    requested_schedule: list[str]
    chosen_schedule: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    records: list[OpRecord] = Field(default_factory=list)
    actors: list[ActorResult] = Field(default_factory=list)
    connections: dict[str, int] = Field(default_factory=dict)
    refusals: list[Refusal] = Field(default_factory=list)
    outcome: LiveOutcome = "completed"
    inconclusive: bool = False
    initial_state: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    final_state: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)
    leaked_threads: list[str] = Field(default_factory=list)
    sessions_left_open: int = 0
    external_blockers: list[int] = Field(default_factory=list)
    evidence: RunEvidence = Field(default_factory=RunEvidence)
    duration_ms: float = 0.0

    @property
    def opaque_steps(self) -> int:
        return sum(r.kind == "opaque_batch" for r in self.records)

    @property
    def statement_level_coverage(self) -> bool:
        """False when any batch ran as one opaque step, whatever else the run did."""
        return self.opaque_steps == 0

    @property
    def scoreable(self) -> bool:
        """Only a supervised run whose control and evidence were all verified can be scored."""
        evidence = self.evidence
        return (
            evidence.supervised and not evidence.flags and not self.inconclusive
            and evidence.quiescent and evidence.role_dropped and not self.leaked_threads
            and self.sessions_left_open == 0
        )

    def results(self) -> dict[str, Any]:
        return {a.actor: a.value for a in self.actors}

    def fingerprint(self) -> str:
        """Hash of observable behaviour, with no clock or process id, so a replay can match it exactly."""
        payload = {
            "chosen": self.chosen_schedule,
            # Not "requested" or "source": they describe how the choice was made, not what happened.
            "decisions": [d.model_dump(exclude={"requested", "source"}) for d in self.decisions],
            "records": [
                r.model_dump(exclude={"admitted_ms", "completed_ms", "backend_pid", "error", "provenance"})
                for r in self.records
            ],
            "actors": [a.model_dump() for a in self.actors],
            "outcome": self.outcome,
            "final_state": self.final_state,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
