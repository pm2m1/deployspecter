"""The defect GitLab published in MR !246505, run on the isolated fixture. Sends no model requests.

This module loads one test only, from its own file, and only the buggy and corrected variants.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .analyzer_support import read_catalog
from .config import PROJECT_ROOT, Settings
from .database import Sandbox
from .models import ExecutionTrace, Requirement, TestProposal
from .pipeline import SystemRun, compile_all, run_tests
from .replay import load_artifact, replay
from .scenarios import Case, load_case
from .scheduler import ScheduleRunner
from .validation import compile_proposal

SCENARIO = "gitlab_authorization_sync"
PUBLIC_VARIANTS = ("buggy", "corrected")
_MR_BASE = "https://gitlab.com/gitlab-org/gitlab/-/merge_requests"

EXTERNAL_CASES: dict[str, dict[str, Any]] = {
    SCENARIO: {
        "id": SCENARIO,
        "title": "GitLab authorization sync",
        "links": [
            {"label": "GitLab MR !246505 (trigger fix)", "url": f"{_MR_BASE}/246505"},
            {"label": "GitLab MR !246509 (backfill requeue)", "url": f"{_MR_BASE}/246509"},
        ],
        "documented_by_gitlab": [
            "project_authorizations can hold several rows per user and project, one per access level.",
            "The migration table holds one row per user and project, kept in sync by a trigger during a backfill.",
            "When one access level row was deleted, the trigger removed the pair even though another access level "
            "remained.",
            "The backfill inserts with ON CONFLICT DO NOTHING, so it did not repair the lost row until it was "
            "re-queued.",
            "GitLab reports that a row went missing on GitLab.com this way. That incident is not reproduced here.",
        ],
        "limitations": [
            "This is a minimal fixture with its own table names, not GitLab's schema, services, locks or workload.",
            "It shows the trigger logic in isolated PostgreSQL. It says nothing about GitLab's production systems.",
            "Passing this test means this test passed. It is not a proof that a trigger is safe in general.",
        ],
    }
}


def _root(settings: Settings, case_id: str) -> Path:
    return settings.external_scenarios_dir / case_id


def documented_source(settings: Settings, case_id: str) -> dict[str, Any]:
    variants = {v: load_case(settings.external_scenarios_dir, case_id, v).files for v in PUBLIC_VARIANTS}
    return {
        "variants": variants,
        "differing_files": sorted(n for n in variants["buggy"] if variants["buggy"][n] != variants["corrected"].get(n)),
    }


def _load_test(settings: Settings, case_id: str) -> tuple[TestProposal, str]:
    doc = json.loads((_root(settings, case_id) / "base" / "documented_test.json").read_text())
    return TestProposal.model_validate(doc["test"]), doc["focus_actor"]


def _pair_rows(state, table: str, key_columns: list[str], pairs: set[tuple[str, ...]]) -> list[dict[str, Any]]:
    return [r for r in state.get(table, []) if tuple(str(r[c]) for c in key_columns) in pairs]


def documented_facts(focused: ExecutionTrace, requirement: Requirement, run: SystemRun) -> dict[str, Any]:
    """Everything here is read from PostgreSQL state and the trace, never assumed from the variant name."""
    source, dest, keys = requirement.source_table or "", requirement.table, requirement.key_columns
    pairs = {tuple(str(r[c]) for c in keys) for r in focused.initial_state.get(source, [])}
    source_after = _pair_rows(focused.final_state, source, keys, pairs)
    dest_before = _pair_rows(focused.initial_state, dest, keys, pairs)
    dest_after = _pair_rows(focused.final_state, dest, keys, pairs)
    disappeared = [r for r in dest_before if r not in dest_after] if not dest_after else []
    exploration = run.runs[0].exploration
    serial = exploration.violates_in_serial_order
    return {
        "source_table": source, "destination_table": dest, "key_columns": keys,
        "source_before": _pair_rows(focused.initial_state, source, keys, pairs), "source_after": source_after,
        "destination_before": dest_before, "destination_after": dest_after,
        "destination_row_disappeared": disappeared, "source_rows_remaining": source_after,
        "schedules": exploration.executed, "space_size": exploration.space_size,
        "exhausted_space": exploration.exhausted_space, "violating_schedules": exploration.violating_total,
        "fails_in_serial_order": serial,
        "failure_class": "serial_migration_defect" if serial else (
            "concurrency_dependent" if exploration.violating_total else "none_in_this_test"
        ),
        "transactions_in_minimal_failure": sum(
            1 for r in (run.minimized.trace.records if run.minimized else []) if r.kind == "begin"
        ),
        "actors_in_minimal_failure": run.minimized.after["actors"] if run.minimized else None,
    }


def run_documented(
    sandbox: Sandbox, runner: ScheduleRunner, settings: Settings, case_id: str, variant: str, artifacts: Path
) -> tuple[SystemRun, Case, ExecutionTrace, dict[str, Any]]:
    if variant not in PUBLIC_VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    case = load_case(settings.external_scenarios_dir, case_id, variant)
    proposal, focus = _load_test(settings, case_id)
    run = SystemRun(system="hand", case_id=case.case_id)
    reports, run.rejections = compile_all([proposal], case, sandbox, "hand")
    run.tests_proposed, run.tests_valid = 1, len(reports)
    if not reports:
        raise RuntimeError(f"documented test rejected: {run.rejections}")
    run_tests(sandbox, runner, case, reports, run, 300, 60.0, artifacts)
    # The same single transaction on both variants, so the side-by-side comparison is like for like.
    compiled = compile_proposal(proposal, case.source_model(), read_catalog(sandbox, case.setup_sql), "hand").spec
    spec = compiled.model_copy(update={"actors": [a for a in compiled.actors if a.name == focus]})
    focused = runner.run(spec, case.setup_sql, [focus] * len(spec.actors[0].steps))
    requirement = next(r for r in case.requirements if r.kind == "replica_pair_retained")
    return run, case, focused, documented_facts(focused, requirement, run)


def write_documented_report(settings: Settings, out_dir: Path | None = None) -> tuple[dict[str, Any], Path]:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = (out_dir or settings.results_dir / "external_public" / SCENARIO) / stamp
    report: dict[str, Any] = {
        "scenario": SCENARIO, "generated_at": stamp, "model_requests": 0,
        "scope": EXTERNAL_CASES[SCENARIO]["limitations"],
        "sources": EXTERNAL_CASES[SCENARIO]["links"], "variants": {},
    }
    with Sandbox(settings) as sandbox, ScheduleRunner(sandbox) as runner:
        for variant in PUBLIC_VARIANTS:
            run, _, _, facts = run_documented(sandbox, runner, settings, SCENARIO, variant, target / "failures")
            entry = {
                "facts": facts, "schedules": facts["schedules"], "violating_schedules": facts["violating_schedules"],
            }
            if run.minimized_artifact_path:
                path = PROJECT_ROOT / run.minimized_artifact_path
                entry |= {
                    "artifact": run.minimized_artifact_path,
                    "replay_reproduced": replay(load_artifact(path), runner, attempts=3).reproduced,
                }
            report["variants"][variant] = entry
    target.mkdir(parents=True, exist_ok=True)
    path = target / "report.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    return report, path
