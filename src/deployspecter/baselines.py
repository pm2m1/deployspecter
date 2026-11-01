"""Non-LLM comparison systems.

Baseline A: conventional static rules over extracted SQL and migrations. It runs
nothing, so it can only warn and cannot verify a failure.

Baseline B: a code-aware test generator that feeds the same validator, scheduler
and budget as the AI system. It uses AST data flow, the live schema, literal
mining and name-matching heuristics.

Both were written by the benchmark's author with the fixtures in view, so they
are best read as optimistic versions of what such tools would achieve.
"""

from __future__ import annotations

import itertools
import re

from pydantic import BaseModel

from .analyzer_support import ColumnInfo, TableInfo
from .models import ActorProposal, ColumnValue, ParamBinding, SeedRow, StatementCall, TestProposal
from .scenarios import Case
from .source_model import ExtractedStatement, FunctionModel, SourceModel

MAX_BASELINE_TESTS = 12


class StaticFinding(BaseModel):
    rule: str
    location: str
    message: str


_SET_RE = re.compile(r"\bSET\s+(.*?)(?:\bWHERE\b|$)", re.I)
_WHERE_RE = re.compile(r"\bWHERE\s+(.*)$", re.I)
_PARAM_RE = re.compile(r"%\((\w+)\)s")


def _kind(sql: str) -> str:
    return sql.lstrip().split(None, 1)[0].upper()


def static_scan(files: dict[str, str], source: SourceModel) -> list[StaticFinding]:
    findings: list[StaticFinding] = []
    for fn in source.functions.values():
        read_earlier = False
        for st in fn.statements:
            kind = _kind(st.sql)
            if kind == "SELECT":
                read_earlier = True
                continue
            if kind == "UPDATE" and read_earlier:
                where = _WHERE_RE.search(st.sql)
                guarded = bool(where and re.search(r"\bAND\b|\bOR\b|\bIS\s+(NOT\s+)?NULL\b", where.group(1), re.I))
                set_clause = _SET_RE.search(st.sql)
                set_params = set(_PARAM_RE.findall(set_clause.group(1))) if set_clause else set()
                from_read = [
                    p for p in set_params
                    if st.flows.get(p, ("unknown", ""))[0] in {"row", "unknown"}
                ]
                if from_read and not guarded:
                    findings.append(StaticFinding(
                        rule="unguarded-read-then-update", location=st.statement_id,
                        message=f"UPDATE writes previously read values {sorted(from_read)} with no guard in WHERE.",
                    ))
            if kind == "INSERT" and read_earlier and re.search(r"ON\s+CONFLICT.*DO\s+UPDATE", st.sql, re.I):
                if not re.search(r"DO\s+UPDATE.*\bWHERE\b", st.sql, re.I):
                    findings.append(StaticFinding(
                        rule="unguarded-upsert-after-read", location=st.statement_id,
                        message="Upsert overwrites an existing row from previously read values with no WHERE guard.",
                    ))
    for name, text in sorted(files.items()):
        if not name.endswith(".sql") or not re.search(r"BEFORE\s+UPDATE", text, re.I):
            continue
        assigns = re.search(r"NEW\.\w+\s*:=", text)
        guarded = re.search(r"IS\s+DISTINCT\s+FROM|\bWHEN\s*\(", text, re.I)
        if assigns and not guarded:
            findings.append(StaticFinding(
                rule="unconditional-trigger-assignment", location=name,
                message="BEFORE UPDATE trigger assigns a column on every update, including unrelated ones.",
            ))
    return findings


# ------------------------------------------------------------- Baseline B

