"""Runtime configuration. Secrets are loaded from `.env` and never rendered."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_DB_NAME = "deployspecter_test"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Timeouts:
    """Explicit database limits applied to every actor session (milliseconds)."""

    statement_ms: int = 5000
    lock_ms: int = 3000
    deadlock_ms: int = 50
    idle_in_transaction_ms: int = 15000


@dataclass(frozen=True)
class Settings:
    # repr=False: these contain credentials and must not reach logs or tracebacks.
    admin_dsn: str = field(repr=False)
    test_dsn: str = field(repr=False)
    model_api_key: str | None = field(repr=False, default=None)
    model_base_url: str = DEFAULT_BASE_URL
    model_name: str = DEFAULT_MODEL
    timeouts: Timeouts = Timeouts()
    results_dir: Path = PROJECT_ROOT / "results"
    scenarios_dir: Path = PROJECT_ROOT / "scenarios"


def redact(text: str) -> str:
    """Remove the API key and database password from text that will be stored or shown."""
    password = urlsplit(os.environ.get("DATABASE_URL", "")).password
    for secret in (os.environ.get("MODEL_API_KEY"), password):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def _with_database(dsn: str, dbname: str) -> str:
    parts = urlsplit(dsn)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ConfigError("DATABASE_URL must be a postgresql:// URL")
    return urlunsplit(parts._replace(path=f"/{dbname}"))


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ConfigError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    return Settings(
        admin_dsn=dsn,
        test_dsn=_with_database(dsn, TEST_DB_NAME),
        model_api_key=os.environ.get("MODEL_API_KEY") or None,
        model_base_url=os.environ.get("MODEL_API_BASE_URL") or DEFAULT_BASE_URL,
        model_name=os.environ.get("MODEL_NAME") or DEFAULT_MODEL,
    )
