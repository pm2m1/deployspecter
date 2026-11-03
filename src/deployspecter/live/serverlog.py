"""Reconciles what the application's sessions actually did, from PostgreSQL's own log, against what the trace claims.

This is the only check that sees raw driver calls on a registered session. It reads the server's CSV log, which keeps
multi-line statements and parameters in structured fields. Anything it can't read or account for fails closed: a
missing, truncated, rotated or unparsable log is ``log_unverified``, and an unknown session or an extra, missing or
out-of-order statement is ``unmanaged_activity``.

Statement text and order are checked strictly. Parameters are compared where the trace holds a plain value, so a
different value on identical text is caught. Application statements inside an opaque batch are counted, not matched
to parameters. The log says nothing about activity on a different server.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .trace import LiveTrace, OpRecord

CSV_COLUMNS = 26
_USER, _PID, _SEVERITY, _MESSAGE, _DETAIL, _BACKEND = 1, 3, 11, 13, 14, 23

_STATEMENT = re.compile(r"^(?:statement|execute [^:]*): (.*)$", re.S)
_PARAM = re.compile(r"\$\d+ = (NULL|'(?:[^']|'')*')")
_PLACEHOLDER = re.compile(r"\$\d+|%\(\w+\)s|%s")

# Sent by the harness on its own sessions in the child, so each one is accounted for exactly.
SESSION_CHECK_SQL = """SELECT current_user, session_user, current_database(), r.rolsuper, r.rolcreatedb,
       r.rolcreaterole, r.rolreplication, r.rolbypassrls, current_setting('search_path'),
       (SELECT setting::int FROM pg_settings WHERE name = 'statement_timeout'),
       (SELECT setting::int FROM pg_settings WHERE name = 'lock_timeout'),
       (SELECT setting::int FROM pg_settings WHERE name = 'idle_in_transaction_session_timeout'),
       (SELECT setting::int FROM pg_settings WHERE name = 'deadlock_timeout'),
       has_schema_privilege(current_user, 'ds_run', 'CREATE'),
       has_database_privilege(current_user, current_database(), 'CREATE'),
       has_database_privilege(current_user, current_database(), 'TEMP'),
       pg_has_role(current_user, 'pg_read_server_files', 'USAGE'),
       pg_has_role(current_user, 'pg_write_server_files', 'USAGE'),
       pg_has_role(current_user, 'pg_execute_server_program', 'USAGE'),
       pg_has_role(current_user, 'pg_signal_backend', 'USAGE')
FROM pg_roles r WHERE r.rolname = current_user"""
BLOCKERS_SQL = "SELECT p, pg_blocking_pids(p) FROM unnest(%s::int[]) AS p"
TERMINATE_SQL = "SELECT pg_terminate_backend(%s)"


class LogUnverified(Exception):
    """The log can't support a conclusion about this run."""


def normal_sql(text: str) -> str:
    collapsed = " ".join(_PLACEHOLDER.sub("?", text).replace('"', "").split()).rstrip("; ")
    return collapsed.replace("%%", "%")


MONITOR_ALLOWED = {normal_sql(s) for s in (SESSION_CHECK_SQL, BLOCKERS_SQL, TERMINATE_SQL)}
_CHECK = normal_sql(SESSION_CHECK_SQL)


@dataclass
class _Expected:
    """One statement the log must show, or may show if ``required`` is False."""

    text: str
    op: OpRecord | None
    required: bool = True
    params: list[Any] | None = None
    repeat: int = 1


def _expected_statements(op: OpRecord) -> list[_Expected]:
    required = op.status == "ok"
    items: list[_Expected] = []
    idle_before = op.txn_before == "IDLE"
    began = idle_before and op.txn_after in {"INTRANS", "INERROR"} and op.kind not in {"begin"}
    if began or op.status != "ok":
        items.append(_Expected("BEGIN", op, required=began and required))
    sql = op.sql
    if op.kind == "opaque_batch":
        items.append(_Expected(normal_sql(sql), op, required=False, repeat=op.batch_count or 0))
        if required and op.batch_count:
            items[-1].required = True
        return items
    if op.kind in {"commit", "rollback", "implicit_rollback"} and idle_before:
        return items  # psycopg sends nothing when there's no transaction to end
    if op.kind == "rollback_to" and sql.startswith("ROLLBACK TO + RELEASE"):
        name = sql.rsplit(" ", 1)[-1]
        return [
            *items, _Expected(f"ROLLBACK TO {name}", op, required=required),
            _Expected(f"RELEASE {name}", op, required=required),
        ]
    params = op.params if isinstance(op.params, list) else None
    items.append(_Expected(normal_sql(sql), op, required=required, params=params))
    return items


def _comparable(value: Any) -> bool:
    return value is None or (isinstance(value, (int, str)) and not isinstance(value, bool)
                             and not str(value).startswith("<"))


