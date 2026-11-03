"""Logical state goals to candidate rows. Research only, offline, pure and unfrozen (step 5B).

``plan_state`` turns the state goals of a genuine ``ValidatedWorkload`` into **candidate** rows, using a trusted schema
context that harness code writes and pins. A candidate is only that: every result is pending independent PostgreSQL
validation, which this module never performs. It reads no database, network or file, imports nothing a proposal
names, never sees an oracle, requirement, schedule or defect, and emits no SQL or code: rows hold trusted identifiers
and typed values only.

Five outcomes, with strict meanings:

* ``CandidatePlan``: every goal has candidate witness rows under the stated assumptions. Not a construction.
* ``UnsupportedGoal``: some goal uses a feature whose meaning can't be established offline, or needs a value no trusted
  policy supplies. Says nothing about feasibility.
* ``InvalidTrustedContext``: the schema context isn't the pinned one, isn't bound to this registry and application, or
  is inconsistent. No goal was examined.
* ``InfeasibleUnderAssumptions``: a sound contradiction under the trusted context and the recorded assumptions: no
  rows could satisfy the goal, whatever any planner chose.
* ``Indeterminate``: a deterministic, operation-counted budget ran out. Never a claim of infeasibility.

A workload that isn't genuine ``validate_arguments`` output isn't planned at all: ``UntrustedWorkload`` is raised.
These checks are against mistakes and forged data objects, not hostile code in the same process.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator

from . import registry as rg
from . import workload_identity as wi
from . import workload_spec as ws
from .argument_validation import Contracts, ValidatedWorkload, check_validated

CONTEXT_VERSION = 1
PLANNER_VERSION = "state-planning/1"
RULES_VERSION = 1
CONTEXT_DOMAIN = b"deployspecter:schema-context:v1\x00"
REQUEST_DOMAIN = b"deployspecter:state-planning-request:v1\x00"
FEASIBILITY = "candidate_pending_postgresql_validation"
MAX_PLAN_ROWS = ws.MAX_TOTAL_GOAL_ROWS  # version 2's own limit, 64
# Phase 9B's constructor accepts at most this many seed rows (the release validator's MAX_SEED_ROWS). It is recorded
# against each request, never adopted as a planning limit, and nothing is truncated to fit it.
PHASE9B_CONSTRUCTOR_MAX_ROWS = 10

HEX64 = r"^[0-9a-f]{64}$"
SQL_NAME = r"^[a-z_][a-z0-9_]{0,62}$"
Identifier = Annotated[str, Field(pattern=ws.IDENTIFIER)]
SqlName = Annotated[str, Field(pattern=SQL_NAME)]

PgType = Literal["smallint", "integer", "bigint", "double precision", "boolean", "text", "varchar", "numeric",
                 "opaque"]
INTEGER_RANGES = {"smallint": (-(2**15), 2**15 - 1), "integer": (-(2**31), 2**31 - 1), "bigint": (-(2**63), 2**63 - 1)}
# The one version 2 type each column type takes without a cast. numeric and opaque take none.
ACCEPTS: dict[str, str] = {"smallint": "int", "integer": "int", "bigint": "int", "double precision": "float",
                           "boolean": "bool", "text": "str", "varchar": "str"}
_PY = {"int": int, "float": float, "bool": bool, "str": str}
ORDER_OPS = {"lt", "le", "gt", "ge"}

SQL_NULL = "goal constraints are SQL predicates: a NULL satisfies only is_null"
COUNT_DISTINCT = "count is a number of distinct witness rows; an exact final count is checked only by PostgreSQL"
CONTEXT_EXACT = "the trusted context describes the schema exactly; nothing it omits is inferred"
ASSUMPTIONS = (SQL_NULL, COUNT_DISTINCT, CONTEXT_EXACT)
# Collation, NULL handling in unique constraints and the database encoding are trusted facts the context must state,
# never assumptions the planner makes.


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ------------------------------------------------------------------ trusted schema context


class Filler(_Strict):
    """A constant the harness approves for a column no goal constrains. The planner never invents one."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    type: Literal["int", "float", "bool", "str"]
    value: Any

    @model_validator(mode="after")
    def _exact(self) -> Filler:
        if type(self.value) is not _PY[self.type]:
            raise ValueError(f"filler tagged {self.type} holds {type(self.value).__name__}")
        if self.type == "float" and not math.isfinite(self.value):
            raise ValueError("a float filler must be finite")
        if self.type == "float" and self.value == 0.0 and math.copysign(1.0, self.value) < 0:
            raise ValueError("a float filler can't be negative zero")
        if self.type == "str" and (len(self.value) > ws.MAX_STRING_VALUE or ws.text_problems(self.value)):
            raise ValueError("a string filler must be safe text of at most 128 characters")
        return self


class TrustedColumn(_Strict):
    field: Identifier  # the name goals use
    column: SqlName  # the database column, for the later database step only
    pg_type: PgType
    max_length: StrictInt | None = Field(default=None, ge=1, le=10_485_760)  # varchar only
    nullable: StrictBool
    has_default: StrictBool = False  # declared by the harness, never inferred
    generated: StrictBool = False  # identity or generated: the database sets it
    filler: Filler | None = None
    # Required for text and varchar, absent otherwise. Equality on a nondeterministic collation isn't planned.
    collation: Literal["deterministic", "nondeterministic"] | None = None

    @model_validator(mode="after")
    def _sound(self) -> TrustedColumn:
        if (self.pg_type == "varchar") != (self.max_length is not None):
            raise ValueError(f"{self.field}: max_length is given exactly for varchar")
        if (self.pg_type in {"text", "varchar"}) != (self.collation is not None):
            raise ValueError(f"{self.field}: collation is stated exactly for text and varchar")
        if self.filler is not None:
            if self.generated:
                raise ValueError(f"{self.field}: a generated column can't have a filler")
            if ACCEPTS.get(self.pg_type) != self.filler.type:
                raise ValueError(f"{self.field}: a {self.filler.type} filler doesn't fit {self.pg_type}")
            if self.pg_type in INTEGER_RANGES:
                low, high = INTEGER_RANGES[self.pg_type]
                if not low <= self.filler.value <= high:
                    raise ValueError(f"{self.field}: the filler is outside {self.pg_type}")
            if self.max_length is not None and len(self.filler.value) > self.max_length:
                raise ValueError(f"{self.field}: the filler is longer than varchar({self.max_length})")
        return self


