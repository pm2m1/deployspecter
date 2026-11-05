"""Checks a proposed initial state by executing it on the disposable schema, before any schedule runs.

Row by row, inside one transaction that is rolled back, each insert runs in a savepoint and the state of every table is
read before and after it. That shows what triggers actually did, and one bad row cannot hide the next. A real
``Sandbox.reset`` then has the last word, including deferred constraints. Strict mode is the default and accepts and
rejects exactly what ``Sandbox.reset`` does.

``reconcile`` mode can skip one kind of row: an explicit seed row that fails on a unique key because a trigger already
produced an identical row. Every condition in ``_equivalence`` must hold, and each skip is recorded. Actor arguments are
never touched, and rows that differ are never removed or changed.

Failure stages stay separate: ``structural`` (the plan cannot be expressed against this schema), ``seed_execution``
(PostgreSQL rejected it), ``schedule_execution`` and ``oracle_violation`` (later, and only for tests that ran).

    python -m deployspecter.seed_feasibility --case scenario/variant --proposal test.json [--external]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from psycopg import sql
from pydantic import BaseModel, Field

from .analyzer_support import TableInfo, read_schema
from .config import load_settings, redact
from .database import RUN_SCHEMA, Sandbox, SetupError
from .models import SeedRow, TestProposal
from .scenarios import load_case
from .triggers import TriggerInfo, fired_closure, read_triggers

SeedMode = Literal["strict", "reconcile"]
FailureStage = Literal["structural", "seed_execution", "schedule_execution", "oracle_violation"]
Category = Literal[
    "DUPLICATE_EXPLICIT_SEED", "TRIGGER_GENERATED_CONFLICT", "SETUP_ROW_CONFLICT", "UNIQUE_CONFLICT",
    "FOREIGN_KEY_FAILURE", "TYPE_MISMATCH", "CHECK_CONSTRAINT_FAILURE", "NOT_NULL_FAILURE",
    "MISSING_REQUIRED_PARAMETER", "UNKNOWN_TABLE_OR_COLUMN", "UNKNOWN_FUNCTION_OR_STATEMENT", "INVALID_ACTORS",
    "UNSUPPORTED_TRIGGER_EFFECT", "UNKNOWN_SEED_EFFECT",
    # Warnings: the seed runs, but the resulting state is not what its rows say.
    "TRIGGER_CHANGED_EXPLICIT_ROW", "EXPLICIT_ROW_NOT_IN_FINAL_STATE",
]

MAX_ROWS_READ = 2000
# A guard against a trigger that never returns, not a judgement on seeds. One statement this slow would use a whole
# schedule budget (60 s), and the slowest saved seed takes about 11 ms.
STATEMENT_TIMEOUT_MS = 60000


class SeedDiagnostic(BaseModel):
    category: Category
    stage: FailureStage | None = None  # None marks a warning
    row_index: int | None = None
    table: str | None = None
    message: str
    sqlstate: str | None = None
    constraint: str | None = None
    raised_inside_trigger: bool = False
    after_earlier_failure: bool = False
    requested_row: dict[str, Any] | None = None
    existing_row: dict[str, Any] | None = None
    generated_by_row: int | None = None
    trigger_candidates: list[str] = Field(default_factory=list)
    differing_columns: list[str] = Field(default_factory=list)
    equivalent: bool | None = None
    not_equivalent_because: list[str] = Field(default_factory=list)
    suggestion: str = ""


class SeedStep(BaseModel):
    row_index: int
    table: str
    status: Literal["inserted", "failed", "skipped_as_redundant"]
    rows_added: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    rows_removed: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class SeedTransformation(BaseModel):
    transformation: Literal["skip_redundant_seed_row"] = "skip_redundant_seed_row"
    row_index: int
    table: str
    requested_row: dict[str, Any]
    actual_row: dict[str, Any]
    generated_by_row: int
    trigger_candidates: list[str]
    constraint: str
    justification: list[str]


class SeedReport(BaseModel):
    mode: SeedMode
    feasible: bool
    stage: FailureStage | None = None
    first_error: str | None = None
    requested_seed: list[SeedRow]
    effective_seed: list[SeedRow]
    requested_seed_sha256: str
    effective_seed_sha256: str
    diagnostics: list[SeedDiagnostic] = Field(default_factory=list)
    warnings: list[SeedDiagnostic] = Field(default_factory=list)
    steps: list[SeedStep] = Field(default_factory=list)
    transformations: list[SeedTransformation] = Field(default_factory=list)
    final_state: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


@dataclass
class SeedContext:
    """What one fixture's schema says. Built once per case."""

    schema: dict[str, TableInfo]
    triggers: list[TriggerInfo]
    tables_with_rules: set[str]


