"""EXPERIMENTAL generic typed construction: typed rows + trusted setup SQL → Phase 9B → exact construction → verify.

    construct_typed(target, sandbox, setup_sql=..., rows=[(table, {column: TV})], schema="ds_sc_...")

1. every value is checked against its tag and the live column (``typed.check_value``), in the constructor's own
   freshly built schema, before anything is constructed;
2. Phase 9B's ``construct_state`` runs on the rows' plain values (its ``SeedRow`` shape) in its sandbox;
3. only a ``PROVEN`` classification continues: the rows are inserted, with their exact typed parameters, into
   ``schema`` in the run database, in the constructor's order (rows it found already present are left out);
4. ``typed.verify`` re-reads every requested table on its own session (exact, count-exact, one-to-one), and the
   whole schema's to_jsonb snapshot must equal the constructor's independently checked state.

The result is a ``Constructed`` with ``status`` ``constructed`` or ``refused_*``. A refused status leaves ``schema``
dropped. Nothing is ever inserted for a refused stage.

Phase 9B's ``SeedRow`` takes plain scalars, so ``uuid``, ``timestamptz`` and ``json`` values reach it as strings. Its
own checks then run PostgreSQL's implicit casts; its final, independent check compares with
``IS NOT DISTINCT FROM %s::<type>``, which PostgreSQL can't do for ``json`` (no equality operator). Whatever it
decides is recorded as-is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

from .. import harness as hz
from . import phase9b, typed
from .pipeline import constructor_order, snapshot


@dataclass
class Constructed:
    status: str  # constructed, refused_typed, refused_constructor, refused_verification
    detail: Any = None
    classification: str | None = None
    report: dict[str, Any] | None = None
    order: list[int] | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    schema: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "classification": self.classification,
                "order": self.order, "verification": self.verification, "schema": self.schema,
                "report": self.report}


def _drop(conn: Any, schema: str) -> None:
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def build(conn: Any, schema: str, setup_sql: str, rows: list[tuple[str, dict[str, typed.TV]]],
          order: list[int]) -> None:
    """A fresh ``schema``, the setup SQL, and the typed rows in ``order``, in one transaction."""
    with conn.transaction():
        _drop(conn, schema)
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET LOCAL search_path = {}").format(sql.Identifier(schema)))
        conn.execute(setup_sql)
        for i in order:
            table, vals = rows[i]
            cols = list(vals)
            conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(typed.placeholder(vals[c]) for c in cols)), [typed.param(vals[c]) for c in cols])


def construct_typed(target: hz.Target, sandbox: Any, *, setup_sql: str, rows: list[tuple[str, dict[str, typed.TV]]],
                    schema: str, budget: Any = None) -> Constructed:
    from deployspecter.database import RUN_SCHEMA
    from deployspecter.models import ColumnValue, SeedRow
    if not schema.startswith("ds_sc_"):
        raise ValueError("construction schemas are named ds_sc_*")
    out = Constructed("refused_typed", schema=schema)
    # 1. types, against the trusted schema built fresh in the constructor's sandbox
    sandbox.reset(setup_sql, [])
    try:
        for table, vals in rows:
            cols = typed.columns(sandbox.admin, RUN_SCHEMA, table)
            if not cols:
                raise typed.TypedRefused(f"no table {table}")
            for c, tv in vals.items():
                if c not in cols:
                    raise typed.TypedRefused(f"{table}.{c} doesn't exist")
                typed.check_value(tv, cols[c])
    except (typed.TypedRefused, ValueError) as exc:
        out.detail = str(exc)
        return out
    # 2. Phase 9B, on the plain values
    seed = [SeedRow(table=t, values=[ColumnValue(column=c, value=tv.value) for c, tv in vals.items()])
            for t, vals in rows]
    try:
        report = phase9b.construct(sandbox, setup_sql, seed, budget=budget)
    except psycopg.Error as exc:  # the constructor's own failure is an outcome, not a crash of this pipeline
        out.status, out.detail = "refused_constructor", f"the constructor raised {type(exc).__name__}: " \
                                                       f"{str(exc).splitlines()[0][:300]}"
        return out
    out.report, out.classification = report, report["classification"]
    if report["classification"] not in phase9b.PROVEN:
        out.status, out.detail = "refused_constructor", report["explanation"]
        return out
    plain = [{"table": t, "values": [{"column": c, "value": tv.value} for c, tv in vals.items()]} for t, vals in rows]
    out.order = constructor_order(report, plain)
    # 3. exact construction in the run database, 4. independent verification on another session
    admin = target.admin()
    try:
        build(admin, schema, setup_sql, rows, out.order)
        with psycopg.connect(**target.conninfo(), autocommit=True, application_name="ds_sc_verify") as conn:
            problems: list[str] = []
            by_table: dict[str, list[dict[str, typed.TV]]] = {}
            for t, vals in rows:  # every requested row, including those the constructor found already present
                by_table.setdefault(t, []).append(vals)
            for t, want in sorted(by_table.items()):
                problems += typed.verify(conn, schema, t, want,
                                         expect_count=len(report["final_snapshot"].get(t, [])))
            state = snapshot(conn, schema)
        equal = json.dumps(state, sort_keys=True) == json.dumps(report["final_snapshot"], sort_keys=True)
        out.verification = {"typed_exact": problems, "constructor_snapshot_equal": equal}
        if problems or not equal:
            out.status, out.detail = "refused_verification", out.verification
            _drop(admin, schema)
            return out
    except psycopg.Error as exc:
        out.status, out.detail = "refused_verification", f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
        _drop(admin, schema)
        return out
    finally:
        admin.close()
    out.status = "constructed"
    return out
