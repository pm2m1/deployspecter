"""Reads user-defined triggers from PostgreSQL's catalogs and classifies what they write. Nothing here runs a trigger.

Trigger function bodies are only tokenized. A write target is reported when a PL/pgSQL statement names it plainly and
PostgreSQL resolves that name to a table. Anything else that could have an effect (dynamic SQL, a call to another
function, another language, a function with its own search_path) makes the effect "unknown", and it stays unknown.

A target means the trigger *may* write there. Branches are not evaluated, so the real effect of a seed comes only from
executing it in the disposable schema (see ``seed_feasibility``). PostgreSQL fires same-timing triggers in name order,
but nothing here relies on that order.
"""

from __future__ import annotations

import re
from typing import Literal

import psycopg
from pydantic import BaseModel, Field

_ROW, _BEFORE, _INSERT, _DELETE, _UPDATE, _TRUNCATE, _INSTEAD = 1, 2, 4, 8, 16, 32, 64
_ENABLED = {"O": "origin", "D": "disabled", "R": "replica", "A": "always"}
_FIRES_IN_SESSIONS = {"origin", "always"}  # the sandbox runs with session_replication_role = origin

# Statements that can have effects this reader cannot follow.
_OPAQUE = {"execute", "merge", "truncate", "copy", "call", "create", "drop", "alter", "grant", "revoke", "listen",
           "notify", "vacuum", "cluster", "reindex", "lock", "import", "commit", "rollback"}
# Words that can come before "(" without being a function call.
_NOT_CALLS = {"values", "in", "exists", "any", "all", "as", "and", "or", "not", "on", "where", "using", "conflict",
              "set", "if", "elsif", "when", "then", "else", "case", "return", "from", "join", "select", "lateral",
              "over", "partition", "by", "filter", "into", "having", "distinct", "while", "returning", "update",
              "row", "array", "between", "like", "is"}
# Built-in functions with no side effects on tables.
_PURE_CALLS = {"min", "max", "count", "sum", "avg", "coalesce", "nullif", "greatest", "least", "now", "lower", "upper",
               "trim", "length", "abs", "round", "floor", "ceil", "ceiling", "concat", "substring", "array_agg",
               "numeric", "varchar", "char", "decimal", "timestamp", "timestamptz", "clock_timestamp"}
_STATEMENT_STARTS = {";", "begin", "then", "else", "loop", "declare"}

Effect = Literal["understood", "unknown"]
Operation = Literal["insert", "upsert", "update", "delete"]
EVENTS_OF_OPERATION = {"insert": {"INSERT"}, "upsert": {"INSERT", "UPDATE"}, "update": {"UPDATE"}, "delete": {"DELETE"}}


class TriggerTarget(BaseModel):
    schema_name: str
    table: str
    operations: list[Operation]
    qualified_in_source: bool  # False means it was resolved through the session's search_path


class TriggerInfo(BaseModel):
    name: str
    schema_name: str
    table: str
    relation_kind: str
    timing: Literal["BEFORE", "AFTER", "INSTEAD OF"]
    events: list[Literal["INSERT", "UPDATE", "DELETE", "TRUNCATE"]]
    level: Literal["ROW", "STATEMENT"]
    enabled_state: str
    fires: bool
    has_when_condition: bool
    deferrable: bool = False
    trigger_definition: str
    function_schema: str
    function_name: str
    function_language: str
    function_definition: str
    targets: list[TriggerTarget] = Field(default_factory=list)
    may_change_new_row: bool = False
    may_skip_row: bool = False
    effect: Effect
    unknown_reasons: list[str] = Field(default_factory=list)


class _Parsed(BaseModel):
    writes: list[tuple[str | None, str, str]] = Field(default_factory=list)  # schema, table, operation
    may_change_new_row: bool = False
    may_skip_row: bool = False
    unknown: list[str] = Field(default_factory=list)


