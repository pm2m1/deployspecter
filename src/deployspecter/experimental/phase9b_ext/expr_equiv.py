"""EXPERIMENTAL ds-p9x/1.1, catalog/1.1: establish that two catalog expressions are equivalent from PostgreSQL's
**parsed** node trees, not from their deparsed text (assurance A2).

The trees come from ``pg_index.indpred`` and ``indexprs``, ``pg_constraint.conbin`` (CHECK) and
``pg_attrdef.adbin``. Both are parsed (``parse_node_tree``), canonicalized with exactly these rules, and must then be
identical:

* **R0:** ``:location`` (a source position) and ``:relabelformat`` (whether a relabel was written explicitly or
  implicitly) are dropped. Neither affects evaluation.
* **R1:** an ``ArrayCoerceExpr`` whose argument is an ``ArrayExpr``, and whose per-element coercion
  (``elemexpr``) is a ``RelabelType`` applied directly to its ``CaseTestExpr``, becomes an ``ArrayExpr`` of the same
  elements, each wrapped in that ``RelabelType``, with the coerced array type.
  - A ``RelabelType`` is a binary-compatible relabel, with no function call.
  - Applying it to every element, then building the array, gives the same array value as building the array, then
    coercing it element by element.
  - This is the difference PostgreSQL produces when ``x IN ('a', 'b')`` on a varchar column is recreated from its own
    ``pg_get_indexdef`` (Chancy 0.26.0's two partial indexes).

Everything else must already be identical after R0 and R1:
* every operator, function and type OID;
* every constant's bytes;
* every collation, and every Var's attribute number and type.

OIDs of built-in objects are the same in the sandbox and the target (one cluster). An expression that references a
schema's own type or function has different OIDs in each, and is refused. That is a supported-scope limit,
fail-closed.

The objects' non-expression attributes are compared too:
* indexes: the access method, key columns, operator classes, collations, options, uniqueness and NULLS NOT DISTINCT;
* CHECK constraints: the no-inherit and validated flags;
* defaults: none besides the expression.
"""

from __future__ import annotations

from typing import Any

RULES = {"R0": "drop :location and :relabelformat",
         "R1": "ArrayCoerceExpr(ArrayExpr, elemexpr = RelabelType(CaseTestExpr)) -> ArrayExpr of RelabelType elements"}


class NodeTreeError(ValueError):
    """Unparsable node tree: never treated as equivalent."""


def _tokens(text: str) -> list[str]:
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in "{}()[]":
            out.append(ch)
            i += 1
        else:
            j, buf = i, []
            while j < n and not text[j].isspace() and text[j] not in "{}()[]":
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            out.append("".join(buf))
            i = j
    return out


def parse_node_tree(text: str | None) -> Any:
    """``pg_node_tree`` text as nested data: a node is a dict with key ``_`` (its type); lists are lists; constants'
    bytes are ``("bytes", [ints])``; ``<>`` is None."""
    if text is None:
        return None
    toks = _tokens(text)
    pos = 0

    def value() -> Any:
        nonlocal pos
        if pos >= len(toks):
            raise NodeTreeError("unexpected end")
        t = toks[pos]
        if t == "{":
            pos += 1
            node: dict[str, Any] = {"_": toks[pos]}
            pos += 1
            while toks[pos] != "}":
                key = toks[pos]
                if not key.startswith(":"):
                    raise NodeTreeError(f"expected a field, got {key!r}")
                pos += 1
                if key == ":constvalue" and toks[pos] != "<>":
                    pos += 1  # the length atom
                    if toks[pos] != "[":
                        raise NodeTreeError("constvalue without bytes")
                    pos += 1
                    data = []
                    while toks[pos] != "]":
                        data.append(int(toks[pos]))
                        pos += 1
                    pos += 1
                    node[key[1:]] = ("bytes", data)
                else:
                    node[key[1:]] = value()
            pos += 1
            return node
        if t == "(":
            pos += 1
            items = []
            while toks[pos] != ")":
                items.append(value())
            pos += 1
            return items
        if t in ")}]":
            raise NodeTreeError(f"unexpected {t!r}")
        pos += 1
        return None if t == "<>" else t

    try:
        v = value()
    except (IndexError, ValueError) as exc:
        raise NodeTreeError(str(exc)) from exc
    if pos != len(toks):
        raise NodeTreeError("trailing tokens")
    return v


def _array_type(conn: Any, element_type: str) -> str | None:
    r = conn.execute("SELECT typarray::text FROM pg_type WHERE oid = %s::oid", (element_type,)).fetchone()
    return r[0] if r and r[0] != "0" else None


