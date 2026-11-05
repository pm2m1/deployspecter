"""EXPERIMENTAL: corroborate a run's driver-level trace against PostgreSQL's own csvlog.

The trace is the primary record (it knows the handler, call site and scheduler decision). The server log is an
independent witness of what actually reached the server. Between two markers the harness writes on its admin session,
every row must be explained:

* each actor backend's logged statements are exactly the trace's granted operations, in order: execute statements
  with their bound values (integers and NULL compared exactly, strings by digest), and the transaction boundaries
  psycopg sends itself (BEGIN, COMMIT, ROLLBACK, SAVEPOINT, RELEASE), including the implicit BEGIN of a
  non-autocommit connection; an operation that failed must have the server's ERROR row;
* the order across backends agrees with the scheduler's grant order;
* each actor's session connects and disconnects as the expected user, database and application name;
* the admin session sends only its allowlisted polls;
* anything else, from any session, is unexplained, and refuses the run (a raw ``pgconn`` call, for example, is caught
  here, since the driver hook can't see it).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from deployspecter.research.track_a.gate2a_audit import parse_log, statement

from .stepping import normal_sql

PID, SESSION, LINE, SEVERITY, SQLSTATE, MESSAGE, DETAIL, APP, BACKEND, USER, DB = 3, 5, 6, 11, 12, 13, 14, 22, 23, 1, 2
PARAM = re.compile(r"\$(\d+) = (NULL|'(?:[^']|'')*')")
ADMIN_ALLOWED = (re.compile(r"^SELECT pg_blocking_pids\(\$1\)$"), re.compile(r"^SELECT pg_cancel_backend\(\$1\)$"),
                 re.compile(r"^SELECT count\(\*\) FROM pg_stat_activity WHERE pid = ANY\(\$1\)$"),
                 re.compile(r"^SELECT current_setting\('cluster_name'\), current_database\(\)$"))  # async monitor
BACKGROUND = {"checkpointer": re.compile(r"^checkpoint (starting|complete)")}


PLACEHOLDER = re.compile(r"%%|%\((\w+)\)s|%s")


def server_text(sql: str, params: Any = ()) -> str:
    return bind(sql, list(params) if isinstance(params, tuple) else params)[0]


def bind(sql: str, params: Any) -> tuple[str, list[Any]]:
    """The text psycopg sends with server-side binding, and the values in ``$n`` order. Positional ``%s`` are
    numbered in turn; a named ``%(x)s`` gets a number on its first occurrence and reuses it after, as psycopg does;
    ``%%`` is a literal ``%``. With no parameters psycopg sends the text unchanged."""
    if params is None:
        return sql, []
    names: dict[str, int] = {}
    values: list[Any] = []
    positional = iter(params) if isinstance(params, list) else iter(())

    def num(m: re.Match[str]) -> str:
        if m.group(0) == "%%":
            return "%"
        if m.group(1) is None:
            values.append(next(positional, None))
            return f"${len(values)}"
        if m.group(1) not in names:
            values.append(params.get(m.group(1)) if isinstance(params, dict) else None)
            names[m.group(1)] = len(values)
        return f"${names[m.group(1)]}"
    return PLACEHOLDER.sub(num, sql), values


@dataclass
class Corroboration:
    ok: bool = False
    matched: int = 0
    params_compared: int = 0
    params_unchecked: int = 0
    sessions: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "matched": self.matched, "params_compared": self.params_compared,
                "params_unchecked": self.params_unchecked, "sessions": self.sessions, "problems": self.problems}


def _expected(op: Any) -> list[tuple[str, list[Any] | None]]:
    """What the server logs for one granted trace operation: (statement text or pattern, values or None)."""
    out: list[tuple[str, list[Any] | None]] = []
    if op.implicit_begin:
        out.append(("BEGIN", None))
    if op.kind == "execute":
        out.append(bind(op.sql, op.params))
    elif op.kind == "executemany":
        out += [bind(op.sql, p) for p in op.params]
    elif op.kind == "transaction_enter":
        out.append(("BEGIN" if op.sql == "BEGIN" else "re:^SAVEPOINT ", None))
    elif op.kind == "transaction_exit":
        out += {"COMMIT": [("COMMIT", None)], "ROLLBACK": [("ROLLBACK", None)],
                "RELEASE SAVEPOINT": [("re:^RELEASE ", None)],
                "ROLLBACK TO SAVEPOINT + RELEASE": [("re:^ROLLBACK TO ", None), ("re:^RELEASE ", None)]}[op.sql]
    elif op.kind in ("commit", "rollback") and op.txn_before not in (None, "IDLE"):
        out.append((op.sql, None))  # psycopg sends nothing when no transaction is open
    return out


def _value_matches(logged: str, want: Any) -> bool | None:
    """True or False when comparable; None when the trace's sanitized form can't be compared with the log."""
    if want is None:
        return logged == "NULL"
    if isinstance(want, bool):
        return logged == ("'t'" if want else "'f'")
    if isinstance(want, int):
        return logged == f"'{want}'"
    if isinstance(want, dict) and "str" in want:
        text = logged[1:-1].replace("''", "'") if logged.startswith("'") else None
        return text is not None and len(text) == want["str"] and hashlib.sha256(
            text.encode()).hexdigest()[:12] == want["sha256"]
    return None


def corroborate(log: bytes, *, start: str, end: str, trace: Any, admin_pid: int | set[int], user: str,
                database: str, application: str = "ds_mvp_actor") -> Corroboration:
    """``admin_pid`` is the harness's admin session, or a set of them (the async engine polls on its own monitor)."""
    out = Corroboration()
    admin_pids = {str(p) for p in (admin_pid if isinstance(admin_pid, set) else {admin_pid})}
    rows = parse_log(log)
    try:
        a = next(i for i, r in enumerate(rows) if r.message == f"statement: SELECT '{start}'")
        b = next(i for i, r in enumerate(rows) if r.message == f"statement: SELECT '{end}'")
    except StopIteration:
        out.problems.append("a window marker isn't in the log")
        return out
    window = rows[a + 1:b]
    actors = {str(v["backend_pid"]): name for name, v in trace.actors.items()}
    granted = sorted((op for op in trace.records if op.grant is not None), key=lambda op: op.grant)
    expected: dict[str, list[tuple[str, list[Any] | None, int, Any]]] = {pid: [] for pid in actors}
    for op in granted:
        pid = str(trace.actors[op.actor]["backend_pid"])
        expected[pid] += [(text, params, op.grant, op) for text, params in _expected(op)]
    for op in trace.records:
        # Operations that ran without a grant: a transaction's exit while the run was being halted, and the
        # harness's recorded cleanup after a failed handler. Last on their session, outside the grant order.
        if op.grant is None and op.outcome == "ok":
            pid = str(trace.actors[op.actor]["backend_pid"])
            items = [("ROLLBACK", None)] if op.kind == "cleanup_rollback" else _expected(op)
            expected[pid] += [(text, params, None, op) for text, params in items]
    cursor = dict.fromkeys(actors, 0)
    order: list[int] = []
    for r in window:
        pid = r[PID]
        if pid in admin_pids:
            parsed = statement(r)
            if parsed and not any(p.match(parsed[1]) for p in ADMIN_ALLOWED):
                out.problems.append(f"admin sent an unallowlisted statement: {parsed[1][:60]}")
            continue
        if r[BACKEND] in BACKGROUND and BACKGROUND[r[BACKEND]].match(r.message):
            continue
        if pid not in actors:
            out.problems.append(f"unexplained session pid {pid}: {r.message[:60]}")
            continue
        out.sessions.setdefault(pid, actors[pid])
        if r.message.startswith("connection authorized:"):
            if r.message != f"connection authorized: user={user} database={database} application_name={application}":
                out.problems.append(f"{actors[pid]} connected with another identity")
            continue
        if r.message.startswith(("connection received:", "connection authenticated:", "disconnection:")):
            continue
        if r[SEVERITY] == "ERROR":
            last = expected[pid][cursor[pid] - 1][3] if cursor[pid] else None
            if last is None or last.outcome != "error" or (last.sqlstate and r[SQLSTATE] != last.sqlstate):
                out.problems.append(f"{actors[pid]}: an ERROR the trace doesn't record: {r.message[:60]}")
            continue
        parsed = statement(r)
        if parsed is None:
            out.problems.append(f"{actors[pid]}: an unexplained row: {r.message[:60]}")
            continue
        _verb, text, _p = parsed
        if cursor[pid] >= len(expected[pid]):
            out.problems.append(f"{actors[pid]}: a statement the trace doesn't record: {text[:60]}")
            continue
        want, values, grant, _op = expected[pid][cursor[pid]]
        text = normal_sql(text)  # the trace keeps statement text whitespace-normalized; values are compared apart
        hit = re.match(want[3:], text) if want.startswith("re:") else text == want
        if not hit:
            out.problems.append(f"{actors[pid]}: logged {text[:50]!r}, the trace expects {want[:50]!r}")
            cursor[pid] += 1
            continue
        cursor[pid] += 1
        out.matched += 1
        if grant is not None:
            order.append(grant)
        if values:
            logged = {int(n): v for n, v in PARAM.findall(r[DETAIL] or "")}
            for i, value in enumerate(values, 1):
                verdict = _value_matches(logged.get(i, "<absent>"), value)
                if verdict is None:
                    out.params_unchecked += 1
                elif verdict:
                    out.params_compared += 1
                else:
                    out.problems.append(f"{actors[pid]}: ${i} of {text[:40]!r} isn't the value the trace records")
    for pid, items in expected.items():
        if cursor[pid] < len(items):
            out.problems.append(f"{actors[pid]}: {len(items) - cursor[pid]} traced operation(s) never reached the log")
        names = [r.message.split(":")[0] for r in window if r[PID] == pid]
        if names.count("connection authorized") != 1 or names.count("disconnection") != 1:
            out.problems.append(f"{actors[pid]}: its session's connection and disconnection aren't both in the window")
    if order != sorted(order):
        out.problems.append("the server's order of statements disagrees with the scheduler's grant order")
    out.ok = not out.problems
    return out