def tokenize(body: str) -> list[tuple[str, str]]:
    """(kind, text): word (lowered), name (quoted, exact), string, punct. Comments are removed, literals are opaque."""
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c.isspace():
            i += 1
        elif body.startswith("--", i):
            end = body.find("\n", i)
            i = n if end < 0 else end
        elif body.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if body.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif body.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif c == "'":
            i += 1
            while i < n:
                if body[i] == "'" and body[i + 1:i + 2] == "'":
                    i += 2
                elif body[i] == "\\" and tokens and tokens[-1] == ("word", "e"):
                    i += 2
                elif body[i] == "'":
                    break
                else:
                    i += 1
            i += 1
            tokens.append(("string", ""))
        elif c == '"':
            i += 1
            name = []
            while i < n:
                if body[i] == '"' and body[i + 1:i + 2] == '"':
                    name.append('"')
                    i += 2
                elif body[i] == '"':
                    break
                else:
                    name.append(body[i])
                    i += 1
            i += 1
            tokens.append(("name", "".join(name)))
        elif c == "$" and (match := re.match(r"\$[A-Za-z_]*\$", body[i:])):
            tag = match.group(0)
            end = body.find(tag, i + len(tag))
            i = n if end < 0 else end + len(tag)
            tokens.append(("string", ""))
        elif c.isalpha() or c == "_":
            start = i
            while i < n and (body[i].isalnum() or body[i] in "_$"):
                i += 1
            tokens.append(("word", body[start:i].lower()))
        elif c.isdigit():
            while i < n and (body[i].isalnum() or body[i] == "."):
                i += 1
            tokens.append(("punct", "0"))
        else:
            tokens.append(("punct", c))
            i += 1
    return tokens


def _read_name(tokens: list[tuple[str, str]], i: int) -> tuple[str | None, str | None, int]:
    """A possibly schema-qualified name at position i: (schema, name, next position)."""
    if i >= len(tokens) or tokens[i][0] not in {"word", "name"}:
        return None, None, i
    first = tokens[i][1]
    if i + 2 < len(tokens) and tokens[i + 1] == ("punct", ".") and tokens[i + 2][0] in {"word", "name"}:
        return first, tokens[i + 2][1], i + 3
    return None, first, i + 1


def _statement_end(tokens: list[tuple[str, str]], i: int) -> int:
    while i < len(tokens) and tokens[i] != ("punct", ";"):
        i += 1
    return i


def parse_trigger_body(body: str) -> _Parsed:
    tokens = tokenize(body)
    out = _Parsed()
    words = [t[1] if t[0] == "word" else None for t in tokens]

    def word(i: int) -> str | None:
        return words[i] if 0 <= i < len(words) else None

    for i, (kind, text) in enumerate(tokens):
        if kind != "word":
            continue
        previous = tokens[i - 1][1] if i else ";"
        if text in _OPAQUE and text != "lock":
            out.unknown.append(f"contains {text.upper()}, whose effect is not followed")
        elif text == "lock" and previous in _STATEMENT_STARTS:
            out.unknown.append("contains LOCK, whose effect is not followed")
        elif text == "do" and word(i + 1) not in {"update", "nothing"}:
            out.unknown.append("contains DO, whose effect is not followed")
        elif text == "set" and previous in _STATEMENT_STARTS:
            out.unknown.append("changes a session setting")
        elif text == "insert":
            if word(i + 1) != "into":
                out.unknown.append("has an INSERT this reader cannot parse")
                continue
            schema, name, after = _read_name(tokens, i + 2)
            if name is None:
                out.unknown.append("has an INSERT with no readable target")
                continue
            end = _statement_end(tokens, after)
            statement = [word(k) for k in range(after, end)]
            upsert = any(
                statement[k:k + 2] == ["do", "update"] for k in range(len(statement) - 1)
            )
            out.writes.append((schema, name, "upsert" if upsert else "insert"))
        elif text == "update":
            if word(i - 1) in {"do", "for", "key"}:  # ON CONFLICT DO UPDATE, FOR UPDATE, FOR NO KEY UPDATE
                continue
            start = i + 2 if word(i + 1) == "only" else i + 1
            schema, name, after = _read_name(tokens, start)
            alias_gap = 2 if word(after) == "as" else (1 if word(after) != "set" else 0)
            if name is None or word(after + alias_gap) != "set":
                out.unknown.append("has an UPDATE this reader cannot parse")
                continue
            out.writes.append((schema, name, "update"))
        elif text == "delete":
            if word(i + 1) != "from":
                out.unknown.append("has a DELETE this reader cannot parse")
                continue
            start = i + 3 if word(i + 2) == "only" else i + 2
            schema, name, _ = _read_name(tokens, start)
            if name is None:
                out.unknown.append("has a DELETE with no readable target")
                continue
            out.writes.append((schema, name, "delete"))
        elif text == "new" and tokens[i + 1:i + 2] == [("punct", ".")]:
            assign = tokens[i + 3:i + 4]
            if assign == [("punct", ":")] or (assign == [("punct", "=")] and previous in _STATEMENT_STARTS):
                out.may_change_new_row = True
        elif text == "new" and previous in _STATEMENT_STARTS:
            out.may_change_new_row = True  # NEW := ...
        elif text == "return" and word(i + 1) == "null":
            out.may_skip_row = True
        elif tokens[i + 1:i + 2] == [("punct", "(")] and text not in _NOT_CALLS and text not in _PURE_CALLS:
            if word(i - 1) == "into" or (previous == "." and word(i - 3) == "into"):
                continue  # the column list of INSERT INTO [schema.]table (...)
            out.unknown.append(f"calls {text}(), whose effect is not followed")
    out.unknown = list(dict.fromkeys(out.unknown))
    return out