def _params_differ(detail: str, wanted: list[Any] | None) -> bool:
    if wanted is None or not detail.startswith("Parameters:"):
        return False
    logged = _PARAM.findall(detail)
    if len(logged) != len(wanted):
        return True
    for got, value in zip(logged, wanted, strict=True):
        if value is None:
            if got != "NULL":
                return True
        elif _comparable(value):
            if got == "NULL" or got[1:-1].replace("''", "'") != str(value):
                return True
    return False


def _parameter_coverage(op: OpRecord, report: Reconciliation) -> None:
    """How much of what was sent could actually be checked, reported rather than implied."""
    if op.kind == "opaque_batch":
        report.statements_parameters_unchecked += op.batch_count or 0
    elif isinstance(op.params, list):
        compared = sum(_comparable(v) for v in op.params)
        report.parameters_compared += compared
        report.parameters_not_compared += len(op.params) - compared
    elif op.params:
        report.statements_parameters_unchecked += 1


@dataclass
class Reconciliation:
    rows: int = 0
    client_sessions: int = 0
    statements_logged: int = 0
    statements_matched: int = 0
    unknown_sessions: list[str] = field(default_factory=list)
    unexpected_statements: list[str] = field(default_factory=list)
    missing_statements: list[str] = field(default_factory=list)
    missing_session_events: list[str] = field(default_factory=list)
    order_violations: list[str] = field(default_factory=list)
    trace_available: bool = True
    parameters_compared: int = 0
    parameters_not_compared: int = 0
    statements_parameters_unchecked: int = 0

    @property
    def clean(self) -> bool:
        return not (self.unknown_sessions or self.unexpected_statements or self.missing_statements
                    or self.missing_session_events or self.order_violations)

    def summary(self) -> dict[str, Any]:
        return {
            "rows": self.rows, "client_sessions": self.client_sessions, "statements_logged": self.statements_logged,
            "statements_matched": self.statements_matched, "trace_available": self.trace_available,
            "parameters_compared": self.parameters_compared, "parameters_not_compared": self.parameters_not_compared,
            "statements_parameters_unchecked": self.statements_parameters_unchecked,
            "unknown_sessions": self.unknown_sessions[:20], "unexpected_statements": self.unexpected_statements[:20],
            "missing_statements": self.missing_statements[:20],
            "missing_session_events": self.missing_session_events[:20], "order_violations": self.order_violations[:20],
        }


def reconcile(
    rows: list[list[str]], trace: LiveTrace | None, expected_pids: dict[int, str], monitor_pid: int | None,
    harness_pids: set[int], halted: bool = False,
) -> Reconciliation:
    """Accounts for every row in the run's window. ``expected_pids`` maps each registered backend to its actor."""
    report = Reconciliation(rows=len(rows), trace_available=trace is not None)
    client: dict[int, list[list[str]]] = {}
    for row in rows:
        pid = int(row[_PID])
        if pid in harness_pids:
            continue
        message = row[_MESSAGE]
        if row[_BACKEND] == "client backend" or message.startswith("connection received") or row[_USER]:
            client.setdefault(pid, []).append(row)
    report.client_sessions = len(client)
    plans: dict[int, list[_Expected]] = {pid: [] for pid in expected_pids if pid != monitor_pid}
    if trace is not None:
        for op in trace.records:
            plans.setdefault(op.backend_pid, []).extend(_expected_statements(op))
    registered = set(expected_pids)
    last_seq = -1
    for pid, session_rows in client.items():
        if pid in registered or _only_a_previous_sessions_end(session_rows):
            continue
        # A cancel request arrives as a connection that goes no further. The harness sends them only on a stopped run.
        probe = all(r[_MESSAGE].startswith("connection received") for r in session_rows)
        if not (probe and halted):
            first = next((r[_MESSAGE] for r in session_rows if not r[_MESSAGE].startswith("connection received")),
                         session_rows[0][_MESSAGE])
            report.unknown_sessions.append(f"pid {pid}: {first[:80]}")
    statement_rows = _statement_rows(rows, registered, harness_pids, report)
    cursors = dict.fromkeys(plans, 0)
    consumed: dict[tuple[int, int], int] = {}
    seen_check: set[int] = set()
    for row, text in statement_rows:
        pid = int(row[_PID])
        report.statements_logged += 1
        if pid == monitor_pid:
            if text in MONITOR_ALLOWED:
                report.statements_matched += 1
            else:
                report.unexpected_statements.append(f"monitor: {text[:80]}")
            continue
        if text == _CHECK and pid not in seen_check:
            seen_check.add(pid)
            report.statements_matched += 1
            continue
        if trace is None:
            continue  # no claim to compare with, and the run is already inconclusive
        matched = _advance(plans[pid], cursors, pid, text, row[_DETAIL], consumed)
        if matched is None:
            if halted and text == "ROLLBACK":
                report.statements_matched += 1  # a stopped run's transaction block rolling back, ungated by design
                continue
            report.unexpected_statements.append(f"{expected_pids[pid]}: {text[:80]}")
            continue
        report.statements_matched += 1
        if matched.seq != last_seq:
            _parameter_coverage(matched, report)
        if matched.seq < last_seq:
            report.order_violations.append(f"op {matched.seq} logged after op {last_seq}")
        last_seq = max(last_seq, matched.seq)
    for pid, plan in plans.items():
        for index in range(cursors.get(pid, 0), len(plan)):
            _note_missing(plan, index, pid, consumed, expected_pids, report)
    for pid, name in expected_pids.items():
        messages = [r[_MESSAGE] for r in client.get(pid, [])]
        for event, prefix in (("authorized", "connection authorized"), ("ended", "disconnection:")):
            if sum(m.startswith(prefix) for m in messages) != 1:
                report.missing_session_events.append(f"{name}: session {event} not logged exactly once")
    return report


