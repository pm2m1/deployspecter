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
    unchecked_kinds: dict[str, int] = field(default_factory=dict)
    params_mismatched: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "matched": self.matched, "params_compared": self.params_compared,
                "params_mismatched": self.params_mismatched, "params_unchecked": self.params_unchecked,
                "unchecked_kinds": self.unchecked_kinds, "sessions": self.sessions, "problems": self.problems}


def _expected(op: Any) -> list[tuple[str, list[Any] | None]]:
    """What the server logs for one granted trace operation: (statement text or pattern, values or None)."""
    out: list[tuple[str, list[Any] | None]] = []
    if op.implicit_begin:
        out.append(("BEGIN", None))
    native = (op.provenance or {}).get("placeholders") == "native"  # a raw cursor: the text is sent as written
    if op.kind == "execute":
        out.append((op.sql, list(op.params or [])) if native else bind(op.sql, op.params))
    elif op.kind == "executemany":
        out += [(op.sql, list(p)) if native else bind(op.sql, p) for p in op.params]
    elif op.kind == "transaction_enter":
        out.append(("BEGIN" if op.sql == "BEGIN" else "re:^SAVEPOINT ", None))
    elif op.kind == "transaction_exit":
        out += {"COMMIT": [("COMMIT", None)], "ROLLBACK": [("ROLLBACK", None)],
                "RELEASE SAVEPOINT": [("re:^RELEASE ", None)],
                "ROLLBACK TO SAVEPOINT + RELEASE": [("re:^ROLLBACK TO ", None), ("re:^RELEASE ", None)]}[op.sql]
    elif op.kind in ("commit", "rollback") and op.txn_before not in (None, "IDLE"):
        out.append((op.sql, None))  # psycopg sends nothing when no transaction is open
    return out


def _array_elements(text: str) -> list[str | None] | None:
    """A one-dimensional PostgreSQL array literal's elements (None for an unquoted NULL); None if not one."""
    if not (text.startswith("{") and text.endswith("}")) or "{" in text[1:-1]:
        return None
    body, out, i = text[1:-1], [], 0
    if not body:
        return []
    while i <= len(body):
        if i < len(body) and body[i] == '"':
            j, buf = i + 1, []
            while j < len(body) and body[j] != '"':
                if body[j] == "\\" and j + 1 < len(body):
                    j += 1
                buf.append(body[j])
                j += 1
            out.append("".join(buf))
            i = j + 2  # past the closing quote and the comma
        else:
            j = body.find(",", i)
            j = len(body) if j < 0 else j
            item = body[i:j]
            out.append(None if item == "NULL" else item)
            i = j + 1
    return out


_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?([+-]\d{2}(?::\d{2}(?::\d{2})?)?)?$")
_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?([+-]\d{2}(?::\d{2}(?::\d{2})?)?)?$")
_INTERVAL = re.compile(r"^(?:(-?\d+) years? ?)?(?:(-?\d+) mons? ?)?(?:(-?\d+) days? ?)?"
                       r"(?:([+-])?(\d+):(\d{2}):(\d{2})(?:\.(\d{1,6}))?)?$")


def _offset_s(text: str | None) -> int | None:
    if not text:
        return None
    sign = -1 if text[0] == "-" else 1
    parts = [int(x) for x in text[1:].split(":")] + [0, 0]
    return sign * (parts[0] * 3600 + parts[1] * 60 + parts[2])


def _us(frac: str | None) -> int:
    return int((frac or "").ljust(6, "0") or 0)


