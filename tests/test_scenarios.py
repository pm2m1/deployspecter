"""Ground-truth check: every fixture behaves as labelled, over its whole schedule space.

Uses hand-written tests only. If a 'corrected' fixture fails here, the fixture is
wrong and benchmark scores against it would be meaningless.
"""

from pathlib import Path

import pytest

from deployspecter.config import load_settings
from deployspecter.explorer import Budget, explore
from deployspecter.scenarios import load_all_cases

CASES = load_all_cases(load_settings().scenarios_dir)
IDS = [c.case_id for c in CASES]


def test_benchmark_has_required_cases():
    assert {"backfill_overwrite/buggy", "backfill_overwrite/corrected", "mixed_version_overwrite/buggy",
            "mixed_version_overwrite/corrected"} <= set(IDS)
    assert {c.split for c in CASES} == {"dev", "heldout"}


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixture_has_no_extraction_warnings(case):
    assert case.source_model().warnings == []


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixture_matches_its_label_exhaustively(case, hand_spec, runner):
    spec = hand_spec(case)
    result = explore(runner, spec, case.setup_sql, case.requirements, Budget(max_schedules=5000, max_seconds=120))
    assert result.exhausted_space, "hand spec must be small enough to explore completely"
    assert result.outcomes.get("completed") == result.executed, result.outcomes
    if case.has_bug:
        assert result.violating_total > 0
    else:
        assert result.violating_total == 0
        # Rule out passing vacuously: the requirement was exercised and held.
        assert result.classifications.get("no_violation", 0) > 0


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixture_sources_do_not_leak_labels(case):
    text = "\n".join(case.files.values()).lower()
    for word in ("buggy", "corrected", " bug", "race", "stale", "fixed", "overwrite"):
        assert word not in text, f"{word!r} in the code shown to generators"


def test_real_fixture_code_agrees_with_extracted_statements(settings, sandbox):
    """Run the actual fixture functions and compare with what static extraction claims."""
    import importlib.util

    from psycopg.rows import dict_row

    from deployspecter.scenarios import load_case

    case = load_case(settings.scenarios_dir, "backfill_overwrite", "corrected")
    root = Path(settings.scenarios_dir) / "backfill_overwrite" / "corrected" / "backfill.py"
    spec = importlib.util.spec_from_file_location("fixture_backfill", root)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from deployspecter.models import ColumnValue, SeedRow

    sandbox.reset(case.setup_sql, [SeedRow(table="profiles", values=[
        ColumnValue(column="id", value=1), ColumnValue(column="legacy_name", value="Alice")])])
    executed = []

    class Recording:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, params=None):
            executed.append(" ".join(sql.split()))
            return self.conn.execute(sql, params)

        def transaction(self):
            return self.conn.transaction()

    conn = sandbox.actor_connection("conformance")
    conn.row_factory = dict_row
    try:
        module.backfill_display_names(Recording(conn), 10)
    finally:
        sandbox.close_actor("conformance")
    extracted = [s.sql for s in case.source_model().functions["backfill.py:backfill_display_names"].statements]
    assert executed == extracted
    assert sandbox.snapshot()["profiles"][0]["display_name"] == "Alice"
