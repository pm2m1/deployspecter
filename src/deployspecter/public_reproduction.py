"""Re-checks the published GitLab-fixture evidence on real PostgreSQL. Sends no model requests.

It reads the saved buggy-fixture responses, never changes them, and passes no model client to the pipeline. It only
covers what is public: the three buggy-fixture trials, the documented serial defect, and the baselines on the buggy
fixture. ``NOT_REPRODUCIBLE`` lists what it can't check, and the report repeats that list.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from .analyzer import PROMPT_VERSION, build_prompt, prompt_identity
from .analyzer_support import read_catalog, read_schema
from .config import Settings, load_settings
from .database import Sandbox
from .documented import SCENARIO, run_documented
from .models import ExecutableSpec, TestProposal
from .pipeline import SystemRun, run_ai, run_baseline, run_baseline_c
from .replay import load_artifact, replay
from .scenarios import Case, load_case
from .scheduler import ScheduleRunner
from .validation import compile_proposal

EXPERIMENT_ID = "gitlab-ai-2aef4b9c09c435b7"
PACKAGE_NAME = f"20261102T130607Z-public-evidence-{EXPERIMENT_ID}"
TRIALS = ("r1", "r2", "r3")
SCHEDULES = 300
SECONDS = 60.0
CHECK_SCHEMA = "ds_public_check"
NOT_REPRODUCIBLE = (
    "The three corrected-fixture trials: those responses and their results are withheld.",
    "The combined six-request figures for tokens, cost and proposals: stated in the summary, not recomputable here.",
    "The original experiment runner and its full protocol: not public, so the run itself can't be repeated as it was.",
    "New Muse Spark responses: this replays saved responses and asks the model nothing.",
    "Any statement about GitLab's production systems: this is a minimal, isolated fixture.",
)


class EvidenceMismatch(RuntimeError):
    """A public file doesn't match its manifest, so nothing built on it can be trusted."""


def package_dir(settings: Settings) -> Path:
    return settings.results_dir / "external_public" / SCENARIO / PACKAGE_NAME


def verify_manifest(package: Path) -> int:
    checked = 0
    for line in (package / "MANIFEST.sha256").read_text().splitlines():
        digest, status, name = line.split("  ", 2)
        if status == "withheld-original":
            continue
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != digest:
            raise EvidenceMismatch(f"{name} does not match MANIFEST.sha256")
        checked += 1
    return checked


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def expect(self, name: str, actual: Any, expected: Any) -> None:
        self.rows.append({"check": name, "passed": actual == expected, "actual": actual, "expected": expected})

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if not r["passed"]]


def _test_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("spec_name", "executed", "space_size", "classifications", "violating_total", "first_violation_index")
    return [{k: run["exploration"][k] for k in keys} for run in runs]


def _rejections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"test": r["test"], "reasons": r["reasons"]} for r in rows]


def check_buggy_trial(settings, sandbox, runner, trial: str, package: Path, artifacts: Path, checks: Checks) -> bool:
    """One published response through the same validator, scheduler and oracle. Returns whether it was flagged."""
    folder = package / "original" / "responses" / f"buggy_{trial}"
    saved = json.loads((folder / "system_run.json").read_text())
    record = json.loads((folder / "analysis.json").read_text())
    case = load_case(settings.external_scenarios_dir, SCENARIO, "buggy")
    prompt = build_prompt(case.files, case.source_model(), read_schema(sandbox, case.setup_sql))
    checks.expect(f"{trial}: prompt identity", prompt_identity(prompt), record["prompt_sha256"])
    checks.expect(f"{trial}: prompt version", record["prompt_version"], PROMPT_VERSION)
    checks.expect(f"{trial}: no retry or repair was used", len(record["attempts"]), 1)
    run = run_ai(
        sandbox, runner, case, SCHEDULES, SECONDS, artifacts / trial, None, folder / "analysis.json",
        reuse=True, label=saved["system"],
    )
    checks.expect(f"{trial}: no pipeline error", run.error, None)
    checks.expect(f"{trial}: tests proposed", run.tests_proposed, saved["tests_proposed"])
    checks.expect(f"{trial}: tests valid", run.tests_valid, saved["tests_valid"])
    now = json.loads(run.model_dump_json())
    checks.expect(f"{trial}: rejection reasons", _rejections(now["rejections"]), _rejections(saved["rejections"]))
    checks.expect(f"{trial}: per-test execution", _test_rows(now["runs"]), _test_rows(saved["runs"]))
    checks.expect(f"{trial}: schedules executed", run.schedules_executed, saved["schedules_executed"])
    checks.expect(f"{trial}: oracle flag", run.detected, saved["detected"])
    return run.detected


