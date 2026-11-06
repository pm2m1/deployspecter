"""EXPERIMENTAL ds-p9x/1.1 assurance A3: the construction session's own provenance, reconciled with PostgreSQL's
server log.

``Journal`` wraps the construction connection and records, in order, everything sent on it:
* each statement's text and bound parameters;
* the transaction boundaries (``BEGIN``, ``COMMIT``, ``ROLLBACK``);
* a kind for each: ``admin`` (session settings, locks, catalog fingerprint, snapshots) or ``construction`` (the
  planned INSERTs).

The session is identified by its backend pid, user, database and ``application_name = ds_p9x_construct``.
Automatic server-side preparation is off, so every statement is logged the same way.

``reconcile`` reads the csvlog window between two markers, written on a separate audit session
(``ds_p9x_audit``), and requires:
* the construction backend's connection to be authorized with the expected user, database and application name;
* its logged statements to be **exactly** the journal, in order, with the same text (whitespace, placeholder and
  identifier-quote normalized) and the same parameter values: strings and integers compared as the server rendered
  them, NULL as NULL;
* an ERROR row only where the journal records a failed statement;
* nothing else in the window: no other session, no unallowlisted statement from the audit session (background
  checkpointer rows excepted).

**What this does and doesn't audit.**
* Audited: what the server received and executed on the construction session, and that nothing else ran there or
  anywhere else on the cluster during the window.
* Not audited: the rows' effects. Trigger effects and the final state are checked by the change comparison and the
  independent readback, which are database reads, not a log audit.
* Not audited: the readback session and the Phase 9B sandbox sessions. The sandbox runs in its own database before
  the window.
* Parameters: compared for str, int, bool and NULL as the server renders them; a parameter cast to
  ``timestamptz`` (which the server logs through the type's output function) as the same instant to the
  microsecond (``corroborate.datetime_matches``); and float bit-exactly
  (``corroborate._value_matches``). Any other parameter type is recorded as unchecked, which fails the audit, so it
  can't pass silently.
"""

from __future__ import annotations

import re
import secrets
import time
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql

from deployspecter.experimental.corroborate import _value_matches, datetime_matches
from deployspecter.live.serverlog import ServerLog, normal_sql
from deployspecter.research.track_a.gate2a_audit import parse_log, statement

PID, SEVERITY, MESSAGE, APP, BACKEND = 3, 11, 13, 22, 23
CHECKPOINTER = re.compile(r"^checkpoint (starting|complete)")
GONE_SQL = "SELECT count(*) FROM pg_stat_activity WHERE pid = %s"
# what the audit session itself may send inside the window: waiting for the construction backend to exit, and reading
# the log (ServerLog) until its disconnection row is there
AUDIT_ALLOWED = {normal_sql(x) for x in (GONE_SQL, "SELECT pg_current_logfile('csvlog')",
                                         "SELECT size FROM pg_stat_file(%s, true)",
                                         "SELECT pg_read_binary_file(%s, %s, %s, true)")}
APPLICATION = "ds_p9x_construct"
AUDIT_APPLICATION = "ds_p9x_audit"


class Journal:
    """The construction connection, journaled. Use ``execute`` and ``transaction`` only."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.entries: list[dict[str, Any]] = []
        self.kind = "admin"

    @property
    def pid(self) -> int:
        return self.conn.info.backend_pid

    def execute(self, query: Any, params: Any = None, *, kind: str | None = None,
                casts: list[str | None] | None = None) -> Any:
        """``casts``: each parameter's SQL cast in the statement (``typed.placeholder``), when it has one."""
        text = query.as_string(self.conn) if isinstance(query, sql.Composable) else str(query)
        entry = {"seq": len(self.entries), "kind": kind or self.kind, "text": text,
                 "params": list(params) if params is not None else [], "outcome": "ok"}
        if casts is not None:
            entry["casts"] = list(casts)
        self.entries.append(entry)
        try:
            return self.conn.execute(query, params)
        except psycopg.Error as exc:
            entry["outcome"] = "error"
            entry["sqlstate"] = exc.sqlstate
            raise

    @contextmanager
    def transaction(self):
        self.entries.append({"seq": len(self.entries), "kind": "boundary", "text": "BEGIN", "params": [],
                             "outcome": "ok"})
        try:
            with self.conn.transaction():
                yield self
        except BaseException:
            self.entries.append({"seq": len(self.entries), "kind": "boundary", "text": "ROLLBACK", "params": [],
                                 "outcome": "sent"})
            raise
        self.entries.append({"seq": len(self.entries), "kind": "boundary", "text": "COMMIT", "params": [],
                             "outcome": "ok"})