class UniqueConstraint(_Strict):
    """A unique constraint with its NULL handling stated, as PostgreSQL 15 and later allow either way."""

    fields: list[Identifier] = Field(min_length=1, max_length=8)
    nulls: Literal["distinct", "not_distinct"]  # no default: the harness must say which


class ForeignKey(_Strict):
    field: Identifier
    references_entity: Identifier
    references_field: Identifier


class TrustedEntity(_Strict):
    entity: Identifier
    table: SqlName
    columns: list[TrustedColumn] = Field(min_length=1, max_length=64)
    primary_key: list[Identifier] = Field(default_factory=list, max_length=8)
    unique: list[UniqueConstraint] = Field(default_factory=list, max_length=16)
    foreign_keys: list[ForeignKey] = Field(default_factory=list, max_length=16)
    trigger_written: StrictBool = False  # some trigger may write rows of this table
    has_insert_triggers: StrictBool = False  # inserting rows here fires triggers
    key_assignment: Literal["ascending_int"] | None = None

    @model_validator(mode="after")
    def _sound(self) -> TrustedEntity:
        fields = [c.field for c in self.columns]
        if len(set(fields)) != len(fields) or len({c.column for c in self.columns}) != len(self.columns):
            raise ValueError(f"{self.entity}: repeated field or column")
        known = set(fields)
        for name in [*self.primary_key, *(f for u in self.unique for f in u.fields),
                     *(k.field for k in self.foreign_keys)]:
            if name not in known:
                raise ValueError(f"{self.entity}: {name!r} isn't a column")
        if len(set(self.primary_key)) != len(self.primary_key):
            raise ValueError(f"{self.entity}: repeated primary-key field")
        for u in self.unique:
            if len(set(u.fields)) != len(u.fields):
                raise ValueError(f"{self.entity}: a repeated field in a unique constraint")
        by_field = {c.field: c for c in self.columns}
        if any(by_field[f].nullable for f in self.primary_key):
            raise ValueError(f"{self.entity}: a primary-key column can't be nullable")
        if self.key_assignment is not None:
            if len(self.primary_key) != 1:
                raise ValueError(f"{self.entity}: key assignment needs a single-column primary key")
            key = by_field[self.primary_key[0]]
            if key.pg_type not in INTEGER_RANGES or key.generated:
                raise ValueError(f"{self.entity}: key assignment needs a non-generated integer key")
        return self


class SchemaContextConfig(_Strict):
    """What the harness says about one application's schema. Metadata only: no SQL, triggers or defaults inferred."""

    context_version: StrictInt
    application: Identifier
    registry_digest: str = Field(pattern=HEX64)
    schema_digest: str = Field(pattern=HEX64)  # of the catalog snapshot the harness wrote this from
    encoding: Literal["UTF8"]  # varchar lengths are checked in code points, which is right only in UTF-8
    entities: list[TrustedEntity] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _sound(self) -> SchemaContextConfig:
        if self.context_version != CONTEXT_VERSION:
            raise ValueError(f"context_version must be {CONTEXT_VERSION}")
        names = [e.entity for e in self.entities]
        if len(set(names)) != len(names) or len({e.table for e in self.entities}) != len(names):
            raise ValueError("repeated entity or table")
        by_name = {e.entity: e for e in self.entities}
        for entity in self.entities:
            columns = {c.field: c for c in entity.columns}
            for key in entity.foreign_keys:
                target = by_name.get(key.references_entity)
                if target is None:
                    raise ValueError(f"{entity.entity}.{key.field} references unknown {key.references_entity!r}")
                target_columns = {c.field: c for c in target.columns}
                referenced = target_columns.get(key.references_field)
                if referenced is None:
                    raise ValueError(f"{entity.entity}.{key.field} references an unknown field")
                if [key.references_field] != target.primary_key and \
                        [key.references_field] not in [u.fields for u in target.unique]:
                    raise ValueError(f"{entity.entity}.{key.field} must reference a single-column key")
                if referenced.pg_type != columns[key.field].pg_type:
                    raise ValueError(f"{entity.entity}.{key.field} and its referenced field differ in type")
        return self


class TrustedPlanning(_Strict):
    """Built by harness code. The pinned digest must come from somewhere other than the context it checks."""

    reviewer: Identifier
    pinned_digest: str = Field(pattern=HEX64)


