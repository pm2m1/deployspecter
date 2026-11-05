"""EXPERIMENTAL: typed values for state construction, and the exact, independent re-read that verifies them.

A typed value is ``(tag, value)``. Supported tags, with the Python type psycopg returns for the column and the only
PostgreSQL column types each may be written to:

    null         None                       any nullable column
    int          int (never bool)           smallint, integer, bigint (range-checked)
    float        float                      double precision (bit-exact, sign of zero kept; NaN only equals NaN)
    bool         bool                       boolean
    str          str                        text, character varying (a length limit is checked)
    uuid         str, canonical lowercase   uuid (read back as uuid.UUID, compared by value)
    timestamptz  str, ISO 8601 with offset  timestamp with time zone (read back as aware datetime, same instant
                                            and microsecond; the original offset isn't stored by PostgreSQL)
    json         str, the exact JSON text   json (kept verbatim by PostgreSQL: compared as text, byte for byte)
    jsonb        str, JSON text             jsonb (PostgreSQL normalizes it: compared as parsed JSON, exactly,
                                            with types: 1 is not 1.0 is not true, and key sets must match)

Anything else (another tag, a tag on a column of another type, a Python type the tag doesn't allow) is refused
before anything is written. ``'NULL'`` (a four-letter string) and NULL are never conflated. ``verify`` reads on its
own connection, in a REPEATABLE READ READ ONLY transaction, and requires the table to hold exactly the requested rows:
no more, no fewer, and each column exactly equal.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import uuid
from dataclasses import dataclass
from typing import Any

from psycopg import sql

INT_RANGES = {"smallint": (-(2**15), 2**15 - 1), "integer": (-(2**31), 2**31 - 1), "bigint": (-(2**63), 2**63 - 1)}
COLUMN_TYPES = {"int": set(INT_RANGES), "float": {"double precision"}, "bool": {"boolean"},
                "str": {"text", "character varying"}, "uuid": {"uuid"}, "timestamptz": {"timestamp with time zone"},
                "json": {"json"}, "jsonb": {"jsonb"}}


class TypedRefused(ValueError):
    """A value this module can't keep exact."""


@dataclass(frozen=True)
class TV:
    tag: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        v = self.value
        if self.tag == "float" and not math.isfinite(v):
            v = repr(v)
        elif self.tag == "float" and v == 0.0 and math.copysign(1.0, v) < 0:
            v = "-0.0"
        return {"tag": self.tag, "value": v}


def check_value(tv: TV, column: dict[str, Any]) -> None:
    """Refuse a value that doesn't fit its tag or its column. ``column``: data_type, is_nullable, char_max."""
    t, v, ctype = tv.tag, tv.value, column["data_type"]
    if t == "null":
        if v is not None:
            raise TypedRefused("a null tag with a value")
        if column["is_nullable"] != "YES":
            raise TypedRefused("NULL in a NOT NULL column")
        return
    if t not in COLUMN_TYPES:
        raise TypedRefused(f"unsupported tag {t!r}")
    if ctype not in COLUMN_TYPES[t]:
        raise TypedRefused(f"a {t} value for a {ctype} column")
    want = {"int": int, "float": float, "bool": bool, "str": str, "uuid": str, "timestamptz": str, "json": str,
            "jsonb": str}[t]
    if type(v) is not want:
        raise TypedRefused(f"a {t} tag holding {type(v).__name__}")
    if t == "int":
        lo, hi = INT_RANGES[ctype]
        if not lo <= v <= hi:
            raise TypedRefused(f"{v} is outside {ctype}")
    if t == "str" and column.get("char_max") is not None and len(v) > column["char_max"]:
        raise TypedRefused(f"a {len(v)}-character string for {ctype}({column['char_max']})")
    if t == "str" and "\x00" in v:
        raise TypedRefused("a NUL character")
    if t == "uuid" and str(uuid.UUID(v)) != v:
        raise TypedRefused("a uuid that isn't in canonical lowercase form")
    if t == "timestamptz":
        parsed = dt.datetime.fromisoformat(v)
        if parsed.tzinfo is None:
            raise TypedRefused("a timestamptz without an offset")
    if t in ("json", "jsonb"):
        json.loads(v, parse_constant=_refuse_constant)  # valid JSON, no NaN/Infinity


def _refuse_constant(name: str) -> Any:
    raise TypedRefused(f"JSON constant {name} isn't valid JSON")


