"""Track A's end-to-end acceptance record. Pure: it judges an evidence bundle and never produces evidence.

Each of the nine conditions is ``not_run`` until its evidence exists, ``fail`` if the evidence is present but short,
and ``pass`` only when every required item is present and holds. The overall verdict is ``PASS`` only if all nine
pass. Missing evidence never passes: absent means ``not_run``, and partial means ``fail``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["not_run", "pass", "fail"]
TARGET_REQUIREMENTS = frozenset({"R1", "R2"})
REQUIRED_DIGESTS = ("registry", "contracts", "spec", "plan_identity", "snapshot", "context", "request",
                    "bridge_typed", "legacy_seed", "setup_sql", "app_source", "oracle_source", "harness_source")
DRY_RUN_LABELS = {"dry_run_fixture", "hand_written_track_a_fixture"}


@dataclass(frozen=True)
class Judgement:
    condition: str
    status: Status
    reason: str


def _missing(bundle: dict[str, Any], *keys: str) -> list[str]:
    return [k for k in keys if bundle.get(k) is None]


def _validated(b: dict[str, Any]) -> Judgement:
    chain = b.get("chain")
    if chain is None:
        return Judgement("1_validated", "not_run", "no offline chain record")
    stages = chain.get("stages", {})
    ok = (stages.get("authorize", {}).get("accepted") and stages.get("validate_arguments", {}).get("accepted")
          and stages.get("plan_identity", {}).get("supported") and chain.get("rechecked") is True)
    return Judgement("1_validated", "pass" if ok else "fail", "stages 1 to 3, recheck and plan identity")


def _schema(b: dict[str, Any]) -> Judgement:
    export = b.get("catalog_export")
    if export is None:
        return Judgement("2_schema_matched", "not_run",
                         "no supervised catalog export; a dry-run fixture isn't evidence")
    if export.get("source_label") in DRY_RUN_LABELS or export.get("exporter") in DRY_RUN_LABELS:
        return Judgement("2_schema_matched", "fail", "the snapshot is a dry-run fixture")
    ok = (export.get("verify_context") == "MATCH" and export.get("parent_digest") == export.get("child_digest")
          and export.get("exports_agree") is True)
    return Judgement("2_schema_matched", "pass" if ok else "fail", "MATCH, parent re-read and repeat export")


def _constructed(b: dict[str, Any]) -> Judgement:
    runs = b.get("construction")
    if runs is None:
        return Judgement("3_state_checked", "not_run", "no construction check")
    ok = all(r.get("initial_state_problems") == [] and r.get("independent_check") == "VERIFIED" for r in runs) and runs
    return Judgement("3_state_checked", "pass" if ok else "fail", "exact initial state and an independent check")


def _supervised(b: dict[str, Any]) -> Judgement:
    traces = b.get("traces")
    if traces is None:
        return Judgement("4_supervised", "not_run", "no supervised runs")
    ok = bool(traces) and all(t.get("supervised") and t.get("scoreable") for t in traces)
    return Judgement("4_supervised", "pass" if ok else "fail", "every scored trace supervised and scoreable")


def _buggy(b: dict[str, Any]) -> Judgement:
    found = b.get("buggy_exploration")
    if found is None:
        return Judgement("5_buggy_violates", "not_run", "buggy version not explored")
    ok = bool(set(found.get("candidate_requirements", [])) & TARGET_REQUIREMENTS) and found.get("candidate_run_id")
    return Judgement("5_buggy_violates", "pass" if ok else "fail", "a candidate violating R1 or R2")


def _fixed(b: dict[str, Any]) -> Judgement:
    fixed = b.get("fixed_exploration")
    if fixed is None:
        return Judgement("6_fixed_passes", "not_run", "fixed version not explored")
    ok = (fixed.get("same_workload_digest") == b.get("digests", {}).get("spec") and fixed.get("violations") == []
          and fixed.get("complete_schedule_coverage") is True and fixed.get("inconclusive_runs") == 0)
    return Judgement("6_fixed_passes", "pass" if ok else "fail",
                     "the same workload, exhausted, with no target violation and no inconclusive run")


def _replay(b: dict[str, Any]) -> Judgement:
    replay = b.get("replay")
    if replay is None:
        return Judgement("7_replay", "not_run", "no confirmation attempts")
    ok = replay.get("result") == "reproduced" and replay.get("independent_oracle_each_attempt") is True
    return Judgement("7_replay", "pass" if ok else "fail", "reproduced under amendment 002, independently checked")


def _attributable(b: dict[str, Any]) -> Judgement:
    digests = b.get("digests")
    if digests is None:
        return Judgement("8_attributable", "not_run", "no digests")
    missing = _missing(digests, *REQUIRED_DIGESTS)
    traces_ok = all(t.get("run_id") for t in b.get("traces") or [])
    ok = not missing and traces_ok and b.get("traces") is not None
    reason = f"missing digests: {missing}" if missing else "every digest and run id present"
    return Judgement("8_attributable", "pass" if ok else "fail", reason)


def _fail_closed(b: dict[str, Any]) -> Judgement:
    if b.get("traces") is None:
        return Judgement("9_fail_closed", "not_run", "nothing executed")
    flagged = [t for t in b["traces"] if t.get("flags") or t.get("opaque_steps") or t.get("refusals")]
    unknown = b.get("unknown_effects", None)
    ok = not flagged and unknown == [] and b.get("missing_evidence", None) == []
    return Judgement("9_fail_closed", "pass" if ok else "fail", "no flags, opaque steps, refusals or unknown effects")


CHECKS: tuple[Callable[[dict[str, Any]], Judgement], ...] = (
    _validated, _schema, _constructed, _supervised, _buggy, _fixed, _replay, _attributable, _fail_closed)


def judge(bundle: dict[str, Any]) -> tuple[str, list[Judgement]]:
    judgements = [check(bundle) for check in CHECKS]
    statuses = {j.status for j in judgements}
    verdict = "FAIL" if "fail" in statuses else ("NOT_RUN" if "not_run" in statuses else "PASS")
    return verdict, judgements
