"""Baseline C: a second code-aware non-LLM generator, added after Baseline B missed the GitLab-derived defect.

Baseline B and its saved results stay untouched. Baseline C differs in generic ways only:
- it parses INSERT parameters correctly;
- it does not seed tables that a trigger writes;
- for composite primary keys it also seeds sibling rows that differ in the last key column;
- it picks WHERE values from seeded rows, and fresh values for writes;
- a function argument used by several statements gets one value.
It was written after this external defect was known and adjusted with all fixtures in view, so its
results are not blind.
"""

from __future__ import annotations

import itertools
import re

from .analyzer_support import TableInfo
from .baselines import (
    _INT_TYPES,
    _NUM_TYPES,
    _WHERE_LITERAL_RE,
    _actor_name,
    _is_writer,
    _kind,
    _select_columns,
    _synth,
    _tables_of,
    _touched,
)
from .database import RUN_SCHEMA, Sandbox
from .models import ActorProposal, ColumnValue, ParamBinding, SeedRow, StatementCall, TestProposal
from .source_model import ExtractedStatement, FunctionModel, SourceModel

_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)\s*(?:VALUES\s*\((.*?)\)(?=\s*(?:ON\b|RETURNING\b|$)))?", re.I
)
_SET_RE = re.compile(r"\bSET\s+(.*?)(?:\bWHERE\b|$)", re.I)
_WHERE_RE = re.compile(r"\bWHERE\s+(.*)$", re.I)
_ASSIGN_RE = re.compile(r"(\w+)\s*=\s*[^,]*?%\((\w+)\)s", re.I)
_TRIGGER_WRITE_RE = re.compile(r"\b(?:INSERT\s+INTO|(?<!DO )UPDATE|DELETE\s+FROM)\s+(\w+)", re.I)
_SIBLING_STEP = 10


def trigger_written_tables(sandbox: Sandbox, setup_sql: str) -> set[str]:
    sandbox.reset(setup_sql, [])
    rows = sandbox.admin.execute(
        """SELECT p.prosrc FROM pg_trigger t
           JOIN pg_proc p ON p.oid = t.tgfoid
           JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = %s AND NOT t.tgisinternal""",
        (RUN_SCHEMA,),
    ).fetchall()
    return {m.lower() for (source,) in rows for m in _TRIGGER_WRITE_RE.findall(source)}


def _is_number(column) -> bool:
    return column.data_type in _INT_TYPES or column.data_type in _NUM_TYPES


def _value(table: TableInfo, column, literals: dict[str, str], sibling: int, fill_nullable: bool, last_key: str | None):
    if column.name == last_key and _is_number(column):
        return _SIBLING_STEP * (sibling + 1)
    if column.name in table.primary_key or column.name in table.foreign_keys:
        return 1
    if column.name in literals:
        return literals[column.name]
    if not column.nullable and not column.has_default:
        return _synth(column, table, column.name, sibling)
    if fill_nullable and column.nullable:
        return _synth(column, table, column.name, sibling)
    return None


def _seed(
    fns: list[FunctionModel], schema: dict[str, TableInfo], skip: set[str], fill_nullable: bool, siblings: bool
) -> list[SeedRow]:
    statements = [s for fn in fns for s in fn.statements]
    insert_only = {t for s in statements if _kind(s.sql) == "INSERT" for t in _tables_of(s)} - {
        t for s in statements if _kind(s.sql) != "INSERT" for t in _tables_of(s)
    }
    wanted = {t for s in statements for t in _tables_of(s) if t in schema} - skip
    if not fill_nullable:
        wanted -= insert_only
    ordered: list[str] = []

    def visit(name: str) -> None:
        if name in ordered or name not in schema or name in skip:
            return
        for parent, _ in schema[name].foreign_keys.values():
            visit(parent)
        ordered.append(name)

    for name in sorted(wanted):
        visit(name)
    literals = {c: v for s in statements for c, v in _WHERE_LITERAL_RE.findall(s.sql)}
    rows = []
    for name in ordered:
        table = schema[name]
        last_key = table.primary_key[-1] if len(table.primary_key) > 1 else None
        for sibling in range(2 if siblings and last_key else 1):
            values = []
            for col in table.columns:
                value = _value(table, col, literals, sibling, fill_nullable, last_key)
                if value is not None:
                    values.append(ColumnValue(column=col.name, value=value))
            rows.append(SeedRow(table=name, values=values))
    return rows


def _role_of(st: ExtractedStatement, param: str) -> tuple[str, str | None]:
    """Whether a placeholder is a written value or a filter, and for which column."""
    insert = _INSERT_RE.search(st.sql)
    if insert and insert.group(2):
        columns = [c.strip() for c in insert.group(1).split(",")]
        values = [v.strip() for v in insert.group(2).split(",")]
        if f"%({param})s" in values:
            return "write", columns[values.index(f"%({param})s")]
    set_clause = _SET_RE.search(st.sql)
    if set_clause:
        for column, name in _ASSIGN_RE.findall(set_clause.group(1)):
            if name == param:
                return "write", column
    where = _WHERE_RE.search(st.sql)
    if where:
        for column, name in _ASSIGN_RE.findall(where.group(1)):
            if name == param:
                return "filter", column
    return "other", None