def read_context(sandbox: Sandbox, setup_sql: str) -> SeedContext:
    schema = read_schema(sandbox, setup_sql)
    rules = sandbox.admin.execute(
        """SELECT c.relname::text FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class
           JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND r.rulename <> '_RETURN'""",
        (RUN_SCHEMA,),
    ).fetchall()
    return SeedContext(schema, read_triggers(sandbox.admin, [RUN_SCHEMA]), {r[0] for r in rules})


def seed_sha256(seed: list[SeedRow]) -> str:
    return hashlib.sha256(json.dumps([r.model_dump() for r in seed], sort_keys=True).encode()).hexdigest()


def proposal_sha256(proposal: TestProposal) -> str:
    return hashlib.sha256(json.dumps(proposal.model_dump(), sort_keys=True).encode()).hexdigest()


def _row_dict(row: SeedRow) -> dict[str, Any]:
    return {v.column: v.value for v in row.values}


def _same(a: Any, b: Any) -> bool:
    """A loose comparison, used only to attribute rows. Reconciliation decides equality in PostgreSQL instead."""
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True)


def _read_state(sandbox: Sandbox) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    """Exact rows through to_jsonb, so timestamps are not masked. The flag is False if a table exceeded the bound."""
    state, complete = {}, True
    for table in sandbox.tables():
        rows = sandbox.admin.execute(
            sql.SQL("SELECT to_jsonb(t) FROM {} t LIMIT {}").format(
                sql.Identifier(table), sql.Literal(MAX_ROWS_READ + 1)
            )
        ).fetchall()
        complete &= len(rows) <= MAX_ROWS_READ
        state[table] = sorted((r[0] for r in rows), key=_key)
    return state, complete


def _diff(before, after) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    added, removed = {}, {}
    for table in after:
        was, now = Counter(map(_key, before.get(table, []))), Counter(map(_key, after[table]))
        if extra := list((now - was).elements()):
            added[table] = [json.loads(k) for k in extra]
        if gone := list((was - now).elements()):
            removed[table] = [json.loads(k) for k in gone]
    return added, removed


def _unique_columns(sandbox: Sandbox, constraint: str | None) -> list[str] | None:
    if not constraint:
        return None
    rows = sandbox.admin.execute(
        """SELECT a.attname::text, x.indexprs IS NULL FROM pg_class i
           JOIN pg_namespace n ON n.oid = i.relnamespace
           JOIN pg_index x ON x.indexrelid = i.oid
           CROSS JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS k(attnum, ord)
           LEFT JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum
           WHERE i.relname = %s AND n.nspname = %s ORDER BY k.ord""",
        (constraint, RUN_SCHEMA),
    ).fetchall()
    if not rows or any(name is None or not plain for name, plain in rows):
        return None
    return [name for name, _ in rows]


def _category_of(state: str | None, inside_trigger: bool) -> Category:
    state = state or ""
    if state == "23505":
        return "TRIGGER_GENERATED_CONFLICT" if inside_trigger else "UNIQUE_CONFLICT"
    if state == "23503":
        return "FOREIGN_KEY_FAILURE"
    if state == "23502":
        return "NOT_NULL_FAILURE"
    if state in {"23514", "23P01"}:
        return "CHECK_CONSTRAINT_FAILURE"
    if state.startswith("22") or state == "42804":
        return "TYPE_MISMATCH"
    if state in {"42703", "42P01"}:
        return "UNKNOWN_TABLE_OR_COLUMN"
    if inside_trigger or state in {"P0001", "54001"}:
        return "UNSUPPORTED_TRIGGER_EFFECT"
    return "UNKNOWN_SEED_EFFECT"


_SUGGESTIONS: dict[str, str] = {
    "FOREIGN_KEY_FAILURE": "Seed the referenced parent row first, with the key this row names.",
    "TYPE_MISMATCH": "Use a value of the column's declared type and range.",
    "CHECK_CONSTRAINT_FAILURE": "Use a value the table's CHECK constraint allows.",
    "NOT_NULL_FAILURE": "Give this NOT NULL column a value.",
    "UNSUPPORTED_TRIGGER_EFFECT": "A trigger rejected or could not process this row. Inspect the trigger.",
    "UNKNOWN_SEED_EFFECT": "PostgreSQL rejected this row for a reason not classified here. Read the error.",
    "UNKNOWN_TABLE_OR_COLUMN": "Name only tables and columns present in the schema.",
    "SETUP_ROW_CONFLICT": "The project's own SQL already created this row. Omit it or use another key.",
    "DUPLICATE_EXPLICIT_SEED": "This proposal names the same key twice. Keep one row or use another key.",
}


