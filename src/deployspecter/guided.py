"""The stale-backfill walkthrough: real schedules on PostgreSQL, and recordings of them for a static site.

Schedules come from a fixed list, and tests from the benchmark's hand-written spec or a recorded Muse Spark
response. Nothing here accepts SQL from a caller or sends a model request.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .analyzer import AnalysisRecord
from .analyzer_support import read_catalog
from .config import PROJECT_ROOT, Settings
from .database import Sandbox
from .explorer import Budget, explore
from .meta_client import PRICING_NOTE
from .minimizer import minimize
from .models import ExecutableSpec, ExecutionTrace, OracleVerdict, TestProposal
from .oracle import evaluate
from .replay import make_artifact, replay, save_artifact
from .scenarios import Case, load_case
from .scheduler import ScheduleRunner
from .validation import SpecRejected, compile_proposal

CASE_ID = "backfill_overwrite"
RECORDED_RESPONSE = "results/ai_responses/v2/backfill_overwrite__buggy__r0.json"
WALKTHROUGH_FILE = Path(__file__).parent / "static" / "data" / "walkthrough.json"
SOURCES = {
    "reference": "Hand-written reference test",
    "muse": "Muse Spark recorded test",
}
PRESETS = {
    "stale_read": {
        "label": "Job reads, user commits, job writes",
        "summary": "The job reads the row, then the user's update commits before the job writes.",
        "plan": [("job", 4), ("user", 3), ("job", 2)],
    },
    "job_first": {
        "label": "Job finishes first",
        "summary": "A serial order: the whole job, then the user.",
        "plan": [("job", 6), ("user", 3)],
    },
    "user_first": {
        "label": "User finishes first",
        "summary": "A serial order: the user, then the whole job.",
        "plan": [("user", 3), ("job", 6)],
    },
}
LANES = {"job": "Background job", "user": "New application"}


def _proposal(settings: Settings, source: str, case: Case) -> TestProposal:
    if source == "reference":
        return load_case(settings.scenarios_dir, CASE_ID, "buggy").hand_spec or case.hand_spec
    record = AnalysisRecord.model_validate_json((PROJECT_ROOT / RECORDED_RESPONSE).read_text())
    return record.output.tests[0]


def _spec(sandbox: Sandbox, settings: Settings, source: str, case: Case) -> ExecutableSpec:
    catalog = read_catalog(sandbox, case.setup_sql)
    return compile_proposal(_proposal(settings, source, case), case.source_model(), catalog, source).spec


def _role(spec_actor: str, spec: ExecutableSpec) -> str:
    actor = next(a for a in spec.actors if a.name == spec_actor)
    return "job" if "backfill" in actor.function else "user"


def schedule_for(spec: ExecutableSpec, preset: str) -> list[str]:
    names = {_role(a.name, spec): a.name for a in spec.actors}
    return [names[role] for role, count in PRESETS[preset]["plan"] for _ in range(count)]


def _describe(record, rows_value: str | None) -> str:
    if record.kind != "sql":
        return record.kind.upper()
    if record.status == "skipped_no_rows":
        return "skipped: the read found no rows"
    if record.status != "ok":
        return record.status.replace("_", " ")
    if record.rows is not None:
        return f"reads legacy_name = {rows_value}" if record.rows else "reads no rows"
    value = record.params.get("display_name")
    return f"writes display_name = {value}" if record.rowcount else f"tries {value}: no row matched"


def build_events(spec: ExecutableSpec, trace: ExecutionTrace) -> list[dict[str, Any]]:
    """Per-step view, with the committed value derived from the trace and checked against PostgreSQL below."""
    committed = trace.initial_state["profiles"][0].get("display_name") if trace.initial_state["profiles"] else None
    pending: dict[str, Any] = {}
    events = []
    for record in trace.records:
        role = _role(record.actor, spec)
        read = record.rows[0].get("legacy_name") if record.rows else None
        if record.kind == "sql" and record.status == "ok" and record.rows is None and record.rowcount:
            pending[record.actor] = record.params.get("display_name")
        if record.kind == "commit" and record.committed and record.actor in pending:
            committed = pending.pop(record.actor)
        events.append({
            "seq": record.seq, "actor": record.actor, "role": role, "lane": LANES[role], "kind": record.kind,
            "label": _describe(record, read), "sql": record.sql, "params": record.params, "status": record.status,
            "rowcount": record.rowcount, "rows": record.rows, "committed": record.committed,
            "blocked_by": record.blocked_by, "committed_value": committed,
        })
    return events


def _marks(events: list[dict[str, Any]], verdict: OracleVerdict) -> dict[str, int | None]:
    if not verdict.violations:
        return {"user_commit_seq": None, "overwrite_seq": None, "overwrite_commit_seq": None}
    user_commit = next(
        (e["seq"] for e in events if e["role"] == "user" and e["kind"] == "commit" and e["committed"]), None
    )
    overwrite = next(
        (e["seq"] for e in events
         if e["role"] == "job" and e["kind"] == "sql" and e["rows"] is None and e["rowcount"]
         and e["seq"] > user_commit),
        None,
    )
    job_commit = next(
        (e["seq"] for e in events if e["role"] == "job" and e["kind"] == "commit" and overwrite is not None
         and e["seq"] > overwrite),
        None,
    )
    return {"user_commit_seq": user_commit, "overwrite_seq": overwrite, "overwrite_commit_seq": job_commit}


def run_view(spec: ExecutableSpec, trace: ExecutionTrace, verdict: OracleVerdict, **fields: Any) -> dict[str, Any]:
    events = build_events(spec, trace)
    final = trace.final_state["profiles"][0]["display_name"] if trace.final_state["profiles"] else None
    if events and events[-1]["committed_value"] != final:
        raise RuntimeError("derived committed value disagrees with the final state read from PostgreSQL")
    return {
        **fields, "schedule": trace.requested_schedule, "issue_order": trace.issue_order, "outcome": trace.outcome,
        "events": events, "marks": _marks(events, verdict), "initial_state": trace.initial_state,
        "final_state": trace.final_state, "final_value": final, "classification": verdict.classification,
        "violations": [v.model_dump() for v in verdict.violations],
        "expected_value": verdict.violations[0].expected if verdict.violations else None,
        "fingerprint": trace.fingerprint(),
    }


def execute(
    sandbox: Sandbox, runner: ScheduleRunner, settings: Settings, source: str, variant: str, preset: str,
    artifacts: Path | None = None, replay_attempts: int = 0,
) -> dict[str, Any]:
    case = load_case(settings.scenarios_dir, CASE_ID, variant)
    spec = _spec(sandbox, settings, source, case)
    trace = runner.run(spec, case.setup_sql, schedule_for(spec, preset))
    verdict = evaluate(case.requirements, trace)
    view = run_view(spec, trace, verdict, source=source, variant=variant, preset=preset)
    if verdict.violations:
        reduced = minimize(runner, spec, case.setup_sql, case.requirements, trace.requested_schedule)
        artifact = make_artifact(
            case.case_id, case.setup_sql, case.requirements, reduced.spec, reduced.trace, reduced.verdict,
            provenance={"system": source, "preset": preset},
        )
        view["minimized"] = {"before": reduced.before, "after": reduced.after}
        if artifacts is not None:
            path = save_artifact(artifact, artifacts / f"{source}_{variant}_{preset}.json")
            inside = path.is_relative_to(PROJECT_ROOT)
            view["artifact_path"] = str(path.relative_to(PROJECT_ROOT)) if inside else str(path)
        if replay_attempts:
            view["replay"] = replay(artifact, runner, attempts=replay_attempts).model_dump(
                include={"attempts", "fingerprints_match", "violations_match", "reproduced"}
            )
    return view


def explore_summary(sandbox: Sandbox, runner: ScheduleRunner, settings: Settings, source: str, variant: str) -> dict:
    case = load_case(settings.scenarios_dir, CASE_ID, variant)
    spec = _spec(sandbox, settings, source, case)
    result = explore(runner, spec, case.setup_sql, case.requirements, Budget(max_schedules=300, max_seconds=60))
    return {
        "source": source, "variant": variant, "executed": result.executed, "space_size": result.space_size,
        "exhausted_space": result.exhausted_space, "violating": result.violating_total,
        "violates_in_serial_order": result.violates_in_serial_order, "outcomes": result.outcomes,
    }


def static_context(settings: Settings, sandbox: Sandbox) -> dict[str, Any]:
    """Source, catalog, recorded model output and validator verdict for the walkthrough's opening steps."""
    buggy = load_case(settings.scenarios_dir, CASE_ID, "buggy")
    corrected = load_case(settings.scenarios_dir, CASE_ID, "corrected")
    record = AnalysisRecord.model_validate_json((PROJECT_ROOT / RECORDED_RESPONSE).read_text())
    proposal = record.output.tests[0]
    attempt = record.attempts[0].reply
    catalog = read_catalog(sandbox, buggy.setup_sql)
    source = buggy.source_model()
    try:
        compile_proposal(proposal, source, catalog, "muse")
        verdict = {"accepted": True, "reasons": []}
    except SpecRejected as exc:  # shown as recorded; a rejected test would simply not be executed
        verdict = {"accepted": False, "reasons": exc.reasons}
    return {
        "scenario": {
            "id": CASE_ID, "title": buggy.title, "description": buggy.description,
            "requirement": buggy.requirements[0].text, "requirement_id": buggy.requirements[0].id,
            "assumptions": buggy.requirements[0].assumptions,
        },
        "files": {
            "buggy": {n: buggy.files[n] for n in ("app_v1.py", "app_v2.py", "backfill.py", "migration.sql")},
            "corrected_backfill": corrected.files["backfill.py"],
        },
        "catalog": [
            {"statement_id": s.statement_id, "function": fn.ref, "sql": s.sql, "params": s.placeholders}
            for fn in source.functions.values() for s in fn.statements
        ],
        "muse": {
            "response_file": RECORDED_RESPONSE, "model": record.model, "prompt_version": record.prompt_version,
            "input_tokens": attempt.input_tokens, "output_tokens": attempt.output_tokens,
            "latency_seconds": attempt.latency_seconds, "approx_cost_usd": attempt.approx_cost_usd,
            "pricing_note": PRICING_NOTE, "tests_in_response": len(record.output.tests),
            "conflict": record.output.conflicts[0].model_dump(), "proposal": proposal.model_dump(),
            "validator": verdict,
        },
    }


