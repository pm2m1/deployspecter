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
              "oracle": "not_run", "hashes": {"src/x.py": H}, "track": er.TRACK_FOR[context], **kw}
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


def stage(name, outcome):
    return er.StageOutcome(stage=name, outcome=outcome)


def test_track_must_match_context():
    with pytest.raises(ValidationError, match="belongs to track_a_demonstration"):
        record(track="track_b_evaluation")


def test_unverified_catalog_or_bridge_never_authorizes_execution():
    verified = (stage("catalog_verification", "MATCH"), stage("bridge", "bridged"), stage("execution", "completed"))
    record(source="supervised_export", stages=verified, independent_verification="VERIFIED")
    for stages, source, verification in (
            (verified, "dry_run_fixture", "VERIFIED"),
            ((stage("catalog_verification", "INCOMPLETE"), *verified[1:]), "supervised_export", "VERIFIED"),
            ((verified[0], stage("bridge", "refused"), verified[2]), "supervised_export", "VERIFIED"),
            (verified, "supervised_export", "NOT_RUN")):
        with pytest.raises(ValidationError, match="can't authorize execution"):
            record(source=source, stages=stages, independent_verification=verification)


def test_construction_attempts_are_listed_and_only_one_admitted():
    with pytest.raises(ValidationError, match="attempts listed"):
        record(stages=(stage("construction", "failed"),))
    attempt = lambda n, ok: er.ConstructionAttempt(attempt=n, method="strict_insertion", outcome="x",  # noqa: E731
                                                   admitted=ok)
    record(stages=(stage("construction", "failed"),), construction_attempts=(attempt(1, False),),
           candidate_failures=(stage("planning", "unsupported"),))
    with pytest.raises(ValidationError, match="at most one"):
        record(stages=(stage("construction", "ok"),), construction_attempts=(attempt(1, True), attempt(2, True)))
