"""Opt-in psycopg 3 instrumentation that stops an application before each database operation.

Nothing global is patched. An application function takes part only if it is handed a ``SteppedConnection``. Its
monitoring counterpart, ``Sandbox.monitor``, is a plain connection and is never intercepted.

Two layers enforce the supported surface:

* The public operations that the prototype can schedule go through ``ActorChannel.perform``, which waits for the
  controller's permission, runs the real driver call on the actor's thread, and reports what happened.
* ``SteppedConnection.wait`` is the one place synchronous psycopg sends traffic. It refuses all traffic that has no
  grant, so a path that was not instrumented (a directly built ``ClientCursor``, say) fails instead of escaping the
  schedule. A failure this layer catches always marks the run ``unsupported``.
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, NoReturn

import psycopg
from psycopg import sql as pgsql
from psycopg.pq import ConnStatus, TransactionStatus
from psycopg.rows import tuple_row
from psycopg.transaction import Transaction

from ..database import normalize

MAX_RECORDED_ROWS = 200

_DRIVER_MODULES = ("psycopg", "contextlib", "deployspecter.live.driver")


class UnsupportedDriverOperation(Exception):
    """The application used a driver path this prototype does not schedule. The run becomes ``unsupported``."""


class RunAborted(BaseException):
    """The controller stopped the run. A BaseException, so an application's ``except Exception`` cannot absorb it."""


# ------------------------------------------------------------------ statement classification