def _instant_us(text: str) -> int:
    import datetime as dt
    t = dt.datetime.fromisoformat(text)
    if t.tzinfo is None:
        raise ValueError("a timestamptz parameter without an offset")
    return (t - dt.datetime(1970, 1, 1, tzinfo=dt.UTC)) // dt.timedelta(microseconds=1)


def server_rendering(v: Any) -> str | None:
    """How the server logs a bound parameter of a checked type, or None if this type is unchecked."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "'t'" if v else "'f'"
    if isinstance(v, int):
        return f"'{v}'"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return None


class Audit:
    """The window around one construction session."""

    def __init__(self, conninfo: dict[str, Any]) -> None:
        self.conn = psycopg.connect(**conninfo, autocommit=True, application_name=AUDIT_APPLICATION)
        self.log = ServerLog(lambda q, p=None: self.conn.execute(q, p).fetchone())
        nonce = secrets.token_hex(6)
        self.start, self.end = f"ds_p9x_audit_start_{nonce}", f"ds_p9x_audit_end_{nonce}"

    def open(self) -> None:
        self.log.position()
        self.conn.execute(f"SELECT '{self.start}'")

    def close(self, construct_pid: int | None, timeout: float = 5.0) -> bytes:
        """Wait until the construction backend has exited and its disconnection row is in the log, then close the
        window."""
        until = time.monotonic() + timeout
        if construct_pid is not None:
            while self.conn.execute(GONE_SQL, (construct_pid,)).fetchone()[0] and time.monotonic() < until:
                time.sleep(0.02)
            needle = f",{construct_pid},".encode()
            while time.monotonic() < until:
                data = self.log.read()
                if any(needle in line and b"disconnection:" in line for line in data.splitlines()):
                    break
                time.sleep(0.02)
        self.conn.execute(f"SELECT '{self.end}'")
        until = time.monotonic() + timeout
        while True:
            data = self.log.read()
            if f"SELECT '{self.end}'".encode() in data and data.endswith(b"\n"):
                return data
            if time.monotonic() > until:
                raise RuntimeError("the end marker never reached the log")
            time.sleep(0.02)

    @property
    def pid(self) -> int:
        return self.conn.info.backend_pid

    def shutdown(self) -> None:
        self.conn.close()


def reconcile(data: bytes, *, start: str, end: str, journal: list[dict[str, Any]], construct_pid: int,
              audit_pid: int, user: str, database: str) -> dict[str, Any]:
    problems: list[str] = []
    rows = parse_log(data)
    try:
        a = next(i for i, r in enumerate(rows) if r.message == f"statement: SELECT '{start}'")
        b = next(i for i, r in enumerate(rows) if r.message == f"statement: SELECT '{end}'")
    except StopIteration:
        return {"ok": False, "problems": ["a window marker isn't in the log"]}
    want = list(journal)
    cursor, matched, params_checked, params_unchecked = 0, 0, 0, 0
    authorized = disconnected = False
    by_kind: dict[str, int] = {}
    for r in rows[a + 1:b]:
        pid = r[PID]
        if pid == str(audit_pid):
            parsed = statement(r)
            if parsed is None or normal_sql(parsed[1]) not in AUDIT_ALLOWED:
                problems.append(f"the audit session sent an unexpected row: {r.message[:60]}")
            continue
        if r[BACKEND] == "checkpointer" and CHECKPOINTER.match(r.message):
            continue
        if pid != str(construct_pid):
            problems.append(f"unexplained session pid {pid} ({r[APP]}): {r.message[:60]}")
            continue
        if r.message.startswith("connection authorized:"):
            authorized = r.message == (f"connection authorized: user={user} database={database} "
                                       f"application_name={APPLICATION}")
            if not authorized:
                problems.append(f"the construction session connected with another identity: {r.message}")
            continue
        if r.message.startswith(("connection received:", "connection authenticated:")):
            continue
        if r.message.startswith("disconnection:"):
            disconnected = True
            continue
        if r[SEVERITY] == "ERROR":
            last = want[cursor - 1] if cursor else None
            if last is None or last["outcome"] != "error" or (last.get("sqlstate") and r[12] != last["sqlstate"]):
                problems.append(f"an ERROR the journal doesn't record: {r.message[:60]}")
            continue
        parsed = statement(r)
        if parsed is None:
            problems.append(f"an unexplained row: {r.message[:60]}")
            continue
        _verb, text, logged = parsed
        if cursor >= len(want):
            problems.append(f"a statement the journal doesn't record: {text[:60]}")
            continue
        e = want[cursor]
        cursor += 1
        if normal_sql(text) != normal_sql(e["text"]):
            problems.append(f"statement {e['seq']}: logged {normal_sql(text)[:70]!r}, "
                            f"sent {normal_sql(e['text'])[:70]!r}")
            continue
        casts = e.get("casts") or []
        for n, v in enumerate(e["params"], start=1):
            cast = casts[n - 1] if n - 1 < len(casts) else None
            if cast == "timestamptz" and isinstance(v, str):
                # PostgreSQL infers the parameter's type from the cast and logs it through timestamptz's output
                # function (session TimeZone UTC): compared as the same instant, exactly to the microsecond
                lg = logged.get(n)
                verdict = None
                if lg and lg.startswith("'") and lg.endswith("'"):
                    verdict = datetime_matches(lg[1:-1], {"timestamptz_us": _instant_us(v)})
                if verdict is None:
                    params_unchecked += 1
                elif not verdict:
                    problems.append(f"statement {e['seq']} parameter ${n}: logged {lg!r}, sent instant {v!r}")
                else:
                    params_checked += 1
                continue
            if isinstance(v, float):  # bit-exact, by the corroborator's own float rule
                verdict = _value_matches(logged.get(n) or "NULL", v)
                if verdict is None:
                    params_unchecked += 1
                elif not verdict:
                    problems.append(f"statement {e['seq']} parameter ${n}: logged {logged.get(n)!r}, sent {v!r}")
                else:
                    params_checked += 1
                continue
            r_ = server_rendering(v)
            if r_ is None:
                params_unchecked += 1
            elif logged.get(n) != r_:
                problems.append(f"statement {e['seq']} parameter ${n}: logged {logged.get(n)!r}, sent {r_!r}")
            else:
                params_checked += 1
        matched += 1
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    if cursor != len(want):
        missing = want[cursor:]
        if not (len(missing) == 1 and missing[0]["text"] == "ROLLBACK" and missing[0]["outcome"] == "sent"):
            problems.append(f"{len(missing)} journaled statement(s) never reached the log: "
                            f"{[m['text'][:40] for m in missing[:3]]}")
    if not authorized:
        problems.append("the construction session's connection isn't in the window")
    if not disconnected:
        problems.append("the construction session's disconnection isn't in the window")
    return {"ok": not problems and params_unchecked == 0, "problems": problems, "matched": matched,
            "journaled": len(want), "by_kind": by_kind, "params_checked": params_checked,
            "params_unchecked": params_unchecked, "construct_pid": construct_pid, "audit_pid": audit_pid,
            "application": APPLICATION}