def datetime_matches(logged_text: str, want: dict[str, Any]) -> bool | None:
    """PostgreSQL's own output of a bound date/time parameter, compared exactly with the trace's typed value. None:
    a representation this checker doesn't reconcile (infinity, BC dates, a non-'postgres' IntervalStyle, months)."""
    import datetime as dt
    if "timestamptz_us" in want or "timestamp" in want:
        m = _TS.match(logged_text)
        if not m:
            return None
        y, mo, d, h, mi, se, frac, off = m.groups()
        wall = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(se), _us(frac))
        if "timestamp" in want:  # a wall-clock value: no offset may appear, and every field must be equal
            return off is None and wall.isoformat(sep=" ", timespec="microseconds") == want["timestamp"]
        if off is None:
            return False  # an instant was sent; the server shows a wall clock without a zone: not the same type
        instant = wall.replace(tzinfo=dt.timezone(dt.timedelta(seconds=_offset_s(off) or 0)))
        epoch = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
        return (instant - epoch) // dt.timedelta(microseconds=1) == want["timestamptz_us"]  # the same instant
    if "date" in want:
        return logged_text == want["date"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", logged_text) else None
    if "time" in want or "timetz" in want:
        m = _TIME.match(logged_text)
        if not m:
            return None
        h, mi, se, frac, off = m.groups()
        wall = dt.time(int(h), int(mi), int(se), _us(frac)).isoformat(timespec="microseconds")
        if "time" in want:
            return off is None and wall == want["time"]
        return off is not None and wall == want["timetz"] and _offset_s(off) == want["offset_s"]  # both, exactly
    if "interval_us" in want:
        m = _INTERVAL.match(logged_text)
        if not m or not logged_text:
            return None
        years, mons, days, sign, h, mi, se, frac = m.groups()
        if years or mons:
            return None  # a month is no fixed number of microseconds; a timedelta never has months
        time_us = 0
        if h is not None:
            time_us = ((int(h) * 60 + int(mi)) * 60 + int(se)) * 1_000_000 + _us(frac)
            time_us = -time_us if sign == "-" else time_us
        # component-wise, as PostgreSQL keeps them: '1 day' is not '24:00:00'
        return int(days or 0) == want["interval_days"] and time_us == want["interval_us"]
    return None


def _value_matches(logged: str, want: Any) -> bool | None:
    """True or False when comparable; None when the trace's sanitized form can't be compared with the log."""
    if isinstance(want, dict) and want.keys() & {"timestamptz_us", "timestamp", "date", "time", "timetz",
                                                  "interval_us"}:
        if logged == "NULL":
            return False
        return datetime_matches(logged[1:-1].replace("''", "'") if logged.startswith("'") else logged, want)
    if isinstance(want, dict) and "uuid_sha256" in want:
        text = logged[1:-1] if logged.startswith("'") else ""
        return bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)) and \
            hashlib.sha256(text.encode()).hexdigest()[:12] == want["uuid_sha256"]
    if isinstance(want, list):  # a one-dimensional array, compared element by element
        text = logged[1:-1].replace("''", "'") if logged.startswith("'") else None
        items = _array_elements(text) if text is not None else None
        if items is None or len(items) != len(want):
            return None if items is None else False
        verdicts = [(_value_matches("NULL", w) if item is None else _value_matches("'" + item.replace("'", "''")
                                                                                   + "'", w)) for item, w in
                    zip(items, want, strict=True)]
        return None if None in verdicts else all(verdicts)
    if want is None:
        return logged == "NULL"
    if isinstance(want, bool):
        return logged == ("'t'" if want else "'f'")
    if isinstance(want, int):
        return logged == f"'{want}'"
    if isinstance(want, dict) and "bytes" in want:  # bytea is logged as '\x<hex>'
        text = logged[1:-1] if logged.startswith("'") else ""
        if not text.startswith("\\x"):
            return False
        try:
            raw = bytes.fromhex(text[2:])
        except ValueError:
            return False
        return len(raw) == want["bytes"] and hashlib.sha256(raw).hexdigest()[:12] == want["sha256"]
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
                    kind = (sorted(value)[0] if isinstance(value, dict) and value else type(value).__name__)
                    out.unchecked_kinds[kind] = out.unchecked_kinds.get(kind, 0) + 1
                elif verdict:
                    out.params_compared += 1
                else:
                    out.params_mismatched += 1
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
