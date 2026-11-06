"""EXPERIMENTAL ds-p9x/1 catalog support: export a live schema as Phase 9B ``setup_sql``, fingerprint catalogs, and
screen triggers.

**Export (``export``), scope v1.** Read from the live catalog with ``search_path`` set to the schema, so PostgreSQL
prints names unqualified. Supported:
* enum and composite types;
* sequences (parameters and owner);
* ordinary tables with typed columns, NOT NULL and defaults;
* constraints (primary key, unique, check, exclusion, then foreign keys last);
* indexes not backing a constraint;
* functions;
* triggers.

Identity columns are exported with their own sequence's parameters (the sequence isn't created separately).

**Refused**, rather than approximated: views, materialized views, partitioned or inherited tables, foreign tables,
domains, generated columns, rules, row-level policies, and function bodies that name the schema itself.

**Fingerprint (``fingerprint``).** A canonical JSON of the same catalog facts with the schema name replaced by
``<S>``, and its sha256.
* Equality between the target and the sandbox (after ``setup_sql``) is what makes an export "verified".
* One declared exception, ``expression_only_differences``: PostgreSQL's deparse isn't always a fixpoint. A partial
  index predicate ``x = ANY ((ARRAY['a'::varchar])::text[])`` comes back as ``x = ANY (ARRAY[('a'::varchar)::text])``
  once recreated from its own ``pg_get_indexdef``. Such a difference is accepted only when it lies in the
  expression text of an index, a CHECK constraint or a column default, and every other attribute of the same
  object is identical. The pipeline also requires the sandbox's own re-export to rebuild to exactly the same
  catalog (a fixpoint). It rests on PostgreSQL's documented purpose for ``pg_get_indexdef`` and the other
  ``pg_get_*def`` functions, which is to reconstruct an equivalent object. Triggers, functions, types, tables and
  everything else must match exactly.
* Equality between planning time and write time (``execute``, under table locks) is what refuses a catalog change in
  between.

**Trigger screen (``screen_triggers``).** Phase 9B classifies each trigger's effect itself (``triggers.py``). ds-p9x/1
adds refusals for behaviour whose effect can't be proven from a static reading:
* dynamic SQL (``EXECUTE``);
* non-deterministic or session-dependent functions (``random``, the clock functions, ``gen_random_uuid``,
  ``current_setting``, ``txid_current``, …);
* languages other than ``plpgsql`` and ``sql``;
* statement-level, constraint (deferrable) and TRUNCATE triggers;
* a trigger whose function inserts into or updates a table with a non-deterministic column default: a clock,
  random or sequence default, or an identity column. The rows it writes would differ between the sandbox and the
  target. This is a static over-approximation from the function's text, so it fails closed.

``pg_notify`` is recorded as an external side effect, not table state, and is not refused. Passing the screen is
necessary, not sufficient: the constructed state is still verified on the database.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from psycopg import sql

VERSION = "catalog/1"


class CatalogRefused(RuntimeError):
    """The schema has something catalog/1 can't export or prove."""


