"""Track A's end-to-end acceptance gates. Pure: they judge an evidence bundle and never produce evidence.

Each gate is ``not_run`` until its evidence exists, ``fail`` if the evidence is present but short, and ``pass`` only
when every required item is present and holds. The verdict is ``PASS`` only if every gate passes. Missing evidence
never passes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["not_run", "pass", "fail"]
TARGET_REQUIREMENTS = frozenset({"R1", "R2"})
REQUIRED_DIGESTS = ("registry", "contracts", "spec", "plan_identity", "snapshot", "context", "request",
                    "bridge_typed", "legacy_seed", "setup_sql", "app_source", "oracle_source", "harness_source",
                    "binding")
DRY_RUN_LABELS = {"dry_run_fixture", "hand_written_track_a_fixture"}
EXPORT_LABEL = "supervised_parent_export"


@dataclass(frozen=True)
class Judgement:
    gate: str
    status: Status
    reason: str


def _j(gate: str, ok: bool, reason: str) -> Judgement:
    return Judgement(gate, "pass" if ok else "fail", reason)


def g0_validated(b: dict[str, Any]) -> Judgement:
    chain = b.get("chain")
    if chain is None:
        return Judgement("G0_validated_proposal", "not_run", "no offline chain record")
    s = chain.get("stages", {})
    ok = bool(s.get("authorize", {}).get("accepted") and s.get("validate_arguments", {}).get("accepted")
              and s.get("plan_identity", {}).get("supported") and chain.get("rechecked") is True
              and s.get("bridge", {}).get("outcome") == "bridged")
    return _j("G0_validated_proposal", ok, "stages 1 to 3, recheck, plan identity, bridged")


def g1_catalog(b: dict[str, Any]) -> Judgement:
    e = b.get("catalog_export")
    if e is None:
        return Judgement("G1_catalog_identity_and_completeness", "not_run",
                         "no supervised export; a dry-run fixture is never evidence")
    if e.get("source_label") in DRY_RUN_LABELS or e.get("source_label") != EXPORT_LABEL:
        return Judgement("G1_catalog_identity_and_completeness", "fail", "not a supervised parent export")
    ok = (e.get("verify_context") == "MATCH" and e.get("exports_agree") is True and e.get("cross_check_agrees") is True
          and e.get("cluster_name") == "ds_live_disposable" and e.get("coverage_complete") is True
          and e.get("statements_reconciled") is True and e.get("time_typed_columns") == [])
    return _j("G1_catalog_identity_and_completeness", ok,
              "MATCH; two exports and the independent re-read agree; disposable cluster; complete; reconciled")


def g2_initial_state(b: dict[str, Any]) -> Judgement:
    s = b.get("initial_states")
    if s is None:
        return Judgement("G2_initial_state_construction", "not_run", "no initial-state verification")
    ok = s.get("problems") == [] and bool(s.get("verified_initial_digest")) and s.get("runs_checked", 0) > 0
    return _j("G2_initial_state_construction", ok, "exact, identical across runs, independently confirmed")


def g3_execution(b: dict[str, Any]) -> Judgement:
    runs = b.get("runs")
    if runs is None:
        return Judgement("G3_operation_execution", "not_run", "no supervised runs")
    ok = bool(runs) and all(r.get("supervised") and r.get("scoreable") and not r.get("flags") for r in runs)
    return _j("G3_operation_execution", ok, "every run supervised, scoreable, unflagged and reconciled")


def g4_oracle(b: dict[str, Any]) -> Judgement:
    o = b.get("oracle")
    if o is None:
        return Judgement("G4_oracle_independence", "not_run", "no oracle record")
    ok = (o.get("source_sha256") == o.get("pinned_sha256") and o.get("imports_tool_or_app") is False
          and o.get("reads") == "parent_final_state_and_independent_read")
    return _j("G4_oracle_independence", ok, "pinned oracle source, no tool or application import, independent reads")


def g5_buggy(b: dict[str, Any]) -> Judgement:
    x = b.get("buggy_exploration")
    if x is None:
        return Judgement("G5_buggy_violation", "not_run", "buggy version not explored")
    ok = bool(set(x.get("candidate_requirements", [])) & TARGET_REQUIREMENTS) and bool(x.get("candidate_run_id"))
    return _j("G5_buggy_violation", ok, "a candidate violating R1 or R2")


def g6_fixed(b: dict[str, Any]) -> Judgement:
    x = b.get("fixed_exploration")
    if x is None:
        return Judgement("G6_fixed_negative_control", "not_run", "fixed version not explored")
    ok = (x.get("same_workload_digest") == b.get("digests", {}).get("spec") and x.get("target_violations") == []
          and x.get("exhausted") is True and x.get("complete_schedule_coverage") is True
          and x.get("inconclusive_runs") == 0)
    return _j("G6_fixed_negative_control", ok, "same workload, exhausted, no target violation, no inconclusive run")


def g7_replay(b: dict[str, Any]) -> Judgement:
    r = b.get("replay")
    if r is None:
        return Judgement("G7_semantic_replay", "not_run", "no confirmation attempts")
    ok = r.get("result") == "reproduced" and r.get("independent_oracle_each_attempt") is True
    return _j("G7_semantic_replay", ok, "reproduced under amendment 002, each comparable attempt independently checked")


def g8_refusal(b: dict[str, Any]) -> Judgement:
    if b.get("runs") is None:
        return Judgement("G8_unknown_effect_and_incomplete_evidence_refusal", "not_run", "nothing executed")
    bad = [r for r in b["runs"] if r.get("flags") or r.get("opaque_steps") or r.get("refusals")]
    ok = not bad and b.get("unknown_effects") == [] and b.get("missing_evidence") == []
    return _j("G8_unknown_effect_and_incomplete_evidence_refusal", ok,
              "no flags, opaque steps, refusals, unknown effects or missing evidence")


def g9_attribution(b: dict[str, Any]) -> Judgement:
    d = b.get("digests")
    if d is None:
        return Judgement("G9_attribution_and_binding", "not_run", "no digests")
    missing = [k for k in REQUIRED_DIGESTS if not d.get(k)]
    ok = not missing and b.get("runs") is not None and all(r.get("run_id") for r in b["runs"])
    return _j("G9_attribution_and_binding", ok, f"missing: {missing}" if missing else "every digest and run id")


GATES: tuple[Callable[[dict[str, Any]], Judgement], ...] = (
    g0_validated, g1_catalog, g2_initial_state, g3_execution, g4_oracle, g5_buggy, g6_fixed, g7_replay, g8_refusal,
    g9_attribution)


def judge(bundle: dict[str, Any]) -> tuple[str, list[Judgement]]:
    judgements = [gate(bundle) for gate in GATES]
    statuses = {j.status for j in judgements}
    verdict = "FAIL" if "fail" in statuses else ("NOT_RUN" if "not_run" in statuses else "PASS")
    return verdict, judgements