def _scan(text: str) -> tuple[str, int]:
    """Return SQL with comments and literal bodies removed, and how many statements it holds."""
    out: list[str] = []
    statements = 0
    has_code = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        pair = text[i : i + 2]
        if pair == "--":
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if pair == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                if text[i : i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif text[i : i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            continue
        if c == "'" or c == '"':
            escapes = c == "'" and i > 0 and text[i - 1] in "eE"
            i += 1
            while i < n:
                if escapes and text[i] == "\\":
                    i += 2
                elif text[i] == c and text[i + 1 : i + 2] == c:
                    i += 2
                elif text[i] == c:
                    i += 1
                    break
                else:
                    i += 1
            out.append("''")
            has_code = True
            continue
        if c == "$":
            match = re.match(r"\$[A-Za-z_]?[A-Za-z0-9_]*\$", text[i:])
            if match:
                tag = match.group(0)
                end = text.find(tag, i + len(tag))
                i = n if end < 0 else end + len(tag)
                out.append("''")
                has_code = True
                continue
        if c == ";":
            statements += 1 if has_code else 0
            has_code = False
            out.append(" ")
            i += 1
            continue
        if not c.isspace():
            has_code = True
        out.append(c)
        i += 1
    return "".join(out), statements + (1 if has_code else 0)


_REFUSED_COMMANDS = {
    "COPY": "COPY",
    "LISTEN": "LISTEN/NOTIFY",
    "UNLISTEN": "LISTEN/NOTIFY",
    "NOTIFY": "LISTEN/NOTIFY",
    "DECLARE": "server-side cursors",
    "FETCH": "server-side cursors",
    "MOVE": "server-side cursors",
    "CLOSE": "server-side cursors",
    "DISCARD": "session reset",
    "RESET": "session settings outside the transaction",
}


@dataclass(frozen=True)
class Classified:
    kind: str
    savepoint: str | None = None
    refused: str | None = None


def classify_sql(text: str) -> Classified:
    """Name what one SQL text does to the transaction, or the reason it is refused."""
    code, count = _scan(text)
    if count > 1:
        return Classified("sql", refused="multiple statements in one execute would be one scheduling step")
    words = code.upper().split()
    if not words:
        return Classified("sql")
    head = words[0]
    if head in _REFUSED_COMMANDS:
        return Classified("sql", refused=_REFUSED_COMMANDS[head])
    if words[:2] == ["PREPARE", "TRANSACTION"] or (head in {"COMMIT", "ROLLBACK"} and words[1:2] == ["PREPARED"]):
        return Classified("sql", refused="two-phase commit")
    if head == "SET":
        joined = " ".join(words)
        if "ROLE" in words or "SESSION AUTHORIZATION" in joined:
            return Classified("sql", refused="changing the sandbox role")
        if words[1:2] not in (["LOCAL"], ["TRANSACTION"], ["CONSTRAINTS"]):
            return Classified("sql", refused="session settings outside the transaction")
        return Classified("sql")
    if head == "BEGIN" or words[:2] == ["START", "TRANSACTION"]:
        return Classified("begin")
    if head in {"COMMIT", "END"}:
        return Classified("commit")
    original = code.split()
    if head in {"ROLLBACK", "ABORT"}:
        rest = [w for w in words[1:] if w not in {"WORK", "TRANSACTION", "AND", "NO", "CHAIN"}]
        if rest[:1] == ["TO"]:
            target = original[-1].strip('"')
            return Classified("rollback_to", savepoint=target)
        return Classified("rollback")
    if head == "SAVEPOINT":
        return Classified("savepoint", savepoint=original[1].strip('"'))
    if head == "RELEASE":
        return Classified("release", savepoint=original[-1].strip('"'))
    return Classified("sql")


# ------------------------------------------------------------------ messages to the controller


@dataclass
class OpRequested:
    actor: str
    kind: str
    sql: str
    params: Any
    savepoint: str | None
    provenance: dict[str, Any]
    releases_savepoint: bool = False


@dataclass
class OpCompleted:
    actor: str
    txn_before: str
    txn_after: str
    autocommit: bool
    rowcount: int | None = None
    command_tag: str | None = None
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    rows_total: int | None = None
    exception: BaseException | None = None
    savepoint: str | None = None
    batch_count: int | None = None


@dataclass
class OpRefused:
    actor: str
    operation: str
    reason: str


@dataclass
class ActorChannel:
    """One actor's side of the control protocol. Every method runs on that actor's own thread."""

    actor: str
    conn: SteppedConnection
    post: Callable[[Any], None]
    hard_limit_s: float
    thread: threading.Thread | None = None
    aborting: bool = False
    granted: bool = False
    _grant: threading.Event = field(default_factory=threading.Event)

    def grant(self) -> None:
        self._grant.set()

    def abort(self) -> None:
        self.aborting = True
        self._grant.set()

    def on_actor_thread(self) -> bool:
        return threading.current_thread() is self.thread

    def refuse(self, operation: str, reason: str) -> NoReturn:
        self.post(OpRefused(self.actor, operation, reason))
        raise UnsupportedDriverOperation(f"{operation}: {reason}")

    def perform(
        self,
        kind: str,
        text: str,
        params: Any,
        call: Callable[[], Any],
        cursor: psycopg.Cursor | None = None,
        savepoint: str | None = None,
        name_after: Callable[[], str | None] | None = None,
        releases_savepoint: bool = False,
        counter: _CountingParams | None = None,
    ) -> Any:
        if not self.on_actor_thread():
            self.refuse(kind, "a connection cannot be shared between threads")
        if self.granted:
            self.refuse(kind, "a database operation started inside another database operation")
        if self.aborting:
            raise RunAborted()
        self.post(OpRequested(
            self.actor, kind, text, _safe_params(params), savepoint, _provenance(), releases_savepoint,
        ))
        if not self._grant.wait(self.hard_limit_s):
            raise RunAborted()
        self._grant.clear()
        if self.aborting:
            raise RunAborted()
        before = _txn_name(self.conn)
        self.granted = True
        try:
            value = call()
        except Exception as exc:
            self.post(OpCompleted(
                self.actor, before, _txn_name(self.conn), self.conn.autocommit, exception=exc, savepoint=savepoint,
                batch_count=counter.count if counter else None,
            ))
            raise
        finally:
            self.granted = False
        done = self._completion(before, cursor, name_after() if name_after else savepoint)
        done.batch_count = counter.count if counter else None
        self.post(done)
        return value

    def _completion(self, before: str, cursor: psycopg.Cursor | None, savepoint: str | None) -> OpCompleted:
        done = OpCompleted(self.actor, before, _txn_name(self.conn), self.conn.autocommit, savepoint=savepoint)
        if cursor is None:
            return done
        done.rowcount = cursor.rowcount
        done.command_tag = cursor.statusmessage
        if cursor.description:
            done.columns = [d.name for d in cursor.description]
            done.rows, done.rows_total = _peek_rows(cursor)
        return done


class _CountingParams:
    """Counts parameter sets as psycopg takes them. Stays lazy, so an application's iterator fails when it would."""

    def __init__(self, source: Any) -> None:
        self._source = iter(source)
        self.count = 0

    def __iter__(self) -> _CountingParams:
        return self

    def __next__(self) -> Any:
        item = next(self._source)
        self.count += 1
        return item


def _txn_name(conn: psycopg.Connection) -> str:
    return TransactionStatus(conn.pgconn.transaction_status).name


def _peek_rows(cursor: psycopg.Cursor) -> tuple[list[list[Any]], int]:
    """Copy the buffered result for the trace, leaving the application's cursor exactly where it was."""
    factory = cursor.row_factory
    try:
        cursor.row_factory = tuple_row
        rows = cursor.fetchall()
        cursor.scroll(0, "absolute")
    finally:
        cursor.row_factory = factory
    return [[_safe_value(v) for v in row] for row in rows[:MAX_RECORDED_ROWS]], len(rows)


def _safe_value(value: Any) -> Any:
    value = normalize(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<bytes:{len(value)}>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _safe_params(params: Any) -> Any:
    if params is None:
        return None
    if isinstance(params, Mapping):
        return {str(k): _safe_value(v) for k, v in params.items()}
    if isinstance(params, Sequence) and not isinstance(params, (str, bytes)):
        return [_safe_value(v) for v in params]
    return f"<{type(params).__name__}>"


def _provenance() -> dict[str, Any]:
    """The application frame that made this call: module, function, line. No locals and no absolute paths."""
    frame = sys._getframe(2)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if not module.startswith(_DRIVER_MODULES):
            return {"module": module, "function": frame.f_code.co_name, "line": frame.f_lineno}
        frame = frame.f_back
    return {}


# ------------------------------------------------------------------ psycopg subclasses


class SteppedCursor(psycopg.Cursor):
    """Client-side cursor. Results are fully buffered by ``execute``, so fetching needs no scheduling."""

    def _channel(self) -> ActorChannel | None:
        return self._conn._channel  # type: ignore[attr-defined]

    def execute(self, query: Any, params: Any = None, *, prepare: bool | None = None, binary: bool | None = None):
        channel = self._channel()
        if channel is None:
            return super().execute(query, params, prepare=prepare, binary=binary)
        text = _query_text(query, self._conn)
        classified = classify_sql(text)
        if classified.refused:
            channel.refuse("execute", f"{classified.refused} is not supported")
        return channel.perform(
            classified.kind, text, params,
            lambda: super(SteppedCursor, self).execute(query, params, prepare=prepare, binary=binary),
            cursor=self, savepoint=classified.savepoint,
        )

    def executemany(self, query: Any, params_seq: Any, *, returning: bool = False) -> None:
        """One opaque step, using psycopg's own implementation. Nothing inside the batch is individually scheduled."""
        channel = self._channel()
        if channel is None:
            return super().executemany(query, params_seq, returning=returning)
        text = _query_text(query, self._conn)
        classified = classify_sql(text)
        if classified.refused or classified.kind != "sql":
            channel.refuse("executemany", classified.refused or "transaction control can't be a batch")
        counter = _CountingParams(params_seq)
        return channel.perform(
            "opaque_batch", text, None,
            lambda: super(SteppedCursor, self).executemany(query, counter, returning=returning),
            cursor=self, counter=counter,
        )

    def stream(self, query: Any, params: Any = None, **kwargs: Any) -> Iterator[Any]:
        self._refuse("stream", "streaming keeps a result open across scheduling points")

    def copy(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        self._refuse("copy", "COPY is not supported")

    def _refuse(self, operation: str, reason: str) -> NoReturn:
        channel = self._channel()
        if channel is None:
            raise UnsupportedDriverOperation(f"{operation}: {reason}")
        channel.refuse(operation, reason)


def _query_text(query: Any, conn: psycopg.Connection) -> str:
    if isinstance(query, str):
        return query
    if isinstance(query, (bytes, bytearray, memoryview)):
        return bytes(query).decode()
    if isinstance(query, pgsql.Composable):
        return query.as_string(conn)
    return str(query)


class SteppedTransaction(Transaction):
    def __enter__(self):
        channel = self._conn._channel
        if channel is None:
            return super().__enter__()
        outer = self.pgconn.transaction_status == TransactionStatus.IDLE
        self._ds_outer = outer
        text = "BEGIN" if outer else "SAVEPOINT"
        channel.perform(
            "begin" if outer else "savepoint", text, None, lambda: super(SteppedTransaction, self).__enter__(),
            name_after=(lambda: None if outer else self.savepoint_name),
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        channel = self._conn._channel
        if channel is None or self.pgconn.status != ConnStatus.OK:
            return super().__exit__(exc_type, exc_val, exc_tb)
        if channel.aborting:
            return self._abort_exit(exc_type, exc_val, exc_tb)
        commits = not exc_val and not self.force_rollback
        if self._ds_outer:
            kind, text = ("commit", "COMMIT") if commits else ("rollback", "ROLLBACK")
        else:
            kind, text = ("release", "RELEASE") if commits else ("rollback_to", "ROLLBACK TO + RELEASE")
        try:
            return channel.perform(
                kind, text, None, lambda: super(SteppedTransaction, self).__exit__(exc_type, exc_val, exc_tb),
                savepoint=None if self._ds_outer else self.savepoint_name,
                releases_savepoint=not self._ds_outer and not commits,
            )
        except RunAborted:
            self._abort_exit(exc_type, exc_val or RunAborted(), exc_tb)
            raise

    def _abort_exit(self, exc_type, exc_val, exc_tb) -> bool:
        """A stopped run never commits, whatever the application did with the exception."""
        self.force_rollback = True
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        except psycopg.Error:
            return False


class SteppedConnection(psycopg.Connection):
    """A real psycopg connection. With no channel attached (harness setup, teardown) it behaves like its parent."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._channel: ActorChannel | None = None
        self.cursor_factory = SteppedCursor

    def attach(self, channel: ActorChannel) -> None:
        self._channel = channel

    def detach(self) -> None:
        self._channel = None

    def wait(self, gen: Any, *args: Any, **kwargs: Any) -> Any:
        channel = self._channel
        if channel is not None and not (channel.on_actor_thread() and (channel.granted or channel.aborting)):
            gen.close()
            channel.refuse("uninstrumented driver path", "database traffic arrived without a scheduler grant")
        return super().wait(gen, *args, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        if self._channel is not None and (kwargs.get("name") or args):
            self._channel.refuse("server-side cursor", "named cursors keep state across scheduling points")
        return super().cursor(*args, **kwargs)

    def commit(self) -> None:
        if self._channel is None:
            return super().commit()
        self._channel.perform("commit", "COMMIT", None, super().commit)

    def rollback(self) -> None:
        if self._channel is None:
            return super().rollback()
        self._channel.perform("rollback", "ROLLBACK", None, super().rollback)

    @contextmanager
    def transaction(self, savepoint_name: str | None = None, force_rollback: bool = False) -> Iterator[Transaction]:
        with SteppedTransaction(self, savepoint_name, force_rollback) as tx:
            yield tx

    def _refuse_on_actor(self, operation: str, reason: str) -> None:
        if self._channel is not None and self._channel.on_actor_thread():
            self._channel.refuse(operation, reason)

    def close(self) -> None:
        self._refuse_on_actor("close", "the harness owns the connection's lifetime")
        super().close()

    def cancel_safe(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_on_actor("cancel", "cancellation by the application is not scheduled")
        super().cancel_safe(*args, **kwargs)

    def cancel(self) -> None:
        self._refuse_on_actor("cancel", "cancellation by the application is not scheduled")
        super().cancel()

    def pipeline(self) -> Any:
        self._refuse_on_actor("pipeline", "pipeline mode batches statements below the scheduling boundary")
        return super().pipeline()

    def notifies(self, *args: Any, **kwargs: Any) -> Any:
        self._refuse_on_actor("notifies", "LISTEN/NOTIFY is not supported")
        return super().notifies(*args, **kwargs)

    def tpc_begin(self, *args: Any, **kwargs: Any) -> None:
        self._refuse_on_actor("two-phase commit", "two-phase commit is not supported")
        super().tpc_begin(*args, **kwargs)
