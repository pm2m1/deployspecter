"""Shared fixtures. Tests run against real PostgreSQL, never a mock."""

from __future__ import annotations

import psycopg
import pytest

from deployspecter.config import load_settings
from deployspecter.database import Sandbox
from deployspecter.scenarios import Case, load_case
from deployspecter.scheduler import ScheduleRunner
from deployspecter.validation import Catalog, compile_proposal


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def sandbox(settings):
    try:
        with Sandbox(settings) as box:
            yield box
    except psycopg.OperationalError as exc:
        pytest.fail(f"PostgreSQL is required for these tests: {type(exc).__name__}")


@pytest.fixture(scope="session")
def runner(sandbox):
    with ScheduleRunner(sandbox) as r:
        yield r


def catalog_for(sandbox: Sandbox, case: Case) -> Catalog:
    from deployspecter.analyzer_support import read_catalog

    return read_catalog(sandbox, case.setup_sql)


@pytest.fixture
def load(settings):
    return lambda scenario, variant: load_case(settings.scenarios_dir, scenario, variant)


@pytest.fixture
def hand_spec(sandbox):
    def build(case: Case):
        return compile_proposal(
            case.hand_spec, case.source_model(), catalog_for(sandbox, case), "hand"
        ).spec

    return build