def _stage_of(state: str | None) -> FailureStage:
    return "structural" if (state or "").startswith("42") else "seed_execution"


def _diagnostic_from_error(exc: psycopg.Error, row_index: int | None, row: SeedRow | None, failed_before: bool):
    context = exc.diag.context or ""
    inside = bool(re.search(r"PL/pgSQL function|trigger", context, re.I))
    category = _category_of(exc.sqlstate, inside)
    return SeedDiagnostic(
        category=category, stage=_stage_of(exc.sqlstate), row_index=row_index,
        table=exc.diag.table_name or (row.table if row else None), message=redact(str(exc).strip()),
        sqlstate=exc.sqlstate, constraint=exc.diag.constraint_name, raised_inside_trigger=inside,
        after_earlier_failure=failed_before, requested_row=_row_dict(row) if row else None,
        suggestion=_SUGGESTIONS.get(category, ""),
    )


def _row_is_identical(sandbox: Sandbox, table: TableInfo, requested: dict[str, Any], existing: dict[str, Any]):
    """(identical, reasons, differing columns). PostgreSQL compares the values, with its own type coercion."""
    reasons, differing = [], []
    for column in table.columns:
        if column.name not in requested:
            if column.has_default:
                reasons.append(f"{column.name} is omitted and has a default, so the intended value can't be proved")
            elif existing.get(column.name) is not None:
                reasons.append(f"{column.name} is omitted but the existing row has a value")
                differing.append(column.name)
            continue
        try:
            with sandbox.admin.transaction():
                matches = sandbox.admin.execute(
                    sql.SQL("SELECT %s::{} IS NOT DISTINCT FROM (%s::jsonb ->> %s)::{}").format(
                        sql.SQL(column.data_type), sql.SQL(column.data_type)
                    ),
                    (requested[column.name], json.dumps(existing), column.name),
                ).fetchone()[0]
        except psycopg.Error:
            reasons.append(f"{column.name}: PostgreSQL could not compare the values")
            differing.append(column.name)
            continue
        # A cast can round (10.6 to 11), so numbers must also be equal before any cast.
        value, stored = requested[column.name], existing.get(column.name)
        if isinstance(value, float) and isinstance(stored, int | float) and float(value) != float(stored):
            matches = False
        if not matches:
            reasons.append(f"{column.name}: requested {requested[column.name]!r}, PostgreSQL has "
                           f"{existing.get(column.name)!r}")
            differing.append(column.name)
    return not reasons, reasons, differing


def _equivalence(context: SeedContext, table_name: str, identical_reasons: list[str], fired: list[TriggerInfo]):
    """Every reason a skip would not be provably the same. An empty list means the skip is allowed."""
    reasons = list(identical_reasons)
    info = context.schema[table_name]
    on_table = [t for t in context.triggers if t.table == table_name and t.fires and "INSERT" in t.events]
    if on_table:
        reasons.append(f"{table_name} has INSERT triggers ({', '.join(t.name for t in on_table)}), and an explicit "
                       "insert could differ from the trigger's")
    if table_name in context.tables_with_rules:
        reasons.append(f"{table_name} has rewrite rules")
    if not info.primary_key:
        reasons.append(f"{table_name} has no primary key")
    if unknown := [t.name for t in fired if t.effect == "unknown"]:
        reasons.append(f"triggers with unknown effects fired: {', '.join(unknown)}")
    return reasons


