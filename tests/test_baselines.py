"""Non-AI systems are run for real: their proposals must compile and execute."""

import pytest

from deployspecter.analyzer_support import read_schema
from deployspecter.baselines import baseline_proposals, static_scan
from deployspecter.config import load_settings
from deployspecter.pipeline import compile_all
from deployspecter.scenarios import load_all_cases

CASES = load_all_cases(load_settings().scenarios_dir)
IDS = [c.case_id for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_baseline_proposals_all_pass_the_validator(case, sandbox):
    proposals = baseline_proposals(case, read_schema(sandbox, case.setup_sql), case.source_model())
    reports, rejections = compile_all(proposals, case, sandbox, "baseline_b")
    assert proposals
    assert not rejections, rejections
    assert len(reports) == len(proposals)


def test_static_scan_reports_rule_and_location():
    case = next(c for c in CASES if c.case_id == "backfill_overwrite/buggy")
    findings = static_scan(case.files, case.source_model())
    assert [f.rule for f in findings] == ["unguarded-read-then-update"]
    assert findings[0].location == "backfill.py:backfill_display_names#1"


def test_static_scan_flags_only_syntax_it_can_see():
    case = next(c for c in CASES if c.case_id == "backfill_overwrite/corrected")
    assert static_scan(case.files, case.source_model()) == []
