"""Literal translation of state goals, and its comparison with the step 5B planner. Research only, pure (5C.2).

``translate_literally`` turns a genuine ``ValidatedWorkload``'s state goals into candidate rows using **only the
literals the goals state**: ``eq`` values and ``is_null``. It never generates a key, uses a filler, chooses a value
from a comparison, searches, casts, or infers a default or trigger effect. Where a row can't be written down from
literals alone, the goal is unsupported, and nothing is invented.

It shares the planner's trust checks, typed rows and result types, so the two can be compared directly by
``compare_with_planner``. Its requests are marked ``method: "literal_translation"``, and every value's origin is
``goal_constraint``. Like the planner, it touches no database, network, file, oracle or proposal-named code, and
emits no SQL.

This is one of two readings of protocol v1.2's arm C ("resolved literally, with no search"). Nothing here adopts
either reading, or runs any arm.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from . import registry as rg
from . import state_planning as sp
from . import workload_identity as wi
from . import workload_spec as ws
from .argument_validation import Contracts, ValidatedWorkload, check_validated

METHOD = "literal_translation"
TRANSLATOR_VERSION = "literal-translation/1"
RULES_VERSION = 1
REQUEST_DOMAIN = b"deployspecter:literal-translation-request:v1\x00"
LITERAL_OPS = {"eq", "is_null"}  # the only operators that state a value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LiteralRequest(_Strict):
    request_version: Literal[1] = 1
    method: Literal["literal_translation"] = "literal_translation"
    translator_version: str
    rules_version: int
    feasibility: Literal["candidate_pending_postgresql_validation"]
    plan_identity: str
    registry_digest: str
    contracts_digest: str
    context_digest: str
    schema_digest: str
    application: str
    rows: tuple[sp.CandidateRow, ...]
    dependencies: tuple[sp.Dependency, ...]
    obligations: tuple[str, ...]
    assumptions: tuple[str, ...]
    phase9b_constructor_compatible: bool
    phase9b_notes: tuple[str, ...]
    request_digest: str


class LiteralCandidate(_Strict):
    outcome: Literal["literal_candidate"] = "literal_candidate"
    request: LiteralRequest
    goals: tuple[sp.GoalStatus, ...]
    counters: sp.Counters


LiteralResult = (LiteralCandidate | sp.UnsupportedGoal | sp.InvalidTrustedContext | sp.InfeasibleUnderAssumptions
                 | sp.Indeterminate)


def request_digest(request: LiteralRequest) -> str:
    data = sp._typed_tree(request.model_dump(mode="python", exclude={"request_digest"}))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(REQUEST_DOMAIN + text.encode("utf-8")).hexdigest()


class _Problem(Exception):
    def __init__(self, status: str, code: str, reason: str, evidence: sp.InfeasibilityEvidence | None = None) -> None:
        super().__init__(reason)
        self.status, self.code, self.reason, self.evidence = status, code, reason, evidence


def _unsupported(code: str, reason: str) -> _Problem:
    return _Problem("unsupported", code, reason)


class _Translator:
    def __init__(self, validated: ValidatedWorkload, context: sp.PlanningContext, meter: sp._Meter,
                 identity: str) -> None:
        self.validated, self.context, self.meter, self.identity = validated, context, meter, identity
        self.goals = sorted(validated.authorized.state_goals, key=lambda g: g.label)
        self.statuses = {g.label: sp.GoalStatus(label=g.label, entity=g.entity, status="not_examined")
                         for g in self.goals}

    def _set(self, goal, status, **fields) -> None:
        current = self.statuses[goal.label].status
        if current == "infeasible" and status != "infeasible":
            return
        if current == "unsupported" and status == "planned":
            return
        self.statuses[goal.label] = self.statuses[goal.label].model_copy(update={"status": status, **fields})

    def _fail(self, goal, problem: _Problem) -> None:
        self._set(goal, problem.status, code=problem.code, reason=problem.reason, evidence=problem.evidence)

    def _goals(self) -> tuple[sp.GoalStatus, ...]:
        return tuple(self.statuses[g.label] for g in self.goals)

    # ---------------------------------------------------------------- one goal

    def _infeasible(self, goal, entity, column, constraints, code, reason, *facts, assumptions=(sp.CONTEXT_EXACT,)):
        base = sp._Planner._column_facts(entity, column)
        evidence = sp.InfeasibilityEvidence(
            goals=(goal.label,), entity=entity.entity, fields=(column.field,), constraints=sp._render(constraints),
            context_facts=tuple([*base, *facts]), assumptions=tuple(a for a in sp.ASSUMPTIONS if a in assumptions))
        return _Problem("infeasible", code, reason, evidence)

    def _check_literal(self, column: sp.TrustedColumn, constraint) -> None:
        """The same type rules as the planner: no value of a different type, and no cast, is ever accepted."""
        tag = constraint.value.type
        if tag == "null":
            raise _unsupported("null_comparison", f"{constraint.field} {constraint.op} null; use is_null")
        if column.pg_type == "numeric":
            raise _unsupported("numeric_coercion" if tag == "float" else "cast_dependent",
                               f"{constraint.field} is numeric; a {tag} value would need a cast")
        if column.pg_type == "opaque":
            raise _unsupported("opaque_type", f"{constraint.field} has a type that can't be compared")
        if sp.ACCEPTS[column.pg_type] != tag:
            raise _unsupported("cast_dependent", f"{constraint.field} is {column.pg_type}; a {tag} value would need "
                                                 "a cast")
        if constraint.op in sp.ORDER_OPS and tag == "str":
            raise _unsupported("collation_dependent_order", f"{constraint.field}: string order depends on collation")
        if constraint.op in sp.ORDER_OPS and tag == "bool":
            raise _unsupported("boolean_order", f"{constraint.field}: booleans have no order here")
        if tag == "str" and column.collation == "nondeterministic":
            raise _unsupported("collation_dependent_equality", f"{constraint.field}: nondeterministic collation")

    def _resolve(self, goal) -> tuple[sp.TrustedEntity, dict[str, tuple]]:
        entity = self.context.entities.get(goal.entity)
        if entity is None:
            raise _unsupported("unknown_entity", f"the trusted context has no entity {goal.entity!r}")
        if goal.count == 0:
            raise _unsupported("absence_claim", "count 0 claims no such rows exist, which only PostgreSQL can check")
        if goal.count > 1 and (entity.primary_key or entity.unique):
            raise _unsupported("literal_cardinality", f"{goal.count} rows of {goal.entity} need distinct key values, "
                                                      "which literal translation would have to invent")
        columns = {c.field: c for c in entity.columns}
        by_field: dict[str, list] = {}
        for constraint in sp._canonical_constraints(goal):
            self.meter.spend("constraint_checks")
            column = columns.get(constraint.field)
            if column is None:
                raise _unsupported("unknown_field", f"{goal.entity} has no field {constraint.field!r}")
            if column.generated:
                raise _unsupported("generated_column", f"{constraint.field} is set by the database")
            if constraint.value is not None:
                self._check_literal(column, constraint)
            by_field.setdefault(constraint.field, []).append(constraint)
        plans: dict[str, tuple] = {}
        for column in entity.columns:
            constraints = by_field.get(column.field)
            plans[column.field] = (self._constrained(goal, entity, column, constraints) if constraints
                                   else self._unconstrained(entity, column))
        return entity, plans

    def _constrained(self, goal, entity, column, constraints) -> tuple:
        ops = {c.op for c in constraints}
        if "is_null" in ops:
            if not column.nullable:
                raise self._infeasible(goal, entity, column, constraints, "not_null_violated",
                                       f"{column.field} is NOT NULL, so it can't be null")
            if ops != {"is_null"}:
                raise self._infeasible(goal, entity, column, constraints, "null_with_comparison",
                                       f"{column.field} is null, and a NULL satisfies no other constraint",
                                       assumptions=(sp.SQL_NULL, sp.CONTEXT_EXACT))
            return ("value", "null", None)
        tag = sp.ACCEPTS.get(column.pg_type, "null")
        literals = [ws.plain_value(c.value) for c in constraints if c.op == "eq"]
        if not literals:
            raise _unsupported("value_synthesis_required",
                               f"{column.field}: {', '.join(sorted(ops))} state no value, and literal translation "
                               "chooses none")
        if len({sp._encode(tag, v) for v in literals}) > 1:
            raise self._infeasible(goal, entity, column, constraints, "contradictory_values",
                                   f"{column.field} must equal two different values")
        value = literals[0]
        if column.pg_type in sp.INTEGER_RANGES:
            low, high = sp.INTEGER_RANGES[column.pg_type]
            if not low <= value <= high:
                raise self._infeasible(goal, entity, column, constraints, "outside_type_range",
                                       f"{value} is outside {column.pg_type}")
        if column.max_length is not None and len(value) > column.max_length:
            raise self._infeasible(goal, entity, column, constraints, "too_long_for_column",
                                   f"{column.field} holds at most {column.max_length} characters",
                                   "the database encoding is UTF8, so varchar length counts code points")
        for constraint in constraints:  # the other constraints only check the literal; they never supply one
            if constraint.op in {"eq", "not_null"}:
                continue
            target = ws.plain_value(constraint.value)
            holds = {"ne": value != target, "lt": value < target, "le": value <= target, "gt": value > target,
                     "ge": value >= target}[constraint.op]
            if not holds:
                raise self._infeasible(goal, entity, column, constraints, "bounds_exclude_value",
                                       f"{column.field} = {value!r} breaks {constraint.op}")
        return ("value", tag, value)

    def _unconstrained(self, entity, column) -> tuple:
        """Leaving a column out is the only thing done without a literal; it invents no value."""
        if column.generated:
            return ("omit", "generated")
        if column.has_default:
            return ("omit", "database_default")
        if column.nullable:
            return ("omit", "nullable_unset")
        code = "key_not_literal" if column.field in entity.primary_key else "required_column_unresolved"
        raise _unsupported(code, f"{column.field} is required and no goal states its value; keys and fillers aren't "
                                 "literal sources")

    # ---------------------------------------------------------------- the whole translation

    def run(self) -> LiteralResult:
        total = sum(g.count for g in self.goals)
        if total > self.meter.limits["rows"]:
            return sp.Indeterminate(code="row_budget", reason=f"{total} rows requested, the budget allows "
                                                              f"{self.meter.limits['rows']}", goals=self._goals(),
                                    counters=self.meter.counters())
        rows: list[sp.CandidateRow] = []
        try:
            resolved = {}
            for goal in self.goals:
                try:
                    resolved[goal.label] = self._resolve(goal)
                    self._set(goal, "planned")
                except _Problem as problem:
                    self._fail(goal, problem)
            if (decided := self._decided()) is not None:
                return decided
            for goal in self.goals:
                entity, plans = resolved[goal.label]
                ids = []
                for ordinal in range(goal.count):
                    self.meter.spend("rows")
                    rows.append(self._row(goal, entity, plans, ordinal))
                    ids.append(rows[-1].row_id)
                self._set(goal, "planned", rows=tuple(ids))
            self._duplicates(rows)
            dependencies = self._foreign_keys(rows) if self._decided() is None else []
            if (decided := self._decided()) is not None:
                return decided
            self._cardinality(rows)
        except sp._Exhausted as exhausted:
            return sp.Indeterminate(code=f"{exhausted.kind}_budget",
                                    reason=f"the {exhausted.kind} budget ran out; not a claim of infeasibility",
                                    goals=self._goals(), counters=self.meter.counters())
        return self._candidate(rows, dependencies)

    def _decided(self):
        for status in ("infeasible", "unsupported"):
            first = next((s for s in self._goals() if s.status == status), None)
            if first is None:
                continue
            if status == "infeasible":
                return sp.InfeasibleUnderAssumptions(goal_label=first.label, code=first.code, reason=first.reason,
                                                     evidence=first.evidence, assumptions=first.evidence.assumptions,
                                                     goals=self._goals(), counters=self.meter.counters())
            return sp.UnsupportedGoal(goal_label=first.label, code=first.code, reason=first.reason,
                                      goals=self._goals(), counters=self.meter.counters())
        return None

    def _row(self, goal, entity, plans, ordinal) -> sp.CandidateRow:
        values, omitted = [], []
        for column in sorted(entity.columns, key=lambda c: c.field):
            plan = plans[column.field]
            if plan[0] == "omit":
                omitted.append(sp.OmittedColumn(field=column.field, column=column.column, reason=plan[1]))
            else:
                values.append(sp.CandidateValue(field=column.field, column=column.column, type=plan[1],
                                                value=plan[2], origin="goal_constraint"))
        obligations = ["postgresql_validation_required", "exact_count_unverified"]
        if entity.trigger_written:
            obligations.append("trigger_written_entity")
        if entity.has_insert_triggers:
            obligations.append("insert_triggers_unverified")
        obligations += sorted({f"{o.reason}_not_compared" for o in omitted})
        if any(f in {o.field for o in omitted} for fields, _, _ in sp._Planner._key_sets(entity) for f in fields):
            obligations.append("unique_with_database_values")
        return sp.CandidateRow(row_id=f"{goal.label}#{ordinal}", goal_label=goal.label, ordinal=ordinal,
                               entity=entity.entity, table=entity.table, values=tuple(values),
                               omitted=tuple(omitted), obligations=tuple(obligations),
                               context_digest=self.context.digest, plan_identity=self.identity)

    def _duplicates(self, rows) -> None:
        """Two goals asking for the same key are never merged into one row: that's unsupported, not resolved."""
        for name in sorted({r.entity for r in rows}):
            entity = self.context.entities[name]
            entity_rows = [r for r in rows if r.entity == name]
            for fields, kind, nulls_equal in sp._Planner._key_sets(entity):
                seen: dict[tuple, sp.CandidateRow] = {}
                for row in entity_rows:
                    self.meter.spend("constraint_checks")
                    members = sp._Planner._members(row, fields)
                    if members is None or (not nulls_equal and any(v is None for _, v, _ in members)):
                        continue
                    key = tuple(sp._encode(tag, value) for tag, value, _ in members)
                    if key in seen:
                        goal = next(g for g in self.goals if g.label == row.goal_label)
                        unset = any(origin == "unset" for _, _, origin in members)
                        self._set(goal, "unsupported", code="unset_nulls_collide" if unset else "overlapping_goal_keys",
                                  reason=f"goals {seen[key].goal_label} and {row.goal_label} ask for the same "
                                         f"({', '.join(fields)}) under {kind}; literal translation never merges them")
                    else:
                        seen[key] = row

    def _foreign_keys(self, rows) -> list[sp.Dependency]:
        dependencies = []
        for row in rows:
            entity = self.context.entities[row.entity]
            goal = next(g for g in self.goals if g.label == row.goal_label)
            present = {v.field: v for v in row.values}
            omitted = {o.field: o.reason for o in row.omitted}
            for key in entity.foreign_keys:
                self.meter.spend("constraint_checks")
                if omitted.get(key.field) == "nullable_unset" or (key.field in present and
                                                                  present[key.field].value is None):
                    continue
                child = present.get(key.field)
                parent = None
                if child is not None:
                    for candidate in (r for r in rows if r.entity == key.references_entity):
                        self.meter.spend("constraint_checks")
                        match = next((v for v in candidate.values if v.field == key.references_field), None)
                        if match is not None and match.type == child.type and match.value == child.value:
                            parent = candidate
                            break
                if parent is None:
                    self._set(goal, "unsupported", code="foreign_key_unresolved",
                              reason=f"{key.field} references no row another goal states literally")
                    continue
                dependencies.append(sp.Dependency(child_row=row.row_id, parent_row=parent.row_id, field=key.field,
                                                  references_entity=key.references_entity,
                                                  references_field=key.references_field))
        return dependencies

    def _cardinality(self, rows) -> None:
        for goal in self.goals:
            matching, undetermined = [], []
            constraints = sp._canonical_constraints(goal)
            for row in rows:
                if row.entity != goal.entity or row.goal_label == goal.label:
                    continue
                verdict = self._matches(row, constraints)
                if verdict is True:
                    matching.append(row.row_id)
                elif verdict is None:
                    undetermined.append(row.row_id)
            self._set(goal, "planned", also_matching_rows=tuple(matching), undetermined_rows=tuple(undetermined))

    def _matches(self, row, constraints) -> bool | None:
        present = {v.field: v.value for v in row.values}
        omitted = {o.field: o.reason for o in row.omitted}
        unknown = False
        for constraint in constraints:
            self.meter.spend("constraint_checks")
            if omitted.get(constraint.field) not in (None, "nullable_unset"):
                unknown = True
                continue
            value = present.get(constraint.field)
            if constraint.op == "is_null":
                if value is not None:
                    return False
                continue
            if value is None:
                return False
            if constraint.op == "not_null":
                continue
            target = ws.plain_value(constraint.value)
            if not {"eq": value == target, "ne": value != target, "lt": value < target, "le": value <= target,
                    "gt": value > target, "ge": value >= target}[constraint.op]:
                return False
        return None if unknown else True

    def _candidate(self, rows, dependencies) -> LiteralCandidate:
        obligations = sorted({o for r in rows for o in r.obligations} | ({"foreign_key_order"} if dependencies
                                                                           else set()))
        compatible = len(rows) <= sp.PHASE9B_CONSTRUCTOR_MAX_ROWS
        notes = () if compatible else (f"{len(rows)} candidate rows; Phase 9B's constructor accepts at most "
                                       f"{sp.PHASE9B_CONSTRUCTOR_MAX_ROWS}. Nothing was truncated.",)
        authorized = self.validated.authorized
        draft = LiteralRequest(
            translator_version=TRANSLATOR_VERSION, rules_version=RULES_VERSION, feasibility=sp.FEASIBILITY,
            plan_identity=self.identity, registry_digest=authorized.registry_digest,
            contracts_digest=self.validated.contracts_digest, context_digest=self.context.digest,
            schema_digest=self.context.config.schema_digest, application=authorized.application, rows=tuple(rows),
            dependencies=tuple(dependencies), obligations=tuple(obligations), assumptions=sp.ASSUMPTIONS,
            phase9b_constructor_compatible=compatible, phase9b_notes=notes, request_digest="0" * 64,
        )
        request = draft.model_copy(update={"request_digest": request_digest(draft)})
        return LiteralCandidate(request=request, goals=self._goals(), counters=self.meter.counters())


