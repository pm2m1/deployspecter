"""EXPERIMENTAL: Phase 9B's deterministic state constructor, loaded unmodified, on the disposable PostgreSQL only.

The constructor (``phase9b_source/``, byte-identical to the origin worktree's uncommitted files, see ``SOURCE.json``)
is imported under a private package name, ``deployspecter._phase9b``, so nothing in ``deployspecter.*`` is replaced.
Its relative imports of this branch's root modules (``database``, ``config``, ``models``, ``analyzer_support``,
``source_model``, ``scenarios``, ``validation``) are aliased to the real modules. Each source's sha256 is checked
before it is executed; a mismatch is refused.

The constructor works through the root ``Sandbox``: database ``deployspecter_test``, schema ``ds_run``, role
``deployspecter_runner``. Here that database lives in the **disposable** cluster (``ds_mvp_disposable`` on
127.0.0.1:55441), checked before the sandbox is created. It is never the protected 55432 instance and never comes
from ``DATABASE_URL``. ``teardown`` drops the database and the role.

``construct`` returns the constructor's own ``ConstructionReport`` (as a dict) and never inserts anything outside
the constructor's own sandbox. Only ``ORIGINAL_VALID``, ``RECONCILED_EQUIVALENT`` and ``CONSTRUCTED_EQUIVALENT``
(the constructor's ``PROVEN``) authorize anything downstream. Every other classification is an explicit refusal.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg

SOURCE_DIR = Path(__file__).resolve().parent / "phase9b_source"
PINNED = json.loads((SOURCE_DIR / "SOURCE.json").read_text())["sha256"]
PACKAGE = "deployspecter._phase9b"
ALIASES = ("database", "config", "models", "analyzer_support", "source_model", "scenarios", "validation")
CLUSTER_NAME = "ds_mvp_disposable"
HOST, FORBIDDEN_PORTS = "127.0.0.1", {55432}
PROVEN = ("ORIGINAL_VALID", "RECONCILED_EQUIVALENT", "CONSTRUCTED_EQUIVALENT")


class ConstructorRefused(RuntimeError):
    """The constructor can't be loaded or used here. Raised, never worked around."""


def load() -> types.SimpleNamespace:
    """The three constructor modules, sha256-checked, under the private package."""
    if PACKAGE in sys.modules and hasattr(sys.modules[PACKAGE], "_loaded"):
        m = sys.modules[PACKAGE]._loaded
        return m
    import deployspecter
    pkg = types.ModuleType(PACKAGE)
    pkg.__path__ = [str(SOURCE_DIR)]  # type: ignore[attr-defined]
    pkg.__package__ = PACKAGE
    sys.modules[PACKAGE] = pkg
    for name in ALIASES:
        sys.modules[f"{PACKAGE}.{name}"] = importlib.import_module(f"deployspecter.{name}")
    loaded = {}
    for name in ("triggers", "seed_feasibility", "state_constructor"):
        path = SOURCE_DIR / f"{name}.py"
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != PINNED[f"{name}.py"]:
            del sys.modules[PACKAGE]
            raise ConstructorRefused(f"{name}.py isn't the pinned Phase 9B source (sha256 {got[:12]})")
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        loaded[name] = mod
    pkg._loaded = types.SimpleNamespace(**loaded)  # type: ignore[attr-defined]
    del deployspecter
    return pkg._loaded  # type: ignore[attr-defined]


def settings(port: int, user: str, password: str) -> Any:
    """Root ``Settings`` for the disposable cluster, after checking its identity on the admin database."""
    if port in FORBIDDEN_PORTS:
        raise ConstructorRefused("the protected PostgreSQL is never a constructor target")
    with psycopg.connect(host=HOST, hostaddr=HOST, port=port, user=user, password=password, dbname="ds_mvp",
                         autocommit=True, connect_timeout=5, application_name="ds_sc_identity") as c:
        name = c.execute("SELECT current_setting('cluster_name')").fetchone()[0]
    if name != CLUSTER_NAME:
        raise ConstructorRefused(f"unexpected cluster {name!r}")
    from deployspecter.config import TEST_DB_NAME, Settings
    base = f"postgresql://{quote(user)}:{quote(password, safe='')}@{HOST}:{port}"
    return Settings(admin_dsn=f"{base}/ds_mvp", test_dsn=f"{base}/{TEST_DB_NAME}")


def construct(sandbox: Any, setup_sql: str, seed: list[Any], *, budget: Any = None) -> dict[str, Any]:
    """The unmodified ``construct_state`` with the unmodified ``read_context``; the report as plain data."""
    m = load()
    context = m.seed_feasibility.read_context(sandbox, setup_sql)
    report = m.state_constructor.construct_state(sandbox, setup_sql, seed, context, budget=budget)
    return json.loads(report.model_dump_json())


def open_sandbox(port: int, user: str, password: str) -> Any:
    from deployspecter.database import Sandbox
    s = settings(port, user, password)
    sb = Sandbox(s)
    if sb.admin.execute("SELECT current_setting('cluster_name')").fetchone()[0] != CLUSTER_NAME:
        sb.close()
        raise ConstructorRefused("the sandbox isn't on the disposable cluster")
    return sb


def teardown(port: int, user: str, password: str) -> dict[str, bool]:
    """Drop the constructor's database and role from the disposable cluster."""
    from deployspecter.config import TEST_DB_NAME
    from deployspecter.database import RUNNER_ROLE
    settings(port, user, password)  # the identity check
    with psycopg.connect(host=HOST, hostaddr=HOST, port=port, user=user, password=password, dbname="ds_mvp",
                         autocommit=True, connect_timeout=5, application_name="ds_sc_teardown") as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> "
                  "pg_backend_pid()", (TEST_DB_NAME,))
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
        c.execute(f"DROP ROLE IF EXISTS {RUNNER_ROLE}")
        db = c.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)).fetchone()[0]
        role = c.execute("SELECT count(*) FROM pg_roles WHERE rolname = %s", (RUNNER_ROLE,)).fetchone()[0]
    return {"database_dropped": db == 0, "role_dropped": role == 0}
