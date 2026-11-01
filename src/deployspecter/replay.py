"""Self-contained failure artifacts and verified replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .models import ExecutableSpec, ExecutionTrace, OracleVerdict, Requirement, Violation
from .oracle import evaluate
from .scheduler import ScheduleRunner


class FailureArtifact(BaseModel):
    """Everything needed to re-run a failure: setup SQL, spec, schedule, oracle input."""

    schema_version: int = 1
    case_id: str
    setup_sql: str
    requirements: list[Requirement]
    spec: ExecutableSpec
    schedule: list[str]
    seed: int | None = None
    trace: ExecutionTrace
    verdict: OracleVerdict
    fingerprint: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class ReplayReport(BaseModel):
    attempts: int
    fingerprints_match: int
    violations_match: int
    reproduced: bool
    mismatches: list[str] = Field(default_factory=list)
    final_state: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    violations: list[Violation] = Field(default_factory=list)


def make_artifact(
    case_id: str, setup_sql: str, requirements: list[Requirement], spec: ExecutableSpec,
    trace: ExecutionTrace, verdict: OracleVerdict, seed: int | None = None,
    provenance: dict[str, Any] | None = None,
) -> FailureArtifact:
    return FailureArtifact(
        case_id=case_id, setup_sql=setup_sql, requirements=requirements, spec=spec,
        schedule=trace.requested_schedule, seed=seed, trace=trace, verdict=verdict,
        fingerprint=trace.fingerprint(), provenance=provenance or {},
    )


def save_artifact(artifact: FailureArtifact, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(indent=2))
    return path


def load_artifact(path: Path) -> FailureArtifact:
    return FailureArtifact.model_validate_json(path.read_text())


def _violation_keys(verdict: OracleVerdict) -> set[tuple[str, str, str, str]]:
    return {
        (v.requirement_id, str(v.key), str(v.expected), str(v.actual)) for v in verdict.violations
    }


def replay(artifact: FailureArtifact, runner: ScheduleRunner, attempts: int = 3) -> ReplayReport:
    """Re-execute against real PostgreSQL and compare behaviour, not just the verdict."""
    fingerprint_ok = violations_ok = 0
    mismatches: list[str] = []
    want = _violation_keys(artifact.verdict)
    last_state: dict[str, list[dict[str, Any]]] = {}
    last_violations: list[Violation] = []
    for attempt in range(attempts):
        trace = runner.run(artifact.spec, artifact.setup_sql, artifact.schedule)
        verdict = evaluate(artifact.requirements, trace)
        if trace.fingerprint() == artifact.fingerprint:
            fingerprint_ok += 1
        else:
            mismatches.append(f"attempt {attempt}: behaviour fingerprint differs")
        last_state, last_violations = trace.final_state, verdict.violations
        got = _violation_keys(verdict)
        if got == want and want:
            violations_ok += 1
        else:
            mismatches.append(f"attempt {attempt}: violations differ: {sorted(got)} vs {sorted(want)}")
    return ReplayReport(
        attempts=attempts, fingerprints_match=fingerprint_ok, violations_match=violations_ok,
        reproduced=attempts > 0 and fingerprint_ok == attempts and violations_ok == attempts,
        mismatches=mismatches, final_state=last_state, violations=last_violations,
    )