def check_seed(
    sandbox: Sandbox, setup_sql: str, seed: list[SeedRow], context: SeedContext, mode: SeedMode = "strict",
) -> SeedReport:
    sandbox.reset(setup_sql, [])
    report = SeedReport(
        mode=mode, feasible=False, requested_seed=seed, effective_seed=seed,
        requested_seed_sha256=seed_sha256(seed), effective_seed_sha256=seed_sha256(seed),
    )
    effective: list[SeedRow] = []
    explicit_keys: dict[str, set[str]] = {}
    generated: dict[str, dict[str, int]] = {}  # table -> row -> seed row that produced it
    fired: list[TriggerInfo] = []
    skipped: list[SeedTransformation] = []
    admin = sandbox.admin
    with admin.transaction():
        admin.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
        state, complete = _read_state(sandbox)
        setup_state = state
        for index, row in enumerate(seed):
            failed_before = bool(report.diagnostics)
            try:
                with admin.transaction():
                    sandbox._insert_seed(row)
            except psycopg.Error as exc:
                diagnostic = _diagnostic_from_error(exc, index, row, failed_before)
                if exc.sqlstate == "23505" and not diagnostic.raised_inside_trigger and row.table in context.schema:
                    transformation = _explain_conflict(
                        sandbox, context, row, index, diagnostic, state, explicit_keys, generated, setup_state, fired,
                        complete,
                    )
                    if transformation is not None and mode == "reconcile":
                        skipped.append(transformation)
                        report.steps.append(SeedStep(row_index=index, table=row.table, status="skipped_as_redundant"))
                        continue
                report.diagnostics.append(diagnostic)
                report.steps.append(SeedStep(row_index=index, table=row.table, status="failed"))
                continue
            effective.append(row)
            after, whole = _read_state(sandbox)
            complete &= whole
            added, removed = _diff(state, after)
            _attribute(row, index, added, removed, explicit_keys, generated, report)
            fired = _merge_fired(fired, context, row.table)
            report.steps.append(SeedStep(
                row_index=index, table=row.table, status="inserted", rows_added=added, rows_removed=removed,
            ))
            state = after
        for trigger in fired:
            if trigger.effect == "unknown":
                report.warnings.append(SeedDiagnostic(
                    category="UNKNOWN_SEED_EFFECT", table=trigger.table, trigger_candidates=[trigger.name],
                    message=f"Trigger {trigger.name} fired and its effect is unknown: "
                            f"{'; '.join(trigger.unknown_reasons)}",
                    suggestion="Rows it wrote inside this schema are in the observed state; anything else isn't.",
                ))
        raise psycopg.Rollback()
    _finish(sandbox, setup_sql, seed, effective, skipped, state, report)
    return report


def _merge_fired(fired: list[TriggerInfo], context: SeedContext, table: str) -> list[TriggerInfo]:
    combined = {t.name + "@" + t.table: t for t in fired}
    combined |= {t.name + "@" + t.table: t for t in fired_closure(context.triggers, RUN_SCHEMA, table, "INSERT")}
    return list(combined.values())


def _attribute(row, index, added, removed, explicit_keys, generated, report) -> None:
    """Separates the row asked for from rows that appeared anyway. Only these rows are called trigger-generated."""
    for table, rows in removed.items():
        for gone in rows:
            generated.get(table, {}).pop(_key(gone), None)
            explicit_keys.get(table, set()).discard(_key(gone))
    requested = _row_dict(row)
    mine = added.get(row.table, [])
    match = next((r for r in mine if all(_same(r.get(c), v) for c, v in requested.items())), None)
    if match is None and mine:
        match = mine[0]
        report.warnings.append(SeedDiagnostic(
            category="TRIGGER_CHANGED_EXPLICIT_ROW", row_index=index, table=row.table, requested_row=requested,
            existing_row=match, message=f"Seed row {index} in {row.table} was stored with different values.",
            suggestion="A BEFORE trigger changed the row. Expect the stored values, not the requested ones.",
        ))
    elif match is None:
        report.warnings.append(SeedDiagnostic(
            category="EXPLICIT_ROW_NOT_IN_FINAL_STATE", row_index=index, table=row.table, requested_row=requested,
            message=f"Seed row {index} in {row.table} did not create a row.",
            suggestion="A BEFORE trigger skipped the row or a rule replaced it.",
        ))
    if match is not None:
        explicit_keys.setdefault(row.table, set()).add(_key(match))
    for table, rows in added.items():
        for new in rows:
            if table == row.table and new is match:
                continue
            generated.setdefault(table, {})[_key(new)] = index