def _seeded_value(seed: list[SeedRow], table: str, column: str, index: int):
    rows = [r for r in seed if r.table == table]
    if not rows:
        return None
    row = rows[index % len(rows)]
    return next((v.value for v in row.values if v.column == column), None)


def _binding(
    st: ExtractedStatement, param: str, fn: FunctionModel, last_read, schema, seed: list[SeedRow], salt: int
) -> ParamBinding:
    flow, detail = st.flows.get(param, ("unknown", ""))
    if flow == "row" and last_read:
        return ParamBinding(name=param, source="row_ref", bind=last_read[0], column=str(detail))
    if flow == "const":
        return ParamBinding(name=param, source="literal", value=detail)
    if flow == "unknown" and param not in fn.args and last_read and param in last_read[1]:
        return ParamBinding(name=param, source="row_ref", bind=last_read[0], column=param)
    tables = _tables_of(st)
    table = schema.get(tables[0]) if tables else None
    role, column_name = _role_of(st, param)
    column = next((c for c in table.columns if c.name == column_name), None) if table and column_name else None
    if role == "filter" and table is not None and column_name:
        value = _seeded_value(seed, table.name, column_name, salt)
        if value is not None:
            return ParamBinding(name=param, source="literal", value=value)
    if role == "write" and table is not None and column is not None:
        key_part = column.name in table.primary_key and column.name != table.primary_key[-1]
        if key_part or column.name in table.foreign_keys:
            return ParamBinding(name=param, source="literal", value=1)
        if _is_number(column):
            return ParamBinding(name=param, source="literal", value=_SIBLING_STEP * (salt + 3))
        # The seed uses position 0, so a write at position salt + 1 always changes the stored value.
        return ParamBinding(name=param, source="literal", value=_synth(column, table, param, salt + 1))
    if param == "id" or param.endswith("_id"):
        return ParamBinding(name=param, source="literal", value=1)
    if re.search(r"limit|count|amount|qty|size|cents", param, re.I):
        return ParamBinding(name=param, source="literal", value=50)
    return ParamBinding(name=param, source="literal", value=_synth(column, table, param, salt))


def _argument_values(fn: FunctionModel, schema, seed: list[SeedRow], salt: int) -> dict[str, ParamBinding]:
    """One value per function argument. A filter's seeded value wins, so writes target the same row."""
    uses = [(st, p, detail) for st in fn.statements for p, (kind, detail) in st.flows.items() if kind == "arg"]
    uses.sort(key=lambda use: _role_of(use[0], use[1])[0] != "filter")
    values: dict[str, ParamBinding] = {}
    for st, param, argument in uses:
        values.setdefault(str(argument), _binding(st, param, fn, None, schema, seed, salt))
    return values


def _actor(name: str, fn: FunctionModel, schema, seed: list[SeedRow], salt: int) -> ActorProposal:
    calls: list[StatementCall] = []
    last_read = None
    arguments = _argument_values(fn, schema, seed, salt)
    for i, st in enumerate(fn.statements):
        params = []
        for p in st.placeholders:
            kind, detail = st.flows.get(p, ("unknown", ""))
            if kind == "arg":
                params.append(arguments[str(detail)].model_copy(update={"name": p}))
            else:
                params.append(_binding(st, p, fn, last_read, schema, seed, salt))
        bind = None
        if _kind(st.sql) == "SELECT":
            bind = f"r{i}"
            last_read = (bind, _select_columns(st, schema))
        calls.append(StatementCall(statement_id=st.statement_id, params=params, bind_as=bind))
    return ActorProposal(name=name, function=fn.ref, calls=calls)


def baseline_c_proposals(schema: dict[str, TableInfo], source: SourceModel, skip: set[str]) -> list[TestProposal]:
    writers = sorted((fn for fn in source.functions.values() if _is_writer(fn)), key=lambda f: f.ref)
    linked = [(a, b) for a, b in itertools.combinations(writers, 2) if _touched(a) & _touched(b)]
    linked.sort(key=lambda p: (p[0].file == p[1].file, p[0].ref, p[1].ref))
    pairs = [*linked, *((fn, fn) for fn in writers)]
    shapes = [("sparse", False, False), ("siblings", False, True), ("filled", True, False)]
    tests = []
    for label, fill, siblings in shapes:
        for a, b in pairs:
            seed = _seed([a, b], schema, skip, fill, siblings)
            if label == "siblings" and not any(len(schema[r.table].primary_key) > 1 for r in seed):
                continue
            tests.append(TestProposal(
                name=f"{a.ref} vs {b.ref} ({label} seed)", seed=seed,
                actors=[_actor(_actor_name(a, 0), a, schema, seed, 0), _actor(_actor_name(b, 1), b, schema, seed, 1)],
            ))
    return tests
