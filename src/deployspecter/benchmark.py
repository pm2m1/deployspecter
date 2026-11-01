"""Runs every system over a split with equal budgets and scores against fixture labels.

Labels are read only here, for scoring, after each system has finished a case.
"""

from __future__ import annotations

import datetime as dt
import statistics
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .analyzer import INSTRUCTIONS, PROMPT_VERSION, prompt_identity
from .baselines import StaticFinding, static_scan
from .config import Settings
from .database import Sandbox
from .meta_client import PRICING_NOTE, MetaClient
from .pipeline import SystemRun, run_ai, run_baseline, run_baseline_c, run_hand
from .scenarios import Case, load_all_cases
from .scheduler import ScheduleRunner


class CaseScore(BaseModel):
    case_id: str
    has_bug: bool
    split: str
    system: str
    repeat: int = 0
    # Baseline A warns without executing anything, so these stay None for it.
    tests_valid: int | None = None
    tests_proposed: int | None = None
    flagged: bool
    verified_by_execution: bool
    schedules_executed: int | None = None
    schedules_to_first_failure: int | None = None
    seconds_to_first_failure: float | None = None
    serial_order_violation: bool | None = None
    matches_reference_manifestation: bool | None = None
    unverified_flows_in_detecting_test: int | None = None
    replay_ok: bool | None = None
    minimized_replay_ok: bool | None = None
    minimized_steps: int | None = None
    original_steps: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    inference_seconds: float | None = None
    approx_cost_usd: float | None = None
    repair_attempts: int | None = None
    invariants_proposed: int | None = None
    invariants_screened_out: int | None = None
    invariant_failures_unconfirmed: int | None = None
    invariant_failures_matching_oracle: int | None = None
    invariant_failures_unrelated_oracle: int | None = None
    requests: int | None = None
    first_attempt_valid: int | None = None
    first_attempt_proposed: int | None = None
    rejections: list[str] = Field(default_factory=list)
    static_findings: list[StaticFinding] = Field(default_factory=list)
    error: str | None = None


def _score_run(case: Case, run: SystemRun, repeat: int, hand: SystemRun | None) -> CaseScore:
    score = CaseScore(
        case_id=case.case_id, has_bug=case.has_bug, split=case.split, system=run.system, repeat=repeat,
        tests_valid=run.tests_valid, tests_proposed=run.tests_proposed, flagged=run.detected,
        verified_by_execution=run.detected and bool(run.replay and run.replay.reproduced),
        schedules_executed=run.schedules_executed,
        schedules_to_first_failure=run.schedules_to_first_failure,
        seconds_to_first_failure=run.seconds_to_first_failure,
        serial_order_violation=run.violates_in_serial_order,
        unverified_flows_in_detecting_test=len(run.detecting_flows_unverified) if run.detected else None,
        replay_ok=run.replay.reproduced if run.replay else None,
        minimized_replay_ok=run.minimized_replay.reproduced if run.minimized_replay else None,
        minimized_steps=run.minimized.after["steps"] if run.minimized else None,
        original_steps=run.minimized.before["steps"] if run.minimized else None,
        rejections=[f"{r.test}: {'; '.join(r.reasons)}" for r in run.rejections], error=run.error,
    )
    if run.detected and hand and hand.detected:
        # A serial-order hit on an interleaving-only defect suggests the generated data flow,
        # not the concurrency, caused the violation.
        score.matches_reference_manifestation = run.violates_in_serial_order == hand.violates_in_serial_order
    if run.analysis:
        attempts = run.analysis.attempts
        score.input_tokens = sum(a.reply.input_tokens or 0 for a in attempts)
        score.output_tokens = sum(a.reply.output_tokens or 0 for a in attempts)
        score.reasoning_tokens = sum(a.reply.reasoning_tokens or 0 for a in attempts)
        score.inference_seconds = round(sum(a.reply.latency_seconds for a in attempts), 2)
        score.approx_cost_usd = round(sum(a.reply.approx_cost_usd or 0 for a in attempts), 5)
        score.repair_attempts = sum(a.kind == "repair" for a in attempts)
        score.requests = len(attempts)
        score.first_attempt_valid = run.first_attempt_valid
        score.first_attempt_proposed = run.first_attempt_proposed
    invariants = [i for t in run.runs for i in t.exploration.invariants]
    if run.system.startswith("ai"):
        score.invariants_proposed = len(invariants)
        score.invariants_screened_out = sum(i.screened_out for i in invariants)
        score.invariant_failures_unconfirmed = sum(
            i.failed_without_oracle_violation for i in invariants if not i.screened_out
        )
        score.invariant_failures_matching_oracle = sum(
            i.failed_matching_oracle_violation for i in invariants if not i.screened_out
        )
        score.invariant_failures_unrelated_oracle = sum(
            i.failed_with_unrelated_oracle_violation for i in invariants if not i.screened_out
        )
    return score