def param(tv: TV) -> Any:
    """The parameter psycopg sends. Text-shaped tags are cast in SQL (``placeholder``), never parsed by Python."""
    return tv.value


def placeholder(tv: TV) -> sql.Composable:
    cast = {"uuid": "uuid", "timestamptz": "timestamptz", "json": "json", "jsonb": "jsonb"}.get(tv.tag)
    return sql.SQL("%s::{}").format(sql.SQL(cast)) if cast else sql.SQL("%s")


def same(tv: TV, got: Any, got_text: str | None = None) -> bool:
    """Exact equality of a requested typed value and what psycopg read back (``got_text``: the column ``::text``)."""
    t, v = tv.tag, tv.value
    if t == "null":
        return got is None
    if got is None:
        return False
    if t == "int":
        return type(got) is int and got == v
    if t == "float":
        if type(got) is not float:
            return False
        if math.isnan(v) or math.isnan(got):
            return math.isnan(v) and math.isnan(got)
        return got == v and math.copysign(1.0, got) == math.copysign(1.0, v)
    if t == "bool":
        return type(got) is bool and got is v
    if t == "str":
        return type(got) is str and got == v
    if t == "uuid":
        return isinstance(got, uuid.UUID) and str(got) == v
    if t == "timestamptz":
        want = dt.datetime.fromisoformat(v)
        return isinstance(got, dt.datetime) and got.tzinfo is not None and got == want
    if t == "json":
        return got_text == v
    if t == "jsonb":
        return got_text is not None and json_exact(json.loads(v), json.loads(got_text))
    return False


def json_exact(a: Any, b: Any) -> bool:
    """JSON values equal with their JSON types: an int isn't a float isn't a bool; objects by key set."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(json_exact(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(json_exact(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float):
        return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)
    return a == b


def columns(conn: Any, schema: str, table: str) -> dict[str, dict[str, Any]]:
    cur = conn.execute("SELECT column_name::text, data_type::text, is_nullable::text, character_maximum_length "
                       "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                       "ORDER BY ordinal_position", (schema, table))
    return {r[0]: {"data_type": r[1], "is_nullable": r[2], "char_max": r[3]} for r in cur.fetchall()}


def verify(conn: Any, schema: str, table: str, rows: list[dict[str, TV]], *,
           expect_count: int | None = None) -> list[str]:
    """Read ``schema.table`` on ``conn`` (its own session) and compare with ``rows`` exactly: the table must hold
    exactly ``len(rows)`` rows, and there must be a one-to-one assignment of read rows to requested rows under which
    every requested column is exactly equal (bipartite matching, so identical-looking rows can't be double-counted).
    ``expect_count`` (default ``len(rows)``): the exact row count, when triggers or setup rows add rows of their own.
    Columns a row doesn't name (database defaults, serials) aren't compared here. Returns the problems."""
    problems: list[str] = []
    cols = columns(conn, schema, table)
    named = sorted({c for r in rows for c in r}) or sorted(cols)[:1]
    if missing := [c for c in named if c not in cols]:
        return [f"{table}: no column(s) {missing}"]
    select = sql.SQL(", ").join([sql.Identifier(c) for c in named] +
                                [sql.SQL("{}::text").format(sql.Identifier(c)) for c in named])
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        got = conn.execute(sql.SQL("SELECT {} FROM {}.{}").format(select, sql.Identifier(schema),
                                                                 sql.Identifier(table))).fetchall()
    n = len(named)
    count = len(rows) if expect_count is None else expect_count
    if len(got) != count:
        problems.append(f"{table}: {len(got)} rows, {count} expected")

    def fits(w: dict[str, TV], r: tuple[Any, ...]) -> bool:
        return all(same(tv, r[named.index(c)], r[named.index(c) + n]) for c, tv in w.items())

    edges = [[j for j, r in enumerate(got) if fits(w, r)] for w in rows]
    owner: dict[int, int] = {}

    def augment(i: int, seen: set[int]) -> bool:
        for j in edges[i]:
            if j not in seen:
                seen.add(j)
                if j not in owner or augment(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    for i, w in enumerate(rows):
        if not augment(i, set()):
            problems.append(f"{table}: no exactly-equal row for requested {[(c, tv.to_dict()) for c, tv in w.items()]}")
    return problems
