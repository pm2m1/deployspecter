"""One system on one case: propose, validate, explore, replay, minimize."""

from __future__ import annotations

import re
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .analyzer import (
    AnalysisAttempt,
    AnalysisRecord,
    analyze,
    build_prompt,
    parse_output,
    prompt_identity,
)
from .analyzer_support import read_catalog, read_schema
from .baselines import baseline_proposals
from .config import PROJECT_ROOT, redact
from .database import Sandbox, SetupError
from .explorer import Budget, ExplorationResult, explore
from .meta_client import MetaClient
from .minimizer import MinimizationResult, minimize
from .models import ExecutableSpec, TestProposal
from .replay import ReplayReport, make_artifact, replay, save_artifact
from .scenarios import Case
from .scheduler import ScheduleRunner
from .validation import CompileReport, SpecRejected, compile_proposal


class Rejection(BaseModel):
    test: str
    reasons: list[str]


class TestRun(BaseModel):
    __test__ = False
    exploration: ExplorationResult
    unverified_flows: list[str] = Field(default_factory=list)
    dropped_invariants: list[str] = Field(default_factory=list)
    renamed_actors: dict[str, str] = Field(default_factory=dict)
    spec: ExecutableSpec | None = None


class SystemRun(BaseModel):
    system: str
    case_id: str
    tests_proposed: int = 0
    tests_valid: int = 0
    rejections: list[Rejection] = Field(default_factory=list)
    runs: list[TestRun] = Field(default_factory=list)
    schedules_executed: int = 0
    schedule_budget: int = 0
    violating_schedules: int = 0
    detected: bool = False
    schedules_to_first_failure: int | None = None
    seconds_to_first_failure: float | None = None
    detecting_test: str | None = None
    violates_in_serial_order: bool | None = None
    detecting_flows_unverified: list[str] = Field(default_factory=list)
    violated_requirements: list[str] = Field(default_factory=list)
    artifact_path: str | None = None
    replay: ReplayReport | None = None
    minimized: MinimizationResult | None = None
    minimized_replay: ReplayReport | None = None
    minimized_artifact_path: str | None = None
    execution_errors: int = 0
    transaction_conflicts: int = 0
    non_serial_final_states: int = 0
    elapsed_seconds: float = 0.0
    analysis: AnalysisRecord | None = None
    first_attempt_valid: int | None = None
    first_attempt_proposed: int | None = None
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class NoRecordedResponse(RuntimeError):
    pass


def _relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path)


def attempt_validity(attempt: AnalysisAttempt, case: Case, sandbox: Sandbox) -> tuple[int, int]:
    """(valid tests, proposed tests) for one recorded model response, judged as it stood."""
    output = parse_output(attempt.model_copy())
    if output is None:
        return 0, 0
    reports, _ = compile_all(output.tests, case, sandbox, "ai")
    return len(reports), len(output.tests)


def compile_all(
    proposals: list[TestProposal], case: Case, sandbox: Sandbox, generator: str
) -> tuple[list[CompileReport], list[Rejection]]:
    source = case.source_model()
    catalog = read_catalog(sandbox, case.setup_sql)
    reports: list[CompileReport] = []
    rejections: list[Rejection] = []
    for proposal in proposals:
        try:
            report = compile_proposal(proposal, source, catalog, generator)
            sandbox.reset(case.setup_sql, report.spec.seed)  # PostgreSQL has the last word on seeds
            reports.append(report)
        except SpecRejected as exc:
            rejections.append(Rejection(test=proposal.name, reasons=exc.reasons))
        except SetupError as exc:
            rejections.append(Rejection(test=proposal.name, reasons=[f"seed rejected by PostgreSQL: {exc}"]))
    return reports, rejections