def _explain_conflict(
    sandbox, context, row, index, diagnostic, state, explicit_keys, generated, setup_state, fired, complete,
) -> SeedTransformation | None:
    """Fills in what the conflict is. Returns a transformation only if a skip would be provably the same state."""
    requested = _row_dict(row)
    columns = _unique_columns(sandbox, diagnostic.constraint)
    existing = None
    if columns and all(c in requested for c in columns):
        existing = next(
            (r for r in state.get(row.table, []) if all(_same(r.get(c), requested[c]) for c in columns)), None
        )
    if existing is None:
        diagnostic.suggestion = "A row with this key already exists, but it could not be identified."
        return None
    diagnostic.existing_row = existing
    key = _key(existing)
    if key in generated.get(row.table, {}):
        source_row = generated[row.table][key]
        diagnostic.category = "TRIGGER_GENERATED_CONFLICT"
        diagnostic.generated_by_row = source_row
        diagnostic.trigger_candidates = sorted({t.name for t in fired})
        identical, reasons, differing = _row_is_identical(sandbox, context.schema[row.table], requested, existing)
        diagnostic.differing_columns = differing
        if not complete:
            reasons.append("a table exceeded the read bound, so the observed state is incomplete")
        not_equivalent = _equivalence(context, row.table, reasons, fired)
        diagnostic.equivalent = not not_equivalent
        diagnostic.not_equivalent_because = not_equivalent
        if identical and not not_equivalent:
            diagnostic.suggestion = (
                f"Seed row {index} repeats the row a trigger already produced from seed row {source_row}. "
                "Remove it, or use reconcile mode, which records the skip."
            )
        else:
            diagnostic.suggestion = (
                f"A trigger already produced this key from seed row {source_row}. Its values are "
                f"{existing}, and this row asks for {requested}. Seed only the rows that make the trigger produce "
                "the state you need, or use another key. Removing this row would change the intended initial state."
            )
        if diagnostic.equivalent:
            return SeedTransformation(
                row_index=index, table=row.table, requested_row=requested, actual_row=existing,
                generated_by_row=source_row, trigger_candidates=diagnostic.trigger_candidates,
                constraint=diagnostic.constraint or "",
                justification=[
                    f"PostgreSQL rejected this row on {diagnostic.constraint} because the key already existed.",
                    f"That row first appeared while seed row {source_row} was inserted, and it wasn't that row itself.",
                    "PostgreSQL compared every requested column and found no differences.",
                    f"{row.table} has no INSERT triggers or rules, so the explicit insert would have no other effect.",
                    "No trigger with an unknown effect fired.",
                ],
            )
        return None
    if key in explicit_keys.get(row.table, set()):
        diagnostic.category = "DUPLICATE_EXPLICIT_SEED"
    elif key in map(_key, setup_state.get(row.table, [])):
        diagnostic.category = "SETUP_ROW_CONFLICT"
    diagnostic.suggestion = _SUGGESTIONS.get(diagnostic.category, diagnostic.suggestion)
    return None


def _finish(sandbox, setup_sql, seed, effective, skipped, observed, report: SeedReport) -> None:
    """The real reset is authoritative. A skip stands only if the state matches and each skipped row is still there."""
    if report.diagnostics:
        first = report.diagnostics[0]
        report.stage, report.first_error = first.stage, first.message
        sandbox.reset(setup_sql, [])
        return
    try:
        sandbox.reset(setup_sql, effective)
    except SetupError as exc:
        cause = exc.__cause__
        diagnostic = (
            _diagnostic_from_error(cause, None, None, False) if isinstance(cause, psycopg.Error) else SeedDiagnostic(
                category="UNKNOWN_SEED_EFFECT", stage="seed_execution", message=redact(str(exc)),
            )
        )
        diagnostic.message = redact(str(exc))
        report.diagnostics.append(diagnostic)
        report.stage, report.first_error = diagnostic.stage, diagnostic.message
        sandbox.reset(setup_sql, [])
        return
    final, _ = _read_state(sandbox)
    if skipped:
        problems = []
        if final != observed:
            problems.append("re-running the effective seed produced a different state, so it isn't deterministic")
        for item in skipped:
            if _key(item.actual_row) not in map(_key, final.get(item.table, [])):
                problems.append(f"a later seed row changed the row that row {item.row_index} depended on")
        if problems:
            report.effective_seed, report.effective_seed_sha256 = seed, seed_sha256(seed)
            for item in skipped:
                diagnostic = SeedDiagnostic(
                    category="TRIGGER_GENERATED_CONFLICT", stage="seed_execution", row_index=item.row_index,
                    table=item.table, requested_row=item.requested_row, existing_row=item.actual_row,
                    generated_by_row=item.generated_by_row, trigger_candidates=item.trigger_candidates,
                    constraint=item.constraint, equivalent=False, not_equivalent_because=problems,
                    message="This row conflicted with a trigger-generated row, and skipping it was not provably safe.",
                    suggestion="Seed only rows whose final state is the state you need.",
                )
                report.diagnostics.append(diagnostic)
            report.stage, report.first_error = "seed_execution", report.diagnostics[0].message
            sandbox.reset(setup_sql, [])
            return
    report.feasible = True
    report.effective_seed = effective
    report.effective_seed_sha256 = seed_sha256(effective)
    report.transformations = skipped
    report.final_state = final
    skipped_rows = {t.row_index for t in skipped}
    for index, row in enumerate(seed):
        if index in skipped_rows:
            continue
        requested = _row_dict(row)
        if not any(all(_same(r.get(c), v) for c, v in requested.items()) for r in final.get(row.table, [])):
            if not any(w.row_index == index and w.table == row.table for w in report.warnings):
                report.warnings.append(SeedDiagnostic(
                    category="EXPLICIT_ROW_NOT_IN_FINAL_STATE", row_index=index, table=row.table,
                    requested_row=requested,
                    message=f"Seed row {index} in {row.table} is not in the final state: a later row's trigger "
                            "changed or removed it.",
                    suggestion="The test starts from the final state, not from this row's values.",
                ))


