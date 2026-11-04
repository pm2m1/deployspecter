"""Parent-session statement reconciliation from PostgreSQL's own CSV log. Research-only; pure over parsed rows.

Gate 2A reconciles the child's sessions. This reconciles the parent's own Track A sessions (catalog exports, the
independent re-read and the independent state checks) the same way: by what the server logged, never by what the
parent or child says it did.

What it establishes, and what it doesn't:

* Every row between two markers belongs to exactly one tagged parent session or to an allowed background process.
* That session connected as the expected user to the expected database, ran exactly the approved statements in order,
  with exactly the approved parameters, raised no error, and disconnected.
* It does **not** make the parent a sandbox. The parent is a superuser; a read-only transaction is not a privilege
  boundary. This only detects a parent session doing anything other than the approved list.

The column layout is PostgreSQL 16's 26-column ``csvlog``, the same layout Gate 2A's ``serverlog`` reads.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from deployspecter.live import serverlog

USER, DATABASE, PID, SESSION, SEVERITY, MESSAGE, DETAIL, APPLICATION, BACKEND = 1, 2, 3, 5, 11, 13, 14, 22, 23
TAG = re.compile(r"^ds_parent_(op0p|op0r|op2a|op2b|op2c|op2d|op6i|op6f|op7b|op9c)_[0-9a-f]{12}$")
MARKER = re.compile(r"^ds_parent_mark_(start|end|batch_start|batch_end)_[0-9a-f]{12}$")
STATEMENT = re.compile(r"^(?:statement|execute [^:]*): (.*)$", re.S)
PARAMETER = re.compile(r"\$(\d+) = (NULL|'(?:[^']|'')*')")
# Non-client background processes whose ordinary LOG lines may appear in any window. Nothing else may.
BACKGROUND = {"checkpointer": re.compile(r"^checkpoint (starting|complete)")}
LINE = 6  # session_line_num: 1 for a session's first row, consecutive after that

# The connection-state records PostgreSQL 16 writes with log_connections and log_disconnections on, as observed on the
# disposable cluster (evidence/gate_a1/20261104T113256Z-d0b21a60). Anything else that isn't a statement fails.
RECEIVED = re.compile(r"^connection received: host=(\S+) port=(\d+)$")
AUTHENTICATED = re.compile(r'^connection authenticated: identity="([^"]*)" method=([a-z0-9-]+) \((/[^():]+):(\d+)\)$')
AUTHORIZED = re.compile(r"^connection authorized: user=(\S+) database=(\S+) application_name=(\S+)$")
DISCONNECTED = re.compile(r"^disconnection: session time: \d+:\d{2}:\d{2}\.\d{3} "
                          r"user=(\S+) database=(\S+) host=(\S+) port=(\d+)$")
AUTH_METHODS = frozenset({"scram-sha-256"})

UNNAMED = ("statement: ", "execute <unnamed>: ")  # how statements log when the client prepares nothing

LOG_READER_APP = "ds_parent_log_reader"
READER_POLL = "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1"


class ParentLogError(RuntimeError):
    """The log can't support a conclusion about the parent session. Fail closed."""


class EndMarkerMissing(ParentLogError):
    """The end marker isn't in the log yet. The only condition a caller may wait on, and only for a bounded time."""


