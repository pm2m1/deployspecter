"""Local FastAPI service. Every value it returns comes from a real execution or saved result."""

from __future__ import annotations

import json
import re
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any, Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .analyzer import PROMPT_VERSION, AnalysisRecord
from .analyzer_support import read_catalog
from .benchmark_report import usage_ledger
from .config import PROJECT_ROOT, load_settings, redact
from .database import Sandbox
from .documented import EXTERNAL_CASES, documented_source, run_documented
from .meta_client import PRICING_NOTE
from .models import ExecutableSpec
from .pipeline import SystemRun, compile_all, run_ai, run_baseline, run_baseline_c, run_hand
from .replay import load_artifact, replay
from .report import render_markdown, timeline
from .scenarios import VARIANTS, Case, list_scenarios, load_case
from .scheduler import ScheduleRunner

app = FastAPI(title="DeploySpecter")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_runs: dict[str, dict[str, Any]] = {}
_exec_lock = threading.Lock()  # the sandbox runs one exploration at a time


class RunRequest(BaseModel):
    variant: Literal["buggy", "corrected"]
    generator: Literal["hand", "baseline", "baseline_c", "ai"] = "hand"
    repeat: int = Field(default=0, ge=0, le=2)
    max_schedules: int = Field(default=300, ge=1, le=2000)
    max_seconds: float = Field(default=60, gt=0, le=300)


class DocumentedRequest(BaseModel):
    variant: Literal["buggy", "corrected"]


def _runs_dir() -> Path:
    return load_settings().results_dir / "runs"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/health")
def health() -> dict[str, Any]:
    settings = load_settings()
    try:
        with psycopg.connect(settings.admin_dsn, connect_timeout=3) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
        database: dict[str, Any] = {"reachable": True, "server_version": version}
    except psycopg.Error as exc:
        database = {"reachable": False, "error": type(exc).__name__}
    return {"status": "ok" if database["reachable"] else "degraded", "database": database,
            "model": settings.model_name, "model_key_configured": bool(settings.model_api_key)}


@app.get("/scenarios")
def scenarios() -> list[dict[str, Any]]:
    settings = load_settings()
    out = []
    for scenario_id in list_scenarios(settings.scenarios_dir):
        case = load_case(settings.scenarios_dir, scenario_id, "buggy")
        out.append({
            "id": scenario_id, "title": case.title, "split": case.split, "description": case.description,
            "variants": list(VARIANTS), "requirements": [r.text for r in case.requirements],
        })
    return out


def _spec_view(spec: ExecutableSpec) -> dict[str, Any]:
    return {
        "rationale": spec.rationale,
        "seed": [{"table": s.table, "values": {v.column: v.value for v in s.values}} for s in spec.seed],
        "actors": [{
            "name": a.name, "function": a.function,
            "steps": [s.sql or s.kind.upper() for s in a.steps],
        } for a in spec.actors],
    }


