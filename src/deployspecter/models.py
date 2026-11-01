"""Typed data model shared by every test generator and by the execution engine.

Two layers are kept apart on purpose:

* ``TestProposal`` is *declarative*. Humans, the non-AI baseline and Muse Spark
  all produce this format. It can only reference statements extracted from the
  submitted source by ``statement_id``; it cannot contain SQL.
* ``ExecutableSpec`` is what ``compile_proposal`` produces after validation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Scalar = str | int | float | bool | None
Transform = Literal["none", "strip", "lower", "upper", "title", "add", "multiply"]


class ParamBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    source: Literal["literal", "row_ref"]
    value: Scalar = None
    bind: str | None = None
    row: int = 0
    column: str | None = None
    transform: Transform = "none"
    operand: float | None = None


class StatementCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement_id: str
    params: list[ParamBinding] = Field(default_factory=list)
    bind_as: str | None = None


class ActorProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    function: str
    calls: list[StatementCall]


class ColumnValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    value: Scalar


class SeedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    values: list[ColumnValue]


InvariantKind = Literal[
    "column_equals",
    "column_in",
    "column_is_null",
    "column_not_null",
    "row_exists",
    "row_absent",
    "column_equals_column",
]


class InvariantProposal(BaseModel):
    """A candidate invariant: a hypothesis, never ground truth."""

    model_config = ConfigDict(extra="forbid")
    id: str
    description: str
    kind: InvariantKind
    table: str
    key_column: str
    key_value: Scalar
    column: str | None = None
    expected: list[Scalar] = Field(default_factory=list)
    other_column: str | None = None


class TestProposal(BaseModel):
    __test__ = False  # not a pytest class
    model_config = ConfigDict(extra="forbid")
    name: str
    rationale: str = ""
    seed: list[SeedRow]
    actors: list[ActorProposal]
    invariants: list[InvariantProposal] = Field(default_factory=list)


# ---------------------------------------------------------------- executable


class Step(BaseModel):
    kind: Literal["begin", "commit", "rollback", "sql"]
    sql: str | None = None
    params: dict[str, ParamBinding] = Field(default_factory=dict)
    bind_as: str | None = None
    statement_id: str | None = None
    # True when the source can skip this statement (branch or zero loop iterations).
    removable: bool = False


class Actor(BaseModel):
    name: str
    function: str
    steps: list[Step]


class ExecutableSpec(BaseModel):
    name: str
    generator: str
    seed: list[SeedRow]
    actors: list[Actor]
    invariants: list[InvariantProposal] = Field(default_factory=list)
    rationale: str = ""

    def step_counts(self) -> dict[str, int]:
        return {a.name: len(a.steps) for a in self.actors}

    def digest(self) -> str:
        payload = self.model_dump(exclude={"rationale", "generator", "name"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


# --------------------------------------------------------------------- traces

StepStatus = Literal["ok", "error", "skipped_no_rows", "skipped_after_abort"]
Outcome = Literal["completed", "transaction_conflict", "execution_error", "timeout", "stalled"]


class StepRecord(BaseModel):
    seq: int
    actor: str
    step_index: int
    kind: str
    function: str
    statement_id: str | None = None
    sql: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = "ok"
    rowcount: int | None = None
    rows: list[dict[str, Any]] | None = None
    command_tag: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    sqlstate: str | None = None
    error: str | None = None
    committed: bool | None = None
    commit_seq: int | None = None
    # Monotonic position of the commit that made this write durable (sql steps).
    durable_commit_seq: int | None = None


class ExecutionTrace(BaseModel):
    requested_schedule: list[str]
    issue_order: list[str]
    records: list[StepRecord]
    outcome: Outcome
    initial_state: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    final_state: dict[str, list[dict[str, Any]]]
    diagnostics: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0

    def fingerprint(self) -> str:
        """Hash of observable behaviour. Excludes timing so replays can match."""
        payload = {
            "issue_order": self.issue_order,
            "records": [
                r.model_dump(include={
                    "seq", "actor", "step_index", "kind", "statement_id", "params", "status",
                    "rowcount", "rows", "command_tag", "blocked_by", "sqlstate", "committed",
                    "commit_seq", "durable_commit_seq",
                })
                for r in self.records
            ],
            "outcome": self.outcome,
            "final_state": self.final_state,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------- oracle


class WriterDecl(BaseModel):
    """Declares that a source function performs an authoritative business write."""

    model_config = ConfigDict(extra="forbid")
    function: str
    key_param: str
    value_param: str | None = None
    # Reference-model mapping from the written parameter to the expected column value.
    value_transform: Literal["none", "lower"] = "none"


class SourceOp(BaseModel):
    """How a source function changes a replicated table, for the reference replay."""

    model_config = ConfigDict(extra="forbid")
    function: str
    op: Literal["insert", "delete", "update", "backfill"]
    params: dict[str, str]


class Requirement(BaseModel):
    """Hand-written business requirement. Never shown to any test generator."""

    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    kind: str  # names a check in oracle._CHECKS
    table: str
    key_column: str
    column: str
    writers: list[WriterDecl] = Field(default_factory=list)
    # committed_delta_sum: expected = seed(base_column) * base_multiplier + sum(deltas)
    base_column: str | None = None
    base_multiplier: float = 1.0
    # replica_* kinds: `table` is kept in sync with `source_table`, one row per `key_columns`.
    source_table: str | None = None
    key_columns: list[str] = Field(default_factory=list)
    source_ops: list[SourceOp] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Violation(BaseModel):
    requirement_id: str
    requirement: str
    table: str
    key: Any
    column: str
    expected: Any
    actual: Any
    evidence: list[int] = Field(default_factory=list)
    explanation: str


class OracleVerdict(BaseModel):
    classification: Literal[
        "true_violation", "no_violation", "vacuous", "transaction_conflict", "execution_error"
    ]
    violations: list[Violation] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list)
    vacuous: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
