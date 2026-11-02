"""Builds the public site's measured data from saved results, and exports a static copy.

The landing page and walkthrough read these files. Every number comes from a saved report, and the export
copies an explicit list of files, so nothing else in the repository can reach it.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
from pathlib import Path
from typing import Any

from .config import Settings

STATIC = Path(__file__).parent / "static"
SITE_DATA = STATIC / "data" / "site.json"
EXPORT_FILES = {
    "landing.html": "index.html",
    "demo.html": "demo.html",
    "favicon.svg": "static/favicon.svg",
    "css/tokens.css": "static/css/tokens.css",
    "css/site.css": "static/css/site.css",
    "js/landing.js": "static/js/landing.js",
    "js/demo.js": "static/js/demo.js",
    "js/lib/dom.js": "static/js/lib/dom.js",
    "js/lib/timeline.js": "static/js/lib/timeline.js",
    "js/lib/guided.js": "static/js/lib/guided.js",
    "data/walkthrough.json": "static/data/walkthrough.json",
    "data/site.json": "static/data/site.json",
}
SYSTEMS = {
    "ai": "Muse Spark (recorded)",
    "baseline_b": "Baseline B (non-LLM)",
    "baseline_c": "Baseline C (non-LLM, added later)",
    "hand": "Hand-written",
    "baseline_a_static": "Static rules",
}


def rollup(scores: list[dict[str, Any]]) -> dict[str, Any]:
    bugs = [s for s in scores if s["has_bug"]]
    clean = [s for s in scores if not s["has_bug"]]
    executes = any(s.get("tests_proposed") is not None for s in scores)
    ran = [s for s in clean if s.get("schedules_executed")] if executes else clean
    firsts = [s["schedules_to_first_failure"] for s in bugs if s.get("schedules_to_first_failure")]
    return {
        "defects_found": sum(s["flagged"] for s in bugs), "defect_runs": len(bugs),
        "replayed": sum(bool(s.get("replay_ok")) for s in bugs), "executes": executes,
        "false_flags": sum(s["flagged"] for s in ran), "corrected_runs_executed": len(ran),
        "corrected_runs_not_executed": len(clean) - len(ran),
        "median_schedules_to_failure": statistics.median(firsts) if firsts else None,
        "valid_tests": sum(s.get("tests_valid") or 0 for s in scores),
        "proposed_tests": sum(s.get("tests_proposed") or 0 for s in scores),
        "first_response_valid": sum(s.get("first_attempt_valid") or 0 for s in scores),
        "first_response_proposed": sum(s.get("first_attempt_proposed") or 0 for s in scores),
        "schedules": sum(s.get("schedules_executed") or 0 for s in scores),
        "requests": sum(s.get("requests") or 0 for s in scores),
        "inference_seconds": round(sum(s.get("inference_seconds") or 0 for s in scores), 1),
        "estimated_usd": round(sum(s.get("approx_cost_usd") or 0 for s in scores), 2),
    }


def _newest(directory: Path, pattern: str) -> Path:
    return sorted(directory.glob(pattern))[-1]


def build_site_data(settings: Settings) -> dict[str, Any]:
    reports = {
        split: json.loads((settings.results_dir / "benchmark" / f"latest_{split}.json").read_text())
        for split in ("dev", "heldout")
    }
    baseline_c = json.loads(_newest(settings.results_dir / "baseline_c", "all_*.json").read_text())
    c_scores = [s for s in baseline_c["scores"] if s["system"] == "baseline_c"]
    splits = {}
    scenarios: dict[str, dict[str, Any]] = {}
    for split, report in reports.items():
        scores = [*report["scores"], *(s for s in c_scores if s["split"] == split)]
        splits[split] = {
            "generated_at": report["generated_at"], "schedules_per_case": report["budget"]["schedules_per_case"],
            "notes": report.get("notes", []),
            "systems": {
                system: {"name": name, **rollup([s for s in scores if s["system"] == system])}
                for system, name in SYSTEMS.items()
            },
        }
        for score in scores:
            if not score["has_bug"] or score["system"] not in {"ai", "baseline_b", "baseline_c", "hand"}:
                continue
            scenario = score["case_id"].split("/")[0]
            entry = scenarios.setdefault(scenario, {"split": split, "systems": {}})
            run = entry["systems"].setdefault(score["system"], {"schedules": [], "differs_from_reference": False})
            run["schedules"].append(score["schedules_to_first_failure"])
            run["differs_from_reference"] |= score.get("matches_reference_manifestation") is False
    documented = json.loads(
        _newest(settings.results_dir / "external_public" / "gitlab_authorization_sync", "*/report.json").read_text()
    )
    return {
        "benchmark": {
            "scenarios": len(scenarios), "cases": len(scenarios) * 2,
            "splits": splits, "by_scenario": scenarios,
            "development_cases": sum(1 for s in scenarios.values() if s["split"] == "dev") * 2,
            "held_out_cases": sum(1 for s in scenarios.values() if s["split"] == "heldout") * 2,
        },
        "gitlab": {
            "sources": documented["sources"], "limits": documented["scope"],
            "buggy": {
                "schedules": documented["variants"]["buggy"]["schedules"],
                "violating": documented["variants"]["buggy"]["violating_schedules"],
                "failure_class": documented["variants"]["buggy"]["facts"]["failure_class"],
                "actors_in_minimal_failure": documented["variants"]["buggy"]["facts"]["actors_in_minimal_failure"],
                "lost": documented["variants"]["buggy"]["facts"]["destination_row_disappeared"],
                "remaining": documented["variants"]["buggy"]["facts"]["source_rows_remaining"],
            },
            "corrected": {
                "schedules": documented["variants"]["corrected"]["schedules"],
                "violating": documented["variants"]["corrected"]["violating_schedules"],
                "kept": documented["variants"]["corrected"]["facts"]["destination_after"],
            },
        },
    }


def write_site_data(settings: Settings, path: Path = SITE_DATA) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_site_data(settings), indent=1, default=str) + "\n")
    return path


def _static_markup(html: str) -> str:
    """Removes workbench links and text, so they can't appear even with JavaScript off, and sets the static mode."""
    html = re.sub(r'\s*<a\b[^>]*\blive-only\b[^>]*>.*?</a>', "", html, flags=re.S)
    html = re.sub(r'<span\b[^>]*\blive-only\b[^>]*>.*?</span>', "", html, flags=re.S)
    return html.replace('<html lang="en">', '<html lang="en" data-site="static">', 1)


def export_static_site(out_dir: Path) -> list[str]:
    """Writes the recorded experience only: no workbench, no API and no database."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    written = []
    for source, target in EXPORT_FILES.items():
        destination = out_dir / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STATIC / source, destination)
        written.append(target)
    for page in ("index.html", "demo.html"):
        path = out_dir / page
        path.write_text(_static_markup(path.read_text()))
    config = out_dir / "static" / "js" / "config.js"
    config.write_text("window.DEPLOYSPECTER = {staticSite: true};\n")
    written.append("static/js/config.js")
    return sorted(written)
