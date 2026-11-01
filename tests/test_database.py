import dataclasses

import psycopg
import pytest

from deployspecter.config import load_settings
from deployspecter.database import Sandbox, SandboxBusy, UnsafeDatabaseError


def test_postgres_16_is_reachable(sandbox):
    version = sandbox.admin.execute("SHOW server_version").fetchone()[0]
    assert version.startswith("16.")


def test_sandbox_uses_dedicated_test_database(sandbox):
    assert sandbox.admin.info.dbname == "deployspecter_test"


def test_refuses_non_test_database(settings):
    unsafe = dataclasses.replace(settings, test_dsn=settings.admin_dsn)
    with pytest.raises(UnsafeDatabaseError):
        Sandbox(unsafe)


def test_actor_sessions_are_not_superuser_and_cannot_read_host_files(sandbox):
    sandbox.reset("CREATE TABLE t (id int)", [])
    conn = sandbox.actor_connection("probe")
    assert conn.execute("SELECT current_setting('is_superuser')").fetchone()[0] == "off"
    assert conn.execute("SELECT current_user").fetchone()[0] == "deployspecter_runner"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT pg_read_file('/etc/passwd')")


def test_actor_session_timeouts_are_explicit(sandbox):
    conn = sandbox.actor_connection("probe")
    values = {
        name: conn.execute(f"SHOW {name}").fetchone()[0]
        for name in ("statement_timeout", "lock_timeout", "deadlock_timeout")
    }
    assert values == {"statement_timeout": "5s", "lock_timeout": "3s", "deadlock_timeout": "50ms"}


def test_settings_repr_hides_credentials():
    text = repr(load_settings())
    assert "postgresql://" not in text
    assert "model_api_key" not in text


def test_busy_sandbox_fails_clearly_instead_of_hanging(settings, sandbox):
    with pytest.raises(SandboxBusy):
        Sandbox(settings, lock_wait_ms=100)