Q = {
    "relations": "SELECT c.relname::text, c.relkind::text FROM pg_class c "
                 "JOIN pg_namespace n ON n.oid = c.relnamespace "
                 "WHERE n.nspname = %s AND c.relkind NOT IN ('i', 'I', 't', 'c') ORDER BY 1",
    "columns": "SELECT c.relname::text, a.attnum, a.attname::text, format_type(a.atttypid, a.atttypmod), a.attnotnull, "
               "pg_get_expr(d.adbin, d.adrelid), a.attidentity::text, a.attgenerated::text FROM pg_attribute a "
               "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
               "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
               "WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped ORDER BY 1, 2",
    "constraints": "SELECT c.relname::text, k.conname::text, k.contype::text, pg_get_constraintdef(k.oid), "
                   "k.condeferrable FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
                   "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s ORDER BY 1, 2",
    "indexes": "SELECT t.relname::text, i.relname::text, pg_get_indexdef(i.oid) FROM pg_index x "
               "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
               "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = %s AND NOT EXISTS "
               "(SELECT 1 FROM pg_constraint k WHERE k.conindid = x.indexrelid) ORDER BY 1, 2",
    "triggers": "SELECT c.relname::text, t.tgname::text, pg_get_triggerdef(t.oid), t.tgtype::int, "
                "t.tgconstraint <> 0, t.tgdeferrable, p.proname::text, t.tgenabled::text FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_proc p ON p.oid = t.tgfoid WHERE n.nspname = %s AND NOT t.tgisinternal ORDER BY 1, 2",
    "functions": "SELECT p.proname::text, pg_get_function_identity_arguments(p.oid), l.lanname::text, "
                 "p.provolatile::text, p.prosrc, pg_get_functiondef(p.oid), p.prokind::text FROM pg_proc p "
                 "JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang "
                 "WHERE n.nspname = %s ORDER BY 1, 2",
    "enums": "SELECT t.typname::text, array_agg(e.enumlabel::text ORDER BY e.enumsortorder) FROM pg_type t "
             "JOIN pg_namespace n ON n.oid = t.typnamespace JOIN pg_enum e ON e.enumtypid = t.oid "
             "WHERE n.nspname = %s GROUP BY 1 ORDER BY 1",
    "composites": "SELECT t.typname::text, array_agg(a.attname::text || ' ' || format_type(a.atttypid, a.atttypmod) "
                  "ORDER BY a.attnum) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                  "JOIN pg_class c ON c.oid = t.typrelid AND c.relkind = 'c' JOIN pg_attribute a ON a.attrelid = c.oid "
                  "AND a.attnum > 0 AND NOT a.attisdropped WHERE n.nspname = %s GROUP BY 1 ORDER BY 1",
    "domains": "SELECT t.typname::text FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
               "WHERE n.nspname = %s AND t.typtype = 'd'",
    "sequences": "SELECT sequencename::text, data_type::text, start_value, min_value, max_value, increment_by, cycle, "
                 "cache_size FROM pg_sequences WHERE schemaname = %s ORDER BY 1",
    "identity_sequences": "SELECT s.relname::text, t.relname::text, a.attname::text FROM pg_depend d "
                          "JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S' "
                          "JOIN pg_class t ON t.oid = d.refobjid JOIN pg_attribute a ON a.attrelid = t.oid "
                          "AND a.attnum = d.refobjsubid JOIN pg_namespace n ON n.oid = s.relnamespace "
                          "WHERE n.nspname = %s AND d.deptype = 'i' ORDER BY 1",
    "sequence_owners": "SELECT s.relname::text, t.relname::text, a.attname::text FROM pg_depend d "
                       "JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S' JOIN pg_class t ON t.oid = d.refobjid "
                       "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
                       "JOIN pg_namespace n ON n.oid = s.relnamespace WHERE n.nspname = %s AND d.deptype = 'a' "
                       "ORDER BY 1",
    "inherits": "SELECT c.relname::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s",
    "rules": "SELECT c.relname::text, r.rulename::text FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class "
             "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND r.rulename <> '_RETURN'",
    "policies": "SELECT c.relname::text, p.polname::text FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s",
}


def read(conn: Any, schema: str) -> dict[str, list[list[Any]]]:
    """The raw catalog facts, read with ``search_path`` = ``schema`` (so PostgreSQL prints names unqualified), in one
    snapshot. Leaves the connection's search_path as it was."""
    old = conn.execute("SELECT current_setting('search_path')").fetchone()[0]
    try:
        conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(schema)))
        return {k: [list(r) for r in conn.execute(q, (schema,)).fetchall()] for k, q in Q.items()}
    finally:
        conn.execute("SELECT set_config('search_path', %s, false)", (old,))


def _norm(text: Any, schema: str) -> Any:
    if not isinstance(text, str):
        return text
    return re.sub(rf'(?<![\w"]){re.escape(schema)}\.|"{re.escape(schema)}"\.', "<S>.", text)


def facts(conn: Any, schema: str) -> dict[str, Any]:
    """Catalog facts with the schema name normalized, for fingerprints."""
    raw = read(conn, schema)
    out = {}
    for k, rows in raw.items():
        if k == "functions":  # the full definition repeats prosrc; compare the source by hash
            rows = [[r[0], r[1], r[2], r[3], hashlib.sha256(_norm(r[4], schema).encode()).hexdigest(), r[6]]
                    for r in rows]
        out[k] = [[_norm(x, schema) if not isinstance(x, list) else [_norm(y, schema) for y in x] for x in r]
                  for r in rows]
    return out


def fingerprint(conn: Any, schema: str) -> dict[str, Any]:
    f = facts(conn, schema)
    return {"version": VERSION, "sha256": hashlib.sha256(json.dumps(f, sort_keys=True, default=str).encode())
            .hexdigest(), "facts": f}


# Where PostgreSQL's deparse of an expression may legitimately differ after a round trip, and which field holds it:
# index definitions (partial-index predicates and index expressions), CHECK constraint definitions, column defaults.
EXPRESSION_FIELDS = {"indexes": 2, "constraints": 3, "columns": 5}


