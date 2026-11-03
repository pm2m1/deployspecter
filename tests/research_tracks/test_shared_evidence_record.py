"""Tests of the PROPOSED shared evidence record. Neither track depends on it yet."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from deployspecter.research.shared import evidence_record as er

ROOT = Path(__file__).resolve().parents[2]
H = "a" * 64


def identities(source="dry_run_fixture"):
    return er.Identities(scenario="track_a", application="reservations", workload_schema_version=2, spec_digest=H,
                         registry_digest=H, contracts_digest=H, snapshot_source=source)


def record(context="synthetic_demonstration", attribution=None, source="dry_run_fixture", **kw):
    fields = {"method": "deterministic_planner", "stages": (), "independent_verification": "NOT_RUN",
              "oracle": "not_run", "hashes": {"src/x.py": H}, **kw}
    return er.EvidenceRecord(context=context, identities=identities(source),
                             attribution=attribution or er.Attribution(), **fields)


def test_a_synthetic_record_carries_no_arm():
    record()
    with pytest.raises(ValidationError, match="only held-out"):
        record(attribution=er.Attribution(arm="D", replicate=1, attempt=1, defect_id="d"))


def test_a_heldout_record_needs_harness_attribution_and_an_export():
    with pytest.raises(ValidationError, match="harness-recorded"):
        record(context="heldout_evaluation", source="supervised_export")
    with pytest.raises(ValidationError, match="supervised catalog export"):
        record(context="heldout_evaluation", attribution=er.Attribution(arm="D", replicate=1, attempt=1, defect_id="d"))


def test_provenance_is_data_and_attribution_source_is_fixed():
    r = record(self_reported_provenance={"origin": "model_proposal", "arm": "E"})
    assert r.attribution.arm is None and r.attribution.source == "harness"
    with pytest.raises(ValidationError):
        er.Attribution(source="document")


def test_an_oracle_result_needs_a_verified_state():
    with pytest.raises(ValidationError, match="verified starting state"):
        record(oracle="violation")


def test_synthetic_records_never_enter_a_primary_analysis():
    with pytest.raises(ValueError, match="primary analysis"):
        er.primary_analysis_records([record()])


def test_no_automatic_retry_can_be_recorded():
    with pytest.raises(ValidationError):
        er.Safety(retried=True)


def test_neither_track_imports_the_proposal_yet():
    for track in ("track_a", "track_b"):
        for path in (ROOT / "src/deployspecter/research" / track).glob("*.py"):
            text = ast.dump(ast.parse(path.read_text()))
            assert "evidence_record" not in text and "research.shared" not in text, path
