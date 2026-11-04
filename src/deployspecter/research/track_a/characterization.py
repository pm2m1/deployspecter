"""Gate A1: minimal PostgreSQL characterization, pure statement builders and judges. Research-only; never run yet.

Gate A1 is separate from the buggy/fixed execution and replay (Gate A2). It uses no Gate 2A supervisor and no
release ``Sandbox``. Every database statement runs on a tagged parent session whose statements are reconciled from
the server log, and a whole-batch audit attributes every logged session. Its purpose is to characterize:

* the parent catalog export and its independent re-read;
* a reset done on a reconciled session, instead of an unaudited trusted call;
* independent typed state reads of the bridged initial row;
* psycopg 3 value binding and decoding (bridge assumptions D1 to D3).

The parent is a superuser. Nothing here is a least-privilege or hostile-code boundary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from deployspecter.live.supervisor import REQUIRED_SETTINGS

from . import parent_reconciliation as pr

SCHEMA, CHAR_SCHEMA = "ds_run", "ds_char"
NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
LOG_READER_APP = pr.LOG_READER_APP

# Beyond Gate 2A's own requirements: settings that would add log rows the reconciler doesn't expect.
A1_SETTINGS = {"log_duration": "off", "log_min_duration_statement": "-1", "log_min_duration_sample": "-1",
               "log_transaction_sample_rate": "0"}
EXPECTED_SETTINGS = {**REQUIRED_SETTINGS, **A1_SETTINGS}

PREFLIGHT = (
    "SELECT name::text, setting::text FROM pg_settings WHERE name IN ("
    + ", ".join(f"'{n}'" for n in sorted(EXPECTED_SETTINGS)) + ") ORDER BY name",
    "SELECT count(*) FROM pg_db_role_setting s, unnest(s.setconfig) c WHERE c LIKE 'log\\_%'",
    "SELECT current_database()::text, current_user::text, "
    "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user), current_setting('server_version_num')::int",
)


def judge_preflight(rows: list[list[tuple]], *, database: str, user: str) -> list[str]:
    settings, overrides, identity = rows
    problems = []
    got = dict(settings)
    for name, wanted in EXPECTED_SETTINGS.items():
        if got.get(name) != wanted:
            problems.append(f"{name} must be {wanted!r}, is {got.get(name)!r}")
    if overrides[0][0] != 0:
        problems.append("a database or role overrides a logging setting")
    db, current, superuser, version = identity[0]
    if db != database or not db.endswith("_test") or current != user:
        problems.append("not the expected test database and user")
    if superuser is not True:
        problems.append("the parent isn't the cluster's admin superuser, as Gate A1 assumes")
    if not 160000 <= version < 170000:
        problems.append("not PostgreSQL 16")
    return problems


def reset_statements(setup_sql: str, seed: list[Any]) -> list[tuple[str, tuple]]:
    """The reset, spelled out on a reconciled session, so it is audited rather than trusted."""
    out: list[tuple[str, tuple]] = [(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE", ()), (f"CREATE SCHEMA {SCHEMA}", ()),
                                    (f"SET search_path = {SCHEMA}", ()), ("BEGIN", ()), (setup_sql, ())]
    for row in seed:
        columns = [v.column for v in row.values]
        if not NAME.match(row.table) or not all(NAME.match(c) for c in columns):
            raise ValueError("seed names must be plain identifiers")
        out.append((f'INSERT INTO "{row.table}" (' + ", ".join(f'"{c}"' for c in columns) + ") VALUES ("
                    + ", ".join("%s" for _ in columns) + ")", tuple(v.value for v in row.values)))
    out.append(("COMMIT", ()))
    return out


def cleanup_statements() -> list[tuple[str, tuple]]:
    return [(f"DROP SCHEMA IF EXISTS {CHAR_SCHEMA} CASCADE", ()), (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE", ())]


# ------------------------------------------------------------------ driver binding and decoding (D1 to D3)

COLUMNS = {"i4": "integer", "i8": "bigint", "f8": "double precision", "b": "boolean", "t": "text", "v": "varchar(16)"}
INT64 = 2**63
CASES: tuple[tuple[str, Any], ...] = (
    ("i4", 2**31 - 1), ("i4", -(2**31)),
    ("i8", 2**53 - 1), ("i8", -(2**53 - 1)), ("i8", 2**53), ("i8", 2**53 + 1), ("i8", INT64 - 1), ("i8", -INT64),
    ("f8", 5e-324), ("f8", 1.7976931348623157e308), ("f8", -1.7976931348623157e308), ("f8", 0.1), ("f8", -2.5),
    ("f8", -0.0), ("f8", math.nan), ("f8", math.inf), ("f8", -math.inf),
    ("b", True), ("b", False),
    ("t", None), ("t", "e\u0301"), ("t", "\U0001F469\u200d\U0001F4BB"), ("t", "\U0001D11E"),
    ("t", "\u05e9\u05dc\u05d5\u05dd"),
    ("t", "01"), ("t", "1e3"), ("v", "\U0001D11E" * 16),
)


def binding_statements() -> list[tuple[str, tuple]]:
    columns = ", ".join(f"{c} {t}" for c, t in COLUMNS.items())
    out = [(f"CREATE SCHEMA {CHAR_SCHEMA}", ()),
           (f"CREATE TABLE {CHAR_SCHEMA}.bindings (id integer PRIMARY KEY, {columns})", ())]
    for i, (column, value) in enumerate(CASES, 1):
        out.append((f"INSERT INTO {CHAR_SCHEMA}.bindings (id, {column}) VALUES (%s, %s)", (i, value)))
    out.append((f"SELECT id, i4, i8, f8, b, t, v FROM {CHAR_SCHEMA}.bindings ORDER BY id", ()))
    out.append((f"SELECT to_jsonb(r) FROM {CHAR_SCHEMA}.bindings r ORDER BY (r).id", ()))
    out.append((f"DROP SCHEMA {CHAR_SCHEMA} CASCADE", ()))
    return out


def _same(expected: Any, got: Any) -> bool:
    if isinstance(expected, float):
        if type(got) is not float:
            return False
        if math.isnan(expected):
            return math.isnan(got)
        return expected.hex() == got.hex()
    return type(got) is type(expected) and got == expected


@dataclass
class BindingResult:
    plain_failures: list[str] = field(default_factory=list)  # D2 and D3: a stored value differs from the bound one
    jsonb_findings: list[str] = field(default_factory=list)  # D1: recorded behaviour, judged against the bridge's rule
    d1_refuted: list[str] = field(default_factory=list)

    @property
    def assumptions_hold(self) -> bool:
        return not self.plain_failures and not self.d1_refuted


def judge_bindings(plain: list[tuple], jsonb: list[Any]) -> BindingResult:
    """Plain reads must return every bound value exactly. Through to_jsonb, the bridge relies only on ints with
    |v| < 2^53, booleans, text and finite non-negative-zero floats decoding exactly. The rest is recorded."""
    out = BindingResult()
    if len(plain) != len(CASES) or len(jsonb) != len(CASES):
        out.plain_failures.append("not every case was read back")
        return out
    index = list(COLUMNS)
    for (column, value), row, doc in zip(CASES, plain, jsonb, strict=True):
        got = row[1 + index.index(column)]
        if value is None:
            if got is not None:
                out.plain_failures.append(f"{column} NULL came back as {got!r}")
        elif not _same(value, got):
            out.plain_failures.append(f"{column} {value!r} came back as {got!r}")
        decoded = doc.get(column) if isinstance(doc, dict) else None
        relied_on = (isinstance(value, bool) or isinstance(value, str) or value is None
                     or (isinstance(value, int) and abs(value) < 2**53)
                     or (isinstance(value, float) and math.isfinite(value)
                         and not (value == 0 and math.copysign(1, value) < 0)))
        if value is None:
            exact = decoded is None
        elif isinstance(value, float) and isinstance(decoded, int) and not isinstance(decoded, bool):
            exact = float(decoded).hex() == value.hex()
        else:
            exact = _same(value, decoded)
        if not exact:
            note = f"{column} {value!r} decodes through to_jsonb as {decoded!r}"
            (out.d1_refuted if relied_on else out.jsonb_findings).append(note)
    return out


# ------------------------------------------------------------------ the whole-batch session audit

LOG_READER_ALLOWED = (
    re.compile(r"^SELECT pg_current_logfile\('csvlog'\)$"),
    re.compile(r"^SELECT size FROM pg_stat_file\(\$1, true\)$"),
    re.compile(r"^SELECT pg_read_binary_file\(\$1, \$2, \$3, true\)$"),
    re.compile(r"^SELECT 'ds_parent_mark_(start|end|batch_start|batch_end)_[0-9a-f]{12}'$"),
    re.compile(r"^SELECT count\(\*\) FROM pg_stat_activity WHERE application_name = \$1$"),
)
GONE_SQL = "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s"
if GONE_SQL.replace("%s", "$1") != pr.READER_POLL:  # the poll the reconciler allows is the one the driver sends
    raise RuntimeError("GONE_SQL and the reconciler's allowed reader poll differ")

DSN_KEYS = frozenset({"host", "port", "dbname", "user", "password"})


def connection_target(dsn: dict[str, Any], package: dict[str, Any]) -> dict[str, str]:
    """Connection keywords for the manifest's fixed target, decided before any connection is opened.

    The host, address, port, database and user come from the manifest, never from the DSN, so neither DATABASE_URL nor
    a libpq variable (PGHOSTADDR, PGSERVICE and the like) can redirect them. The DSN must agree with the manifest and
    may only add the password. Messages name keys, never values, so the password can't reach a record.
    """
    want = {"host": package["container"]["host"], "port": str(package["container"]["port"]),
            "dbname": package["cluster"]["database"], "user": package["cluster"]["admin_user"]}
    extra = sorted(set(dsn) - DSN_KEYS)
    if extra:
        raise ValueError(f"DATABASE_URL has connection keys the manifest doesn't allow: {extra}")
    wrong = sorted(k for k, v in want.items() if str(dsn.get(k)) != v)
    if wrong:
        raise ValueError(f"DATABASE_URL doesn't point at the manifest's fixed local target (differs on {wrong})")
    if not dsn.get("password"):
        raise ValueError("DATABASE_URL has no password")
    return {**want, "hostaddr": want["host"], "password": str(dsn["password"])}


def operator_commands(package: dict[str, Any]) -> list[str]:
    """The operator's exact commands, built only from the reviewed manifest. The password is never written here."""
    c = package["container"]
    settings = " ".join(f"-c {k}={v}" for k, v in package["cluster"]["server_settings"].items())
    run = (f"docker run -d --rm --name {c['name']} -p {c['publish']} --tmpfs {c['tmpfs']} --memory {c['memory']} "
           f"--cpus {c['cpus']} --pids-limit {c['pids_limit']} -e POSTGRES_USER={package['cluster']['admin_user']} "
           f"-e POSTGRES_DB={c['initial_database']} -e POSTGRES_PASSWORD=\"$DS_PG_PASSWORD\" "
           f"{package['cluster']['image']} {settings}")
    # Over TCP inside the container: the image's temporary initialisation server listens only on its Unix socket, so
    # this answers only once the final server is up. It runs no SQL.
    ready = (f"docker exec {c['name']} pg_isready -h 127.0.0.1 -p {c['publish'].rsplit(':', 1)[1]} "
             f"-U {package['cluster']['admin_user']} -d {c['initial_database']}")
    create = (f"docker exec {c['name']} psql -U {package['cluster']['admin_user']} -d {c['initial_database']} "
              f"-c 'CREATE DATABASE {package['cluster']['database']}'")
    return [run, ready, create, f"docker stop {c['name']}"]