def _agg(scores: list[CaseScore]) -> dict[str, Any]:
    buggy = [s for s in scores if s.has_bug]
    clean = [s for s in scores if not s.has_bug]
    firsts = [s.schedules_to_first_failure for s in buggy if s.schedules_to_first_failure]
    secs = [s.seconds_to_first_failure for s in buggy if s.seconds_to_first_failure is not None]
    replays = [s.replay_ok for s in buggy + clean if s.replay_ok is not None]
    out = {
        "runs": len(scores),
        "buggy_runs": len(buggy),
        "detected": sum(s.flagged for s in buggy),
        "verified_by_execution": sum(s.verified_by_execution for s in buggy),
        "matches_reference_manifestation": sum(bool(s.matches_reference_manifestation) for s in buggy),
        "corrected_runs": len(clean),
        # None for a system that executes nothing; a run with no valid test cannot be a clean result.
        "corrected_runs_executed": (
            sum(bool(s.schedules_executed) for s in clean)
            if any(s.tests_proposed is not None for s in scores)
            else None
        ),
        "false_positives": sum(s.flagged for s in clean),
        "median_schedules_to_failure": statistics.median(firsts) if firsts else None,
        "median_seconds_to_failure": round(statistics.median(secs), 4) if secs else None,
        "replay_success": f"{sum(replays)}/{len(replays)}" if replays else None,
        "valid_tests": sum(s.tests_valid or 0 for s in scores),
        "proposed_tests": sum(s.tests_proposed or 0 for s in scores),
        "schedules_executed": sum(s.schedules_executed or 0 for s in scores),
    }
    if any(s.input_tokens is not None for s in scores):
        out |= {
            "input_tokens": sum(s.input_tokens or 0 for s in scores),
            "output_tokens": sum(s.output_tokens or 0 for s in scores),
            "reasoning_tokens": sum(s.reasoning_tokens or 0 for s in scores),
            "inference_seconds": round(sum(s.inference_seconds or 0 for s in scores), 1),
            "approx_cost_usd": round(sum(s.approx_cost_usd or 0 for s in scores), 4),
            "repair_attempts": sum(s.repair_attempts or 0 for s in scores),
            "requests": sum(s.requests or 0 for s in scores),
            "first_attempt_valid": sum(s.first_attempt_valid or 0 for s in scores),
            "first_attempt_proposed": sum(s.first_attempt_proposed or 0 for s in scores),
            "invariants_proposed": sum(s.invariants_proposed or 0 for s in scores),
            "invariants_screened_out": sum(s.invariants_screened_out or 0 for s in scores),
        }
    return out


def _notes(systems: list[str], missing: list[str], rescored: bool) -> list[str]:
    if "ai" not in systems:
        return ["Muse Spark was not evaluated on this split."]
    notes = []
    if missing:
        notes.append(f"Incomplete AI evaluation: no recorded response for {', '.join(missing)}.")
    if rescored:
        notes.append("AI results were re-scored from recorded responses with the current validator.")
    return notes


