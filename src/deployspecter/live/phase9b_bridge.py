"""A typed bridge from candidate rows to Phase 9B's seed-row shape. Research only, offline, pure, unfrozen (5C.3).

Phase 9B's constructor takes untagged ``SeedRow(table, values=[ColumnValue(column, value)])`` and compares values with a
loose rule (``_same`` in its ``seed_feasibility``): ``None`` only with ``None``, booleans by identity, and otherwise
``float(a) == float(b)`` with a fallback to ``str(a) == str(b)``. It compares requested values against **every** row a
table holds, including setup rows and rows that triggers or defaults created, so restricting the values we send
can't, by itself, make its comparisons exact.

This module never imports or runs Phase 9B. It:

1. **Re-derives everything it's given.** It regenerates the request from the validated workload, registry, contracts
   and pinned context, and recomputes the catalog verification from the pinned snapshot. A forged or stale request,
   a forged ``MATCH``, or a mismatched pin raises ``UntrustedBridgeInput``.
2. **Builds a typed representation** that keeps every value's type tag, column presence versus explicit NULL,
   candidate identity and order, dependency order, provenance and obligations. It has its own versioned,
   domain-separated digest.
3. **Classifies every value** for the legacy matcher: equivalent under stated assumptions, unverified (not compared by
   it), or refused. It projects to the ``SeedRow`` shape only when no value is refused, there are at most 10 rows,
   and the order is acyclic.

The legacy ``seed_sha256`` of the projection is computed separately. A matching legacy digest says nothing about
semantic equivalence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from . import catalog_verification as cv
from . import literal_translation as lt
from . import registry as rg
from . import state_planning as sp
from .argument_validation import Contracts, ValidatedWorkload

BRIDGE_VERSION = "phase9b-bridge/1"
BRIDGE_DOMAIN = b"deployspecter:phase9b-bridge:v1\x00"
PHASE9B_MAX_SEED_ROWS = sp.PHASE9B_CONSTRUCTOR_MAX_ROWS  # 10, the constructor's current limit
EXACT_INTEGER_BOUND = 2**53  # |v| must be strictly below this; float(2**53 + 1) == float(2**53)

# Under these, Phase 9B's comparison of an "equivalent" value against any stored value of its column agrees with
# PostgreSQL's equality. They're stated, not verified here.
EQUIVALENCE_ASSUMPTIONS = (
    "Phase 9B reads stored rows with to_jsonb and psycopg's default jsonb loader (json.loads), so integer columns "
    "decode as int, booleans as bool, text as str, and float8 as int, float or the strings NaN and Infinity",
    "every stored value in a column has that column's type, as the MATCHed catalog snapshot states",
    "text columns use a deterministic collation, so equality is code-point equality",
)
LEGACY_UNVERIFIABLE = (
    "Phase 9B's reorder-change detection compares stored values with each other by the same loose rule; changes "
    "between values it conflates, in columns the request doesn't name, can go unnoticed",
    "Phase 9B can count a requested row as already present when a trigger or setup row matches it; that is its "
    "construction semantics, verified only in PostgreSQL",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class UntrustedBridgeInput(ValueError):
    pass


ValueStatus = Literal["equivalent", "refused"]


class BridgeValue(_Strict):
    field: str
    column: str
    pg_type: str
    type: Literal["int", "float", "bool", "str", "null"]
    value: Any
    origin: str
    legacy: ValueStatus
    legacy_reason: str | None = None


class BridgeOmitted(_Strict):
    field: str
    column: str
    reason: str  # database_default, generated, nullable_unset: not listed, and not compared by Phase 9B


class BridgeRow(_Strict):
    row_id: str
    goal_label: str
    ordinal: int
    candidate_index: int  # position in the request, which is canonical (goal label, then ordinal)
    seed_position: int  # position in the dependency-respecting seed order
    entity: str
    table: str
    values: tuple[BridgeValue, ...]
    omitted: tuple[BridgeOmitted, ...]
    depends_on: tuple[str, ...]
    obligations: tuple[str, ...]


class BridgeRepresentation(_Strict):
    bridge_version: str
    method: Literal["deterministic_planner", "literal_translation"]
    method_version: str
    request_digest: str
    plan_identity: str
    registry_digest: str
    contracts_digest: str
    context_digest: str
    snapshot_digest: str
    application: str
    candidate_order: tuple[str, ...]
    seed_order: tuple[str, ...]
    rows: tuple[BridgeRow, ...]  # in seed order
    obligations: tuple[str, ...]
    typed_digest: str


class LegacyProjection(_Strict):
    """Plain data in the SeedRow shape, in seed order. Built only when no value is refused."""

    status: Literal["equivalent", "equivalent_for_listed_columns_only"]
    rows: tuple[dict, ...]
    legacy_seed_sha256: str  # Phase 9B's own formula; integrity of the projection, not semantic equivalence
    assumptions: tuple[str, ...]
    not_compared: tuple[str, ...]  # "row_id.column" for omitted columns the legacy matcher never checks
    unverifiable_legacy_behaviour: tuple[str, ...]


class Bridged(_Strict):
    """A typed bridge with a legacy projection. Candidate-only, and never authorization to execute anything."""

    outcome: Literal["bridged"] = "bridged"
    feasibility: Literal["candidate_pending_postgresql_validation"] = "candidate_pending_postgresql_validation"
    execution_authorized: Literal[False] = False
    representation: BridgeRepresentation
    projection: LegacyProjection

    @model_validator(mode="after")
    def _consistent(self) -> Bridged:
        """A projection exists only for an all-equivalent representation, and is exactly that representation."""
        rows = self.representation.rows
        if any(v.legacy != "equivalent" for r in rows for v in r.values):
            raise ValueError("a projection can't include a refused value")
        if len(rows) > PHASE9B_MAX_SEED_ROWS:
            raise ValueError("a projection can't exceed Phase 9B's row limit")
        expected = tuple({"table": r.table, "values": [{"column": v.column, "value": v.value} for v in r.values]}
                         for r in rows)
        if self.projection.rows != expected or self.projection.legacy_seed_sha256 != legacy_seed_sha256(expected):
            raise ValueError("the projection isn't the representation's own")
        if typed_digest(self.representation) != self.representation.typed_digest:
            raise ValueError("the typed digest doesn't recompute")
        return self


class BridgeRefusal(_Strict):
    outcome: Literal["refused"] = "refused"
    code: str
    reason: str
    row_id: str | None = None
    column: str | None = None
    representation: BridgeRepresentation | None = None  # the typed encoding, when one could be built


BridgeResult = Bridged | BridgeRefusal


# ------------------------------------------------------------------ trust


def _regenerate(request, validated, registry, contracts, context, context_trust, budget):
    """The request must be exactly what the method produces now, from these trusted inputs."""
    common = {"registry": registry, "contracts": contracts, "context": context, "trusted": context_trust,
              "budget": budget}
    try:
        if isinstance(request, sp.StatePlanningRequest):
            if sp.request_digest(request) != request.request_digest:
                raise UntrustedBridgeInput("the planner request's digest doesn't recompute")
            again = sp.plan_state(validated, **common)
            expected = again.request if isinstance(again, sp.CandidatePlan) else None
            method, version = "deterministic_planner", request.planner_version
        elif isinstance(request, lt.LiteralRequest):
            if lt.request_digest(request) != request.request_digest:
                raise UntrustedBridgeInput("the literal request's digest doesn't recompute")
            again = lt.translate_literally(validated, **common)
            expected = again.request if isinstance(again, lt.LiteralCandidate) else None
            method, version = "literal_translation", request.translator_version
        else:
            raise TypeError("the bridge takes a StatePlanningRequest or a LiteralRequest")
    except sp.UntrustedWorkload as exc:
        raise UntrustedBridgeInput(f"the validated workload doesn't verify: {exc}") from exc
    if expected is None:
        raise UntrustedBridgeInput(f"these trusted inputs don't produce a candidate ({again.outcome})")
    if expected != request:
        raise UntrustedBridgeInput("the request isn't what its method produces from these trusted inputs")
    return method, version


# ------------------------------------------------------------------ the legacy matcher, analysed


def _float_parses(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _legacy_status(value: sp.CandidateValue, column: sp.TrustedColumn) -> tuple[ValueStatus, str | None]:
    """Whether Phase 9B's loose comparison of this value against ANY stored value of its column agrees with
    PostgreSQL's equality, under EQUIVALENCE_ASSUMPTIONS."""
    tag, v = value.type, value.value
    if tag == "null":
        return "equivalent", None  # None matches only None, as NULL is distinct from every value
    if tag == "bool":
        return ("equivalent", None) if column.pg_type == "boolean" and type(v) is bool else (
            "refused", "a boolean outside a boolean column")
    if tag == "int":
        if column.pg_type not in sp.INTEGER_RANGES or type(v) is not int:
            return "refused", "an integer outside an integer column"
        if abs(v) >= EXACT_INTEGER_BOUND:
            return "refused", f"|{v}| >= 2^53: float() would equate it with a neighbouring stored integer"
        return "equivalent", None
    if tag == "float":
        if column.pg_type != "double precision" or type(v) is not float:
            return "refused", "a float outside a double precision column"
        if v != v or v in (float("inf"), float("-inf")):
            return "refused", "a non-finite float"
        if v == 0.0 and str(v).startswith("-"):
            return "refused", "negative zero"
        return "equivalent", None  # stored float8 decodes as an exact int, float, or a NaN or Infinity string
    if tag == "str":
        if column.pg_type not in {"text", "varchar"} or type(v) is not str:
            return "refused", "a string outside a text column"
        if column.collation != "deterministic":
            return "refused", "a nondeterministic collation"
        if _float_parses(v):
            return "refused", f"{v!r} parses as a number, so the legacy matcher would compare it numerically"
        return "equivalent", None  # float(v) raises, so the comparison falls back to exact str equality
    return "refused", f"an unknown tag {tag!r}"