@dataclass
class BatchAudit:
    ok: bool
    problems: list[str] = field(default_factory=list)
    sessions: dict[str, str] = field(default_factory=dict)  # session id -> classification


def audit_batch(rows: list[list[str]], *, reconciled_tags: set[str], user: str, databases: set[str],
                reader: pr.ReaderSession) -> BatchAudit:
    """Every session in the batch window must be a reconciled tagged parent session, the one established log reader
    issuing only its allowed statements on consecutive session lines, or an allowed background process. Anything
    else, including a second session calling itself the log reader, fails Gate A1."""
    out = BatchAudit(ok=False)
    by_session: dict[str, list[list[str]]] = {}
    for r in rows:
        by_session.setdefault(r[pr.SESSION], []).append(r)
    for session, session_rows in by_session.items():
        apps = {r[pr.APPLICATION] for r in session_rows if r[pr.APPLICATION]}
        backends = {r[pr.BACKEND] for r in session_rows}
        if backends <= set(pr.BACKGROUND) and all(pr.BACKGROUND[r[pr.BACKEND]].match(r[pr.MESSAGE])
                                                  and r[pr.SEVERITY] == "LOG" for r in session_rows):
            out.sessions[session] = "background"
        elif len(apps) == 1 and next(iter(apps)) in reconciled_tags:
            out.sessions[session] = f"reconciled:{next(iter(apps))}"
        elif apps == {LOG_READER_APP} and session != reader.session:
            out.sessions[session] = "log_reader_violation"
            out.problems.append(f"a second session {session} calls itself the log reader")
        elif apps == {LOG_READER_APP}:
            bad = []
            if {r[pr.PID] for r in session_rows} != {reader.pid}:
                bad.append("process id")
            if [r[pr.LINE] for r in session_rows] != [str(n) for n in range(reader.start_line + 1, reader.end_line)]:
                bad.append("session lines incomplete or out of sequence")
            for r in session_rows:
                if r[pr.USER] and (r[pr.USER] != user or r[pr.DATABASE] not in databases):
                    bad.append("identity")
                match = pr.STATEMENT.match(r[pr.MESSAGE])
                if match and not any(p.match(match.group(1)) for p in LOG_READER_ALLOWED):
                    bad.append(match.group(1)[:60])
                if match and not r[pr.MESSAGE].startswith(pr.UNNAMED):  # Gate A1's reader prepares nothing
                    bad.append(f"prepared statement {r[pr.MESSAGE].split(':', 1)[0]}")
                if r[pr.SEVERITY] != "LOG":
                    bad.append(r[pr.SEVERITY])
            out.sessions[session] = "log_reader" if not bad else "log_reader_violation"
            if bad:
                out.problems.append(f"log reader issued unapproved activity: {bad[:3]}")
        else:
            out.sessions[session] = "unattributed"
            out.problems.append(f"unattributed session {session} ({sorted(apps) or 'no application name'})")
    out.ok = not out.problems
    return out