def _view(run: SystemRun, case: Case) -> dict[str, Any]:
    tests = [{
        "name": t.exploration.spec_name, "space_size": t.exploration.space_size,
        "executed": t.exploration.executed, "exhausted_space": t.exploration.exhausted_space,
        "classifications": t.exploration.classifications, "outcomes": t.exploration.outcomes,
        "violations": t.exploration.violating_total, "deferred": t.exploration.deferred_schedules,
        "non_serial_final_states": t.exploration.non_serial_final_states,
        "renamed_actors": t.renamed_actors,
        "spec": _spec_view(t.spec) if t.spec else None,
        "unverified_flows": t.unverified_flows, "dropped_invariants": t.dropped_invariants,
        "invariants": [i.model_dump() for i in t.exploration.invariants],
    } for t in run.runs]
    view: dict[str, Any] = {
        "case_id": case.case_id, "generator": run.system, "detected": run.detected, "error": run.error,
        "tests_proposed": run.tests_proposed, "tests_valid": run.tests_valid,
        "rejections": [r.model_dump() for r in run.rejections], "tests": tests,
        "schedules_executed": run.schedules_executed, "schedule_budget": run.schedule_budget,
        "schedules_to_first_failure": run.schedules_to_first_failure,
        "seconds_to_first_failure": run.seconds_to_first_failure,
        "elapsed_seconds": run.elapsed_seconds, "notes": run.notes,
        "serial_order_violation": run.violates_in_serial_order,
        "requirements": [r.text for r in case.requirements],
    }
    if run.analysis:
        attempts = run.analysis.attempts
        view["analysis"] = {
            "model": run.analysis.model, "prompt_version": run.analysis.prompt_version,
            "attempts": [a.kind for a in attempts],
            "input_tokens": sum(a.reply.input_tokens or 0 for a in attempts),
            "output_tokens": sum(a.reply.output_tokens or 0 for a in attempts),
            "latency_seconds": round(sum(a.reply.latency_seconds for a in attempts), 2),
            "conflicts": [c.model_dump() for c in run.analysis.output.conflicts] if run.analysis.output else [],
        }
    if run.detected and run.minimized:
        artifact = load_artifact(PROJECT_ROOT / run.minimized_artifact_path)
        trace, verdict = run.minimized.trace, run.minimized.verdict
        view |= {
            "actors": [a.model_dump() for a in run.minimized.spec.actors],
            "timeline": timeline(trace), "schedule": trace.requested_schedule, "issue_order": trace.issue_order,
            "initial_state": trace.initial_state, "final_state": trace.final_state,
            "classification": verdict.classification,
            "violations": [v.model_dump() for v in verdict.violations], "assumptions": verdict.assumptions,
            "replay": run.replay.model_dump() if run.replay else None,
            "minimized_replay": run.minimized_replay.model_dump() if run.minimized_replay else None,
            "minimized": {"before": run.minimized.before, "after": run.minimized.after,
                          "attempts": run.minimized.attempts},
            "replay_command": f"uv run deployspecter replay {run.minimized_artifact_path}",
            "artifact_path": run.minimized_artifact_path,
            "report_markdown": render_markdown(artifact, run.minimized_artifact_path),
        }
    else:
        clean = next((t.exploration.first_clean_trace for t in run.runs if t.exploration.first_clean_trace), None)
        if clean:
            view |= {"timeline": timeline(clean), "schedule": clean.requested_schedule,
                     "issue_order": clean.issue_order, "initial_state": clean.initial_state,
                     "final_state": clean.final_state, "example_note": "First schedule with no violation."}
    return view


def _execute(run_id: str, scenario_id: str, request: RunRequest) -> None:
    record = _runs[run_id]
    settings = load_settings()
    try:
        case = load_case(settings.scenarios_dir, scenario_id, request.variant)
        artifacts = settings.results_dir / "failures" / "ui" / run_id
        with _exec_lock:
            record["status"] = "running"
            with Sandbox(settings) as sandbox, ScheduleRunner(sandbox) as runner:
                shared = (sandbox, runner, case, request.max_schedules, request.max_seconds, artifacts)
                if request.generator == "hand":
                    run = run_hand(*shared)
                elif request.generator == "baseline":
                    run = run_baseline(*shared)
                elif request.generator == "baseline_c":
                    run = run_baseline_c(*shared)
                else:
                    cache = (settings.results_dir / "ai_responses" / PROMPT_VERSION
                             / f"{case.case_id.replace('/', '__')}__r{request.repeat}.json")
                    # The interface never spends credits: it only replays recorded responses.
                    run = run_ai(*shared, None, cache, reuse=True)
        record |= {"status": "finished", "result": _view(run, case)}
    except Exception as exc:  # surfaced to the caller as a failed run, never as a clean result
        traceback.print_exc()
        record |= {"status": "failed", "error": redact(f"{type(exc).__name__}: {str(exc)[:500]}")}
    finally:
        directory = _runs_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{run_id}.json").write_text(json.dumps(record, indent=2, default=str))


@app.get("/scenarios/{scenario_id}/source")
def scenario_source(scenario_id: str) -> dict[str, Any]:
    """Application code and migrations for both variants, and which files differ between them."""
    settings = load_settings()
    if scenario_id not in list_scenarios(settings.scenarios_dir):
        raise HTTPException(404, "unknown scenario")
    variants = {v: load_case(settings.scenarios_dir, scenario_id, v).files for v in VARIANTS}
    return {
        "variants": variants,
        "differing_files": sorted(n for n in variants["buggy"] if variants["buggy"][n] != variants["corrected"].get(n)),
    }