def run_tests(
    sandbox: Sandbox, runner: ScheduleRunner, case: Case, reports: list[CompileReport], run: SystemRun,
    total_schedules: int, total_seconds: float, artifacts_dir: Path, seed: int | None = None,
) -> SystemRun:
    """Spend one shared budget across tests; unused share rolls over to later tests."""
    started = time.perf_counter()
    run.schedule_budget = total_schedules
    executed = 0
    for i, report in enumerate(reports):
        left = total_schedules - executed
        time_left = total_seconds - (time.perf_counter() - started)
        if left <= 0 or time_left <= 0:
            run.notes.append(f"budget exhausted before test {i + 1} of {len(reports)}")
            break
        share = max(1, left // (len(reports) - i))
        before = time.perf_counter() - started
        result = explore(
            runner, report.spec, case.setup_sql, case.requirements,
            Budget(max_schedules=share, max_seconds=time_left, seed=seed),
        )
        run.runs.append(TestRun(
            exploration=result, unverified_flows=report.unverified_flows,
            dropped_invariants=report.dropped_invariants, renamed_actors=report.renamed_actors,
            spec=report.spec,
        ))
        if report.renamed_actors:
            run.notes.append(f"{report.spec.name}: actor names normalized: {report.renamed_actors}")
        run.violating_schedules += result.violating_total
        run.non_serial_final_states += result.non_serial_final_states
        run.execution_errors += result.classifications.get("execution_error", 0)
        run.transaction_conflicts += result.classifications.get("transaction_conflict", 0)
        if result.first_violation_index is not None and not run.detected:
            run.detected = True
            run.schedules_to_first_failure = executed + result.first_violation_index + 1
            run.seconds_to_first_failure = round(before + (result.first_violation_seconds or 0), 4)
            run.detecting_test = report.spec.name
            run.violates_in_serial_order = result.violates_in_serial_order
            run.detecting_flows_unverified = report.unverified_flows
            run.violated_requirements = sorted({v.requirement_id for v in result.first_verdict.violations})
            _verify_failure(runner, case, report, result, run, artifacts_dir, seed)
        executed += result.executed
    run.schedules_executed = executed
    run.elapsed_seconds = round(time.perf_counter() - started, 3)
    return run


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")


def _verify_failure(runner, case, report, result, run, artifacts_dir: Path, seed) -> None:
    spec = report.spec
    stem = f"{_slug(case.case_id)}__{_slug(run.system)}"
    artifact = make_artifact(
        case.case_id, case.setup_sql, case.requirements, spec, result.first_trace, result.first_verdict,
        seed=seed, provenance={"system": run.system, "test": spec.name, "schedule_index": result.first_violation_index},
    )
    run.artifact_path = _relative(save_artifact(artifact, artifacts_dir / f"{stem}.json"))
    run.replay = replay(artifact, runner, attempts=3)
    minimized = minimize(runner, spec, case.setup_sql, case.requirements, result.first_trace.requested_schedule)
    run.minimized = minimized
    small = make_artifact(
        case.case_id, case.setup_sql, case.requirements, minimized.spec, minimized.trace, minimized.verdict,
        provenance={"system": run.system, "minimized_from": run.artifact_path},
    )
    run.minimized_artifact_path = _relative(save_artifact(small, artifacts_dir / f"{stem}__min.json"))
    run.minimized_replay = replay(small, runner, attempts=3)


def run_hand(sandbox, runner, case, budget_schedules, budget_seconds, artifacts_dir) -> SystemRun:
    run = SystemRun(system="hand", case_id=case.case_id)
    proposals = [case.hand_spec] if case.hand_spec else []
    reports, run.rejections = compile_all(proposals, case, sandbox, "hand")
    run.tests_proposed, run.tests_valid = len(proposals), len(reports)
    return run_tests(sandbox, runner, case, reports, run, budget_schedules, budget_seconds, artifacts_dir)


def run_baseline(sandbox, runner, case, budget_schedules, budget_seconds, artifacts_dir) -> SystemRun:
    run = SystemRun(system="baseline_b", case_id=case.case_id)
    proposals = baseline_proposals(case, read_schema(sandbox, case.setup_sql), case.source_model())
    reports, run.rejections = compile_all(proposals, case, sandbox, "baseline_b")
    run.tests_proposed, run.tests_valid = len(proposals), len(reports)
    return run_tests(sandbox, runner, case, reports, run, budget_schedules, budget_seconds, artifacts_dir)


def _save_record(record: AnalysisRecord, cache_path: Path | None) -> None:
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(record.model_dump_json(indent=2))


def obtain_analysis(
    client: MetaClient | None, case: Case, sandbox: Sandbox, cache_path: Path | None, reuse: bool,
    allow_repair: bool = False,
) -> tuple[AnalysisRecord, list[TestProposal], list[str]]:
    """Load a recorded response. Muse Spark is called only when the caller passes a client."""
    notes: list[str] = []
    if reuse and cache_path and cache_path.exists():
        record = AnalysisRecord.model_validate_json(cache_path.read_text())
        notes.append(f"recorded model response reused: {cache_path.name}")
        return record, record.output.tests if record.output else [], notes
    if client is None:
        raise NoRecordedResponse(f"no recorded model response for {case.case_id}; model calls are disabled")
    source = case.source_model()
    prompt = build_prompt(case.files, source, read_schema(sandbox, case.setup_sql))
    record = AnalysisRecord(prompt_sha256=prompt_identity(prompt), model=client.model)
    first = analyze(client, prompt)
    record.attempts.append(first)
    _save_record(record, cache_path)  # a paid response must survive any later failure
    output = parse_output(first)
    _save_record(record, cache_path)
    problems: list[str] = []
    if output is None:
        problems = [f"response failed schema validation: {first.parse_error or 'empty'}"]
    else:
        reports, rejections = compile_all(output.tests, case, sandbox, "ai")
        if not reports:
            problems = [f"{r.test}: {reason}" for r in rejections for reason in r.reasons] or ["no tests proposed"]
    if problems and allow_repair:  # measured, and at most once
        repair = analyze(client, prompt, repair_context=problems[:20])
        record.attempts.append(repair)
        _save_record(record, cache_path)
        repaired = parse_output(repair)
        output = repaired if repaired is not None else output
    record.output = output
    _save_record(record, cache_path)
    return record, output.tests if output else [], notes


def run_ai(
    sandbox, runner, case, budget_schedules, budget_seconds, artifacts_dir,
    client: MetaClient | None, cache_path: Path | None, reuse: bool = True, label: str = "ai",
    allow_repair: bool = False,
) -> SystemRun:
    run = SystemRun(system=label, case_id=case.case_id)
    try:
        record, proposals, notes = obtain_analysis(client, case, sandbox, cache_path, reuse, allow_repair)
    except Exception as exc:  # recorded as a measured failure, never turned into a pass
        run.error = redact(f"{type(exc).__name__}: {str(exc)[:500]}")
        return run
    run.analysis = record
    if record.attempts:
        run.first_attempt_valid, run.first_attempt_proposed = attempt_validity(record.attempts[0], case, sandbox)
    run.notes.extend(notes)
    reports, run.rejections = compile_all(proposals, case, sandbox, "ai")
    run.tests_proposed, run.tests_valid = len(proposals), len(reports)
    return run_tests(sandbox, runner, case, reports, run, budget_schedules, budget_seconds, artifacts_dir)