# ------------------------------------------------------------------ ordering and digests


def _seed_order(rows: tuple[sp.CandidateRow, ...], dependencies) -> list[int] | None:
    """Kahn's algorithm, parents first, ties by candidate position. None for a cycle. Self-references need no edge."""
    index = {r.row_id: i for i, r in enumerate(rows)}
    parents: dict[int, set[int]] = {i: set() for i in range(len(rows))}
    for dependency in dependencies:
        child, parent = index[dependency.child_row], index[dependency.parent_row]
        if child != parent:
            parents[child].add(parent)
    order: list[int] = []
    placed: set[int] = set()
    while len(order) < len(rows):
        ready = [i for i in range(len(rows)) if i not in placed and parents[i] <= placed]
        if not ready:
            return None
        order.append(ready[0])
        placed.add(ready[0])
    return order


def _canonical(node: Any) -> Any:
    if isinstance(node, dict):
        if {"type", "value", "legacy"} <= set(node):
            return {**node, "value": sp._encode(node["type"], node["value"])}
        return {k: _canonical(v) for k, v in node.items()}
    if isinstance(node, list | tuple):
        return [_canonical(v) for v in node]
    return node


def typed_digest(representation: BridgeRepresentation) -> str:
    data = _canonical(representation.model_dump(mode="python", exclude={"typed_digest"}))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(BRIDGE_DOMAIN + text.encode("utf-8")).hexdigest()