@dataclass(frozen=True)
class ExpectedStatement:
    text: str  # as the parent sends it; %s placeholders become $n on the server
    params: tuple[Any, ...] = ()

    def server_text(self) -> str:
        count = 0

        def number(_match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return f"${count}"

        return re.sub(r"%s", number, self.text)


def float8_log_text(value: float) -> str:
    """How PostgreSQL 16 logs a float8 parameter, in the forms that are established, and no others.

    psycopg 3 sends a Python float in binary as float8 (FloatBinaryDumper), so the server logs it with float8's text
    output, which with extra_float_digits >= 1 (the default; Gate A1 pins it to 1) is the shortest exact decimal. The
    third real run logged negative zero as "-0", where Python writes "-0.0". For non-integral finite values PostgreSQL's
    shortest digits are Python's repr, and the notation agrees as long as Python doesn't print a fixed-point number
    PostgreSQL would write with an exponent (|x| >= 1e15). Positive zero, integral values and 1e15 <= |x| < 1e16 have
    no established form here and are refused rather than guessed.
    """
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        if math.copysign(1.0, value) < 0:
            return "-0"
        raise ParentLogError("positive zero has no established float8 log form")
    text = repr(value)
    if "e" in text or (abs(value) < 1e15 and not value.is_integer()):
        return text
    raise ParentLogError(f"{text} has no established float8 log form")


def literal(value: Any) -> str:
    """How PostgreSQL logs a bound parameter. Strings are sent as text and logged as given; booleans, integers and
    floats are sent in binary and logged with their type's text output."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "'t'" if value else "'f'"
    if isinstance(value, float):
        return f"'{float8_log_text(value)}'"
    if isinstance(value, int) or isinstance(value, str):
        return "'" + str(value).replace("'", "''") + "'"
    raise ParentLogError(f"{type(value).__name__} parameters have no established log form")


@dataclass
class ParentReconciliation:
    ok: bool
    problems: list[str] = field(default_factory=list)
    session_id: str | None = None
    statements_seen: int = 0
    counts: dict[str, int] = field(default_factory=dict)


def marker_sql(token: str) -> str:
    if not MARKER.match(token):
        raise ParentLogError(f"marker {token!r} isn't a parent marker")
    return f"SELECT '{token}'"


def window(log_bytes: bytes, start: str, end: str) -> list[list[str]]:
    """Gate 2A's own window parser, unchanged. A missing end marker or an unparsable log fails closed."""
    if not (MARKER.match(start) and MARKER.match(end)):
        raise ParentLogError("markers must be parent markers")
    try:
        rows = serverlog.parse_window(log_bytes, start, end)
    except serverlog.LogUnverified as exc:
        raise ParentLogError(str(exc)) from exc
    if rows is None:
        raise EndMarkerMissing("the end marker isn't in the log")
    return rows


@dataclass(frozen=True)
class ReaderSession:
    """The one log-reader session, identified by the rows that wrote an operation's (or the batch's) markers."""
    session: str
    pid: str
    user: str
    database: str
    start_line: int
    end_line: int


def marker_rows(log_bytes: bytes, start: str, end: str) -> tuple[list[str], list[str]]:
    """The two rows that wrote the markers, each exactly once, as the exact marker statement."""
    if not (MARKER.match(start) and MARKER.match(end)):
        raise ParentLogError("markers must be parent markers")
    found: dict[str, list[list[str]]] = {start: [], end: []}
    try:
        for r in csv.reader(io.StringIO(log_bytes.decode("utf-8", errors="replace"), newline=""), strict=True):
            for token in found:
                if len(r) == serverlog.CSV_COLUMNS and r[MESSAGE] == f"statement: {marker_sql(token)}":
                    found[token].append(r)
    except csv.Error as exc:
        raise ParentLogError(f"unparsable log: {exc}") from exc
    if len(found[start]) != 1 or len(found[end]) != 1:
        raise ParentLogError("each marker must be logged exactly once")
    return found[start][0], found[end][0]


def reader_session(start_row: list[str], end_row: list[str], *, user: str, database: str) -> ReaderSession:
    """The log reader that wrote both markers: one session and process, the reader's name, the expected identity."""
    for r in (start_row, end_row):
        if (r[APPLICATION], r[BACKEND], r[SEVERITY], r[USER], r[DATABASE]) != (
                LOG_READER_APP, "client backend", "LOG", user, database):
            raise ParentLogError("a marker wasn't written by the log reader as the expected user and database")
    if (start_row[SESSION], start_row[PID]) != (end_row[SESSION], end_row[PID]) or not start_row[SESSION]:
        raise ParentLogError("the two markers came from different sessions")
    try:
        start_line, end_line = int(start_row[LINE]), int(end_row[LINE])
    except ValueError:
        raise ParentLogError("a marker row has no session line number") from None
    if end_line <= start_line:
        raise ParentLogError("the end marker precedes the start marker in its session")
    return ReaderSession(start_row[SESSION], start_row[PID], user, database, start_line, end_line)


def reconcile_parent_session(rows: list[list[str]], *, tag: str, expected: tuple[ExpectedStatement, ...],
                             user: str, database: str, authentication: str | None = None,
                             reader: ReaderSession | None = None, reader_poll_limit: int = 0,
                             unnamed_only: bool = False) -> ParentReconciliation:
    """Reconcile one tagged parent session from the rows strictly between its markers.

    ``authentication``: the one method the session must authenticate with. When None, an authentication record is
    optional, but if present it must still be exactly one valid record in its place.
    ``reader``: the established log-reader session. Only it may appear besides the tagged session and background
    processes, and only issuing the exact end-of-session poll for this tag, after the session's work, at most
    ``reader_poll_limit`` times, on consecutive session lines from the start marker to the end marker.
    ``unnamed_only``: the client disabled automatic preparation, so a statement logged under a prepared-statement name
    ("execute _pg3_0:" and the like) means the connection isn't the one that was reviewed, and fails.
    """
    out = ParentReconciliation(ok=False)
    if not TAG.match(tag):
        out.problems.append(f"tag {tag!r} isn't a parent operation tag")
        return out
    tagged = {r[SESSION] for r in rows if r[APPLICATION] == tag}
    if len(tagged) != 1:
        out.problems.append(f"{len(tagged)} sessions carry the tag; exactly one must")
        return out
    session = next(iter(tagged))
    out.session_id = session
    mine_at = [i for i, r in enumerate(rows) if r[SESSION] == session]
    mine = [rows[i] for i in mine_at]

    other_sessions, reader_rows = set(), []
    for i, r in enumerate(rows):
        if r[SESSION] == session:
            continue
        if reader is not None and r[SESSION] == reader.session:
            reader_rows.append((i, r))
            continue
        pattern = BACKGROUND.get(r[BACKEND])
        if pattern is None or r[SEVERITY] != "LOG" or not pattern.match(r[MESSAGE]):
            other_sessions.add(r[SESSION])
    if other_sessions:
        out.problems.append(f"{len(other_sessions)} unattributed session(s) in the window")

    if {r[PID] for r in mine} != {mine[0][PID]}:
        out.problems.append("the session's process id changed")
    if [r[LINE] for r in mine] != [str(n) for n in range(1, len(mine) + 1)]:
        out.problems.append("the session's log lines aren't complete and in order from its first")
    if any(r[SEVERITY] != "LOG" for r in mine):
        out.problems.append(f"severities other than LOG: {sorted({r[SEVERITY] for r in mine} - {'LOG'})}")
    if any(r[USER] != user or r[DATABASE] != database for r in mine if r[USER] or r[DATABASE]):
        out.problems.append("a row of the session has another user or database")

    # Connection state, in PostgreSQL's order: received, [authenticated], authorized ... disconnection.
    messages = [r[MESSAGE] for r in mine]
    received = RECEIVED.match(messages[0]) if messages else None
    if received is None:
        out.problems.append("the session's connection isn't in the window")
    at = 1
    authenticated = [m for m in messages if m.startswith("connection authenticated:")]
    if len(authenticated) > 1:
        out.problems.append("the session authenticated more than once")
    elif authenticated:
        match = AUTHENTICATED.match(messages[at]) if len(messages) > at else None
        if match is None:
            out.problems.append("the authentication record is malformed or out of order")
        elif match.group(1) != user or match.group(2) not in AUTH_METHODS or (
                authentication is not None and match.group(2) != authentication):
            out.problems.append("the session authenticated as another identity or with another method")
        at += 1
    elif authentication is not None:
        out.problems.append("the session's authentication isn't in the window")
    if sum(m.startswith("connection authorized:") for m in messages) != 1:
        out.problems.append("the session wasn't authorized exactly once in the window")
    else:
        match = AUTHORIZED.match(messages[at]) if len(messages) > at else None
        if match is None:
            out.problems.append("the authorization record is malformed or out of order")
        elif match.groups() != (user, database, tag):
            out.problems.append("connection identity differs from the expected user, database or tag")
        at += 1
    ended = DISCONNECTED.match(messages[-1]) if messages else None
    if ended is None or sum(m.startswith("disconnection:") for m in messages) != 1:
        out.problems.append("the session didn't disconnect inside the window")
    elif ended.group(1, 2) != (user, database) or (
            received is not None and ended.group(3, 4) != received.group(1, 2)):
        out.problems.append("the disconnection doesn't match the session's connection")

    body = mine[at:-1] if len(mine) > at else []
    statements = [(STATEMENT.match(r[MESSAGE]), r[DETAIL]) for r in body]
    seen = [(m.group(1), detail) for m, detail in statements if m]
    unexplained = [r[MESSAGE][:60] for r, (m, _d) in zip(body, statements, strict=True) if not m]
    if unexplained:
        out.problems.append(f"unexplained session rows: {unexplained[:3]}")
    named = [r for r, (m, _d) in zip(body, statements, strict=True) if m and not r[MESSAGE].startswith(UNNAMED)]
    if unnamed_only and named:
        out.problems.append("a statement was logged under a prepared-statement name")

    if reader is not None:
        last_work = mine_at[-2] if len(mine_at) >= 2 else len(rows)  # the session's last row before disconnecting
        poll = (reader.pid, reader.user, reader.database, LOG_READER_APP, "client backend", "LOG",
                f"execute <unnamed>: {READER_POLL}", f"parameters: $1 = {literal(tag)}")
        if any((r[PID], r[USER], r[DATABASE], r[APPLICATION], r[BACKEND], r[SEVERITY], r[MESSAGE], r[DETAIL]) != poll
               for _i, r in reader_rows):
            out.problems.append("the log reader did something in the window other than this session's poll")
        if [r[LINE] for _i, r in reader_rows] != [str(n) for n in range(reader.start_line + 1, reader.end_line)]:
            out.problems.append("the log reader's rows in the window are incomplete or out of sequence")
        if not 1 <= len(reader_rows) <= reader_poll_limit:
            out.problems.append(f"{len(reader_rows)} log-reader polls in the window, allowed 1 to {reader_poll_limit}")
        if any(i < last_work for i, _r in reader_rows):
            out.problems.append("the log reader polled before the session's work was logged")

    out.statements_seen = len(seen)
    out.counts = dict(Counter(text for text, _ in seen))
    if len(seen) != len(expected):
        out.problems.append(f"{len(seen)} statements logged, {len(expected)} approved")
    for i, (want, (text, detail)) in enumerate(zip(expected, seen, strict=False)):
        if text != want.server_text():
            out.problems.append(f"statement {i} differs from the approved text")
            continue
        logged = dict((int(n), v) for n, v in PARAMETER.findall(detail or ""))
        try:
            wanted = {n + 1: literal(v) for n, v in enumerate(want.params)}
        except ParentLogError as exc:
            out.problems.append(f"statement {i} has a parameter without an established log form: {exc}")
            continue
        if logged != wanted:
            out.problems.append(f"statement {i} has unapproved parameters")
    out.ok = not out.problems
    return out