_TABLE_RE = re.compile(r"\b(?:FROM|UPDATE|INTO)\s+(\w+)", re.I)
_SELECT_COLS_RE = re.compile(r"^\s*SELECT\s+(.*?)\s+FROM\s+(\w+)", re.I)
_COL_PARAM_RE = re.compile(r"(\w+)\s*=\s*[^,]*?%\((\w+)\)s", re.I)
_INSERT_RE = re.compile(r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", re.I)
_ENUM_RE = re.compile(r"'([^']+)'")
_WHERE_LITERAL_RE = re.compile(r"(\w+)\s*=\s*'([^']+)'")
_INT_TYPES = {"integer", "bigint", "smallint"}
_NUM_TYPES = {"numeric", "real", "double precision"}


def _tables_of(st: ExtractedStatement) -> list[str]:
    return _TABLE_RE.findall(st.sql)


def _is_writer(fn: FunctionModel) -> bool:
    return any(_kind(s.sql) in {"UPDATE", "INSERT", "DELETE"} for s in fn.statements)


def _touched(fn: FunctionModel) -> set[str]:
    return {t for s in fn.statements for t in _tables_of(s)}


def _enum_values(table: TableInfo, column: str) -> list[str]:
    for check in table.checks:
        if re.search(rf"\b{re.escape(column)}\b", check):
            values = _ENUM_RE.findall(check)
            if values:
                return values
    return []


def _synth(column: ColumnInfo | None, table: TableInfo | None, label: str, salt: int):
    if column is not None and table is not None:
        choices = _enum_values(table, column.name)
        if choices:
            return choices[salt % len(choices)]
    dtype = column.data_type if column else ""
    if dtype in _INT_TYPES or dtype in _NUM_TYPES:
        return 10 + 7 * salt
    if dtype == "boolean":
        return bool(salt % 2)
    if "timestamp" in dtype or dtype == "date":
        return None
    return f"{label}_{salt}"


def _param_column(st: ExtractedStatement, param: str, schema: dict[str, TableInfo]):
    """Which column does this placeholder feed? Parsed from SET/WHERE or INSERT lists."""
    tables = _tables_of(st)
    table = schema.get(tables[0]) if tables else None
    column_name = None
    match = _INSERT_RE.search(st.sql)
    if match:
        cols = [c.strip() for c in match.group(1).split(",")]
        vals = [v.strip() for v in match.group(2).split(",")]
        if f"%({param})s" in vals:
            column_name = cols[vals.index(f"%({param})s")]
    if column_name is None:
        for col, name in _COL_PARAM_RE.findall(st.sql):
            if name == param:
                column_name = col
                break
    if table is None or column_name is None:
        return table, None
    return table, next((c for c in table.columns if c.name == column_name), None)


def _select_columns(st: ExtractedStatement, schema: dict[str, TableInfo]) -> set[str]:
    match = _SELECT_COLS_RE.search(st.sql)
    if not match:
        return set()
    if match.group(1).strip() == "*":
        table = schema.get(match.group(2))
        return {c.name for c in table.columns} if table else set()
    return {c.strip().split()[-1].split(".")[-1] for c in match.group(1).split(",")}


def _binding(
    st: ExtractedStatement, param: str, fn: FunctionModel, last_read: tuple[str, set[str]] | None,
    schema: dict[str, TableInfo], salt: int,
) -> ParamBinding:
    flow, detail = st.flows.get(param, ("unknown", ""))
    if flow == "row" and last_read:
        return ParamBinding(name=param, source="row_ref", bind=last_read[0], column=str(detail))
    if flow == "const":
        return ParamBinding(name=param, source="literal", value=detail)
    if flow == "unknown" and param not in fn.args and last_read and param in last_read[1]:
        return ParamBinding(name=param, source="row_ref", bind=last_read[0], column=param)
    table, column = _param_column(st, param, schema)
    if param == "id" or param.endswith("_id") or (column is not None and table and column.name in table.primary_key):
        return ParamBinding(name=param, source="literal", value=1)
    if column is None and re.search(r"limit|count|amount|qty|size|cents", param, re.I):
        return ParamBinding(name=param, source="literal", value=50)
    return ParamBinding(name=param, source="literal", value=_synth(column, table, param, salt))


def _actor(name: str, fn: FunctionModel, schema: dict[str, TableInfo], salt: int) -> ActorProposal:
    calls: list[StatementCall] = []
    last_read: tuple[str, set[str]] | None = None
    for i, st in enumerate(fn.statements):
        params = [_binding(st, p, fn, last_read, schema, salt) for p in st.placeholders]
        bind = None
        if _kind(st.sql) == "SELECT":
            bind = f"r{i}"
            last_read = (bind, _select_columns(st, schema))
        calls.append(StatementCall(statement_id=st.statement_id, params=params, bind_as=bind))
    # A bind nothing reads is harmless, and it keeps results visible in the trace.
    return ActorProposal(name=name, function=fn.ref, calls=calls)


def _seed(
    fns: list[FunctionModel], schema: dict[str, TableInfo], fill_nullable: bool
) -> list[SeedRow]:
    statements = [s for fn in fns for s in fn.statements]
    insert_only = {t for s in statements if _kind(s.sql) == "INSERT" for t in _tables_of(s)} - {
        t for s in statements if _kind(s.sql) != "INSERT" for t in _tables_of(s)
    }
    wanted = {t for s in statements for t in _tables_of(s) if t in schema}
    if not fill_nullable:
        wanted -= insert_only
    pending = sorted(wanted)
    ordered: list[str] = []

    def visit(name: str) -> None:
        if name in ordered or name not in schema:
            return
        for parent, _ in schema[name].foreign_keys.values():
            visit(parent)
        ordered.append(name)

    for name in pending:
        visit(name)
    literals = {c: v for s in statements for c, v in _WHERE_LITERAL_RE.findall(s.sql)}
    rows = []
    for name in ordered:
        table = schema[name]
        values = []
        for col in table.columns:
            if col.name in table.primary_key or col.name in table.foreign_keys:
                values.append(ColumnValue(column=col.name, value=1))
            elif col.name in literals:
                values.append(ColumnValue(column=col.name, value=literals[col.name]))
            elif not col.nullable and not col.has_default:
                values.append(ColumnValue(column=col.name, value=_synth(col, table, col.name, 0)))
            elif fill_nullable and col.nullable:
                value = _synth(col, table, col.name, 0)
                if value is not None:
                    values.append(ColumnValue(column=col.name, value=value))
        rows.append(SeedRow(table=name, values=values))
    return rows


def _actor_name(fn: FunctionModel, index: int) -> str:
    return f"a{index}_{fn.name}"[:30]


def baseline_proposals(case: Case, schema: dict[str, TableInfo], source: SourceModel) -> list[TestProposal]:
    writers = [fn for fn in source.functions.values() if _is_writer(fn)]
    linked = [
        (a, b) for a, b in itertools.combinations(sorted(writers, key=lambda f: f.ref), 2)
        if _touched(a) & _touched(b)
    ]
    # Pairs from different files first: mixed-version hazards cross release boundaries.
    linked.sort(key=lambda p: (p[0].file == p[1].file, p[0].ref, p[1].ref))
    same_function = [(fn, fn) for fn in sorted(writers, key=lambda f: f.ref)]
    tests: list[TestProposal] = []
    for fill_nullable in (False, True):
        for a, b in [*linked, *same_function]:
            tests.append(TestProposal(
                name=f"{a.ref} vs {b.ref} ({'filled' if fill_nullable else 'sparse'} seed)",
                seed=_seed([a, b], schema, fill_nullable),
                actors=[_actor(_actor_name(a, 0), a, schema, 1), _actor(_actor_name(b, 1), b, schema, 2)],
            ))
    return tests[:MAX_BASELINE_TESTS]
