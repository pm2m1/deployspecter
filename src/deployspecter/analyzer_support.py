"""Schema catalog reads shared by the validator's callers and the non-AI baselines."""

from __future__ import annotations

from dataclasses import dataclass, field

from .database import RUN_SCHEMA, Sandbox
from .validation import Catalog


def read_catalog(sandbox: Sandbox, setup_sql: str) -> Catalog:
    sandbox.reset(setup_sql, [])
    rows = sandbox.admin.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s",
        (RUN_SCHEMA,),
    ).fetchall()
    catalog: Catalog = {}
    for table, column in rows:
        catalog.setdefault(table, set()).add(column)
    return catalog


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    has_default: bool


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)


def read_schema(sandbox: Sandbox, setup_sql: str) -> dict[str, TableInfo]:
    """Detailed schema for prompts and the non-AI baseline, read from PostgreSQL itself."""
    sandbox.reset(setup_sql, [])
    admin = sandbox.admin
    tables: dict[str, TableInfo] = {}
    for table, column, dtype, nullable, default in admin.execute(
        """SELECT table_name, column_name, data_type, is_nullable, column_default
           FROM information_schema.columns WHERE table_schema = %s
           ORDER BY table_name, ordinal_position""",
        (RUN_SCHEMA,),
    ).fetchall():
        info = tables.setdefault(table, TableInfo(table))
        info.columns.append(ColumnInfo(column, dtype, nullable == "YES", default is not None))
    for table, contype, attnames, ftable, fattnames, definition in admin.execute(
        """SELECT c.conrelid::regclass::text, c.contype::text,
                  ARRAY(SELECT a.attname::text FROM unnest(c.conkey) k
                        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k),
                  c.confrelid::regclass::text,
                  ARRAY(SELECT a.attname::text FROM unnest(c.confkey) k
                        JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k),
                  pg_get_constraintdef(c.oid)
           FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
           WHERE n.nspname = %s""",
        (RUN_SCHEMA,),
    ).fetchall():
        name = table.split(".")[-1]
        info = tables.get(name)
        if info is None:
            continue
        if contype == "p":
            info.primary_key = list(attnames)
        elif contype == "f":
            for col, fcol in zip(attnames, fattnames, strict=True):
                info.foreign_keys[col] = (ftable.split(".")[-1], fcol)
        elif contype == "c":
            info.checks.append(definition)
    return tables
