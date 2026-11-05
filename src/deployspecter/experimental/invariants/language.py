"""EXPERIMENTAL: ``ds-invariant/1``, a restricted predicate language for invariant hypotheses.

A hypothesis is JSON data. It is checked for admissibility against a declared domain (tables, typed columns), then
evaluated by the pure function ``evaluate`` on observed states. Nothing in it is code; no hypothesis can name a column
or table outside its domain, compare incompatible types, or grow past fixed size bounds.

Two kinds:

* ``row``: for every row of ``table`` satisfying all ``where`` conditions, all ``require`` conditions hold.
  A condition compares two terms with ``eq ne lt le gt ge``. A term is a column of the row, a typed constant, or a
  child aggregate over another table's rows whose ``fk`` column equals this row's ``id`` (``child_sum``/
  ``child_count``).
* ``aggregate``: a signed sum of table aggregates (``sum`` of a column or ``count`` of rows, optionally filtered by
  one column equal to one constant, over the ``initial`` or the ``final`` state) compared with another.

NULL semantics, fixed: ``eq``/``ne`` compare NULL like a value (NULL eq NULL is true); an ordering comparison with a
NULL operand is false, so a ``where`` with one excludes the row and a ``require`` with one is a violation. Booleans
are never ordered, and an int is never compared with a str or a bool.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel

LANGUAGE = "ds-invariant/1"
OPS = ("eq", "ne", "lt", "le", "gt", "ge")
MAX_CONDITIONS = 4
MAX_TERMS = 4

# Domains: table -> column -> (type, nullable). ``type`` is int, str or bool.
DOMAINS: dict[str, dict[str, dict[str, tuple[str, bool]]]] = {
    "reservations": {
        "events": {"id": ("int", False), "capacity": ("int", False), "reserved": ("int", False)},
        "reservations": {"id": ("int", False), "event_id": ("int", False), "holder": ("str", False),
                         "qty": ("int", False)},
        "rejections": {"id": ("int", False), "event_id": ("int", False), "holder": ("str", False),
                       "seats_available": ("int", False)},
        # what each handler call returned (from the run's results, not the database)
        "calls": {"id": ("int", False), "actor": ("str", False), "handler": ("str", False),
                  "status": ("str", True)},
    },
    "chancy_jobs": {
        # one row per job, read back from chancy_jobs after the run, joined with what the actors observed
        "jobs": {"id": ("int", False), "n": ("int", False), "state": ("str", False), "attempts": ("int", False),
                 "max_attempts": ("int", False), "has_claim_id": ("bool", True), "completed": ("bool", False),
                 "errors": ("int", False), "taken": ("bool", False), "cancelled": ("bool", False),
                 "executions": ("int", False), "successes": ("int", False)},
    },
}


class Term(BaseModel):
    kind: Literal["col", "int", "str", "bool", "null", "child_sum", "child_count"]
    col: str | None
    int_value: int | None
    str_value: str | None
    bool_value: bool | None
    table: str | None
    fk: str | None


class Condition(BaseModel):
    left: Term
    op: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    right: Term


class AggTerm(BaseModel):
    sign: Literal[1, -1]
    kind: Literal["sum", "count", "int"]
    table: str | None
    col: str | None
    state: Literal["initial", "final"]
    filter_col: str | None
    filter_str: str | None
    int_value: int | None


class Hypothesis(BaseModel):
    id: str
    statement: str
    domain: Literal["reservations", "chancy_jobs"]
    kind: Literal["row", "aggregate"]
    table: str | None
    where: list[Condition]
    require: list[Condition]
    lhs: list[AggTerm]
    op: Literal["eq", "ne", "lt", "le", "gt", "ge"] | None
    rhs: list[AggTerm]


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis]


def digest(h: dict[str, Any]) -> str:
    """The hypothesis's identity: its semantic content, without id or statement."""
    core = {k: v for k, v in h.items() if k not in ("id", "statement")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ admissibility

def _term_type(t: dict[str, Any], domain: dict[str, Any], table: str) -> tuple[str | None, str | None]:
    """(type or None for null, problem)."""
    k = t["kind"]
    if k == "col":
        col = domain[table].get(t.get("col") or "")
        return (col[0], None) if col else (None, f"{table}.{t.get('col')} isn't a column")
    if k == "int":
        return ("int", None) if type(t.get("int_value")) is int else (None, "int term without an int value")
    if k == "str":
        return ("str", None) if isinstance(t.get("str_value"), str) else (None, "str term without a str value")
    if k == "bool":
        return ("bool", None) if type(t.get("bool_value")) is bool else (None, "bool term without a bool value")
    if k == "null":
        return None, None
    child = t.get("table")
    if child not in domain or child == table:
        return None, f"child aggregate over {child!r}: not another table of the domain"
    if t.get("fk") not in domain[child] or domain[child][t["fk"]][0] != "int":
        return None, f"{child}.{t.get('fk')} isn't an int column"
    if k == "child_sum":
        col = domain[child].get(t.get("col") or "")
        if not col or col[0] != "int":
            return None, f"child_sum of {child}.{t.get('col')}: not an int column"
    return "int", None


def _cond_problem(c: dict[str, Any], domain: dict[str, Any], table: str) -> str | None:
    lt, lp = _term_type(c["left"], domain, table)
    rt, rp = _term_type(c["right"], domain, table)
    if lp or rp:
        return lp or rp
    if c["left"]["kind"] == "null" and c["right"]["kind"] == "null":
        return "compares NULL with NULL"
    if c["op"] in ("lt", "le", "gt", "ge"):
        if "null" in (c["left"]["kind"], c["right"]["kind"]):
            return "orders against NULL"
        if lt == "bool" or rt == "bool":
            return "orders booleans"
    if lt is not None and rt is not None and lt != rt:
        return f"compares {lt} with {rt}"
    if c["left"] == c["right"]:
        return "compares a term with itself"
    return None


def problems(h: Any) -> list[str]:
    """Why a hypothesis is inadmissible (empty: admissible). Shape, domain, types and size bounds."""
    try:
        hyp = Hypothesis.model_validate(h).model_dump()
    except Exception as exc:  # noqa: BLE001 - a malformed hypothesis is a recorded rejection
        return [f"shape: {type(exc).__name__}: {str(exc)[:160]}"]
    domain = DOMAINS[hyp["domain"]]
    out: list[str] = []
    if hyp["kind"] == "row":
        if hyp["table"] not in domain:
            return [f"row hypothesis over unknown table {hyp['table']!r}"]
        if not hyp["require"]:
            out.append("a row hypothesis needs at least one require condition")
        if len(hyp["where"]) > MAX_CONDITIONS or len(hyp["require"]) > MAX_CONDITIONS:
            out.append(f"more than {MAX_CONDITIONS} conditions")
        if hyp["lhs"] or hyp["rhs"] or hyp["op"] is not None:
            out.append("a row hypothesis has no lhs/rhs/op")
        for c in hyp["where"] + hyp["require"]:
            p = _cond_problem(c, domain, hyp["table"])
            if p:
                out.append(p)
    else:
        if hyp["where"] or hyp["require"]:
            out.append("an aggregate hypothesis has no where/require")
        if hyp["op"] is None or not hyp["lhs"] or not hyp["rhs"]:
            out.append("an aggregate hypothesis needs lhs, op and rhs")
        if len(hyp["lhs"]) > MAX_TERMS or len(hyp["rhs"]) > MAX_TERMS:
            out.append(f"more than {MAX_TERMS} terms a side")
        for a in hyp["lhs"] + hyp["rhs"]:
            if a["kind"] == "int":
                if type(a["int_value"]) is not int:
                    out.append("an int aggregate term without a value")
                continue
            if a["table"] not in domain:
                out.append(f"aggregate over unknown table {a['table']!r}")
                continue
            if a["kind"] == "sum":
                col = domain[a["table"]].get(a["col"] or "")
                if not col or col[0] != "int":
                    out.append(f"sum of {a['table']}.{a['col']}: not an int column")
            if a["filter_col"] is not None:
                col = domain[a["table"]].get(a["filter_col"])
                if not col or col[0] != "str" or not isinstance(a["filter_str"], str):
                    out.append(f"filter {a['table']}.{a['filter_col']}: needs a str column and a str value")
        if json.dumps(hyp["lhs"], sort_keys=True) == json.dumps(hyp["rhs"], sort_keys=True):
            out.append("both sides are the same")
    return out


# ------------------------------------------------------------------------------------------------ evaluation

def _value(t: dict[str, Any], row: dict[str, Any], state: dict[str, list[dict[str, Any]]]) -> Any:
    k = t["kind"]
    if k == "col":
        return row[t["col"]]
    if k in ("int", "str", "bool"):
        return t[f"{k}_value"]
    if k == "null":
        return None
    children = [c for c in state[t["table"]] if c[t["fk"]] == row["id"]]
    if k == "child_count":
        return len(children)
    vals = [c[t["col"]] for c in children]
    return None if any(v is None for v in vals) else sum(vals)


def _compare(a: Any, op: str, b: Any) -> bool:
    if op == "eq":
        return a == b and type(a) is type(b) if a is not None and b is not None else a is b
    if op == "ne":
        return not _compare(a, "eq", b)
    if a is None or b is None:
        return False
    return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]