def expression_only_differences(target: dict[str, Any], sandbox: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The differences between two catalogs' facts if **all** of them are in an expression text (see
    ``EXPRESSION_FIELDS``; constraints: CHECK only) of objects that are otherwise identical, row by row. Otherwise
    None, which means refuse. An empty list means the catalogs are equal."""
    out = []
    for k in sorted(set(target) | set(sandbox)):
        a, b = target.get(k, []), sandbox.get(k, [])
        if a == b:
            continue
        if k not in EXPRESSION_FIELDS or len(a) != len(b):
            return None
        f = EXPRESSION_FIELDS[k]
        for x, y in zip(a, b, strict=True):
            if x == y:
                continue
            if [v for i, v in enumerate(x) if i != f] != [v for i, v in enumerate(y) if i != f]:
                return None  # a different object, or another attribute differs
            if k == "constraints" and x[2] != "c":
                return None
            out.append({"kind": k, "object": x[:2], "target": x[f], "sandbox": y[f]})
    return out


def export(conn: Any, schema: str) -> dict[str, Any]:
    """``setup_sql`` (unqualified, for Phase 9B's ``ds_run`` sandbox) of ``schema``, or CatalogRefused."""
    c = read(conn, schema)
    refused = []
    kinds = {r[1] for r in c["relations"]} - {"r", "S"}
    if kinds:
        refused.append(f"relation kinds {sorted(kinds)} (views, partitioned or foreign tables)")
    for what in ("inherits", "rules", "policies", "domains"):
        if c[what]:
            refused.append(f"{what}: {c[what]}")
    for r in c["columns"]:
        if r[7]:
            refused.append(f"{r[0]}.{r[2]} is a generated column")
    for f in c["functions"]:
        if f[6] != "f":
            refused.append(f"{f[0]} is a {f[6]} (only plain functions)")
        if re.search(rf"\b{re.escape(schema)}\b", f[4]):
            refused.append(f"function {f[0]}'s body names the schema {schema}")
    if refused:
        raise CatalogRefused("; ".join(refused))
    out = []
    for name, labels in c["enums"]:
        out.append(sql.SQL("CREATE TYPE {} AS ENUM ({})").format(
            sql.Identifier(name), sql.SQL(", ").join(sql.Literal(x) for x in labels)).as_string(conn))
    for name, attrs in c["composites"]:
        out.append(f"CREATE TYPE {sql.Identifier(name).as_string(conn)} AS ({', '.join(attrs)})")
    identity = {s[0]: (s[1], s[2]) for s in c["identity_sequences"]}
    seq_params = {s[0]: s for s in c["sequences"]}
    for s in c["sequences"]:
        if s[0] in identity:
            continue  # created by its identity column below
        out.append(f"CREATE SEQUENCE {sql.Identifier(s[0]).as_string(conn)} AS {s[1]} START {s[2]} MINVALUE {s[3]} "
                   f"MAXVALUE {s[4]} INCREMENT {s[5]} {'CYCLE' if s[6] else 'NO CYCLE'} CACHE {s[7]}")
    for f in c["functions"]:  # before tables: defaults, checks and triggers may call them
        head, _, rest = f[5].partition("\n")
        head = re.sub(rf'FUNCTION (?:"{re.escape(schema)}"|{re.escape(schema)})\.', "FUNCTION ", head, count=1)
        out.append(head + "\n" + rest)
    tables: dict[str, list[str]] = {}
    for r in c["columns"]:
        col = f"{sql.Identifier(r[2]).as_string(conn)} {r[3]}"
        if r[6]:
            seq = next((s for s, (t, a) in identity.items() if (t, a) == (r[0], r[2])), None)
            p = seq_params.get(seq)
            if p is None:
                raise CatalogRefused(f"{r[0]}.{r[2]}: identity column without a readable sequence")
            col += (f" GENERATED {'ALWAYS' if r[6] == 'a' else 'BY DEFAULT'} AS IDENTITY (SEQUENCE NAME "
                    f"{sql.Identifier(seq).as_string(conn)} START {p[2]} MINVALUE {p[3]} MAXVALUE {p[4]} "
                    f"INCREMENT {p[5]} {'CYCLE' if p[6] else 'NO CYCLE'} CACHE {p[7]})")
        elif r[5] is not None:
            col += f" DEFAULT {r[5]}"
        if r[4]:
            col += " NOT NULL"
        tables.setdefault(r[0], []).append(col)
    for t, cols in tables.items():
        out.append(f"CREATE TABLE {sql.Identifier(t).as_string(conn)} ({', '.join(cols)})")
    for s, t, col in c["sequence_owners"]:
        if s in identity:
            continue
        out.append(f"ALTER SEQUENCE {sql.Identifier(s).as_string(conn)} OWNED BY "
                   f"{sql.Identifier(t).as_string(conn)}.{sql.Identifier(col).as_string(conn)}")
    for kind_first in (("p", "u", "c", "x"), ("f",)):
        for t, name, kind, condef, _ in c["constraints"]:
            if kind in kind_first:
                out.append(f"ALTER TABLE {sql.Identifier(t).as_string(conn)} ADD CONSTRAINT "
                           f"{sql.Identifier(name).as_string(conn)} {condef}")
    for _, _, definition in c["indexes"]:
        out.append(_norm(definition, schema).replace("<S>.", ""))
    for _, _, definition, *_ in c["triggers"]:
        out.append(_norm(definition, schema).replace("<S>.", ""))
    setup = ";\n".join(out) + (";\n" if out else "")
    return {"version": VERSION, "setup_sql": setup, "setup_sql_sha256": hashlib.sha256(setup.encode()).hexdigest(),
            "objects": {k: len(v) for k, v in c.items()}}


_DYNAMIC = re.compile(r"\bEXECUTE\b", re.I)
_VOLATILE = re.compile(r"\b(random|setseed|clock_timestamp|statement_timestamp|timeofday|now|transaction_timestamp|"
                       r"gen_random_uuid|uuid_generate_v\w*|current_setting|set_config|txid_current\w*|"
                       r"pg_current_xact_id|inet_client_addr|pg_backend_pid|pg_sleep\w*|nextval|currval|lastval|"
                       r"dblink\w*)\s*\(|\b(current_timestamp|localtimestamp|current_time|localtime|current_user|"
                       r"session_user)\b", re.I)


def screen_triggers(conn: Any, schema: str) -> dict[str, Any]:
    """Every user trigger in ``schema``: its table, timing, events and level, and whether ds-p9x/1 refuses it.
    Returns ``{"controlled": {table: [...]}, "refused": [...], "notifications": [...]}``."""
    c = read(conn, schema)
    fns = {f[0]: f for f in c["functions"]}
    volatile_defaults: dict[str, list[tuple[str, str]]] = {}
    for r in c["columns"]:
        if (r[5] is not None and _VOLATILE.search(r[5])) or r[6]:  # a clock, random or sequence default, identity
            volatile_defaults.setdefault(r[0], []).append((r[2], r[5] or "identity"))
    controlled: dict[str, list[dict[str, Any]]] = {}
    refused, notes = [], []
    for table, name, _definition, tgtype, is_constraint, deferrable, fn, enabled in c["triggers"]:
        row_level = bool(tgtype & 1)
        timing = "BEFORE" if tgtype & 2 else ("INSTEAD OF" if tgtype & 64 else "AFTER")
        events = [e for bit, e in ((4, "INSERT"), (8, "DELETE"), (16, "UPDATE"), (32, "TRUNCATE")) if tgtype & bit]
        controlled.setdefault(table, []).append({"trigger": name, "timing": timing, "events": events,
                                                 "row_level": row_level, "function": fn, "enabled": enabled})
        why = []
        if not row_level:
            why.append("statement-level")
        if is_constraint or deferrable:
            why.append("constraint or deferrable trigger")
        if "TRUNCATE" in events:
            why.append("TRUNCATE trigger")
        f = fns.get(fn)
        if f is None:
            why.append(f"function {fn} is outside the schema")
        else:
            if f[2] not in ("plpgsql", "sql"):
                why.append(f"language {f[2]}")
            if _DYNAMIC.search(f[4]):
                why.append("dynamic SQL (EXECUTE)")
            if m := _VOLATILE.search(f[4]):
                why.append(f"non-deterministic or session-dependent call {m.group(0).strip()!r}")
            for written in sorted(set(re.findall(r"\b(?:INSERT\s+INTO|UPDATE)\s+(?:\w+\.)?\"?(\w+)\"?", f[4], re.I))):
                for col in volatile_defaults.get(written, []):
                    why.append(f"writes {written}, whose column {col[0]} defaults to non-deterministic {col[1]!r}")
            if re.search(r"\bpg_notify\s*\(|\bNOTIFY\b", f[4], re.I):
                notes.append(f"{name}: sends a notification (an external effect, not table state)")
        if why:
            refused.append(f"{table}.{name}: {', '.join(why)}")
    return {"controlled": controlled, "refused": refused, "notifications": notes}