def _resolve(conn: psycopg.Connection, schema: str | None, name: str) -> tuple[str, str, str] | None:
    quote = lambda part: '"' + part.replace('"', '""') + '"'  # noqa: E731
    text = f"{quote(schema)}.{quote(name)}" if schema else quote(name)
    row = conn.execute(
        """SELECT n.nspname::text, c.relname::text, c.relkind::text FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = to_regclass(%s)""",
        (text,),
    ).fetchone()
    return tuple(row) if row else None


def read_triggers(conn: psycopg.Connection, schemas: list[str]) -> list[TriggerInfo]:
    """Every user-defined trigger on tables in ``schemas``. Unqualified targets resolve through conn's search_path."""
    rows = conn.execute(
        """SELECT t.tgname::text, n.nspname::text, c.relname::text, c.relkind::text, t.tgtype::int, t.tgenabled::text,
                  t.tgqual IS NOT NULL, pg_get_triggerdef(t.oid), pn.nspname::text, p.proname::text, l.lanname::text,
                  p.prosrc, pg_get_functiondef(p.oid), p.proconfig, t.tgdeferrable
           FROM pg_trigger t
           JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_proc p ON p.oid = t.tgfoid
           JOIN pg_namespace pn ON pn.oid = p.pronamespace
           JOIN pg_language l ON l.oid = p.prolang
           WHERE NOT t.tgisinternal AND n.nspname = ANY(%s)
           ORDER BY n.nspname, c.relname, t.tgname""",
        (schemas,),
    ).fetchall()
    found = []
    for name, schema, table, kind, tgtype, enabled, has_when, definition, f_schema, f_name, language, source, \
            f_definition, f_config, deferrable in rows:
        timing = "INSTEAD OF" if tgtype & _INSTEAD else ("BEFORE" if tgtype & _BEFORE else "AFTER")
        events = [e for bit, e in ((_INSERT, "INSERT"), (_UPDATE, "UPDATE"), (_DELETE, "DELETE"),
                                   (_TRUNCATE, "TRUNCATE")) if tgtype & bit]
        state = _ENABLED.get(enabled, "unknown")
        unknown: list[str] = []
        targets: dict[tuple[str, str], TriggerTarget] = {}
        parsed = _Parsed()
        if language != "plpgsql":
            unknown.append(f"its function is written in {language}, which this reader does not follow")
        else:
            parsed = parse_trigger_body(source)
            unknown.extend(parsed.unknown)
            has_own_path = any(str(s).startswith("search_path=") for s in (f_config or []))
            for w_schema, w_table, operation in parsed.writes:
                if has_own_path and not w_schema:
                    unknown.append(f"{w_table} depends on the function's own search_path")
                    continue
                resolved = _resolve(conn, w_schema, w_table)
                if resolved is None or resolved[2] not in {"r", "p"}:
                    unknown.append(f"{w_table} is not a table PostgreSQL can resolve here")
                    continue
                target = targets.setdefault(
                    (resolved[0], resolved[1]),
                    TriggerTarget(schema_name=resolved[0], table=resolved[1], operations=[],
                                  qualified_in_source=bool(w_schema)),
                )
                if operation not in target.operations:
                    target.operations.append(operation)
        found.append(TriggerInfo(
            name=name, schema_name=schema, table=table, relation_kind=kind, timing=timing, events=events,
            level="ROW" if tgtype & _ROW else "STATEMENT", enabled_state=state,
            fires=state in _FIRES_IN_SESSIONS, has_when_condition=has_when, deferrable=deferrable,
            trigger_definition=definition,
            function_schema=f_schema, function_name=f_name, function_language=language,
            function_definition=f_definition, targets=sorted(targets.values(), key=lambda t: (t.schema_name, t.table)),
            may_change_new_row=parsed.may_change_new_row, may_skip_row=parsed.may_skip_row,
            effect="unknown" if unknown else "understood", unknown_reasons=list(dict.fromkeys(unknown)),
        ))
    return found