def canonical(v: Any, conn: Any, applied: set[str]) -> Any:
    """R0 and R1, bottom-up. ``applied`` collects the rules that changed something."""
    if isinstance(v, list):
        return [canonical(x, conn, applied) for x in v]
    if not isinstance(v, dict):
        return v
    node = {k: canonical(x, conn, applied) for k, x in v.items() if k not in ("location", "relabelformat")}
    if len(node) != len(v):
        applied.add("R0")
    if node["_"] == "ARRAYCOERCEEXPR":
        arg, elem = node.get("arg"), node.get("elemexpr")
        relabel_of_case = isinstance(elem, dict) and elem["_"] == "RELABELTYPE" and \
            isinstance(elem.get("arg"), dict) and elem["arg"]["_"] == "CASETESTEXPR"
        if isinstance(arg, dict) and arg["_"] == "ARRAYEXPR" and relabel_of_case and arg.get("multidims") != "true":
            target_elem = elem["resulttype"]
            arr = _array_type(conn, target_elem)
            if arr is not None and node.get("resulttype") == arr:
                applied.add("R1")
                wrapped = [{"_": "RELABELTYPE", "arg": e, "resulttype": elem["resulttype"],
                            "resulttypmod": elem["resulttypmod"], "resultcollid": elem["resultcollid"]}
                           for e in arg["elements"]]
                return {"_": "ARRAYEXPR", "array_typeid": arr, "array_collid": node.get("resultcollid"),
                        "element_typeid": target_elem, "elements": wrapped, "multidims": "false"}
    return node


Q_INDEX = ("SELECT am.amname::text, x.indkey::text, x.indclass::text, x.indcollation::text, x.indoption::text, "
           "x.indisunique, x.indnullsnotdistinct, x.indpred::text, x.indexprs::text FROM pg_index x "
           "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
           "JOIN pg_am am ON am.oid = i.relam JOIN pg_namespace n ON n.oid = t.relnamespace "
           "WHERE n.nspname = %s AND t.relname = %s AND i.relname = %s")
Q_CHECK = ("SELECT k.connoinherit, k.convalidated, k.conbin::text FROM pg_constraint k JOIN pg_class t "
           "ON t.oid = k.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = %s AND "
           "t.relname = %s AND k.conname = %s AND k.contype = 'c'")
Q_DEFAULT = ("SELECT d.adbin::text FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND "
             "a.attnum = d.adnum JOIN pg_class t ON t.oid = d.adrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
             "WHERE n.nspname = %s AND t.relname = %s AND a.attname = %s")


def _fetch(conn: Any, schema: str, kind: str, obj: list[str]) -> tuple[list[Any], list[str]] | None:
    """(non-expression attributes, expression trees) of one object, or None if it doesn't exist."""
    q = {"indexes": Q_INDEX, "constraints": Q_CHECK, "columns": Q_DEFAULT}[kind]
    r = conn.execute(q, (schema, *obj)).fetchone()
    if r is None:
        return None
    r = list(r)
    return {"indexes": (r[:7], r[7:]), "constraints": (r[:2], r[2:]), "columns": ([], r)}[kind]


def establish(target: Any, target_schema: str, sandbox: Any, sandbox_schema: str,
              differences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each expression-only difference (``catalog.expression_only_differences``), whether equivalence is
    established. The object key is ``[table, index]``, ``[table, constraint]`` or ``[table, column]`` (for
    ``columns``, the second field is the column name)."""
    out = []
    for d in differences:
        kind = d["kind"]
        obj = [d["object"][0], d.get("name", d["object"][1])]
        res: dict[str, Any] = {"kind": kind, "object": obj, "established": False, "rules": []}
        try:
            a, b = _fetch(target, target_schema, kind, obj), _fetch(sandbox, sandbox_schema, kind, obj)
            if a is None or b is None:
                res["reason"] = "object missing on one side"
            elif a[0] != b[0]:
                res["reason"] = f"non-expression attributes differ: {a[0]} vs {b[0]}"
            else:
                applied: set[str] = set()
                ta = [canonical(parse_node_tree(t), target, applied) for t in a[1]]
                tb = [canonical(parse_node_tree(t), sandbox, applied) for t in b[1]]
                res["rules"] = sorted(applied)
                if ta == tb:
                    res["established"] = True
                else:
                    res["reason"] = "the parsed expressions differ after R0 and R1"
        except NodeTreeError as exc:
            res["reason"] = f"unparsable node tree: {exc}"
        out.append(res)
    return out