def legacy_seed_sha256(rows: tuple[dict, ...]) -> str:
    """Phase 9B's seed_sha256, as its source defines it: json.dumps of [row.model_dump()] with sort_keys."""
    return hashlib.sha256(json.dumps(list(rows), sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------ the bridge


def bridge_to_phase9b(request: Any, *, validated: ValidatedWorkload, registry: rg.Registry, contracts: Contracts,
                      context: Any, context_trust: Any, snapshot: Any, snapshot_trust: Any,
                      verification_policy: cv.VerificationPolicy | None = None,
                      planning_budget: sp.PlanningBudget | None = None) -> BridgeResult:
    """Typed representation and, where safe, a SeedRow-shaped projection. Nothing given is trusted as given."""
    if not isinstance(request, sp.StatePlanningRequest | lt.LiteralRequest):
        raise TypeError("the bridge takes a StatePlanningRequest or a LiteralRequest")
    method, method_version = _regenerate(request, validated, registry, contracts, context, context_trust,
                                         planning_budget)
    try:
        verification = cv.verify_context(context, registry=registry, context_trust=context_trust, snapshot=snapshot,
                                         snapshot_trust=snapshot_trust, policy=verification_policy)
    except (cv.UntrustedInput, cv.SnapshotError) as exc:
        raise UntrustedBridgeInput(f"the catalog verification doesn't verify: {exc}") from exc
    if verification.context_digest != request.context_digest or \
            verification.snapshot.digest != cv.PREFIX + request.schema_digest:
        raise UntrustedBridgeInput("the request names a different context or catalog snapshot")
    if request.application != registry.application:
        raise UntrustedBridgeInput("the request is for a different application")
    built = sp.build_context(context, registry=registry, trusted=context_trust)
    if verification.outcome != "MATCH":
        return BridgeRefusal(code="context_not_matched",
                             reason=f"the context's catalog verification is {verification.outcome}, not MATCH")
    order = _seed_order(request.rows, request.dependencies)
    if order is None:
        return BridgeRefusal(code="dependency_cycle", reason="the rows' foreign-key dependencies form a cycle")
    representation = _representation(request, built, verification, method, method_version, order)
    refused = next(((r, v) for r in representation.rows for v in r.values if v.legacy == "refused"), None)
    if refused is not None:
        row, value = refused
        code = {"int": "matcher_ambiguous_integer", "str": "matcher_ambiguous_text"}.get(value.type,
                                                                                       "uncastable_value")
        return BridgeRefusal(code=code, reason=value.legacy_reason, row_id=row.row_id, column=value.column,
                             representation=representation)
    if len(representation.rows) > PHASE9B_MAX_SEED_ROWS:
        return BridgeRefusal(code="too_many_rows_for_phase9b",
                             reason=f"{len(representation.rows)} rows; Phase 9B's constructor takes at most "
                                    f"{PHASE9B_MAX_SEED_ROWS}. Nothing is truncated or split.",
                             representation=representation)
    return Bridged(representation=representation, projection=_projection(representation))


def _representation(request, built, verification, method, method_version, order) -> BridgeRepresentation:
    columns = {e.entity: {c.field: c for c in e.columns} for e in built.config.entities}
    position = {candidate: seat for seat, candidate in enumerate(order)}
    depends: dict[str, list[str]] = {}
    for dependency in request.dependencies:
        if dependency.child_row != dependency.parent_row:
            depends.setdefault(dependency.child_row, []).append(dependency.parent_row)
    rows = []
    for candidate in order:
        row = request.rows[candidate]
        entity_columns = columns[row.entity]
        values = []
        obligations = set(row.obligations)
        for value in row.values:
            column = entity_columns[value.field]
            status, reason = _legacy_status(value, column)
            values.append(BridgeValue(field=value.field, column=value.column, pg_type=column.pg_type,
                                      type=value.type, value=value.value, origin=value.origin, legacy=status,
                                      legacy_reason=reason))
            if column.has_default and value.value is not None:
                obligations.add("explicit_value_with_database_default")  # a sequence behind it isn't advanced
        entity = built.entities[row.entity]
        if entity.trigger_written or entity.has_insert_triggers:
            obligations.add("trigger_rows_may_satisfy_or_change_requested_rows")
        rows.append(BridgeRow(
            row_id=row.row_id, goal_label=row.goal_label, ordinal=row.ordinal, candidate_index=candidate,
            seed_position=position[candidate], entity=row.entity, table=row.table, values=tuple(values),
            omitted=tuple(BridgeOmitted(field=o.field, column=o.column, reason=o.reason) for o in row.omitted),
            depends_on=tuple(sorted(depends.get(row.row_id, []))), obligations=tuple(sorted(obligations)),
        ))
    draft = BridgeRepresentation(
        bridge_version=BRIDGE_VERSION, method=method, method_version=method_version,
        request_digest=request.request_digest, plan_identity=request.plan_identity,
        registry_digest=request.registry_digest, contracts_digest=request.contracts_digest,
        context_digest=request.context_digest, snapshot_digest=verification.snapshot.digest,
        application=request.application, candidate_order=tuple(r.row_id for r in request.rows),
        seed_order=tuple(request.rows[i].row_id for i in order), rows=tuple(rows),
        obligations=tuple(sorted({o for r in rows for o in r.obligations} | set(request.obligations))),
        typed_digest="0" * 64,
    )
    return draft.model_copy(update={"typed_digest": typed_digest(draft)})


def _projection(representation: BridgeRepresentation) -> LegacyProjection:
    rows = tuple({"table": r.table, "values": [{"column": v.column, "value": v.value} for v in r.values]}
                 for r in representation.rows)
    not_compared = tuple(f"{r.row_id}.{o.column}" for r in representation.rows for o in r.omitted)
    return LegacyProjection(
        status="equivalent_for_listed_columns_only" if not_compared else "equivalent", rows=rows,
        legacy_seed_sha256=legacy_seed_sha256(rows), assumptions=EQUIVALENCE_ASSUMPTIONS, not_compared=not_compared,
        unverifiable_legacy_behaviour=LEGACY_UNVERIFIABLE,
    )
