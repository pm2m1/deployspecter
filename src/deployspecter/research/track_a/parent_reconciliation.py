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

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from deployspecter.live import serverlog

USER, DATABASE, PID, SESSION, SEVERITY, MESSAGE, DETAIL, APPLICATION, BACKEND = 1, 2, 3, 5, 11, 13, 14, 22, 23
TAG = re.compile(r"^ds_parent_(op2a|op2b|op2c|op2d|op6i|op6f)_[0-9a-f]{12}$")
MARKER = re.compile(r"^ds_parent_mark_(start|end)_[0-9a-f]{12}$")
STATEMENT = re.compile(r"^(?:statement|execute [^:]*): (.*)$", re.S)
PARAMETER = re.compile(r"\$(\d+) = (NULL|'(?:[^']|'')*')")
# Non-client background processes whose ordinary LOG lines may appear in any window. Nothing else may.
BACKGROUND = {"checkpointer": re.compile(r"^checkpoint (starting|complete)")}


class ParentLogError(RuntimeError):
    """The log can't support a conclusion about the parent session. Fail closed."""


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


def literal(value: Any) -> str:
    """How PostgreSQL logs a bound text parameter."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "'t'" if value else "'f'"
    return "'" + str(value).replace("'", "''") + "'"


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
        raise ParentLogError("the end marker isn't in the log")
    return rows


def reconcile_parent_session(rows: list[list[str]], *, tag: str, expected: tuple[ExpectedStatement, ...],
                             user: str, database: str) -> ParentReconciliation:
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
    other_sessions = set()
    for r in rows:
        if r[SESSION] == session:
            continue
        pattern = BACKGROUND.get(r[BACKEND])
        if pattern is None or r[SEVERITY] != "LOG" or not pattern.match(r[MESSAGE]):
            other_sessions.add(r[SESSION])
    if other_sessions:
        out.problems.append(f"{len(other_sessions)} unattributed session(s) in the window")
    mine = [r for r in rows if r[SESSION] == session]
    if {r[PID] for r in mine} != {mine[0][PID]}:
        out.problems.append("the session's process id changed")
    if any(r[SEVERITY] != "LOG" for r in mine):
        out.problems.append(f"severities other than LOG: {sorted({r[SEVERITY] for r in mine} - {'LOG'})}")
    messages = [r[MESSAGE] for r in mine]
    if not messages or not messages[0].startswith("connection received:"):
        out.problems.append("the session's connection isn't in the window")
    authorized = [r for r in mine if r[MESSAGE].startswith("connection authorized:")]
    if len(authorized) != 1:
        out.problems.append("the session wasn't authorized exactly once in the window")
    else:
        a = authorized[0]
        if a[USER] != user or a[DATABASE] != database or f"application_name={tag}" not in a[MESSAGE]:
            out.problems.append("connection identity differs from the expected user, database or tag")
    if not messages or not messages[-1].startswith("disconnection:"):
        out.problems.append("the session didn't disconnect inside the window")
    if any(r[USER] != user or r[DATABASE] != database for r in mine if r[USER] or r[DATABASE]):
        out.problems.append("a row of the session has another user or database")
    statements = [(STATEMENT.match(r[MESSAGE]), r[DETAIL]) for r in mine]
    seen = [(m.group(1), detail) for m, detail in statements if m]
    unexplained = [r[MESSAGE][:60] for r, (m, _d) in zip(mine, statements, strict=True)
                   if not m and not r[MESSAGE].startswith(("connection received:", "connection authorized:",
                                                           "disconnection:"))]
    if unexplained:
        out.problems.append(f"unexplained session rows: {unexplained[:3]}")
    out.statements_seen = len(seen)
    out.counts = dict(Counter(text for text, _ in seen))
    if len(seen) != len(expected):
        out.problems.append(f"{len(seen)} statements logged, {len(expected)} approved")
    for i, (want, (text, detail)) in enumerate(zip(expected, seen, strict=False)):
        if text != want.server_text():
            out.problems.append(f"statement {i} differs from the approved text")
            continue
        logged = dict((int(n), v) for n, v in PARAMETER.findall(detail or ""))
        wanted = {n + 1: literal(v) for n, v in enumerate(want.params)}
        if logged != wanted:
            out.problems.append(f"statement {i} has unapproved parameters")
    out.ok = not out.problems
    return out
