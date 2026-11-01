"""Disposable PostgreSQL sandbox: dedicated test DB, restricted role, safe reset."""

from __future__ import annotations

import datetime as dt
import decimal
from typing import Any

import psycopg
from psycopg import sql

from .config import TEST_DB_NAME, Settings
from .models import SeedRow

RUN_SCHEMA = "ds_run"
RUNNER_ROLE = "deployspecter_runner"
_ADVISORY_LOCK_KEY = 7_420_001


class UnsafeDatabaseError(RuntimeError):
    pass


class SandboxBusy(RuntimeError):
    pass


class SetupError(RuntimeError):
    """The scenario schema or a proposed seed could not be applied."""


def normalize(value: Any) -> Any:
    """JSON-stable form. Timestamps are masked: wall-clock values break replay comparison."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return "<timestamp>"
    if isinstance(value, decimal.Decimal):
        return str(value.normalize()) if value == value.to_integral_value() else str(value)
    return value


def ensure_test_database(settings: Settings) -> None:
    with psycopg.connect(settings.admin_dsn, autocommit=True, connect_timeout=5) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        ).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME)))
        conn.execute(
            f"""DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNNER_ROLE}') THEN
                    CREATE ROLE {RUNNER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
                  END IF;
                END $$"""
        )


class Sandbox:
    """Owns the admin/monitor sessions and one reusable session per actor name.

    Actor sessions run under a non-superuser role, in autocommit mode, so every
    BEGIN and COMMIT in a trace is an explicit statement that we issued.
    """

    def __init__(self, settings: Settings, lock_wait_ms: int = 30000) -> None:
        self.settings = settings
        ensure_test_database(settings)
        self.admin = psycopg.connect(settings.test_dsn, autocommit=True, connect_timeout=5)
        dbname = self.admin.info.dbname
        if not dbname.endswith("_test"):  # destructive resets must never reach another DB
            self.admin.close()
            raise UnsafeDatabaseError(f"refusing to use non-test database {dbname!r}")
        # Only one explorer may use the shared run schema at a time.
        self.admin.execute(f"SET lock_timeout = {int(lock_wait_ms)}")
        try:
            self.admin.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        except psycopg.errors.LockNotAvailable as exc:
            self.admin.close()
            raise SandboxBusy("another exploration is using the sandbox; try again when it finishes") from exc
        self.admin.execute("SET lock_timeout = 0")
        self.monitor = psycopg.connect(settings.test_dsn, autocommit=True, connect_timeout=5)
        self._actors: dict[str, psycopg.Connection] = {}

    def close(self) -> None:
        for conn in self._actors.values():
            conn.close()
        self._actors.clear()
        self.monitor.close()
        self.admin.close()

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close_actor(self, name: str) -> None:
        conn = self._actors.pop(name, None)
        if conn is not None:
            conn.close()

    def actor_connection(self, name: str) -> psycopg.Connection:
        conn = self._actors.get(name)
        if conn is None or conn.closed:
            t = self.settings.timeouts
            conn = psycopg.connect(
                self.settings.test_dsn, autocommit=True, connect_timeout=5, prepare_threshold=None
            )
            conn.execute("SELECT set_config('application_name', %s, false)", (f"ds:{name}",))
            # deadlock_timeout is superuser-only, so it is set before dropping privileges.
            conn.execute(f"SET deadlock_timeout = {int(t.deadlock_ms)}")
            conn.execute(f"SET statement_timeout = {int(t.statement_ms)}")
            conn.execute(f"SET lock_timeout = {int(t.lock_ms)}")
            conn.execute(f"SET idle_in_transaction_session_timeout = {int(t.idle_in_transaction_ms)}")
            conn.execute(f"SET search_path = {RUN_SCHEMA}")
            conn.execute(f"SET ROLE {RUNNER_ROLE}")
            self._actors[name] = conn
        return conn

    def reset(self, setup_sql: str, seed: list[SeedRow]) -> None:
        """Rebuild the run schema from nothing, then apply setup SQL and seed rows."""
        for name, conn in list(self._actors.items()):
            if conn.closed or conn.info.transaction_status == psycopg.pq.TransactionStatus.UNKNOWN:
                self.close_actor(name)
            elif conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.rollback()
        admin = self.admin
        admin.execute(f"DROP SCHEMA IF EXISTS {RUN_SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {RUN_SCHEMA}")
        admin.execute(f"SET search_path = {RUN_SCHEMA}")
        try:
            with admin.transaction():
                admin.execute(setup_sql)
                for row in seed:
                    self._insert_seed(row)
        except psycopg.Error as exc:
            raise SetupError(str(exc).strip()) from exc
        admin.execute(f"GRANT USAGE ON SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}")
        admin.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}"
        )
        admin.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}")

    def _insert_seed(self, row: SeedRow) -> None:
        columns = [v.column for v in row.values]
        stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(row.table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() * len(columns)),
        )
        self.admin.execute(stmt, [v.value for v in row.values])

    def tables(self) -> list[str]:
        rows = self.admin.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY 1""",
            (RUN_SCHEMA,),
        ).fetchall()
        return [r[0] for r in rows]

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        state: dict[str, list[dict[str, Any]]] = {}
        for table in self.tables():
            cur = self.admin.execute(
                sql.SQL("SELECT * FROM {} ORDER BY 1").format(sql.Identifier(table))
            )
            names = [d.name for d in cur.description]
            state[table] = [
                {n: normalize(v) for n, v in zip(names, row, strict=True)} for row in cur.fetchall()
            ]
        return state

    def blockers(self, pids: list[int]) -> dict[int, list[int]]:
        """Lock-wait graph straight from PostgreSQL; no sleep-based guessing."""
        rows = self.monitor.execute(
            "SELECT p, pg_blocking_pids(p) FROM unnest(%s::int[]) AS p", (pids,)
        ).fetchall()
        return {p: list(b) for p, b in rows}
