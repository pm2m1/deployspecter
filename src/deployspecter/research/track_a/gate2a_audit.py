"""Gate A2.2: an offline audit of what Gate 2A's supervisor does, from PostgreSQL's own log. Research-only; pure.

It doesn't replace, bypass or patch the supervisor. It reads the CSV log after Gate 2A has returned control, and holds
the supervisor's administrative session to a declared, fixed template of the statements frozen ``supervisor.py`` and
``database.py`` send for one run (spelled out here, and checked offline against the frozen code itself). Every claim
it makes carries one of five statuses:

    VERIFIED       the log shows it, exactly, bound to this run
    UNOBSERVABLE   the log can't show it, by design (the role-creation interval, values a query returned)
    REFUTED        the log contradicts it: an extra, missing, reordered or misparameterized operation, a foreign session
    INDETERMINATE  the log stops before it can be decided (a stopped run, an incomplete flush)
    NOT_TESTED     no evidence of this kind exists yet (nothing here has run on PostgreSQL)

A batch with an UNOBSERVABLE claim is never classified as "all SQL audited". REFUTED or INDETERMINATE fail closed.

The supervisor's per-run sequence on its admin session (``SupervisedRunner._execute`` and ``_run_child``):
preflight; once per runner, hardening; the stale-role sweep; the outside-session sweep; prepared transactions; the
reset limits; ``Sandbox.reset``; restored limits; the initial snapshot; the log position and its start marker; the
session counter; role creation with ``log_statement`` off (unlogged); role configuration; supervision polls,
registration and escalation; teardown; the final snapshot; dropping the role; the counter; its end marker and log reads.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from deployspecter.database import RUN_SCHEMA, RUNNER_ROLE

from . import evidence_binding as eb
from . import parent_reconciliation as pr

VERIFIED, UNOBSERVABLE, REFUTED, INDETERMINATE, NOT_TESTED = (
    "VERIFIED", "UNOBSERVABLE", "REFUTED", "INDETERMINATE", "NOT_TESTED")
STATUSES = (VERIFIED, UNOBSERVABLE, REFUTED, INDETERMINATE, NOT_TESTED)
BACKGROUND = pr.BACKGROUND
HARNESS_APP = "ds_gate2a_harness"  # gate_a2_controls.HARNESS_APP; the Sandbox's sessions carry it in their URL

# ------------------------------------------------------------------ the declared template (server-side text)

# Gate 2A's REQUIRED_SETTINGS, in its dict order: preflight reads each one.
PREFLIGHT_SETTINGS = ("cluster_name", "logging_collector", "log_destination", "log_connections", "log_disconnections",
                      "log_statement", "log_parameter_max_length", "log_rotation_age", "log_rotation_size",
                      "autovacuum")
SETTING_SQL = "SELECT current_setting($1, true)"
OVERRIDES_SQL = "SELECT count(*) FROM pg_db_role_setting s, unnest(s.setconfig) c WHERE c LIKE 'log\\_%%'"
DATABASES_SQL = "SELECT datname FROM pg_database WHERE datallowconn"
STALE_ROLES_SQL = "SELECT rolname FROM pg_roles WHERE rolname LIKE 'ds\\_run\\_%%'"
ACTIVITY_SQL = ("SELECT pid, usename, datname, application_name, backend_type, coalesce(left(query, 200), '')\n"
                "FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> ALL($1)")
TERMINATE_SQL = "SELECT pg_terminate_backend($1)"
CANCEL_SQL = "SELECT pg_cancel_backend($1)"
PREPARED_SQL = "SELECT gid FROM pg_prepared_xacts WHERE database = current_database()"
TABLES_SQL = ("SELECT table_name FROM information_schema.tables\n"
              "               WHERE table_schema = $1 AND table_type = 'BASE TABLE' ORDER BY 1")
LOGFILE_SQL = "SELECT pg_current_logfile('csvlog')"
STAT_SQL = "SELECT size FROM pg_stat_file($1, true)"
READ_SQL = "SELECT pg_read_binary_file($1, $2, $3, true)"
COUNTER_SQL = "SELECT sessions FROM pg_stat_database WHERE datname = current_database()"
LOG_OFF_SQL = "SET log_statement = 'none'"
REGISTRATION_SQL = "SELECT pid, usename FROM pg_stat_activity WHERE pid = ANY($1)"
ROLE_COUNT_SQL = "SELECT count(*) FROM pg_roles WHERE rolname = $1"
# Sandbox construction (database.py): ensure_test_database on the initial database, then the admin session.
DB_EXISTS_SQL = "SELECT 1 FROM pg_database WHERE datname = $1"
RUNNER_ROLE_SQL = (f"""DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNNER_ROLE}') THEN
                    CREATE ROLE {RUNNER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
                  END IF;
                END $$""")
ADVISORY_LOCK_SQL = "SELECT pg_advisory_lock($1)"
ADVISORY_LOCK_KEY = 7_420_001
SANDBOX_LOCK_WAIT_MS = 30000
ROLE_SETTINGS = ("search_path", "statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout",
                 "deadlock_timeout")


def template_texts() -> list[str]:
    """Every fixed statement text of the template, for the manifest's digest."""
    return [SETTING_SQL, OVERRIDES_SQL, DATABASES_SQL, STALE_ROLES_SQL, ACTIVITY_SQL, TERMINATE_SQL, CANCEL_SQL,
            PREPARED_SQL, TABLES_SQL, LOGFILE_SQL, STAT_SQL, READ_SQL, COUNTER_SQL, LOG_OFF_SQL, REGISTRATION_SQL,
            ROLE_COUNT_SQL, DB_EXISTS_SQL, RUNNER_ROLE_SQL, ADVISORY_LOCK_SQL]


class AuditError(ValueError):
    """The log can't be read or trusted at all. Nothing is concluded."""


# ------------------------------------------------------------------ the log, with byte offsets


@dataclass(frozen=True)
class Row:
    cells: list[str]
    start: int  # absolute byte offsets in the log file
    end: int

    def __getitem__(self, i: int) -> str:
        return self.cells[i]

    @property
    def session(self) -> str:
        return self.cells[pr.SESSION]

    @property
    def message(self) -> str:
        return self.cells[pr.MESSAGE]


def parse_log(data: bytes, base_offset: int = 0) -> list[Row]:
    """Every row, strictly: a row without 26 columns or unparsable CSV refuses. Offsets are absolute."""
    if data and not data.endswith(b"\n"):
        raise AuditError("the log ends inside a row (an incomplete flush)")
    lines = data.splitlines(keepends=True)
    consumed = [0]

    def feed():
        for line in lines:
            consumed[0] += len(line)
            yield line.decode("utf-8", errors="replace")

    out, start = [], 0
    try:
        for cells in csv.reader(feed(), strict=True):
            if len(cells) != 26:
                raise AuditError(f"a log row with {len(cells)} columns")
            out.append(Row(cells, base_offset + start, base_offset + consumed[0]))
            start = consumed[0]
    except csv.Error as exc:
        raise AuditError(f"unparsable log: {exc}") from None
    if start != len(data):
        raise AuditError("the log ends inside a row (an incomplete flush)")
    return out


def literal(value: Any) -> str:
    """Gate A1's established log forms, plus integer arrays (psycopg sends lists as text arrays; not characterized)."""
    if isinstance(value, (list, tuple)) and all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return "'{" + ",".join(str(v) for v in value) + "}'"
    return pr.literal(value)


def statement(row: Row) -> tuple[str, str, dict[int, str]] | None:
    """(verb, text, parameters) for a statement row, or None."""
    match = re.match(r"^(statement|execute [^:]*): (.*)$", row.message, re.S)
    if not match:
        return None
    return match.group(1), match.group(2), {int(n): v for n, v in pr.PARAMETER.findall(row[pr.DETAIL] or "")}


# ------------------------------------------------------------------ claims and coverage


@dataclass
class Claim:
    scope: str  # "batch", "gap 2", "run <id>", ...
    claim: str
    status: str
    basis: str

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")


@dataclass
class Coverage:
    claims: list[Claim] = field(default_factory=list)

    def add(self, scope: str, claim: str, status: str, basis: str) -> Claim:
        c = Claim(scope, claim, status, basis)
        self.claims.append(c)
        return c

    def extend(self, other: Coverage) -> None:
        self.claims.extend(other.claims)

    def counts(self) -> dict[str, int]:
        return {s: sum(c.status == s for c in self.claims) for s in STATUSES}

    @property
    def classification(self) -> str:
        counts = self.counts()
        if counts[REFUTED]:
            return "REFUTED"
        if counts[INDETERMINATE]:
            return "INDETERMINATE"
        if counts[UNOBSERVABLE] or counts[NOT_TESTED]:
            return "OBSERVABLE_SQL_VERIFIED_WITH_DISCLOSED_GAPS"
        return "ALL_SQL_VERIFIED"

    @property
    def fails_closed(self) -> bool:
        return self.classification in {"REFUTED", "INDETERMINATE"}

    def report(self) -> dict[str, Any]:
        return {"classification": self.classification, "counts": self.counts(),
                "claims": [c.__dict__ for c in self.claims]}


# ------------------------------------------------------------------ one run's context, from trusted inputs only


@dataclass(frozen=True)
class RunContext:
    """What one Gate 2A call is expected to have done. Everything comes from the reviewed manifest, the prepared
    scenario, the harness sessions' own connection info, or the supervisor's in-process trace; nothing from the log."""
    run_id: str
    mode: str  # "stepped" or "plain"
    setup_sql: str
    seed: tuple[tuple[str, tuple[tuple[str, Any], ...]], ...]  # (table, ((column, value), ...)) in order
    tables: tuple[str, ...]
    database: str
    harness_pids: frozenset[int]
    child_sessions: Mapping[str, int]  # trace.evidence.expected_sessions: actors in order, then "monitor"
    first_run: bool
    connectable_databases: frozenset[str]
    reset_lock_ms: int
    reset_statement_ms: int
    role_settings: Mapping[str, Any]  # search_path and the four LiveLimits timeouts
    log_path: str
    escalation: tuple[str, ...] = ()  # trace.evidence.escalation steps that weren't skipped
    max_polls: int = 10000
    max_log_reads: int = 2600
    max_sweep_rounds: int = 1000
    flags: tuple[str, ...] = ()  # trace.evidence flag details
    # The reviewed manifest's initial_rows: an expectation independent of the prepared scenario's own seed.
    expected_rows: tuple[tuple[str, tuple[tuple[str, Any], ...]], ...] = ()

    @property
    def role(self) -> str:
        return f"ds_run_{self.run_id}"

    @property
    def actor_pids(self) -> list[int]:
        return [pid for name, pid in self.child_sessions.items() if name != "monitor"]


def seed_tuples(seed: Iterable[Any]) -> tuple[tuple[str, tuple[tuple[str, Any], ...]], ...]:
    return tuple((r.table, tuple((v.column, v.value) for v in r.values)) for r in seed)


def reset_template(setup_sql: str, seed: Iterable[tuple[str, tuple[tuple[str, Any], ...]]]) -> list[tuple[str, tuple]]:
    """Sandbox.reset, server-side: Gate A1's reconciled reset, then the three GRANTs."""
    out: list[tuple[str, tuple]] = [(f"DROP SCHEMA IF EXISTS {RUN_SCHEMA} CASCADE", ()),
                                    (f"CREATE SCHEMA {RUN_SCHEMA}", ()), (f"SET search_path = {RUN_SCHEMA}", ()),
                                    ("BEGIN", ()), (setup_sql, ())]
    for table, values in seed:
        cols = [c for c, _ in values]
        out.append((f'INSERT INTO "{table}" (' + ", ".join(f'"{c}"' for c in cols) + ") VALUES ("
                    + ", ".join(f"${i}" for i in range(1, len(cols) + 1)) + ")", tuple(v for _, v in values)))
    out += [("COMMIT", ()), (f"GRANT USAGE ON SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}", ()),
            (f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}", ()),
            (f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}", ())]
    return out


# ------------------------------------------------------------------ the admin-session matcher


class _Stop(Exception):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


class AdminRun:
    """Walks one run's admin-session rows through the template, phase by phase. A deviation refutes that phase and
    leaves every later phase INDETERMINATE; rows that end early leave the rest INDETERMINATE."""

    def __init__(self, rows: list[Row], ctx: RunContext, coverage: Coverage) -> None:
        self.rows, self.ctx, self.cov, self.i = rows, ctx, coverage, 0
        self.scope = f"run {ctx.run_id}"
        self.marks: dict[str, Row] = {}  # named rows other checks need (markers, reset, snapshot, role interval)

    # -- primitives

    def _peek(self) -> tuple[Row, tuple[str, str, dict[int, str]]] | None:
        if self.i >= len(self.rows):
            return None
        row = self.rows[self.i]
        parsed = statement(row)
        if row[pr.SEVERITY] != "LOG" or parsed is None:
            raise _Stop(REFUTED, f"{row[pr.SEVERITY]} row on the admin session: {row.message[:80]}")
        if not row.message.startswith(pr.UNNAMED):
            raise _Stop(REFUTED, f"admin statement logged under a prepared-statement name: "
                                 f"{row.message.split(':', 1)[0]} (B5)")
        return row, parsed

    def _is(self, text: str, params: tuple | None = ()) -> bool:
        got = self._peek()
        if got is None:
            return False
        _row, (_verb, logged, logged_params) = got
        if logged != text:
            return False
        if params is None:
            return True
        return logged_params == {n: literal(v) for n, v in enumerate(params, 1)}

    def take(self, text: str, params: tuple = (), what: str = "") -> Row:
        got = self._peek()
        if got is None:
            raise _Stop(INDETERMINATE, f"the log stops before {what or text[:60]}")
        row, (_verb, logged, logged_params) = got
        if logged != text:
            raise _Stop(REFUTED, f"expected {what or text[:60]!r}, logged {logged[:80]!r}")
        wanted = {n: literal(v) for n, v in enumerate(params, 1)}
        if logged_params != wanted:
            raise _Stop(REFUTED, f"{what or text[:60]}: unapproved parameters")
        self.i += 1
        return row

    def _params(self) -> dict[int, str]:
        got = self._peek()
        return got[1][2] if got else {}

    def take_activity(self) -> None:
        got = self._peek()
        if got is None:
            raise _Stop(INDETERMINATE, "the log stops before an activity poll")
        if got[1][1] != ACTIVITY_SQL:
            raise _Stop(REFUTED, f"expected an activity poll, logged {got[1][1][:80]!r}")
        if not self._harness_param(got[1][2]):
            raise _Stop(REFUTED, "an activity poll excluded sessions other than the supervisor's own")
        self.i += 1

    def _harness_param(self, params: dict[int, str]) -> bool:
        value = params.get(1, "")
        if len(params) != 1 or not re.fullmatch(r"'\{[0-9,]*\}'", value):
            return False
        pids = {int(p) for p in value[2:-2].split(",") if p}
        return pids == set(self.ctx.harness_pids)

    def done(self) -> bool:
        return self.i >= len(self.rows)

    # -- phases

    def walk(self) -> None:
        c = self.ctx
        phases = [
            ("preflight", "the logging and disposable-cluster settings Gate 2A requires were read before any reset",
             self.preflight),
            *([("hardening", "connect privilege was revoked from PUBLIC on exactly the expected databases",
                self.harden)] if c.first_run else []),
            ("outside-session sweep", "no client session outside the supervisor's own existed when the reset began; "
             "no earlier run role could log in; no prepared transaction was left", self.sweep),
            ("reset", "the schema was rebuilt from the pinned setup SQL and exactly the bridged seed, committed",
             self.reset),
            ("initial snapshot", "the supervisor read the state after the reset, before any session of the run "
             "existed", self.initial_snapshot),
            ("window opened", "Gate 2A positioned its log and wrote its start marker for this run id", self.window),
            ("role creation", "CREATE ROLE and RESET log_statement aren't logged (log_statement='none' on the admin "
             "session only); what ran in that interval can't be seen in the log (B2)", self.role_creation),
            ("role configuration", "the run role got CONNECT on the test database and exactly the reviewed settings",
             self.role_configuration),
            ("supervision", "only polls, one registration check and the escalation steps the trace records",
             self.supervision),
            ("teardown", "the role was disabled and the supervisor ended only this run's sessions", self.teardown),
            ("final snapshot", "the supervisor read the state after teardown", self.final_snapshot),
            ("role removal", "the run role was dropped", self.drop_role),
            ("window closed", "Gate 2A wrote its end marker and read its log", self.close_window),
        ]
        stopped = None
        for name, guarantee, fn in phases:
            if stopped is not None:
                self.cov.add(self.scope, name, INDETERMINATE, f"not reached: {stopped}")
                continue
            try:
                fn()
            except _Stop as stop:
                self.cov.add(self.scope, name, stop.status, stop.detail)
                stopped = f"{name} {stop.status.lower()}"
                continue
            self.cov.add(self.scope, name, UNOBSERVABLE if name == "role creation" else VERIFIED, guarantee)
        if stopped is None and not self.done():
            self.cov.add(self.scope, "nothing else on the admin session", REFUTED,
                         f"{len(self.rows) - self.i} admin row(s) after the template ended")
        elif stopped is None:
            self.cov.add(self.scope, "nothing else on the admin session", VERIFIED,
                         "every admin row of this call is in the template")

    def preflight(self) -> None:
        for name in PREFLIGHT_SETTINGS:
            self.take(SETTING_SQL, (name,), f"the {name} setting read")
        self.take(OVERRIDES_SQL, (), "the logging-override count")

    def harden(self) -> None:
        self.take(DATABASES_SQL, (), "the connectable-database list")
        seen = []
        while (got := self._peek()) is not None and got[1][1].startswith("REVOKE ALL ON DATABASE "):
            m = re.fullmatch(r'REVOKE ALL ON DATABASE "([a-z_][a-z0-9_]*)" FROM PUBLIC', got[1][1])
            if not m or got[1][2]:
                raise _Stop(REFUTED, f"an unexpected revoke: {got[1][1][:80]}")
            seen.append(m.group(1))
            self.i += 1
        if sorted(seen) != sorted(self.ctx.connectable_databases) or len(set(seen)) != len(seen):
            raise _Stop(REFUTED, f"revoked on {sorted(seen)}, expected {sorted(self.ctx.connectable_databases)}")

    def sweep(self) -> None:
        self.take(STALE_ROLES_SQL, (), "the stale-role query")
        if (got := self._peek()) and got[1][1].startswith("ALTER ROLE "):
            raise _Stop(REFUTED, "a stale run role existed and was disabled: an earlier run didn't clean up")
        self.take_activity()
        if self._is(TERMINATE_SQL, None):
            pid = self._params().get(1)
            raise _Stop(REFUTED, f"an outside session (pid {pid}) was present at the supervisor's sweep and was "
                                 "terminated; a Gate A2 reader or other client was connected")
        self.take(PREPARED_SQL, (), "the prepared-transaction query")
        if (got := self._peek()) and got[1][1].startswith("ROLLBACK PREPARED"):
            raise _Stop(REFUTED, "a prepared transaction was left before the reset")
        self.take(f"SET lock_timeout = {self.ctx.reset_lock_ms}", (), "the reset lock timeout")
        self.take(f"SET statement_timeout = {self.ctx.reset_statement_ms}", (), "the reset statement timeout")

    def reset(self) -> None:
        steps = reset_template(self.ctx.setup_sql, self.ctx.seed)
        for n, (text, params) in enumerate(steps):
            row = self.take(text, params, f"reset statement {n + 1} of {len(steps)}")
            if n == 0:
                self.marks["reset_start"] = row
            if text == "COMMIT":
                self.marks["reset_commit"] = row
        self.take("SET lock_timeout = 0", (), "restoring the lock timeout")
        self.take("SET statement_timeout = 0", (), "restoring the statement timeout")

    def _snapshot(self, key: str) -> None:
        self.marks[key] = self.take(TABLES_SQL, (RUN_SCHEMA,), "the table list")
        for table in sorted(self.ctx.tables):
            self.marks[key + "_end"] = self.take(f'SELECT * FROM "{table}" ORDER BY 1', (), f"reading {table}")

    def initial_snapshot(self) -> None:
        self._snapshot("initial")

    def window(self) -> None:
        self.take(LOGFILE_SQL, (), "the log file query")
        self.take(STAT_SQL, (self.ctx.log_path,), "the log size query")
        self.marks["start_marker"] = self.take(f"SELECT 'ds_mark_start_{self.ctx.run_id}'", (), "the start marker")
        self.take(COUNTER_SQL, (), "the session counter")

    def role_creation(self) -> None:
        self.marks["log_off"] = self.take(LOG_OFF_SQL, (), "turning statement logging off")
        nxt = self._peek()
        if nxt is None:
            raise _Stop(INDETERMINATE, "the log stops inside the unlogged interval; logging may not have been restored")
        if nxt[1][1] != f'GRANT CONNECT ON DATABASE "{self.ctx.database}" TO "{self.ctx.role}"':
            raise _Stop(REFUTED, "the first logged admin statement after the unlogged interval isn't this run's "
                                 "GRANT CONNECT: logging wasn't restored as the source does, or something else ran")
        self.marks["log_on"] = nxt[0]

    def role_configuration(self) -> None:
        role = self.ctx.role
        self.take(f'GRANT CONNECT ON DATABASE "{self.ctx.database}" TO "{role}"', (), "GRANT CONNECT")
        for name in ROLE_SETTINGS:
            value = self.ctx.role_settings[name]
            rendered = f"'{value}'" if isinstance(value, str) else str(value)
            self.take(f'ALTER ROLE "{role}" SET "{name}" = {rendered}', (), f"the role's {name}")

    def supervision(self) -> None:
        registered = list(self.ctx.child_sessions.values())
        polls, registration = 0, False
        groups = {"cancel": CANCEL_SQL, "terminate": TERMINATE_SQL}
        wanted = [s for s in self.ctx.escalation if s in groups]
        teardown_start = f'ALTER ROLE "{self.ctx.role}" NOLOGIN'
        while (got := self._peek()) is not None and got[1][1] != teardown_start:
            text = got[1][1]
            if text == ACTIVITY_SQL:
                self.take_activity()
                polls += 1
                if polls > self.ctx.max_polls:
                    raise _Stop(REFUTED, f"more than {self.ctx.max_polls} supervision polls")
            elif text == REGISTRATION_SQL and not registration and registered:
                self.take(REGISTRATION_SQL, (registered,), "the registration check")
                registration = True
            elif text in groups.values() and wanted:
                step = wanted.pop(0)
                for pid in self.ctx.actor_pids:
                    self.take(groups[step], (pid,), f"the {step} step for pid {pid}")
            else:
                raise _Stop(REFUTED, f"unexpected during supervision: {text[:80]!r}")
        if got is None:
            raise _Stop(INDETERMINATE, "the log stops during supervision")
        if registered and not registration:
            raise _Stop(REFUTED, "the child registered sessions but no registration check was logged")
        if wanted and self.ctx.actor_pids:
            raise _Stop(REFUTED, f"escalation steps the trace records weren't logged: {wanted}")

    def teardown(self) -> None:
        self.take(f'ALTER ROLE "{self.ctx.role}" NOLOGIN', (), "disabling the role")
        self.take_activity()
        rounds = 0
        children = set(self.ctx.child_sessions.values())
        while self._is(TERMINATE_SQL, None):
            pid = self._params().get(1, "")
            if not re.fullmatch(r"'\d+'", pid) or int(pid[1:-1]) not in children:
                raise _Stop(REFUTED, f"teardown terminated pid {pid}, which isn't one of this run's sessions")
            self.i += 1
            self.take_activity()
            rounds += 1
            if rounds > self.ctx.max_sweep_rounds:
                raise _Stop(REFUTED, "more teardown sweep rounds than the quiescence limit allows")
        self.take(PREPARED_SQL, (), "the prepared-transaction query")
        if (got := self._peek()) and got[1][1].startswith("ROLLBACK PREPARED"):
            if not any("prepared transactions left behind" in f for f in self.ctx.flags):
                raise _Stop(REFUTED, "prepared transactions were rolled back but the trace doesn't record it")
            while (got := self._peek()) and got[1][1].startswith("ROLLBACK PREPARED"):
                self.i += 1
        self.take_activity()

    def final_snapshot(self) -> None:
        self._snapshot("final")

    def drop_role(self) -> None:
        self.take(f'DROP OWNED BY "{self.ctx.role}"', (), "DROP OWNED")
        self.marks["drop_role"] = self.take(f'DROP ROLE "{self.ctx.role}"', (), "DROP ROLE")
        self.take(ROLE_COUNT_SQL, (self.ctx.role,), "the role count")
        self.take(COUNTER_SQL, (), "the session counter")

    def close_window(self) -> None:
        self.marks["end_marker"] = self.take(f"SELECT 'ds_mark_end_{self.ctx.run_id}'", (), "the end marker")
        reads, offset = 0, None
        while self._is(LOGFILE_SQL):
            self.take(LOGFILE_SQL, (), "the log file query")
            self.take(STAT_SQL, (self.ctx.log_path,), "the log size query")
            got = self._peek()
            if got is None:
                raise _Stop(INDETERMINATE, "the log stops before Gate 2A's log read")
            params = got[1][2]
            if got[1][1] != READ_SQL or len(params) != 3 or params[1] != literal(self.ctx.log_path) \
                    or not all(re.fullmatch(r"'\d+'", params[k]) for k in (2, 3)):
                raise _Stop(REFUTED, "Gate 2A's log read isn't the expected one")
            this = int(params[2][1:-1])
            if offset is not None and this != offset:
                raise _Stop(REFUTED, "Gate 2A's log reads don't start from one position")
            offset = this
            self.marks.setdefault("read_offsets", []).append((this, this + int(params[3][1:-1])))  # type: ignore
            self.i += 1
            reads += 1
            if reads > self.ctx.max_log_reads:
                raise _Stop(REFUTED, "more log reads than Gate 2A's wait allows")
        if reads == 0:
            raise _Stop(INDETERMINATE if self.done() else REFUTED, "Gate 2A didn't read its log after its end marker")


# ------------------------------------------------------------------ state claims derived from the log


def seeded_rows_from_log(rows: list[Row], ctx: RunContext) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """The rows the logged seed INSERTs carry, as (table, ((column, logged literal), ...))."""
    out = []
    for row in rows:
        parsed = statement(row)
        if parsed is None:
            continue
        m = re.fullmatch(r'INSERT INTO "([a-z_][a-z0-9_]*)" \(([^)]*)\) VALUES \(([^)]*)\)', parsed[1])
        if m:
            cols = [c.strip().strip('"') for c in m.group(2).split(",")]
            out.append((m.group(1), tuple((c, parsed[2].get(i)) for i, c in enumerate(cols, 1))))
    return out


def state_claims(run: AdminRun, trace_initial: Mapping[str, Any] | None, verified_initial_digest: str | None,
                 child_first_row: int | None, cov: Coverage) -> None:
    """The per-run initial-state assurance, split into what the log shows and what it can't."""
    ctx, scope, m = run.ctx, run.scope, run.marks
    if "reset_commit" not in m:
        cov.add(scope, "initial state derived from the logged reset", INDETERMINATE, "the reset wasn't matched")
    else:
        reset_rows = [r for r in run.rows if m["reset_start"].start <= r.start <= m["reset_commit"].start]
        logged = sorted((t, sorted(vals)) for t, vals in seeded_rows_from_log(reset_rows, ctx))
        expected = sorted((t, sorted((c, literal(v)) for c, v in vals)) for t, vals in ctx.expected_rows)
        same = bool(expected) and logged == expected
        cov.add(scope, "initial state derived from the logged reset", VERIFIED if same else REFUTED,
                "the logged DROP/CREATE SCHEMA, pinned setup SQL and seed INSERT parameters, committed with no error "
                "row, give exactly the manifest's reviewed initial rows" if same
                else "the logged seed rows aren't the manifest's reviewed initial rows")
    if "initial_end" in m:
        before_child = child_first_row is None or m["initial_end"].start < child_first_row
        cov.add(scope, "initial snapshot taken after the reset and before the run's first session",
                VERIFIED if before_child else REFUTED,
                "log order on the admin session; no other client wrote in between (gap attribution)")
    cov.add(scope, "initial snapshot values independently observed", UNOBSERVABLE,
            "a query's results aren't logged; no other session may open inside the protected window to re-read them")
    if trace_initial is None or verified_initial_digest is None:
        cov.add(scope, "initial snapshot equals the verified initial state", INDETERMINATE, "no trace or digest")
    else:
        same = eb.canonical_digest(trace_initial) == verified_initial_digest
        cov.add(scope, "initial snapshot equals the verified initial state", VERIFIED if same else REFUTED,
                "the supervisor's own in-process read (trusted control plane), compared with the digest the "
                "construction batch verified independently")
    cov.add(scope, "state unchanged between the initial snapshot and the worker's first statement", UNOBSERVABLE,
            "other sessions are excluded by the log, but the admin session's unlogged role-creation interval "
            "(B2) lies between the snapshot and the worker")


# ------------------------------------------------------------------ child sessions of one run


def child_claims(rows: list[Row], ctx: RunContext, start: Row | None, end: Row | None, call: Call,
                 terminated: set[int], cov: Coverage) -> set[str]:
    """Attribute this run's child sessions: its role, its application names, inside its window. Returns the session
    ids attributed. Statement-level correctness is Gate 2A's own reconciliation, so it is taken from the trace."""
    scope, names = f"run {ctx.run_id}", {f"ds:{ctx.run_id}:{n}" for n in ctx.child_sessions}
    halted = call.halted
    by_session: dict[str, list[Row]] = {}
    for r in rows:
        by_session.setdefault(r.session, []).append(r)
    attributed, problems = set(), []
    pids = {str(p) for p in ctx.child_sessions.values()}
    for session, srows in by_session.items():
        users = {r[pr.USER] for r in srows if r[pr.USER]}
        apps = {r[pr.APPLICATION] for r in srows if r[pr.APPLICATION]}
        if users != {ctx.role} and not (apps and apps <= names):
            if halted and all(r.message.startswith("connection received:") for r in srows) and users == set():
                attributed.add(session)  # a cancel request's probe, on a stopped run only
            continue
        attributed.add(session)
        if users != {ctx.role} or not apps <= names or {r[pr.DATABASE] for r in srows if r[pr.DATABASE]} != {
                ctx.database}:
            problems.append(f"session {session} has another role, application or database")
        if start is None or end is None or not all(start.start < r.start < end.start for r in srows):
            problems.append(f"session {session} is outside Gate 2A's window for this run")
        for r in srows:
            if r[pr.SEVERITY] in {"FATAL", "ERROR"} and not (int(r[pr.PID]) in terminated or halted):
                problems.append(f"session {session} has an unexplained {r[pr.SEVERITY]} row")
        if {r[pr.PID] for r in srows} - pids:
            problems.append(f"session {session} isn't one the supervisor registered")
    if problems:
        cov.add(scope, "child sessions attributed", REFUTED, "; ".join(problems[:3]))
    else:
        cov.add(scope, "child sessions attributed", VERIFIED,
                "every session of the run's role is inside its window with its registered pid and name")
    kinds = set(call.flag_kinds)
    if "unmanaged_activity" in kinds:
        cov.add(scope, "child statements reconciled", REFUTED, "Gate 2A's own checks report unmanaged activity")
    elif not call.reconciled or kinds:
        cov.add(scope, "child statements reconciled", INDETERMINATE,
                f"the run is flagged {sorted(kinds)} or wasn't reconciled; it isn't scoreable")
    else:
        cov.add(scope, "child statements reconciled", VERIFIED,
                "Gate 2A's own per-run log reconciliation (serverlog.reconcile), clean, from the trace")
    return attributed


# ------------------------------------------------------------------ claims that no offline work can establish


def standing_claims(cov: Coverage) -> None:
    cov.add("batch", "the template and fakes agree with PostgreSQL 16's real log of a supervised run", NOT_TESTED,
            "no Gate 2A run has been made under Gate A2's reader; the template is checked against frozen code only")
    cov.add("batch", "integer-array parameters log as '{a,b}'", NOT_TESTED,
            "Gate A1 characterized scalars only; arrays come from psycopg's text dumper and aren't characterized")
    cov.add("batch", "role catalog before and after each run (option A)", NOT_TESTED,
            "proposed, not implemented")


# ------------------------------------------------------------------ gaps between reader segments (B3, B6)

CONNECTION_ROWS = ("connection received:", "connection authenticated:", "connection authorized:")


@dataclass(frozen=True)
class Harness:
    """Gate 2A's Sandbox sessions, identified by the backend pids psycopg reports for them (conn.info)."""
    admin_pid: int
    monitor_pid: int
    user: str
    database: str
    initial_database: str


@dataclass
class Call:
    """One Gate 2A call between two reader segments, as the supervisor's in-process trace describes it."""
    ctx: RunContext | None  # None when the call raised before Gate 2A chose a run id
    error: str | None = None  # the exception the call raised, if any
    reconciled: bool = False  # Gate 2A's own log reconciliation ran (trace.evidence.reconciliation)
    flag_kinds: tuple[str, ...] = ()  # trace.evidence.flags kinds
    halted: bool = False
    trace_initial: Mapping[str, Any] | None = None
    verified_initial_digest: str | None = None


def _sessions(rows: list[Row]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for r in rows:
        out.setdefault(r.session, []).append(r)
    return out


def _background(srows: list[Row]) -> bool:
    return all(r[pr.BACKEND] in BACKGROUND and BACKGROUND[r[pr.BACKEND]].match(r.message) and r[pr.SEVERITY] == "LOG"
               for r in srows)


READER_FILE_READS = (LOGFILE_SQL, STAT_SQL, READ_SQL)  # what Gate A1's reader may send outside its markers


def _reader_reads_only(rows: list[Row]) -> bool:
    for r in rows:
        parsed = statement(r)
        if parsed is None or parsed[1] not in READER_FILE_READS or r[pr.SEVERITY] != "LOG" \
                or not r.message.startswith(pr.UNNAMED):
            return False
    return True


def _reader_boundaries(scope: str, by: dict[str, list[Row]], prev_reader: str | None, next_reader: str | None,
                       first_gate2a: Row | None, last_gate2a: Row | None, cov: Coverage) -> set[str]:
    """The previous reader may only read the log file after its end marker and then disconnect, before Gate 2A's first
    row. The next reader may only connect and position the log, after Gate 2A's last row."""
    done: set[str] = set()
    if prev_reader is not None:
        rows = by.get(prev_reader, [])
        done.add(prev_reader)
        claim = "the previous reader closed before Gate 2A began"
        if any(r[pr.SEVERITY] == "FATAL" for r in rows):
            cov.add(scope, claim, REFUTED,
                    "the previous reader was terminated: it was still connected at the supervisor's sweep")
        elif not rows or not rows[-1].message.startswith("disconnection:"):
            cov.add(scope, claim, INDETERMINATE,
                    "the previous reader's disconnection isn't in this gap; whether it was gone can't be decided")
        elif not _reader_reads_only(rows[:-1]):
            cov.add(scope, claim, REFUTED, "the previous reader did more than read the log file and disconnect")
        elif first_gate2a is not None and rows[-1].start > first_gate2a.start:
            cov.add(scope, claim, REFUTED,
                    "the previous reader's disconnection is logged after the supervisor's first statement")
        else:
            cov.add(scope, claim, VERIFIED,
                    "its disconnection (logged once its backend has left pg_stat_activity) precedes every Gate 2A row")
    if next_reader is not None:
        rows = by.get(next_reader, [])
        done.add(next_reader)
        claim = "the next reader connected only after Gate 2A returned"
        if [r.message.split(":")[0] + ":" for r in rows[:3]] != list(CONNECTION_ROWS) \
                or not _reader_reads_only(rows[3:]):
            cov.add(scope, claim, REFUTED,
                    "the next reader's rows in the gap aren't its connection records and log-file reads")
        elif last_gate2a is not None and rows[0].start < last_gate2a.start:
            cov.add(scope, claim, REFUTED, "the next reader connected before Gate 2A's last logged row")
        else:
            cov.add(scope, claim, VERIFIED, "its connection records follow every Gate 2A row of the gap")
    return done


def audit_run_gap(rows: list[Row], *, index: int, prev_reader: str | None, next_reader: str | None,
                  harness: Harness, call: Call, cov: Coverage) -> AdminRun | None:
    """Everything between two reader segments around one Gate 2A call. Every session must be attributed."""
    scope = f"gap {index}"
    by = _sessions(rows)
    admin = [r for r in rows if r[pr.PID] == str(harness.admin_pid)]
    monitor = [r for r in rows if r[pr.PID] == str(harness.monitor_pid)]
    run = None
    attributed: set[str] = {r.session for r in admin} | {r.session for r in monitor}
    if monitor:
        cov.add(scope, "Gate 2A's monitor session stayed idle", REFUTED, f"{len(monitor)} monitor row(s)")
    else:
        cov.add(scope, "Gate 2A's monitor session stayed idle", VERIFIED, "no monitor row in the gap")
    children: set[str] = set()
    if call.error is not None:
        cov.add(scope, "the Gate 2A call returned", INDETERMINATE, f"it raised {call.error}; nothing after it runs")
    if call.ctx is not None:
        run = AdminRun(admin, call.ctx, cov)
        run.walk()
        start, end = run.marks.get("start_marker"), run.marks.get("end_marker")
        signalled = [statement(r) for r in admin]
        terminated = {int(v[1:-1]) for parsed in signalled if parsed and parsed[1] in (TERMINATE_SQL, CANCEL_SQL)
                      for v in parsed[2].values() if re.fullmatch(r"'\d+'", v)}
        children = child_claims([r for r in rows if r.session not in attributed], call.ctx, start, end, call,
                                terminated, cov)
        first_child = min((r.start for r in rows if r.session in children), default=None)
        state_claims(run, call.trace_initial, call.verified_initial_digest, first_child, cov)
    elif admin:
        cov.add(scope, "admin rows of a call without a run id", INDETERMINATE,
                f"{len(admin)} admin row(s); the call stopped before Gate 2A chose a run id")
    gate2a = [r for r in rows if r.session in attributed | children]
    readers = _reader_boundaries(scope, by, prev_reader, next_reader, gate2a[0] if gate2a else None,
                                 gate2a[-1] if gate2a else None, cov)
    foreign = [s for s, srows in by.items() if s not in attributed | children | readers and not _background(srows)]
    if foreign:
        cov.add(scope, "every session in the gap attributed", REFUTED,
                f"{len(foreign)} unattributed session(s): " + ", ".join(
                    f"{s} ({sorted({r[pr.APPLICATION] for r in by[s]} - {''}) or 'no name'})" for s in foreign[:3]))
    else:
        cov.add(scope, "every session in the gap attributed", VERIFIED,
                "readers, the supervisor's admin and monitor sessions, this run's children and the checkpointer only")
    return run


def audit_bootstrap_gap(rows: list[Row], *, index: int, prev_reader: str | None, next_reader: str | None,
                        harness: Harness, cov: Coverage) -> None:
    """Sandbox construction (B6): ensure_test_database on the initial database, then the admin and monitor sessions.
    What the operator did before the batch (container, initdb, CREATE DATABASE) is outside the log's reach."""
    scope = f"gap {index} (sandbox bootstrap)"
    by = _sessions(rows)
    admin_session = next((r.session for r in rows if r[pr.PID] == str(harness.admin_pid)), None)
    monitor_session = next((r.session for r in rows if r[pr.PID] == str(harness.monitor_pid)), None)
    ensure = [s for s, srows in by.items() if any(r[pr.DATABASE] == harness.initial_database for r in srows)
              and s not in {admin_session, monitor_session}]
    problems = []

    def body(srows: list[Row]) -> list[tuple[str, dict[int, str]]]:
        out = []
        for r in srows:
            parsed = statement(r)
            if parsed:
                out.append((parsed[1], parsed[2]))
                if not r.message.startswith(pr.UNNAMED):
                    problems.append("a bootstrap statement was logged under a prepared-statement name")
            elif not r.message.startswith((*CONNECTION_ROWS, "disconnection:")):
                problems.append(f"unexplained bootstrap row: {r.message[:60]}")
            if r[pr.SEVERITY] != "LOG":
                problems.append(f"{r[pr.SEVERITY]} row during bootstrap")
            if r[pr.USER] and r[pr.USER] != harness.user:
                problems.append("a bootstrap session connected as another user")
        return out

    def connected(srows: list[Row], database: str) -> bool:
        auth = next((r for r in srows if r.message.startswith("connection authorized:")), None)
        return auth is not None and auth.message == (f"connection authorized: user={harness.user} "
                                                     f"database={database} application_name={HARNESS_APP}")

    if len(ensure) != 1:
        problems.append(f"{len(ensure)} sessions on the initial database; ensure_test_database opens exactly one")
    else:
        srows = by[ensure[0]]
        if not connected(srows, harness.initial_database):
            problems.append("ensure_test_database's session isn't the harness's")
        got = body(srows)
        if any(t.startswith("CREATE DATABASE") for t, _ in got):
            problems.append("CREATE DATABASE ran: the test database didn't exist, which the manifest requires")
        if got != [(DB_EXISTS_SQL, {1: literal(harness.database)}), (RUNNER_ROLE_SQL, {})]:
            problems.append("ensure_test_database's statements aren't the template's")
        if not srows[-1].message.startswith("disconnection:"):
            problems.append("ensure_test_database's session didn't disconnect inside the gap")
    for name, session, wanted in (("admin", admin_session, [("SET lock_timeout = 30000", {}),
                                                            (ADVISORY_LOCK_SQL, {1: literal(ADVISORY_LOCK_KEY)}),
                                                            ("SET lock_timeout = 0", {})]),
                                  ("monitor", monitor_session, [])):
        if session is None:
            problems.append(f"the harness {name} session's connection isn't in the gap")
            continue
        if not connected(by[session], harness.database):
            problems.append(f"the harness {name} session isn't the admin user on the test database")
        if body(by[session]) != wanted:
            problems.append(f"the harness {name} session's statements aren't the template's")
    cov.add(scope, "Sandbox construction statements", REFUTED if problems else VERIFIED,
            "; ".join(dict.fromkeys(problems)) if problems else
            "ensure_test_database's existence check and runner-role DO block, the admin session's advisory lock, "
            "the monitor's connection: exactly the template")
    cov.add(scope, "the runner role's attributes after the DO block", UNOBSERVABLE,
            "a DO block logs as one statement; whether it created deployspecter_runner, and how, isn't logged")
    cov.add(scope, "container start, initdb and the operator's CREATE DATABASE", UNOBSERVABLE,
            "before the batch's first log position; outside the log this audit reads")
    readers = _reader_boundaries(scope, by, prev_reader, next_reader,
                                 min((r for r in rows if r.session not in {prev_reader, next_reader}),
                                     key=lambda r: r.start, default=None),
                                 max((r for r in rows if r.session not in {prev_reader, next_reader}),
                                     key=lambda r: r.start, default=None), cov)
    known = {s for s in (admin_session, monitor_session) if s} | set(ensure) | readers
    foreign = [s for s, srows in by.items() if s not in known and not _background(srows)]
    cov.add(scope, "every session in the gap attributed", REFUTED if foreign else VERIFIED,
            f"{len(foreign)} unattributed session(s)" if foreign else "readers, the ensure session, admin, monitor")


# ------------------------------------------------------------------ the whole batch


def line_completeness(rows: list[Row], cov: Coverage) -> None:
    """Each session's log lines must be consecutive. A gap means rows are missing (a lost or incomplete flush)."""
    last: dict[str, int] = {}
    gaps = []
    for r in rows:
        try:
            n = int(r[pr.LINE])
        except ValueError:
            gaps.append(f"{r.session}: no line number")
            continue
        if r.session in last and n != last[r.session] + 1:
            gaps.append(f"{r.session}: line {last[r.session]} then {n}")
        last[r.session] = n
    cov.add("batch", "every session's log lines are consecutive", REFUTED if gaps else VERIFIED,
            "; ".join(gaps[:3]) if gaps else "no session skips a line between the batch's first and last marker")


def marker_row(rows: list[Row], token: str) -> Row:
    found = [r for r in rows if r.message == f"statement: {pr.marker_sql(token)}"]
    if len(found) != 1:
        raise AuditError(f"marker {token} is logged {len(found)} times")
    return found[0]