def run_benchmark(
    settings: Settings, split: str, systems: list[str], ai_repeats: int = 3,
    budget_schedules: int = 300, budget_seconds: float = 60.0, reuse_ai: bool = True,
    out_dir: Path | None = None, max_requests: int = 0, max_estimated_usd: float = 0.0,
    allow_repair: bool = False,
) -> dict[str, Any]:
    """Uses recorded model responses unless ``max_requests`` explicitly allows new ones."""
    results_dir = out_dir or settings.results_dir / "benchmark"
    # A new directory per run, so a later run cannot replace an earlier run's failure artifacts.
    run_stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    artifacts = results_dir / "failures" / split / run_stamp
    ai_cache = settings.results_dir / "ai_responses" / PROMPT_VERSION
    cases = load_all_cases(settings.scenarios_dir, None if split == "all" else split)
    client: MetaClient | None = None
    if "ai" in systems and max_requests > 0:
        client = MetaClient(settings, max_requests=max_requests, max_estimated_usd=max_estimated_usd)

    scores: list[CaseScore] = []
    missing: list[str] = []
    results_dir.mkdir(parents=True, exist_ok=True)
    partial = results_dir / f"{split}_partial.jsonl"
    partial.write_text("")

    def keep(score: CaseScore) -> None:
        scores.append(score)
        with partial.open("a") as handle:
            handle.write(score.model_dump_json() + "\n")

    with Sandbox(settings) as sandbox, ScheduleRunner(sandbox) as runner:
        for case in cases:
            shared = (sandbox, runner, case, budget_schedules, budget_seconds, artifacts)
            needs_hand = {"hand", "ai", "baseline_b", "baseline_c"} & set(systems)
            hand = run_hand(*shared) if needs_hand else None
            if "static" in systems:
                findings = static_scan(case.files, case.source_model())
                keep(CaseScore(
                    case_id=case.case_id, has_bug=case.has_bug, split=case.split, system="baseline_a_static",
                    flagged=bool(findings), verified_by_execution=False, static_findings=findings,
                ))
            if "hand" in systems and hand:
                keep(_score_run(case, hand, 0, hand))
            if "baseline_b" in systems:
                keep(_score_run(case, run_baseline(*shared), 0, hand))
            if "baseline_c" in systems:
                keep(_score_run(case, run_baseline_c(*shared), 0, hand))
            if "ai" in systems:
                for repeat in range(ai_repeats):
                    cache = ai_cache / f"{case.case_id.replace('/', '__')}__r{repeat}.json"
                    if client is None and not cache.exists():
                        missing.append(f"{case.case_id} repeat {repeat}")
                        continue
                    run = run_ai(
                        sandbox, runner, case, budget_schedules, budget_seconds, artifacts / f"r{repeat}",
                        client, cache, reuse=reuse_ai, label="ai", allow_repair=allow_repair,
                    )
                    keep(_score_run(case, run, repeat, hand))

    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "split": split,
        "budget": {"schedules_per_case": budget_schedules, "seconds_per_case": budget_seconds},
        "prompt_version": PROMPT_VERSION,
        "instructions_sha256": prompt_identity(INSTRUCTIONS)[:16],
        "model": settings.model_name,
        "pricing_note": PRICING_NOTE,
        "ai_repeats": ai_repeats,
        "artifacts_dir": str(artifacts.relative_to(results_dir.parent)),
        "new_model_requests_sent": client.requests_sent if client else 0,
        "complete": not missing and "ai" in systems,
        "missing_ai_responses": missing,
        "notes": _notes(systems, missing, client is None),
        "summary": {
            system: _agg([s for s in scores if s.system == system])
            for system in dict.fromkeys(s.system for s in scores)
        },
        "scores": [s.model_dump() for s in scores],
    }
    return report