def plain_sql_serial_check(settings: Settings, case: Case, spec: ExecutableSpec) -> list[dict[str, Any]]:
    """Every serial order with plain psycopg in its own schema. None of the scheduler or the oracle is used."""
    results = []
    for order in itertools.permutations(spec.actors):
        with psycopg.connect(settings.test_dsn, autocommit=True, connect_timeout=5) as conn:
            if not conn.info.dbname.endswith("_test"):
                raise RuntimeError("this check runs only on the test database")
            conn.execute(f"DROP SCHEMA IF EXISTS {CHECK_SCHEMA} CASCADE")
            conn.execute(f"CREATE SCHEMA {CHECK_SCHEMA}")
            conn.execute(f"SET search_path = {CHECK_SCHEMA}")
            try:
                conn.execute(case.setup_sql)
                for row in spec.seed:
                    names = [v.column for v in row.values]
                    conn.execute(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            sql.Identifier(row.table), sql.SQL(", ").join(map(sql.Identifier, names)),
                            sql.SQL(", ").join(sql.Placeholder() * len(names)),
                        ),
                        [v.value for v in row.values],
                    )
                for actor in order:
                    for step in actor.steps:
                        if step.kind == "sql":
                            conn.execute(step.sql, {k: b.value for k, b in step.params.items()})
                lost = conn.execute(
                    """SELECT DISTINCT s.user_id, s.project_id FROM project_grants s
                       LEFT JOIN project_grants_compact d USING (user_id, project_id)
                       WHERE d.user_id IS NULL ORDER BY 1, 2"""
                ).fetchall()
            finally:
                conn.execute(f"DROP SCHEMA IF EXISTS {CHECK_SCHEMA} CASCADE")
        results.append({"order": [a.name for a in order], "pairs_lost": [list(p) for p in lost]})
    return results


def check_documented_defect(settings, sandbox, runner, package: Path, artifacts: Path, checks: Checks) -> None:
    buggy = load_case(settings.external_scenarios_dir, SCENARIO, "buggy")
    corrected = load_case(settings.external_scenarios_dir, SCENARIO, "corrected")
    run, _, _, facts = run_documented(sandbox, runner, settings, SCENARIO, "buggy", artifacts / "reference")
    checks.expect("reference: schedules that lose the row", facts["violating_schedules"], 20)
    checks.expect("reference: schedules executed", facts["schedules"], 20)
    checks.expect("reference: fails in a serial order", facts["fails_in_serial_order"], True)
    checks.expect("reference: failure class", facts["failure_class"], "serial_migration_defect")
    checks.expect("reference: replay reproduced", run.replay.reproduced, True)
    checks.expect("reference: minimized replay reproduced", run.minimized_replay.reproduced, True)
    checks.expect("reference: minimized to one actor", run.minimized.after["actors"], 1)
    published = package / "original" / "baselines" / "reference"
    for artifact in sorted(published.glob("*.json")):
        result = replay(load_artifact(artifact), runner)
        checks.expect(f"published artifact replays: {artifact.name}", result.reproduced, True)
    test = run.runs[0].exploration.spec_name
    document = json.loads((settings.external_scenarios_dir / SCENARIO / "base" / "documented_test.json").read_text())
    checks.expect("reference: same test as the fixture documents", test, document["test"]["name"])
    serial = plain_sql_serial_check(settings, buggy, run.runs[0].spec)
    checks.expect("plain SQL: every serial order loses the row on the buggy fixture",
                  all(s["pairs_lost"] for s in serial), True)
    proposal = TestProposal.model_validate(document["test"])
    catalog = read_catalog(sandbox, corrected.setup_sql)
    spec = compile_proposal(proposal, corrected.source_model(), catalog, "hand").spec
    on_corrected = plain_sql_serial_check(settings, corrected, spec)
    checks.expect("plain SQL: this hand-written test keeps the row on the corrected fixture",
                  any(s["pairs_lost"] for s in on_corrected), False)
    fixed, _, _, fixed_facts = run_documented(sandbox, runner, settings, SCENARIO, "corrected", artifacts / "fixed")
    checks.expect("reference on corrected fixture: violating schedules", fixed_facts["violating_schedules"], 0)
    checks.expect("reference on corrected fixture: schedules", fixed.runs[0].exploration.executed, 20)