def fired_closure(triggers: list[TriggerInfo], schema: str, table: str, event: str) -> list[TriggerInfo]:
    """Triggers that may fire from one event, following understood writes through chains. Cycles terminate.

    The result is a set, not an order. Foreign-key cascades are not followed. An ``unknown`` member means more may fire.
    """
    seen: dict[tuple[str, str, str], TriggerInfo] = {}
    pending = [(schema, table, event)]
    visited = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for trigger in triggers:
            if (trigger.schema_name, trigger.table) == current[:2] and current[2] in trigger.events and trigger.fires:
                seen[(trigger.schema_name, trigger.table, trigger.name)] = trigger
                for target in trigger.targets:
                    for operation in target.operations:
                        pending.extend((target.schema_name, target.table, e) for e in EVENTS_OF_OPERATION[operation])
    return sorted(seen.values(), key=lambda t: (t.schema_name, t.table, t.name))


def describe_trigger(trigger: TriggerInfo, default_schema: str | None = None) -> str:
    """One neutral line for a model prompt: what fires and where it may write, never whether that is correct."""

    def show(schema: str, table: str) -> str:
        return table if schema == default_schema else f"{schema}.{table}"

    head = (f"{trigger.name} on {show(trigger.schema_name, trigger.table)}: {trigger.timing} "
            f"{'/'.join(trigger.events)}, {'each row' if trigger.level == 'ROW' else 'each statement'}, "
            f"{'active' if trigger.fires else f'not active ({trigger.enabled_state})'}")
    details = []
    if trigger.has_when_condition:
        details.append("runs only when its WHEN condition holds")
    if trigger.targets:
        writes = "; ".join(
            f"{show(t.schema_name, t.table)} ({', '.join(sorted(t.operations))})" for t in trigger.targets
        )
        details.append(f"may write {writes}")
    if trigger.may_change_new_row:
        details.append("may change the values of the row being written")
    if trigger.may_skip_row:
        details.append("may skip the row being written")
    if trigger.effect == "unknown":
        details.append("EFFECT UNKNOWN: " + "; ".join(trigger.unknown_reasons))
    elif not trigger.targets and not trigger.may_change_new_row and not trigger.may_skip_row:
        details.append("no table writes found")
    return f"- {head}. " + ". ".join(d[0].upper() + d[1:] for d in details) + "."