# ---------------------------------------------------------------------------- validator reasons and offline help


_REASON_CATEGORIES: list[tuple[str, Category]] = [
    (r"needs params", "MISSING_REQUIRED_PARAMETER"),
    (r"unknown (table|column)", "UNKNOWN_TABLE_OR_COLUMN"),
    (r"unknown function|is not in |out of source order|skips unconditional|omits unconditional|no statements",
     "UNKNOWN_FUNCTION_OR_STATEMENT"),
    (r"actors, got|duplicate actor|too many seed rows", "INVALID_ACTORS"),
]


def categorize_validator_reason(reason: str) -> Category:
    for pattern, category in _REASON_CATEGORIES:
        if re.search(pattern, reason):
            return category
    return "UNKNOWN_SEED_EFFECT"


def stage_of_verdict(classification: str) -> FailureStage | None:
    """Keeps a schedule's own failures apart from seed failures and from data corruption."""
    return {
        "true_violation": "oracle_violation",
        "execution_error": "schedule_execution",
        "transaction_conflict": "schedule_execution",
    }.get(classification)


def suggest_corrections(
    sandbox: Sandbox, setup_sql: str, report: SeedReport, context: SeedContext,
) -> list[dict[str, Any]]:
    """Shows what could be changed and what each change would do. It applies nothing and sends nothing."""
    options = []
    for diagnostic in report.diagnostics:
        option: dict[str, Any] = {
            "row_index": diagnostic.row_index, "category": diagnostic.category, "advice": diagnostic.suggestion,
            "applied": False,
        }
        if diagnostic.category == "TRIGGER_GENERATED_CONFLICT" and diagnostic.row_index is not None:
            candidate = [r for i, r in enumerate(report.requested_seed) if i != diagnostic.row_index]
            preview = check_seed(sandbox, setup_sql, candidate, context, "strict")
            option |= {
                "candidate": f"remove seed row {diagnostic.row_index}",
                "candidate_executes": preview.feasible,
                "candidate_first_error": preview.first_error,
                "changes_intended_state": not diagnostic.equivalent,
                "columns_that_would_differ_from_request": diagnostic.differing_columns,
                "requires_review": not diagnostic.equivalent,
            }
        options.append(option)
    return options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain a proposed seed offline. Sends no model requests.")
    parser.add_argument("--case", required=True, help="scenario/variant")
    parser.add_argument("--proposal", required=True, help="a JSON file holding one test proposal")
    parser.add_argument("--external", action="store_true", help="look in scenarios_external")
    parser.add_argument("--mode", choices=["strict", "reconcile"], default="strict")
    args = parser.parse_args(argv)
    settings = load_settings()
    scenario, variant = args.case.split("/", 1)
    case = load_case(settings.external_scenarios_dir if args.external else settings.scenarios_dir, scenario, variant)
    with open(args.proposal) as handle:
        proposal = TestProposal.model_validate_json(handle.read())
    with Sandbox(settings) as sandbox:
        context = read_context(sandbox, case.setup_sql)
        report = check_seed(sandbox, case.setup_sql, proposal.seed, context, args.mode)
        out = {
            "report": report.model_dump(), "corrections": suggest_corrections(sandbox, case.setup_sql, report, context),
        }
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