def translate_literally(validated: ValidatedWorkload, *, registry: rg.Registry, contracts: Contracts, context: Any,
                        trusted: Any, budget: sp.PlanningBudget | None = None) -> LiteralResult:
    """Candidate rows from goal literals alone. The same trust checks, in the same order, as ``plan_state``."""
    if not isinstance(validated, ValidatedWorkload):
        raise TypeError("translate_literally takes a ValidatedWorkload")
    problem = check_validated(validated, registry=registry, contracts=contracts)
    if problem:
        raise sp.UntrustedWorkload(problem)
    budget = sp.PlanningBudget() if budget is None else budget
    if not isinstance(budget, sp.PlanningBudget):
        raise TypeError("translate_literally takes a PlanningBudget")
    meter = sp._Meter(rg.revalidate_model(sp.PlanningBudget, budget))
    try:
        built = sp.build_context(context, registry=registry, trusted=trusted)
    except sp.ContextError as exc:
        return sp.InvalidTrustedContext(reasons=tuple(exc.reasons), counters=meter.counters())
    identity = wi.validated_plan_identity(validated, registry=registry, contracts=contracts)
    if not identity.supported:
        raise sp.UntrustedWorkload(f"no validated-plan identity: {identity.reason}")
    return _Translator(validated, built, meter, identity.identity.value).run()


