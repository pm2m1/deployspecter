"""Writes benchmark results as JSON and a Markdown table. Numbers come only from the run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _cell(value: Any) -> str:
    return "n/a" if value is None else str(value)


def to_markdown(report: dict[str, Any]) -> str:
    budget = report["budget"]
    lines = [
        f"# Benchmark: {report['split']} split",
        "",
        f"Generated {report['generated_at']}. Budget per system per case: "
        f"{budget['schedules_per_case']} schedules, {budget['seconds_per_case']}s. "
        f"Model {report['model']}, prompt {report['prompt_version']}, {report['ai_repeats']} AI repeats per case.",
        "",
        "| System | Detected | Verified by replay | Same manifestation as reference | False positives | "
        "Median schedules to failure | Valid tests | Schedules run |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for system, s in report["summary"].items():
        lines.append(
            f"| {system} | {s['detected']}/{s['buggy_runs']} | {s['verified_by_execution']}/{s['buggy_runs']} | "
            f"{s['matches_reference_manifestation']}/{s['buggy_runs']} | "
            f"{s['false_positives']}/{s.get('corrected_runs_executed') or s['corrected_runs']} | "
            f"{_cell(s['median_schedules_to_failure'])} | "
            f"{s['valid_tests']}/{s['proposed_tests']} | {s['schedules_executed']} |"
        )
    ai = report["summary"].get("ai")
    if ai and "input_tokens" in ai:
        lines += [
            "",
            f"AI usage in the responses scored here: {ai['requests']} requests, {ai['input_tokens']} input and "
            f"{ai['output_tokens']} output tokens ({ai['reasoning_tokens']} reasoning), "
            f"{ai['inference_seconds']}s inference, {ai['repair_attempts']} repair attempts, estimated "
            f"${ai['approx_cost_usd']} ({report['pricing_note']}). First responses alone: "
            f"{ai['first_attempt_valid']}/{ai['first_attempt_proposed']} tests valid. Invariants proposed: "
            f"{ai['invariants_proposed']}; screened out by serial runs: {ai['invariants_screened_out']}.",
        ]
    lines += ["", *report.get("notes", [])]
    lines += [
        "",
        "## Per case",
        "",
        "| Case | Label | System | Run | Flagged | Schedules to failure | Serial? | Unverified flows | "
        "Replay | Valid tests | Min steps | Error/rejections |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["scores"]:
        notes = s["error"] or ("; ".join(s["rejections"])[:160] if s["rejections"] else "")
        steps = f"{s['original_steps']}→{s['minimized_steps']}" if s["minimized_steps"] is not None else "n/a"
        valid = f"{s['tests_valid']}/{s['tests_proposed']}" if s["tests_proposed"] is not None else "n/a"
        lines.append(
            f"| {s['case_id']} | {'bug' if s['has_bug'] else 'clean'} | {s['system']} | {s['repeat']} | "
            f"{'yes' if s['flagged'] else 'no'} | {_cell(s['schedules_to_first_failure'])} | "
            f"{_cell(s['serial_order_violation'])} | {_cell(s['unverified_flows_in_detecting_test'])} | "
            f"{_cell(s['replay_ok'])} | {valid} | {steps} | {notes} |"
        )
    return "\n".join(lines) + "\n"


def usage_ledger(responses_dir: Path) -> dict[str, Any]:
    """Totals from saved responses. Token counts are API-reported; costs are estimates, not billing."""
    versions: dict[str, dict[str, Any]] = {}
    for path in sorted(responses_dir.glob("*/*.json")):
        totals = versions.setdefault(path.parent.name, dict.fromkeys(
            ("requests", "input_tokens", "output_tokens", "reasoning_tokens"), 0
        ) | {"latency_seconds": 0.0, "estimated_usd": 0.0})
        for attempt in json.loads(path.read_text())["attempts"]:
            reply = attempt["reply"]
            totals["requests"] += 1
            for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
                totals[field] += reply[field] or 0
            totals["latency_seconds"] = round(totals["latency_seconds"] + reply["latency_seconds"], 3)
            totals["estimated_usd"] = round(totals["estimated_usd"] + (reply["approx_cost_usd"] or 0), 6)
    return {"versions": versions, "scope": "saved responses only; a killed in-flight request leaves no record"}


def write_report(report: dict[str, Any], directory: Path, update_latest: bool = False) -> Path:
    """Always writes a new timestamped report. ``latest_*`` moves only on request, or for a first report."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{report['split']}_{report['generated_at'].replace(':', '').replace('+0000', 'Z')}"
    (directory / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str))
    path = directory / f"{stem}.md"
    path.write_text(to_markdown(report))
    latest = directory / f"latest_{report['split']}.json"
    if update_latest or not latest.exists():
        latest.write_text(json.dumps(report, indent=2, default=str))
    (directory / f"{report['split']}_partial.jsonl").unlink(missing_ok=True)
    return path
