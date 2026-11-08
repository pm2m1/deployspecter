"""Builds the frozen onb-pilot/2 manifests: one per condition (D, M), identical except for the condition, so
``pilot.py`` (unchanged) gives each session its condition. Selection was made from PyPI metadata and sdist file
listings only, before any onboarding work.

    python research_tools/onboarding/pilot2_manifest.py --sdists DIR --out docs/experimental_mvp/onboarding/pilot_2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODE = ["research_tools/onboarding/pilot.py", "research_tools/onboarding/pilot_inside.py",
        "research_tools/onboarding/pilot_runner.py", "research_tools/onboarding/pilot2_analyze.py",
        "research_tools/pg_lock.py",
        "src/deployspecter/experimental/onboarding_pilot/__init__.py",
        "src/deployspecter/experimental/onboarding_pilot/analysis.py",
        "src/deployspecter/experimental/onboarding_pilot/correctness.py",
        "src/deployspecter/experimental/onboarding_pilot/muse_assist.py",
        "src/deployspecter/experimental/onboarding_pilot/recorder.py",
        "src/deployspecter/experimental/onboarding_pilot/session.py",
        "src/deployspecter/experimental/onboarding_pilot/spec.py",
        "docs/experimental_mvp/onboarding/pilot_2/OPERATOR_INSTRUCTIONS.md"]
APPS = [("pgtq", "0.5.0", "pgtq-0.5.0.tar.gz",
         "MIT; psycopg[binary]>=3.0.0 only; pure-Python wheel; not previously integrated or exposed"),
        ("postgres_tq", "1.2.1", "postgres_tq-1.2.1.tar.gz",
         "MIT; psycopg[binary]>=3.1.12 and psycopg-pool>=3.1.12; pure-Python wheel; not previously integrated or "
         "exposed")]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest(condition: str, sdists: Path) -> dict:
    return {
        "version": "onb-pilot-manifest/2", "condition_of_this_manifest": condition,
        "label": "AI-OPERATOR, NONBLIND EXPLORATORY: each session is a fresh AI agent context with no task-specific "
                 "memory; not an independent human participant",
        "question": "On the same application, under identical pins, success criteria, resources and budgets, does "
                    "Muse assistance (M) reduce the automated work to reach S3 compared with D (no Muse)?",
        "apps": [{"app": a, "version": v, "condition": condition, "sdist": f, "sdist_sha256": sha(sdists / f),
                  "declared_paths": [f"src/deployspecter/experimental/onboarding_pilot/adapters/{a}.py"],
                  "why_eligible": why} for a, v, f, why in APPS],
        "selection": {
            "rule": "PostgreSQL job-queue libraries found on PyPI on 2026-11-08 that declare psycopg 3, carry an "
                    "OSI licence, ship a pure-Python wheel and a self-contained sdist, run on CPython 3.12, and "
                    "were never integrated or exposed in this project; ALL eligible ones are used",
            "examined": {"pgtq 0.5.0": "eligible", "postgres-tq 1.2.1": "eligible",
                         "queueio 0.8.0": "requires Python >= 3.15 (the runner is CPython 3.12)",
                         "absurd-sdk 0.5.1": "the sdist ships no SQL schema; the library needs Absurd's schema "
                                             "from outside the pinned sdist",
                         "the onb-pilot/1 exclusions": "unchanged (see PILOT_MANIFEST_v1.json)"},
            "read_before_freeze": "PyPI JSON metadata and the sdist file listings only; no source, docs or "
                                  "repository of either app"},
        "design": {
            "pairing": "each app is onboarded under BOTH D and M, by separate fresh agent contexts, in separate "
                       "worktrees made from the same commit",
            "replicates": "2 per (app, condition): replicate 1 then replicate 2; each replicate starts its four "
                          "sessions at the same time, so neither condition runs first and image caches are cold or "
                          "warm for both alike",
            "contamination_controls": "separate worktrees, no shared task memory, an instruction not to read "
                                      "other sessions or worktrees, identical frozen instructions",
            "contamination_limits": "all operators are instances of one model family; they may share general "
                                    "knowledge of these libraries; filesystem isolation is by instruction only"},
        "allowed_resources": {"both": ["the pinned sdist (source and PKG-INFO), read as data",
                                       "PyPI JSON metadata (wheel resolution)",
                                       "this repository's harness, code and docs, including existing adapters",
                                       "the operator's general knowledge"],
                              "M_only": ["Muse via pilot.py muse (JSON proposals; never executed)"]},
        "budgets": {"max_attempts": 10, "wall_clock_seconds": 5400, "work_timeout_s": 60,
                    "muse": {"condition": "M only", "max_calls": 3, "max_estimated_usd": 0.30}},
        "success": {"S1": "the first spec and workload both accepted by spec.validate (against these pins)",
                    "S2": "the first run whose server-log corroboration is ok with 0 parameters mismatched, "
                          "isolation ok, teardown complete",
                    "S3": "the first S2 run where the harness recorder saw every enqueued key handled exactly once "
                          "and every step finished without an exception",
                    "workload": "at least 2 jobs of one declared task with an int argument"},
        "measures": {"per_session": "session.measure (unchanged), plus: database-lock waiting time from the "
                                    "session's pg_lock.log, subtracted from every elapsed time it falls inside "
                                    "(pilot2_analyze.py); interventions; failed attempts; adapter lines final and "
                                    "revised; code revisions (snapshots with a changed adapter); rejected "
                                    "proposals; S2/S3 verification outcomes; Muse calls, tokens, est. USD",
                     "censoring": "an unmet milestone is censored at the budget, never imputed"},
        "analysis": {"code": "research_tools/onboarding/pilot2_analyze.py",
                     "primary": "per app and replicate, the paired difference M - D in lock-adjusted seconds to "
                                "S3 (censored at 5400) and in final adapter lines; reported per app, with the "
                                "median over replicates",
                     "claim_rule": "PROSPECTIVE_MUSE_PLAN.md §7: a claim needs a >= 30% median reduction in both "
                                   "minutes and lines across >= 3 applications with no lost detection. Only 2 "
                                   "applications are eligible, so NO claim is possible by this rule; the result is "
                                   "descriptive",
                     "no_inference": True},
        "sprint_cost": "each M session's maximum ($0.30) is reserved in the sprint ledger (research_tools/nbc/"
                       "budget.py, $5 cap) before the session starts",
        "code_sha256": {c: sha(REPO / c) for c in CODE},
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "FROZEN before any onboarding work on either app",
    }


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--sdists", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    for c in ("D", "M"):
        p = o.out / f"PILOT_MANIFEST_v2_{c}.json"
        p.write_text(json.dumps(manifest(c, o.sdists), indent=1, sort_keys=True) + "\n")
        print(p, sha(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