# ------------------------------------------------------------------ comparison with the planner


class Comparison(_Strict):
    """How literal translation and the step 5B planner differ on one workload. Not an evaluation of either."""

    planner_outcome: str
    literal_outcome: str
    planner_code: str | None
    literal_code: str | None
    both_accept: bool
    planner_only: bool
    literal_only: bool
    same_rows: bool | None  # None unless both accept
    row_differences: tuple[str, ...]
    obligations_only_planner: tuple[str, ...]
    obligations_only_literal: tuple[str, ...]
    assumptions_differ: bool
    planner_counters: sp.Counters
    literal_counters: sp.Counters


def _code(result) -> str | None:
    coded = (sp.UnsupportedGoal, sp.InfeasibleUnderAssumptions, sp.Indeterminate)
    return result.code if isinstance(result, coded) else None


def _accepted(result) -> bool:
    return result.outcome in {"candidate_plan", "literal_candidate"}


def compare_with_planner(validated: ValidatedWorkload, *, registry: rg.Registry, contracts: Contracts, context: Any,
                         trusted: Any, budget: sp.PlanningBudget | None = None) -> Comparison:
    planned = sp.plan_state(validated, registry=registry, contracts=contracts, context=context, trusted=trusted,
                            budget=budget)
    literal = translate_literally(validated, registry=registry, contracts=contracts, context=context,
                                  trusted=trusted, budget=budget)
    both = _accepted(planned) and _accepted(literal)
    differences, only_planner, only_literal, assumptions_differ, same = [], (), (), False, None
    if both:
        p_rows = {r.row_id: r for r in planned.request.rows}
        l_rows = {r.row_id: r for r in literal.request.rows}
        for row_id in sorted(set(p_rows) | set(l_rows)):
            if row_id not in l_rows:
                differences.append(f"{row_id}: planner only")
            elif row_id not in p_rows:
                differences.append(f"{row_id}: literal translation only")
            elif p_rows[row_id] != l_rows[row_id]:
                p_values = {v.field: (v.type, v.value, v.origin) for v in p_rows[row_id].values}
                l_values = {v.field: (v.type, v.value, v.origin) for v in l_rows[row_id].values}
                fields = sorted(f for f in set(p_values) | set(l_values) if p_values.get(f) != l_values.get(f))
                differences.append(f"{row_id}: differs in {', '.join(fields) or 'obligations or omissions'}")
        same = not differences and planned.request.dependencies == literal.request.dependencies
        only_planner = tuple(sorted(set(planned.request.obligations) - set(literal.request.obligations)))
        only_literal = tuple(sorted(set(literal.request.obligations) - set(planned.request.obligations)))
        assumptions_differ = planned.request.assumptions != literal.request.assumptions
    return Comparison(
        planner_outcome=planned.outcome, literal_outcome=literal.outcome,
        planner_code=_code(planned), literal_code=_code(literal),
        both_accept=both, planner_only=_accepted(planned) and not _accepted(literal),
        literal_only=_accepted(literal) and not _accepted(planned), same_rows=same,
        row_differences=tuple(differences), obligations_only_planner=only_planner,
        obligations_only_literal=only_literal, assumptions_differ=assumptions_differ,
        planner_counters=planned.counters, literal_counters=literal.counters,
    )