def _aggregate(run: SystemRun) -> dict[str, Any]:
    inconclusive = sum(
        t.exploration.classifications.get(k, 0)
        for t in run.runs for k in ("vacuous", "transaction_conflict", "execution_error")
    )
    return {
        "tests_proposed": run.tests_proposed, "tests_valid": run.tests_valid,
        "schedules_executed": run.schedules_executed, "oracle_flagged": run.detected,
        "schedules_to_first_failure": run.schedules_to_first_failure,
        "serially_reachable_in_engine": run.violates_in_serial_order,
        "replay_reproduced": run.replay.reproduced if run.replay else None,
        "minimized_replay_reproduced": run.minimized_replay.reproduced if run.minimized_replay else None,
        "inconclusive_schedules": inconclusive,
    }


def check_baselines(settings, sandbox, runner, package: Path, artifacts: Path, checks: Checks) -> None:
    """Buggy fixture only, against the published aggregate figures."""
    summary = json.loads((package / "derived" / "evidence_summary.json").read_text())
    published = summary["comparisons_on_buggy_fixture_aggregate_only"]
    case = load_case(settings.external_scenarios_dir, SCENARIO, "buggy")
    for name, generator in (("baseline_b", run_baseline), ("baseline_c", run_baseline_c)):
        got = _aggregate(generator(sandbox, runner, case, SCHEDULES, SECONDS, artifacts / name))
        for key, value in got.items():
            checks.expect(f"{name}: {key}", value, published[name][key])


def run_reproduction(settings: Settings, sandbox: Sandbox, runner: ScheduleRunner, artifacts: Path) -> dict[str, Any]:
    package = package_dir(settings)
    files = verify_manifest(package)
    summary = json.loads((package / "derived" / "evidence_summary.json").read_text())
    checks = Checks()
    flagged = [check_buggy_trial(settings, sandbox, runner, t, package, artifacts, checks) for t in TRIALS]
    detections = sum(flagged)
    checks.expect("detections on the buggy fixture", f"{detections} of {len(TRIALS)}",
                  summary["result"]["verified_detection_on_buggy_fixture"])
    check_documented_defect(settings, sandbox, runner, package, artifacts, checks)
    check_baselines(settings, sandbox, runner, package, artifacts, checks)
    return {
        "experiment_id": EXPERIMENT_ID, "package": PACKAGE_NAME, "model_requests": 0, "manifest_files_verified": files,
        "scope": "Buggy-fixture evidence only. A minimal fixture; says nothing about GitLab's production systems.",
        "buggy_fixture_detections": f"{detections} of {len(TRIALS)}",
        "checks_run": len(checks.rows), "checks_failed": len(checks.failed), "failures": checks.failed,
        "checks": checks.rows, "not_reproducible_from_public_files": list(NOT_REPRODUCIBLE),
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"Experiment {report['experiment_id']}, model requests: {report['model_requests']}",
        f"Manifest files verified: {report['manifest_files_verified']}",
        f"Muse Spark detections on the buggy fixture: {report['buggy_fixture_detections']}",
        f"Checks: {report['checks_run'] - report['checks_failed']} of {report['checks_run']} passed",
    ]
    lines += [f"FAILED {f['check']}: got {f['actual']!r}, expected {f['expected']!r}" for f in report["failures"]]
    lines += ["", "Not reproducible from public files:"]
    lines += [f"- {item}" for item in report["not_reproducible_from_public_files"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="also write the full report here")
    args = parser.parse_args(argv)
    settings = load_settings()
    with tempfile.TemporaryDirectory(prefix="deployspecter-repro-") as artifacts:
        with Sandbox(settings) as sandbox, ScheduleRunner(sandbox) as runner:
            report = run_reproduction(settings, sandbox, runner, Path(artifacts))
    print(format_report(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return 1 if report["checks_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