@app.post("/runs/{run_id}/replay")
def replay_run(run_id: str, attempts: int = Query(default=3, ge=1, le=10)) -> dict[str, Any]:
    """Re-executes the saved failing schedule on PostgreSQL and compares observed behaviour."""
    result = get_run(run_id).get("result") or {}
    if not result.get("artifact_path"):
        raise HTTPException(409, "this run has no saved failure to replay")
    artifact = load_artifact(PROJECT_ROOT / result["artifact_path"])
    with _exec_lock, Sandbox(load_settings()) as sandbox, ScheduleRunner(sandbox) as runner:
        report = replay(artifact, runner, attempts=attempts)
    return {"artifact_path": result["artifact_path"], **report.model_dump()}


@app.post("/scenarios/{scenario_id}/run", status_code=202)
def start_run(scenario_id: str, request: RunRequest) -> dict[str, Any]:
    if scenario_id not in list_scenarios(load_settings().scenarios_dir):
        raise HTTPException(404, "unknown scenario")
    run_id = uuid.uuid4().hex
    _runs[run_id] = {"run_id": run_id, "scenario_id": scenario_id, "request": request.model_dump(), "status": "queued"}
    threading.Thread(target=_execute, args=(run_id, scenario_id, request), daemon=True).start()
    return {"run_id": run_id, "status": "queued"}


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    if not _RUN_ID.match(run_id):
        raise HTTPException(404, "unknown run")
    if run_id in _runs:
        return _runs[run_id]
    path = _runs_dir() / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "unknown run")
    return json.loads(path.read_text())


def _latest_report(split: str) -> dict[str, Any] | None:
    path = load_settings().results_dir / "benchmark" / f"latest_{split}.json"
    return json.loads(path.read_text()) if path.exists() else None


@app.get("/comparison")
def comparison() -> dict[str, Any]:
    """Saved benchmark per split, plus Baseline C's own newest report. Empty when nothing has been run."""
    out: dict[str, Any] = {}
    for split in ("dev", "heldout"):
        report = _latest_report(split)
        if report:
            out[split] = report | {"pricing_note": PRICING_NOTE}
    baseline_c = sorted((load_settings().results_dir / "baseline_c").glob("all_*.json"))
    if baseline_c:
        out["baseline_c"] = json.loads(baseline_c[-1].read_text())
    return out


@app.get("/usage")
def usage() -> dict[str, Any]:
    return usage_ledger(load_settings().results_dir / "ai_responses")


@app.get("/scenarios/{scenario_id}/recordings")
def recordings(scenario_id: str) -> dict[str, list[int]]:
    settings = load_settings()
    if scenario_id not in list_scenarios(settings.scenarios_dir):
        raise HTTPException(404, "unknown scenario")
    directory = settings.results_dir / "ai_responses" / PROMPT_VERSION
    return {
        variant: [r for r in range(3) if (directory / f"{scenario_id}__{variant}__r{r}.json").exists()]
        for variant in VARIANTS
    }