def build_recording(settings: Settings, generated_at: str | None = None) -> dict[str, Any]:
    with Sandbox(settings) as sandbox, ScheduleRunner(sandbox) as runner:
        version = sandbox.admin.execute("SHOW server_version").fetchone()[0].split(" ")[0]
        isolation = sandbox.admin.execute("SHOW default_transaction_isolation").fetchone()[0]
        runs = {
            f"{source}/{variant}/{preset}": execute(
                sandbox, runner, settings, source, variant, preset, replay_attempts=3
            )
            for source in SOURCES for variant in ("buggy", "corrected") for preset in PRESETS
        }
        exploration = {
            f"{source}/{variant}": explore_summary(sandbox, runner, settings, source, variant)
            for source in SOURCES for variant in ("buggy", "corrected")
        }
        context = static_context(settings, sandbox)
    return {
        "generated_at": generated_at or dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"),
        "postgres_version": version, "isolation": isolation, "model_requests": 0,
        "sources": SOURCES, "presets": {k: {"label": v["label"], "summary": v["summary"]} for k, v in PRESETS.items()},
        **context, "runs": runs, "exploration": exploration,
    }


def write_recording(settings: Settings, path: Path = WALKTHROUGH_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_recording(settings), indent=1, default=str) + "\n")
    return path