def _only_a_previous_sessions_end(session_rows: list[list[str]]) -> bool:
    return all(r[_MESSAGE].startswith("disconnection:") for r in session_rows)


def _statement_rows(
    rows: list[list[str]], registered: set[int], harness_pids: set[int], report: Reconciliation,
) -> list[tuple[list[str], str]]:
    found = []
    for row in rows:
        pid = int(row[_PID])
        if pid in harness_pids or row[_SEVERITY] != "LOG":
            continue
        match = _STATEMENT.match(row[_MESSAGE])
        if not match:
            continue
        text = normal_sql(match.group(1))
        if pid in registered:
            found.append((row, text))
        else:
            report.statements_logged += 1
            report.unexpected_statements.append(f"pid {pid}: {text[:80]}")
    return found


def _advance(
    plan: list[_Expected], cursors: dict[int, int], pid: int, text: str, detail: str,
    consumed: dict[tuple[int, int], int],
) -> OpRecord | None:
    """Move through this session's plan. Optional items may be skipped; a required one can't be."""
    index = cursors[pid]
    while index < len(plan):
        item = plan[index]
        used = consumed.get((pid, index), 0)
        fits = text == item.text or (item.text == "BEGIN" and text.startswith("BEGIN"))
        if fits and used < item.repeat and not _params_differ(detail, item.params):
            consumed[(pid, index)] = used + 1
            cursors[pid] = index + (1 if used + 1 >= item.repeat else 0)
            return item.op
        if used >= item.repeat or (not item.required) or used > 0:
            index += 1  # optional, or a batch that stopped early
            continue
        return None
    return None


def _note_missing(
    plan: list[_Expected], index: int, pid: int, consumed: dict[tuple[int, int], int], names: dict[int, str],
    report: Reconciliation,
) -> None:
    item = plan[index]
    if item.required and consumed.get((pid, index), 0) < item.repeat:
        report.missing_statements.append(f"{names.get(pid, pid)}: {item.text[:80]}")


# -------------------------------------------------------------------- reading the log


def parse_window(data: bytes, start_token: str, end_token: str) -> list[list[str]] | None:
    """Rows from the start marker to the end marker. None if the end isn't there yet. Raises if it can't be trusted."""
    reader = csv.reader(io.StringIO(data.decode("utf-8", errors="replace"), newline=""), strict=True)
    rows: list[list[str]] = []
    started = False
    bad = None
    try:
        for row in reader:
            if len(row) != CSV_COLUMNS:
                bad = bad or f"row with {len(row)} columns"
                continue
            if not started:
                started = start_token in row[_MESSAGE]
                continue
            if end_token in row[_MESSAGE]:
                if bad:
                    raise LogUnverified(f"unparsable log inside the run's window: {bad}")
                return rows
            rows.append(row)
    except csv.Error as exc:
        raise LogUnverified(f"unparsable log: {exc}") from exc
    if not started and data:
        raise LogUnverified("the run's start marker isn't in the log")
    return None


class ServerLog:
    """Reads the current CSV log through the admin session, from a byte offset, so a run's cost doesn't grow with it."""

    def __init__(self, query: Callable[..., Any]) -> None:
        self.query = query
        self.path = ""
        self.offset = 0

    def position(self) -> None:
        path = self.query("SELECT pg_current_logfile('csvlog')")[0]
        if not path:
            raise LogUnverified("the server isn't writing a CSV log")
        stat = self.query("SELECT size FROM pg_stat_file(%s, true)", (path,))
        if stat is None or stat[0] is None:
            raise LogUnverified("the log file can't be read")
        self.path, self.offset = path, stat[0]

    def read(self) -> bytes:
        if self.query("SELECT pg_current_logfile('csvlog')")[0] != self.path:
            raise LogUnverified("the log rotated during the run")
        stat = self.query("SELECT size FROM pg_stat_file(%s, true)", (self.path,))
        if stat is None or stat[0] is None:
            raise LogUnverified("the log file disappeared during the run")
        if stat[0] < self.offset:
            raise LogUnverified("the log was truncated during the run")
        length = stat[0] - self.offset
        row = self.query("SELECT pg_read_binary_file(%s, %s, %s, true)", (self.path, self.offset, length))
        if row is None or row[0] is None:
            raise LogUnverified("the log file can't be read")
        return bytes(row[0])