class ContextError(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class UntrustedWorkload(ValueError):
    """The workload isn't genuine validate_arguments output for these trusted objects, so nothing is planned."""


def context_digest(config: SchemaContextConfig) -> str:
    data = config.model_dump(mode="json")
    for entity in data["entities"]:
        entity["columns"].sort(key=lambda c: c["field"])
        entity["unique"] = sorted(({"fields": sorted(u["fields"]), "nulls": u["nulls"]} for u in entity["unique"]),
                                  key=lambda u: (u["fields"], u["nulls"]))
        entity["foreign_keys"].sort(key=lambda k: (k["field"], k["references_entity"], k["references_field"]))
    data["entities"].sort(key=lambda e: e["entity"])
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(CONTEXT_DOMAIN + text.encode("utf-8")).hexdigest()


class PlanningContext:
    """A verified context. Build one with ``build_context``."""

    def __init__(self, config: SchemaContextConfig, digest: str, trusted: TrustedPlanning) -> None:
        self.config, self.digest, self.trusted = config, digest, trusted
        self.entities = {e.entity: e for e in config.entities}


def build_context(config: Any, *, registry: rg.Registry, trusted: Any) -> PlanningContext:
    """Verify a context against the trusted pin, the registry and its application. Any disagreement fails closed."""
    problems: list[str] = []
    try:
        trusted = rg.revalidate_model(TrustedPlanning, trusted) if isinstance(trusted, TrustedPlanning) else None
    except Exception:  # a forged trust object may fail to serialize in several ways
        trusted = None
    if trusted is None:
        raise ContextError(["a TrustedPlanning built by harness code is required"])
    try:
        parsed = rg.revalidate_model(SchemaContextConfig, config)
    except Exception as exc:  # text policy, validation, or a forged instance
        if isinstance(exc, ValidationError):
            reasons = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        else:
            reasons = [ws.safe_repr(str(exc), 300)]
        raise ContextError(reasons) from exc
    digest = context_digest(parsed)
    if digest != trusted.pinned_digest:
        problems.append("the schema context's digest isn't the pinned one")
    if parsed.registry_digest != registry.digest:
        problems.append("the schema context is bound to a different registry")
    if parsed.application != registry.application:
        problems.append("the schema context is for a different application")
    if problems:
        raise ContextError(problems)
    return PlanningContext(parsed, digest, trusted)


# ------------------------------------------------------------------ budget and results


class PlanningBudget(_Strict):
    """Operation-counted limits. There's no time limit, so results don't depend on the machine."""

    max_rows: StrictInt = Field(default=MAX_PLAN_ROWS, ge=1, le=MAX_PLAN_ROWS)
    max_expansions: StrictInt = Field(default=4096, ge=1, le=1_000_000)  # rows built and value-search steps
    max_constraint_checks: StrictInt = Field(default=65_536, ge=1, le=10_000_000)


class Counters(_Strict):
    rows: int
    expansions: int
    constraint_checks: int
    max_rows: int
    max_expansions: int
    max_constraint_checks: int


GoalState = Literal["planned", "unsupported", "infeasible", "not_examined"]
Origin = Literal["goal_constraint", "key_assignment", "comparison_choice", "filler"]
OmitReason = Literal["database_default", "generated", "nullable_unset"]
ValueTag = Literal["int", "float", "bool", "str", "null"]


class InfeasibilityEvidence(_Strict):
    """What an infeasibility rests on: the goals, fields and constraints involved, and the trusted facts and
    assumptions that make them contradict."""

    goals: tuple[str, ...]
    entity: str
    fields: tuple[str, ...]
    constraints: tuple[str, ...]
    context_facts: tuple[str, ...]
    assumptions: tuple[str, ...]


class GoalStatus(_Strict):
    label: str
    entity: str
    status: GoalState
    code: str | None = None
    reason: str | None = None
    rows: tuple[str, ...] = ()
    # Rows built for other goals that also satisfy this goal offline, or whose match the database must decide.
    also_matching_rows: tuple[str, ...] = ()
    undetermined_rows: tuple[str, ...] = ()
    evidence: InfeasibilityEvidence | None = None  # for infeasible goals only


class CandidateValue(_Strict):
    field: str
    column: str
    type: ValueTag
    value: Any
    origin: Origin

    @model_validator(mode="after")
    def _exact(self) -> CandidateValue:
        if self.type == "null" and self.value is not None:
            raise ValueError("a null value holds None")
        if self.type != "null" and type(self.value) is not _PY[self.type]:
            raise ValueError(f"value tagged {self.type} holds {type(self.value).__name__}")
        return self


class OmittedColumn(_Strict):
    field: str
    column: str
    reason: OmitReason


class CandidateRow(_Strict):
    row_id: str  # "<goal label>#<ordinal>"
    goal_label: str
    ordinal: int
    entity: str
    table: str
    values: tuple[CandidateValue, ...]
    omitted: tuple[OmittedColumn, ...]
    obligations: tuple[str, ...]
    context_digest: str
    plan_identity: str


class Dependency(_Strict):
    child_row: str
    parent_row: str
    field: str
    references_entity: str
    references_field: str


class StatePlanningRequest(_Strict):
    request_version: Literal[1] = 1
    planner_version: str
    rules_version: int
    feasibility: Literal["candidate_pending_postgresql_validation"]
    plan_identity: str
    registry_digest: str
    contracts_digest: str
    context_digest: str
    schema_digest: str
    application: str
    rows: tuple[CandidateRow, ...]
    dependencies: tuple[Dependency, ...]
    obligations: tuple[str, ...]
    assumptions: tuple[str, ...]
    phase9b_constructor_compatible: bool
    phase9b_notes: tuple[str, ...]
    request_digest: str


class CandidatePlan(_Strict):
    outcome: Literal["candidate_plan"] = "candidate_plan"
    request: StatePlanningRequest
    goals: tuple[GoalStatus, ...]
    counters: Counters


class UnsupportedGoal(_Strict):
    outcome: Literal["unsupported_goal"] = "unsupported_goal"
    goal_label: str
    code: str
    reason: str
    goals: tuple[GoalStatus, ...]
    counters: Counters


class InvalidTrustedContext(_Strict):
    outcome: Literal["invalid_trusted_context"] = "invalid_trusted_context"
    reasons: tuple[str, ...]
    counters: Counters


class InfeasibleUnderAssumptions(_Strict):
    outcome: Literal["infeasible_under_assumptions"] = "infeasible_under_assumptions"
    goal_label: str
    code: str
    reason: str
    evidence: InfeasibilityEvidence
    assumptions: tuple[str, ...]  # the ones this contradiction relies on
    goals: tuple[GoalStatus, ...]
    counters: Counters


class Indeterminate(_Strict):
    outcome: Literal["indeterminate"] = "indeterminate"
    code: str
    reason: str
    goals: tuple[GoalStatus, ...]
    counters: Counters


PlanningResult = CandidatePlan | UnsupportedGoal | InvalidTrustedContext | InfeasibleUnderAssumptions | Indeterminate


# ------------------------------------------------------------------ planning internals


class _Exhausted(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class _Problem(Exception):
    def __init__(self, status: Literal["unsupported", "infeasible"], code: str, reason: str,
                 evidence: InfeasibilityEvidence | None = None) -> None:
        super().__init__(reason)
        self.status, self.code, self.reason, self.evidence = status, code, reason, evidence


def _unsupported(code: str, reason: str) -> _Problem:
    return _Problem("unsupported", code, reason)


def _infeasible(code: str, reason: str, evidence: InfeasibilityEvidence) -> _Problem:
    """Only with evidence: an infeasibility must show the contradiction it claims."""
    return _Problem("infeasible", code, reason, evidence)


def _render(constraints) -> tuple[str, ...]:
    return tuple(f"{c.field} {c.op}" + ("" if c.value is None else f" {_encode(c.value.type, ws.plain_value(c.value))}")
                 for c in constraints)


class _Meter:
    def __init__(self, budget: PlanningBudget) -> None:
        self.budget = budget
        self.used = {"rows": 0, "expansions": 0, "constraint_checks": 0}
        self.limits = {"rows": budget.max_rows, "expansions": budget.max_expansions,
                       "constraint_checks": budget.max_constraint_checks}

    def spend(self, kind: str) -> None:
        if self.used[kind] >= self.limits[kind]:
            raise _Exhausted(kind)
        self.used[kind] += 1

    def counters(self) -> Counters:
        return Counters(**self.used, max_rows=self.limits["rows"], max_expansions=self.limits["expansions"],
                        max_constraint_checks=self.limits["constraint_checks"])


def _encode(tag: str, value: Any) -> str:
    if tag == "null":
        return "null"
    if tag == "float":
        return "float:" + value.hex()
    if tag == "bool":
        return "bool:" + ("true" if value else "false")
    return f"{tag}:{value}"


class _FieldPlan:
    """How one field of one goal gets its value."""

    def __init__(self, kind: str, column: TrustedColumn, *, tag: str = "null", value: Any = None,
                 origin: Origin = "goal_constraint", lo: Any = None, hi: Any = None, start: str = "low",
                 excluded: frozenset = frozenset(), unique: bool = False, endpoint_start: bool = False,
                 omit: OmitReason | None = None, evidence: Any = None) -> None:
        self.kind, self.column, self.tag, self.value, self.origin = kind, column, tag, value, origin
        self.lo, self.hi, self.start, self.excluded, self.unique = lo, hi, start, excluded, unique
        self.endpoint_start, self.omit, self.evidence = endpoint_start, omit, evidence


def _canonical_constraints(goal: ws.StateGoal) -> list[ws.GoalConstraint]:
    def key(c: ws.GoalConstraint) -> tuple:
        value = "" if c.value is None else _encode(c.value.type, ws.plain_value(c.value))
        return c.field, c.op, value
    return sorted(goal.constraints, key=key)


def _next_float(value: float, upward: bool) -> float:
    step = math.nextafter(value, math.inf if upward else -math.inf)
    return 0.0 if step == 0.0 else step  # never hand out negative zero


class _Planner:
    def __init__(self, validated: ValidatedWorkload, context: PlanningContext, meter: _Meter,
                 identity: str, registry: rg.Registry, contracts: Contracts) -> None:
        self.validated, self.context, self.meter, self.identity = validated, context, meter, identity
        self.registry, self.contracts = registry, contracts
        self.goals = sorted(validated.authorized.state_goals, key=lambda g: g.label)
        self.statuses: dict[str, GoalStatus] = {
            g.label: GoalStatus(label=g.label, entity=g.entity, status="not_examined") for g in self.goals
        }
        self.assumptions: list[str] = list(ASSUMPTIONS)

    def _set(self, goal: ws.StateGoal, status: GoalState, **fields: Any) -> None:
        """A sound infeasibility is never downgraded, and a problem is never undone by a later step."""
        current = self.statuses[goal.label].status
        if current == "infeasible" and status != "infeasible":
            return
        if current == "unsupported" and status == "planned":
            return
        self.statuses[goal.label] = self.statuses[goal.label].model_copy(update={"status": status, **fields})

    def _goals(self) -> tuple[GoalStatus, ...]:
        return tuple(self.statuses[g.label] for g in self.goals)

    def _fail(self, goal: ws.StateGoal, problem: _Problem) -> None:
        self._set(goal, problem.status, code=problem.code, reason=problem.reason, evidence=problem.evidence)

    @staticmethod
    def _column_facts(entity: TrustedEntity, column: TrustedColumn) -> list[str]:
        name = f"{entity.entity}.{column.field}"
        kind = column.pg_type + (f"({column.max_length})" if column.max_length is not None else "")
        facts = [f"{name} is {kind}, {'nullable' if column.nullable else 'NOT NULL'}"]
        if column.pg_type in INTEGER_RANGES:
            low, high = INTEGER_RANGES[column.pg_type]
            facts.append(f"{column.pg_type} holds {low} to {high}")
        if column.pg_type == "double precision":
            facts.append("double precision holds the finite IEEE-754 binary64 values")
        if column.collation is not None:
            facts.append(f"{name} has a {column.collation} collation")
        return facts

    def _evidence(self, goal, entity, fields, constraints, facts, assumptions) -> InfeasibilityEvidence:
        return InfeasibilityEvidence(goals=(goal.label,), entity=entity.entity, fields=tuple(fields),
                                     constraints=_render(constraints), context_facts=tuple(facts),
                                     assumptions=tuple(a for a in ASSUMPTIONS if a in assumptions))

    # ---------------------------------------------------------------- per goal, per field

    def _unique_fields(self, entity: TrustedEntity) -> set[str]:
        found = {u.fields[0] for u in entity.unique if len(u.fields) == 1}
        if len(entity.primary_key) == 1:
            found.add(entity.primary_key[0])
        return found

    def _resolve_goal(self, goal: ws.StateGoal) -> tuple[TrustedEntity, dict[str, _FieldPlan]]:
        entity = self.context.entities.get(goal.entity)
        if entity is None:
            raise _unsupported("unknown_entity", f"the trusted context has no entity {goal.entity!r}")
        if goal.count == 0:
            raise _unsupported("absence_claim", "count 0 claims no such rows exist, which only PostgreSQL can check")
        columns = {c.field: c for c in entity.columns}
        by_field: dict[str, list[ws.GoalConstraint]] = {}
        for constraint in _canonical_constraints(goal):
            self.meter.spend("constraint_checks")
            column = columns.get(constraint.field)
            if column is None:
                raise _unsupported("unknown_field", f"{goal.entity} has no field {constraint.field!r}")
            if column.generated:
                raise _unsupported("generated_column", f"{constraint.field} is set by the database")
            if constraint.value is not None:
                self._check_literal(goal, column, constraint)
            by_field.setdefault(constraint.field, []).append(constraint)
        unique = self._unique_fields(entity)
        plans: dict[str, _FieldPlan] = {}
        for column in entity.columns:
            constraints = by_field.get(column.field, [])
            is_key = (entity.key_assignment is not None and entity.primary_key == [column.field])
            if constraints:
                plans[column.field] = self._plan_constrained(goal, entity, column, constraints,
                                                             column.field in unique, is_key)
            else:
                plans[column.field] = self._plan_unconstrained(goal, entity, column, column.field in unique, is_key)
        return entity, plans

    def _check_literal(self, goal: ws.StateGoal, column: TrustedColumn, constraint: ws.GoalConstraint) -> None:
        tag = constraint.value.type
        if tag == "null":
            raise _unsupported("null_comparison", f"{constraint.field} {constraint.op} null has no SQL meaning; "
                                                  "use is_null")
        if column.pg_type == "numeric":
            code = "numeric_coercion" if tag == "float" else "cast_dependent"
            raise _unsupported(code, f"{constraint.field} is numeric; a {tag} value would need a cast")
        if column.pg_type == "opaque":
            raise _unsupported("opaque_type", f"{constraint.field} has a type the planner can't compare")
        if ACCEPTS[column.pg_type] != tag:
            raise _unsupported("cast_dependent", f"{constraint.field} is {column.pg_type}; a {tag} value would need "
                                                 "a cast")
        if constraint.op in ORDER_OPS and tag == "str":
            raise _unsupported("collation_dependent_order", f"{constraint.field} {constraint.op}: string order "
                                                            "depends on the collation")
        if constraint.op in ORDER_OPS and tag == "bool":
            raise _unsupported("boolean_order", f"{constraint.field} {constraint.op}: booleans have no order here")
        if tag == "str" and column.collation == "nondeterministic":
            raise _unsupported("collation_dependent_equality", f"{constraint.field} {constraint.op}: equality "
                                                               "depends on its nondeterministic collation")

    def _plan_constrained(self, goal, entity, column, constraints, unique, is_key) -> _FieldPlan:
        ops = [c.op for c in constraints]
        values = [(c.op, ws.plain_value(c.value)) for c in constraints if c.value is not None]
        fixed = [v for op, v in values if op == "eq"]
        excluded = frozenset(v for op, v in values if op == "ne")
        lower = [(v, op == "gt") for op, v in values if op in {"gt", "ge"}]
        upper = [(v, op == "lt") for op, v in values if op in {"lt", "le"}]
        tag = ACCEPTS.get(column.pg_type, "null")
        base = self._column_facts(entity, column)

        def evidence(*facts: str, assumptions: tuple[str, ...] = (CONTEXT_EXACT,)) -> InfeasibilityEvidence:
            return self._evidence(goal, entity, [column.field], constraints, [*base, *facts], assumptions)

        if "is_null" in ops:
            if not column.nullable:
                raise _infeasible("not_null_violated", f"{column.field} is NOT NULL, so it can't be null",
                                  evidence())
            if any(c.op != "is_null" for c in constraints):  # a repeated is_null contradicts nothing
                raise _infeasible("null_with_comparison", f"{column.field} is null, and a NULL satisfies no other "
                                                          "constraint", evidence(assumptions=(SQL_NULL, CONTEXT_EXACT)))
            return _FieldPlan("value", column, tag="null", value=None)
        if fixed:
            if len({_encode(tag, v) for v in fixed}) > 1:
                raise _infeasible("contradictory_values", f"{column.field} must equal two different values",
                                  evidence())
            value = fixed[0]
            self._check_range(column, value, evidence)
            if not self._satisfies(value, lower, upper, excluded):
                raise _infeasible("bounds_exclude_value", f"{column.field} = {value!r} breaks its other constraints",
                                  evidence())
            return _FieldPlan("value", column, tag=tag, value=value, unique=unique)
        if lower or upper or excluded:
            return self._plan_choice(column, tag, lower, upper, excluded, unique, is_key, goal.count, evidence)
        # only not_null
        return self._plan_unconstrained(goal, entity, column, unique, is_key, needs_value=True)

    def _plan_unconstrained(self, goal, entity, column, unique, is_key, needs_value=False) -> _FieldPlan:
        if column.generated:
            return _FieldPlan("omit", column, omit="generated")
        if is_key:
            def evidence(*facts: str, assumptions: tuple[str, ...] = (CONTEXT_EXACT,)) -> InfeasibilityEvidence:
                return self._evidence(goal, entity, [column.field], [], [*self._column_facts(entity, column), *facts],
                                      assumptions)
            return self._plan_choice(column, "int", [], [], frozenset(), True, True, goal.count, evidence)
        if not needs_value and column.has_default:
            return _FieldPlan("omit", column, omit="database_default")
        if not needs_value and column.nullable:
            return _FieldPlan("omit", column, omit="nullable_unset")
        if column.filler is not None:
            return _FieldPlan("value", column, tag=column.filler.type, value=column.filler.value, origin="filler",
                              unique=unique)
        if column.field in entity.primary_key:
            raise _unsupported("no_key_policy", f"{column.field} is a key with no value and no trusted key policy")
        raise _unsupported("no_filler_policy", f"{column.field} needs a value and no trusted policy supplies one")

    def _check_range(self, column: TrustedColumn, value: Any, evidence) -> None:
        if column.pg_type in INTEGER_RANGES:
            low, high = INTEGER_RANGES[column.pg_type]
            if not low <= value <= high:
                raise _infeasible("outside_type_range", f"{value} is outside {column.pg_type}", evidence())
        if column.max_length is not None and len(value) > column.max_length:
            raise _infeasible("too_long_for_column", f"{column.field} holds at most {column.max_length} characters",
                              evidence("the database encoding is UTF8, so varchar length counts code points"))

    @staticmethod
    def _satisfies(value, lower, upper, excluded) -> bool:
        if value in excluded:
            return False
        if any(value < bound or (strict and value == bound) for bound, strict in lower):
            return False
        return not any(value > bound or (strict and value == bound) for bound, strict in upper)

    def _plan_choice(self, column, tag, lower, upper, excluded, unique, is_key, count, evidence) -> _FieldPlan:
        if tag == "int":
            low, high = INTEGER_RANGES[column.pg_type]
            lo = max([low, *[b + 1 if s else b for b, s in lower]])
            hi = min([high, *[b - 1 if s else b for b, s in upper]])
            blocked = len({v for v in excluded if lo <= v <= hi})
            available = hi - lo + 1 - blocked if lo <= hi else 0
            if available < 1:
                raise _infeasible("empty_domain", f"no {column.pg_type} value satisfies {column.field}'s constraints",
                                  evidence())
            if unique and available < count:
                raise _infeasible("insufficient_distinct_values",
                                  f"{column.field} is unique and only {available} values satisfy it, not {count}",
                                  evidence(f"{column.field} is a single-column key or unique constraint",
                                           assumptions=(COUNT_DISTINCT, CONTEXT_EXACT)))
            if lower:
                return _FieldPlan("choose_int", column, tag="int", lo=lo, hi=hi, start="low", excluded=excluded,
                                  unique=unique, origin="comparison_choice", endpoint_start=True, evidence=evidence)
            if upper:
                return _FieldPlan("choose_int", column, tag="int", lo=lo, hi=hi, start="high", excluded=excluded,
                                  unique=unique, origin="comparison_choice", endpoint_start=True, evidence=evidence)
            first = max(lo, 1 if is_key else 0)
            return _FieldPlan("choose_int", column, tag="int", lo=first, hi=hi, start="low", excluded=excluded,
                              unique=unique, origin="key_assignment" if is_key else "comparison_choice",
                              evidence=evidence)
        if tag == "float":
            lo, hi = -math.inf, math.inf
            for bound, strict in lower:
                lo = max(lo, _next_float(bound, True) if strict else bound)
            for bound, strict in upper:
                hi = min(hi, _next_float(bound, False) if strict else bound)
            if lo > hi or (lo == hi and lo in excluded):
                raise _infeasible("empty_domain", f"no double precision value satisfies {column.field}", evidence())
            if (lower and not math.isfinite(lo)) or (upper and not math.isfinite(hi)):
                raise _unsupported("non_finite_value_required", f"only an infinite value satisfies {column.field}")
            if lower:
                return _FieldPlan("choose_float", column, tag="float", lo=lo, hi=hi, start="low", excluded=excluded,
                                  unique=unique, origin="comparison_choice", endpoint_start=True, evidence=evidence)
            if upper:
                return _FieldPlan("choose_float", column, tag="float", lo=lo, hi=hi, start="high",
                                  excluded=excluded, unique=unique, origin="comparison_choice", endpoint_start=True,
                                  evidence=evidence)
            return _FieldPlan("choose_float", column, tag="float", lo=0.0, hi=hi, start="low", excluded=excluded,
                              unique=unique, origin="comparison_choice", evidence=evidence)
        if tag == "bool":
            options = [v for v in (False, True) if v not in excluded]
            if not options:
                raise _infeasible("empty_domain", f"{column.field} excludes both booleans", evidence())
            if unique and len(options) < count:
                raise _infeasible("insufficient_distinct_values", f"{column.field} is unique with too few values",
                                  evidence(f"{column.field} is a single-column key or unique constraint",
                                           assumptions=(COUNT_DISTINCT, CONTEXT_EXACT)))
            return _FieldPlan("value", column, tag="bool", value=options[0], origin="comparison_choice", unique=unique)
        # str with only ne: a value can come only from a trusted filler
        if column.filler is not None and column.filler.value not in excluded:
            return _FieldPlan("value", column, tag="str", value=column.filler.value, origin="filler", unique=unique)
        raise _unsupported("string_value_required", f"{column.field} needs a string other than the excluded ones, "
                                                    "and no trusted filler supplies one")

    # ---------------------------------------------------------------- choosing values

    def _choose(self, plan: _FieldPlan, taken: set) -> Any:
        """A deterministic walk from one end of the domain. Each step is an expansion."""
        value = plan.lo if plan.start == "low" else plan.hi
        blocked_by_taken = False
        is_float = plan.kind == "choose_float"
        while (plan.lo <= value <= plan.hi) and (not is_float or math.isfinite(value)):
            self.meter.spend("expansions")
            if value not in plan.excluded and not (plan.unique and value in taken):
                return value
            blocked_by_taken |= value in taken
            if is_float:
                value = _next_float(value, plan.start == "low")
            else:
                value += 1 if plan.start == "low" else -1
        if is_float and not math.isfinite(value):
            # PostgreSQL's double precision holds Infinity, and version 2 can't: unsupported, never infeasible.
            raise _unsupported("non_finite_value_required", f"no finite value is left for {plan.column.field}")
        if plan.endpoint_start and not blocked_by_taken:
            # The walk started at one end of the domain and passed the other: every value was excluded.
            raise _infeasible("empty_domain", f"every value of {plan.column.field} was excluded", plan.evidence())
        raise _unsupported("planner_choice_collision", f"values chosen for other rows used up {plan.column.field}")

    # ---------------------------------------------------------------- the whole plan

    def run(self) -> PlanningResult:
        total = sum(g.count for g in self.goals)
        if total > self.meter.limits["rows"]:
            return Indeterminate(code="row_budget", reason=f"{total} rows requested, the budget allows "
                                                           f"{self.meter.limits['rows']}", goals=self._goals(),
                                 counters=self.meter.counters())
        resolved: dict[str, tuple[TrustedEntity, dict[str, _FieldPlan]]] = {}
        try:
            for goal in self.goals:
                try:
                    resolved[goal.label] = self._resolve_goal(goal)
                    self._set(goal, "planned")
                except _Problem as problem:
                    self._fail(goal, problem)
            decided = self._decided()
            if decided is not None:
                return decided
            rows = self._build_rows(resolved)
            decided = self._decided()
            if decided is not None:
                return decided
            dependencies = self._check_cross_rows(rows)
            decided = self._decided()
            if decided is not None:
                return decided
            self._cardinality(rows)
        except _Exhausted as exhausted:
            return Indeterminate(code=f"{exhausted.kind}_budget",
                                 reason=f"the {exhausted.kind} budget ran out; this is not a claim of infeasibility",
                                 goals=self._goals(), counters=self.meter.counters())
        return self._candidate(rows, dependencies)

    def _decided(self) -> PlanningResult | None:
        """Infeasibility is sound for the whole workload, so it outranks an unsupported goal."""
        for status in ("infeasible", "unsupported"):
            first = next((s for s in self._goals() if s.status == status), None)
            if first is None:
                continue
            if status == "infeasible":
                return InfeasibleUnderAssumptions(goal_label=first.label, code=first.code, reason=first.reason,
                                                  evidence=first.evidence, assumptions=first.evidence.assumptions,
                                                  goals=self._goals(), counters=self.meter.counters())
            return UnsupportedGoal(goal_label=first.label, code=first.code, reason=first.reason,
                                   goals=self._goals(), counters=self.meter.counters())
        return None

    def _build_rows(self, resolved) -> list[CandidateRow]:
        taken: dict[tuple[str, str], set] = {}
        for goal in self.goals:  # goal-fixed values claim their place first
            entity, plans = resolved[goal.label]
            for field, plan in plans.items():
                if plan.kind == "value" and plan.unique and plan.origin == "goal_constraint" and plan.value is not None:
                    taken.setdefault((entity.entity, field), set()).add(plan.value)
        rows: list[CandidateRow] = []
        for goal in self.goals:
            entity, plans = resolved[goal.label]
            row_ids = []
            try:
                for ordinal in range(goal.count):
                    self.meter.spend("rows")
                    self.meter.spend("expansions")
                    rows.append(self._row(goal, entity, plans, ordinal, taken))
                    row_ids.append(rows[-1].row_id)
            except _Problem as problem:
                self._fail(goal, problem)
                continue
            self._set(goal, "planned", rows=tuple(row_ids))
        return rows

    def _row(self, goal, entity, plans, ordinal, taken) -> CandidateRow:
        values, omitted = [], []
        for column in sorted(entity.columns, key=lambda c: c.field):
            plan = plans[column.field]
            if plan.kind == "omit":
                omitted.append(OmittedColumn(field=column.field, column=column.column, reason=plan.omit))
                continue
            if plan.kind == "value":
                value = plan.value
            else:
                value = self._choose(plan, taken.setdefault((entity.entity, column.field), set()))
                if plan.unique:
                    taken[(entity.entity, column.field)].add(value)
            values.append(CandidateValue(field=column.field, column=column.column, type=plan.tag, value=value,
                                         origin=plan.origin))
        obligations = ["postgresql_validation_required", "exact_count_unverified"]
        if entity.trigger_written:
            obligations.append("trigger_written_entity")
        if entity.has_insert_triggers:
            obligations.append("insert_triggers_unverified")
        obligations += sorted({f"{o.reason}_not_compared" for o in omitted})
        if any(f in {o.field for o in omitted} for fields, _, _ in self._key_sets(entity) for f in fields):
            obligations.append("unique_with_database_values")
        return CandidateRow(
            row_id=f"{goal.label}#{ordinal}", goal_label=goal.label, ordinal=ordinal, entity=entity.entity,
            table=entity.table, values=tuple(values), omitted=tuple(omitted), obligations=tuple(obligations),
            context_digest=self.context.digest, plan_identity=self.identity,
        )

    @staticmethod
    def _key_sets(entity: TrustedEntity) -> list[tuple[list[str], str, bool]]:
        """(fields, description, whether NULLs count as equal). A primary key holds no NULLs."""
        sets = [(entity.primary_key, "primary key", False)] if entity.primary_key else []
        return sets + [(u.fields, f"unique NULLS {'NOT DISTINCT' if u.nulls == 'not_distinct' else 'DISTINCT'}",
                        u.nulls == "not_distinct") for u in entity.unique]

    @staticmethod
    def _members(row: CandidateRow, fields: list[str]) -> list[tuple[str, Any, str]] | None:
        """(type, value, origin) per field, with an unset nullable column as NULL; None if the database decides."""
        present = {v.field: v for v in row.values}
        omitted = {o.field: o.reason for o in row.omitted}
        members = []
        for field in fields:
            if field in present:
                value = present[field]
                members.append((value.type, value.value, value.origin))
            elif omitted[field] == "nullable_unset":
                members.append(("null", None, "unset"))
            else:
                return None
        return members

    def _check_cross_rows(self, rows: list[CandidateRow]) -> list[Dependency]:
        by_entity: dict[str, list[CandidateRow]] = {}
        for row in rows:
            by_entity.setdefault(row.entity, []).append(row)
        for name, entity_rows in by_entity.items():
            entity = self.context.entities[name]
            for fields, kind, nulls_equal in self._key_sets(entity):
                seen: dict[tuple, list[tuple[CandidateRow, list]]] = {}
                for row in entity_rows:
                    self.meter.spend("constraint_checks")
                    members = self._members(row, fields)
                    if members is None:
                        continue  # a database-set value: for PostgreSQL to decide (an obligation on the row)
                    if not nulls_equal and any(value is None for _, value, _ in members):
                        continue  # NULLS DISTINCT: a row with a NULL here collides with nothing
                    key = tuple(_encode(tag, value) for tag, value, _ in members)
                    earlier = seen.setdefault(key, [])
                    # Same-goal duplicates first: they're the sound contradictions, and must be found even when
                    # another goal claimed the key earlier.
                    for other, other_members in sorted(earlier, key=lambda e: e[0].goal_label != row.goal_label):
                        self.meter.spend("constraint_checks")
                        self._duplicate(other, other_members, row, members, entity, fields, kind)
                    earlier.append((row, members))
        if any(s.status != "planned" for s in self._goals()):
            return []
        return self._foreign_keys(rows, by_entity)

    def _duplicate(self, first, first_members, second, members, entity, fields, kind) -> None:
        goal = next(g for g in self.goals if g.label == second.goal_label)
        origins = {origin for _, _, origin in [*first_members, *members]}
        if origins == {"goal_constraint"} and first.goal_label == second.goal_label:
            nulls = any(value is None for _, value, _ in members)
            constraints = [c for c in _canonical_constraints(goal) if c.field in fields]
            evidence = InfeasibilityEvidence(
                goals=(goal.label,), entity=entity.entity, fields=tuple(fields), constraints=_render(constraints),
                context_facts=(f"{entity.entity} has a {kind} constraint on ({', '.join(fields)})",),
                assumptions=tuple(a for a in ASSUMPTIONS if a in ({COUNT_DISTINCT, CONTEXT_EXACT} |
                                                                  ({SQL_NULL} if nulls else set()))))
            self._fail(goal, _infeasible("duplicate_key_within_goal",
                                         f"{goal.count} distinct rows can't share ({', '.join(fields)}) under its "
                                         f"{kind} constraint", evidence))
        elif origins == {"goal_constraint"}:
            self._set(goal, "unsupported", code="overlapping_goal_keys",
                      reason=f"goals {first.goal_label} and {second.goal_label} ask for rows with the same "
                             f"({', '.join(fields)}); merging witness rows isn't supported offline")
        elif "unset" in origins:
            self._set(goal, "unsupported", code="unset_nulls_collide",
                      reason=f"columns left unset are NULL, and ({', '.join(fields)}) is {kind}")
        else:
            code = "filler_collides_on_unique" if "filler" in origins else "planner_choice_collision"
            self._set(goal, "unsupported", code=code,
                      reason=f"values the planner or a filler supplied collide on ({', '.join(fields)})")

    def _foreign_keys(self, rows, by_entity) -> list[Dependency]:
        dependencies = []
        for row in rows:
            entity = self.context.entities[row.entity]
            present = {v.field: v for v in row.values}
            omitted = {o.field: o for o in row.omitted}
            goal = next(g for g in self.goals if g.label == row.goal_label)
            for key in entity.foreign_keys:
                self.meter.spend("constraint_checks")
                if key.field in omitted:
                    if omitted[key.field].reason == "nullable_unset":
                        continue
                    self._set(goal, "unsupported", code="foreign_key_unresolved",
                              reason=f"{key.field} is set by the database, so its reference can't be resolved")
                    continue
                child = present[key.field]
                if child.value is None:
                    continue
                if child.origin != "goal_constraint":
                    self._set(goal, "unsupported", code="foreign_key_value_not_from_goal",
                              reason=f"{key.field} would reference a row only through a value the planner chose")
                    continue
                parent = None
                for candidate in by_entity.get(key.references_entity, []):
                    self.meter.spend("constraint_checks")
                    match = next((v for v in candidate.values if v.field == key.references_field), None)
                    if match is not None and match.origin == "goal_constraint" and match.type == child.type \
                            and match.value == child.value:
                        parent = candidate
                        break
                if parent is None:
                    self._set(goal, "unsupported", code="foreign_key_unresolved",
                              reason=f"{key.field} = {child.value!r} matches no goal's {key.references_entity}; a "
                                     "parent row nobody asked for is never invented")
                    continue
                dependencies.append(Dependency(child_row=row.row_id, parent_row=parent.row_id, field=key.field,
                                               references_entity=key.references_entity,
                                               references_field=key.references_field))
        return dependencies

    def _cardinality(self, rows: list[CandidateRow]) -> None:
        """Rows from other goals that satisfy a goal too. The exact final count is PostgreSQL's to check."""
        for goal in self.goals:
            matching, undetermined = [], []
            constraints = _canonical_constraints(goal)
            for row in rows:
                if row.entity != goal.entity or row.goal_label == goal.label:
                    continue
                verdict = self._row_matches(row, constraints)
                if verdict is True:
                    matching.append(row.row_id)
                elif verdict is None:
                    undetermined.append(row.row_id)
            self._set(goal, "planned", also_matching_rows=tuple(matching), undetermined_rows=tuple(undetermined))

    def _row_matches(self, row: CandidateRow, constraints) -> bool | None:
        present = {v.field: v for v in row.values}
        omitted = {o.field: o.reason for o in row.omitted}
        unknown = False
        for constraint in constraints:
            self.meter.spend("constraint_checks")
            if constraint.field in omitted and omitted[constraint.field] != "nullable_unset":
                unknown = True
                continue
            value = None if constraint.field in omitted else present[constraint.field].value
            if constraint.op == "is_null":
                if value is not None:
                    return False
                continue
            if value is None:
                return False
            if constraint.op == "not_null":
                continue
            target = ws.plain_value(constraint.value)
            holds = {"eq": value == target, "ne": value != target, "lt": value < target, "le": value <= target,
                     "gt": value > target, "ge": value >= target}[constraint.op]
            if not holds:
                return False
        return None if unknown else True

    def _candidate(self, rows: list[CandidateRow], dependencies: list[Dependency]) -> CandidatePlan:
        obligations = sorted({o for r in rows for o in r.obligations} | ({"foreign_key_order"} if dependencies
                                                                           else set()))
        compatible = len(rows) <= PHASE9B_CONSTRUCTOR_MAX_ROWS
        notes = () if compatible else (
            f"{len(rows)} candidate rows; Phase 9B's constructor accepts at most {PHASE9B_CONSTRUCTOR_MAX_ROWS}, so "
            "this request can't be passed to it as it stands. Nothing was truncated.",
        )
        authorized = self.validated.authorized
        fields = dict(
            planner_version=PLANNER_VERSION, rules_version=RULES_VERSION, feasibility=FEASIBILITY,
            plan_identity=self.identity, registry_digest=authorized.registry_digest,
            contracts_digest=self.validated.contracts_digest, context_digest=self.context.digest,
            schema_digest=self.context.config.schema_digest, application=authorized.application, rows=tuple(rows),
            dependencies=tuple(dependencies), obligations=tuple(obligations), assumptions=tuple(self.assumptions),
            phase9b_constructor_compatible=compatible, phase9b_notes=notes,
        )
        draft = StatePlanningRequest(**fields, request_digest="0" * 64)
        request = draft.model_copy(update={"request_digest": request_digest(draft)})
        return CandidatePlan(request=request, goals=self._goals(), counters=self.meter.counters())


def _typed_tree(node: Any) -> Any:
    if isinstance(node, dict):
        if set(node) == {"field", "column", "type", "value", "origin"}:
            return {**node, "value": _encode(node["type"], node["value"])}
        return {k: _typed_tree(v) for k, v in node.items()}
    if isinstance(node, list | tuple):
        return [_typed_tree(v) for v in node]
    return node


def request_digest(request: StatePlanningRequest) -> str:
    """Domain-separated SHA-256 of the request without its digest. Typed values are exact text, so 1, 1.0 and true
    differ, and integers beyond 2^53 stay exact."""
    data = _typed_tree(request.model_dump(mode="python", exclude={"request_digest"}))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(REQUEST_DOMAIN + text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the entry point


def plan_state(validated: ValidatedWorkload, *, registry: rg.Registry, contracts: Contracts, context: Any,
               trusted: Any, budget: PlanningBudget | None = None) -> PlanningResult:
    """Plan candidate rows for a validated workload's state goals. Pure: reads and changes nothing outside itself."""
    if not isinstance(validated, ValidatedWorkload):
        raise TypeError("plan_state takes a ValidatedWorkload")
    problem = check_validated(validated, registry=registry, contracts=contracts)  # TypeError for wrong types
    if problem:
        raise UntrustedWorkload(problem)
    budget = PlanningBudget() if budget is None else budget
    if not isinstance(budget, PlanningBudget):
        raise TypeError("plan_state takes a PlanningBudget")
    budget = rg.revalidate_model(PlanningBudget, budget)
    meter = _Meter(budget)
    try:
        built = build_context(context, registry=registry, trusted=trusted)
    except ContextError as exc:
        return InvalidTrustedContext(reasons=tuple(exc.reasons), counters=meter.counters())
    if built.config.application != validated.authorized.application:
        return InvalidTrustedContext(reasons=("the schema context is for a different application",),
                                     counters=meter.counters())
    identity = wi.validated_plan_identity(validated, registry=registry, contracts=contracts)
    if not identity.supported:
        raise UntrustedWorkload(f"no validated-plan identity: {identity.reason}")
    return _Planner(validated, built, meter, identity.identity.value, registry, contracts).run()