def _agg(a: dict[str, Any], initial: dict[str, list[dict[str, Any]]], final: dict[str, list[dict[str, Any]]]) -> int:
    if a["kind"] == "int":
        return a["int_value"]
    rows = (initial if a["state"] == "initial" else final)[a["table"]]
    if a["filter_col"] is not None:
        rows = [r for r in rows if r[a["filter_col"]] == a["filter_str"]]
    if a["kind"] == "count":
        return len(rows)
    return sum(r[a["col"]] or 0 for r in rows)


def evaluate(h: dict[str, Any], initial: dict[str, list[dict[str, Any]]],
             final: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """``{"holds": bool, "matched": rows the hypothesis constrained, "violations": [...]}``. Pure. Assumes
    ``problems(h) == []``."""
    if h["kind"] == "aggregate":
        left = sum(a["sign"] * _agg(a, initial, final) for a in h["lhs"])
        right = sum(a["sign"] * _agg(a, initial, final) for a in h["rhs"])
        ok = _compare(left, h["op"], right)
        return {"holds": ok, "matched": 1, "violations": [] if ok else [f"{left} {h['op']} {right} is false"]}
    matched, bad = 0, []
    for row in final[h["table"]]:
        if not all(_compare(_value(c["left"], row, final), c["op"], _value(c["right"], row, final))
                   for c in h["where"]):
            continue
        matched += 1
        for c in h["require"]:
            if not _compare(_value(c["left"], row, final), c["op"], _value(c["right"], row, final)):
                bad.append(f"{h['table']} id={row.get('id')}: {_render(c)}")
                break
    return {"holds": not bad, "matched": matched, "violations": bad[:5]}


def _render(c: dict[str, Any]) -> str:
    def t(x: dict[str, Any]) -> str:
        if x["kind"] == "col":
            return x["col"]
        if x["kind"] in ("int", "str", "bool"):
            return repr(x[f"{x['kind']}_value"])
        if x["kind"] == "null":
            return "NULL"
        return f"{x['kind']}({x['table']}.{x.get('col') or '*'} by {x['fk']})"
    return f"{t(c['left'])} {c['op']} {t(c['right'])}"


# ------------------------------------------------------------------------------------------------ builders

def term(kind: str, **kw: Any) -> dict[str, Any]:
    base = {"kind": kind, "col": None, "int_value": None, "str_value": None, "bool_value": None, "table": None,
            "fk": None}
    base.update(kw)
    return base


def col(name: str) -> dict[str, Any]:
    return term("col", col=name)


def const(v: Any) -> dict[str, Any]:
    if v is None:
        return term("null")
    if type(v) is bool:
        return term("bool", bool_value=v)
    if type(v) is int:
        return term("int", int_value=v)
    return term("str", str_value=v)


def cond(left: dict[str, Any], op: str, right: dict[str, Any]) -> dict[str, Any]:
    return {"left": left, "op": op, "right": right}


def agg(kind: str, table: str | None = None, col_: str | None = None, *, state: str = "final", sign: int = 1,
        filter_col: str | None = None, filter_str: str | None = None, value: int | None = None) -> dict[str, Any]:
    return {"sign": sign, "kind": kind, "table": table, "col": col_, "state": state, "filter_col": filter_col,
            "filter_str": filter_str, "int_value": value}


def row_hyp(hid: str, statement: str, domain: str, table: str, where: list[dict[str, Any]],
            require: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": hid, "statement": statement, "domain": domain, "kind": "row", "table": table, "where": where,
            "require": require, "lhs": [], "op": None, "rhs": []}


def agg_hyp(hid: str, statement: str, domain: str, lhs: list[dict[str, Any]], op: str,
            rhs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": hid, "statement": statement, "domain": domain, "kind": "aggregate", "table": None, "where": [],
            "require": [], "lhs": lhs, "op": op, "rhs": rhs}


def grammar_description(domain: str) -> dict[str, Any]:
    """What a proposer is told about the language and the domain (no examples drawn from any oracle)."""
    return {"language": LANGUAGE,
            "domain": {t: {c: {"type": ty, "nullable": n} for c, (ty, n) in cols.items()}
                       for t, cols in DOMAINS[domain].items()},
            "kinds": {"row": "for every row of `table` satisfying all `where` conditions, every `require` condition "
                             "holds; lhs and rhs are empty and op is null",
                      "aggregate": "sum(sign * aggregate) over lhs compared by op with the same over rhs; where and "
                                   "require are empty and table is null"},
            "terms": {"col": "a column of the row (col)", "int|str|bool": "a constant (int_value|str_value|"
                      "bool_value)", "null": "NULL", "child_sum": "sum of `col` over rows of `table` whose `fk` "
                      "equals this row's id", "child_count": "count of rows of `table` whose `fk` equals this row's "
                      "id"},
            "aggregate_terms": {"sum": "sum of an int column of `table` in the initial or final state, optionally "
                                       "over rows whose str column filter_col equals filter_str",
                                "count": "count of rows, same filter", "int": "a constant int_value"},
            "null_semantics": "eq/ne compare NULL like a value; an ordering comparison with NULL is false",
            "bounds": {"conditions": MAX_CONDITIONS, "terms_per_side": MAX_TERMS},
            "unused_fields": "null"}
