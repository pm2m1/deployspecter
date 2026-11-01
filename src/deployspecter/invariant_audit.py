"""Offline audit of recorded model invariants. Sends no model requests.

Re-executes saved proposals on PostgreSQL and also, where the proposal compiles,
on the other variant of the same scenario. That differential tells apart a
hypothesis tied to the known defect from one that holds regardless of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analyzer import AnalysisRecord
from .explorer import Budget, ViolatingSchedule, _same_target, _state_key
from .models import ExecutableSpec, InvariantProposal
from .oracle import evaluate, invariant_holds
from .pipeline import compile_all
from .scenarios import VARIANTS, Case, load_case
from .scheduler import enumerate_schedules, preemptions


def _audit_one(runner, spec: ExecutableSpec, inv: InvariantProposal, case: Case, budget: Budget) -> dict[str, Any]:
    counts = spec.step_counts()
    out: dict[str, Any] = {
        "schedules": 0, "failed": 0, "serial_failed": 0, "oracle_same_target": 0, "oracle_other_target": 0,
        "no_oracle_violation": 0, "execution_or_conflict": 0, "distinct_failing_final_states": 0,
        "example": None,
    }
    states: set[str] = set()
    for index, schedule in enumerate(enumerate_schedules(counts)):
        if index >= budget.max_schedules:
            break
        trace = runner.run(spec, case.setup_sql, schedule)
        verdict = evaluate(case.requirements, trace)
        out["schedules"] += 1
        if invariant_holds(inv, trace.final_state):
            continue
        out["failed"] += 1
        states.add(_state_key(trace))
        out["serial_failed"] += preemptions(schedule, counts) == 0
        if trace.outcome != "completed":
            out["execution_or_conflict"] += 1
        if any(_same_target(inv, v) for v in verdict.violations):
            out["oracle_same_target"] += 1
        elif verdict.violations:
            out["oracle_other_target"] += 1
        else:
            out["no_oracle_violation"] += 1
            if out["example"] is None:
                out["example"] = {
                    "schedule": ViolatingSchedule(
                        index=index, schedule=schedule, preemptions=preemptions(schedule, counts), requirement_ids=[]
                    ).model_dump(),
                    "final_state": trace.final_state,
                    "outcome": trace.outcome,
                    "reads": [r.rows for r in trace.records if r.rows is not None],
                }
    out["distinct_failing_final_states"] = len(states)
    return out


def classify(same: dict[str, Any], other: dict[str, Any] | None) -> str:
    """Mechanical category; a person still decides whether a potential issue matters."""
    if same["failed"] == 0:
        return "held in every schedule run"
    if same["serial_failed"]:
        return "incorrect or unsupported invariant (fails in a serial order)"
    if same["execution_or_conflict"] == same["failed"]:
        return "execution artifact"
    if same["oracle_same_target"] == same["failed"]:
        return "independently verified (same row and column as an oracle violation)"
    if same["oracle_same_target"]:
        return "partly verified: some failures match the oracle, others need evidence"
    return "potentially real, needs additional oracle evidence"


def audit_responses(sandbox, runner, scenarios_dir: Path, paths: list[Path], budget: Budget) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        scenario, variant, repeat = path.stem.split("__")
        record = AnalysisRecord.model_validate_json(path.read_text())
        if record.output is None:
            continue
        case = load_case(scenarios_dir, scenario, variant)
        other_variant = next(v for v in VARIANTS if v != variant)
        other_case = load_case(scenarios_dir, scenario, other_variant)
        for proposal in record.output.tests:
            reports, _ = compile_all([proposal], case, sandbox, "ai")
            if not reports:
                continue
            spec = reports[0].spec
            other_reports, other_rejections = compile_all([proposal], other_case, sandbox, "ai")
            for inv in spec.invariants:
                same = _audit_one(runner, spec, inv, case, budget)
                other = None
                if other_reports and inv in other_reports[0].spec.invariants:
                    other = _audit_one(runner, other_reports[0].spec, inv, other_case, budget)
                rows.append({
                    "response": path.name, "scenario": scenario, "variant": variant, "repeat": repeat,
                    "test": proposal.name, "invariant": inv.model_dump(), "result": same,
                    "other_variant": other_variant, "other_variant_result": other,
                    "other_variant_rejected": [r.reasons for r in other_rejections],
                    "category": classify(same, other),
                })
    return rows