@app.get("/scenarios/{scenario_id}/ai-pipeline")
def ai_pipeline(
    scenario_id: str, variant: Literal["buggy", "corrected"] = "buggy", repeat: int = Query(default=0, ge=0, le=2)
) -> dict[str, Any]:
    """The path from source to generated test, built from one recorded response. Sends no model request."""
    settings = load_settings()
    if scenario_id not in list_scenarios(settings.scenarios_dir):
        raise HTTPException(404, "unknown scenario")
    case = load_case(settings.scenarios_dir, scenario_id, variant)
    path = settings.results_dir / "ai_responses" / PROMPT_VERSION / f"{scenario_id}__{variant}__r{repeat}.json"
    if not path.exists():
        raise HTTPException(404, f"no recorded model response for {case.case_id} repeat {repeat}")
    record = AnalysisRecord.model_validate_json(path.read_text())
    source = case.source_model()
    catalog = [
        {
            "statement_id": st.statement_id, "function": fn.ref, "sql": st.sql, "params": st.placeholders,
            "required": not (st.conditional or st.in_loop),
        }
        for fn in source.functions.values() for st in fn.statements
    ]
    tests = []
    with _exec_lock, Sandbox(settings) as sandbox:
        known = read_catalog(sandbox, case.setup_sql)
        for proposal in record.output.tests if record.output else []:
            reports, rejected = compile_all([proposal], case, sandbox, "ai")
            tests.append({
                "proposal": proposal.model_dump(),
                "accepted": bool(reports),
                "rejections": [reason for r in rejected for reason in r.reasons],
                "renamed_actors": reports[0].renamed_actors if reports else {},
                "dropped_invariants": reports[0].dropped_invariants if reports else [],
            })
    report = _latest_report(case.split)
    score = next(
        (s for s in (report or {}).get("scores", [])
         if s["case_id"] == case.case_id and s["system"] == "ai" and s["repeat"] == repeat),
        None,
    )
    return {
        "case_id": case.case_id, "split": case.split, "repeat": repeat, "files": case.files,
        "tables": sorted(known), "catalog": catalog, "model": record.model,
        "prompt_version": record.prompt_version, "prompt_sha256": record.prompt_sha256,
        "attempts": [{
            "kind": a.kind, "status": a.reply.status, "input_tokens": a.reply.input_tokens,
            "output_tokens": a.reply.output_tokens, "latency_seconds": a.reply.latency_seconds,
            "parse_error": bool(a.parse_error),
        } for a in record.attempts],
        "operations": [o.model_dump() for o in record.output.operations] if record.output else [],
        "conflicts": [c.model_dump() for c in record.output.conflicts] if record.output else [],
        "tests": tests,
        "as_scored_in_saved_benchmark": score and {
            "tests_valid": score["tests_valid"], "tests_proposed": score["tests_proposed"],
            "first_attempt_valid": score.get("first_attempt_valid"),
            "first_attempt_proposed": score.get("first_attempt_proposed"),
            "rejections": score["rejections"], "generated_at": report["generated_at"],
        },
    }


@app.get("/external-cases")
def external_cases() -> list[dict[str, Any]]:
    return list(EXTERNAL_CASES.values())


@app.get("/external-cases/{case_id}/source")
def external_source(case_id: str) -> dict[str, Any]:
    if case_id not in EXTERNAL_CASES:
        raise HTTPException(404, "unknown external case")
    return documented_source(load_settings(), case_id)


def _execute_documented(run_id: str, case_id: str, variant: str) -> None:
    record = _runs[run_id]
    settings = load_settings()
    try:
        artifacts = settings.results_dir / "failures" / "ui" / run_id
        with _exec_lock:
            record["status"] = "running"
            with Sandbox(settings) as sandbox, ScheduleRunner(sandbox) as runner:
                run, case, focused, facts = run_documented(sandbox, runner, settings, case_id, variant, artifacts)
                view = _view(run, case)
        record |= {"status": "finished", "result": view | {
            "timeline": timeline(focused), "schedule": focused.requested_schedule, "issue_order": focused.issue_order,
            "initial_state": focused.initial_state, "final_state": focused.final_state, "documented": facts,
        }}
    except Exception as exc:  # surfaced to the caller as a failed run, never as a clean result
        traceback.print_exc()
        record |= {"status": "failed", "error": redact(f"{type(exc).__name__}: {str(exc)[:500]}")}
    finally:
        directory = _runs_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{run_id}.json").write_text(json.dumps(record, indent=2, default=str))


@app.post("/external-cases/{case_id}/documented-defect/run", status_code=202)
def start_documented(case_id: str, request: DocumentedRequest) -> dict[str, Any]:
    if case_id not in EXTERNAL_CASES:
        raise HTTPException(404, "unknown external case")
    run_id = uuid.uuid4().hex
    _runs[run_id] = {"run_id": run_id, "external_case": case_id, "request": request.model_dump(), "status": "queued"}
    threading.Thread(target=_execute_documented, args=(run_id, case_id, request.variant), daemon=True).start()
    return {"run_id": run_id, "status": "queued"}
