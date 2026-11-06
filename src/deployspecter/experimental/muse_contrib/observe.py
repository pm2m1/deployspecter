"""EXPERIMENTAL: runs → invariant observations (``invariants.validator``'s input). Pure functions over what the
host read back independently after each run; nothing here touches a database.
"""

from __future__ import annotations

import re
from typing import Any

ROLE = {"corrected": "reference", "buggy": "defective", "fixed": "defective"}
CANCEL_RE = re.compile(r"SET state = 'failed'")
CLAIM_RE = re.compile(r"WITH selected_jobs AS")
FLUSH_RE = re.compile(r'UPDATE "chancy_jobs" SET state = %\(state\)s')


def reservation_calls(invocations: list[dict[str, Any]], raw_results: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": i + 1, "actor": inv["actor"], "handler": inv["handler"],
             "status": (raw_results.get(inv["actor"]) or {}).get("status")} for i, inv in enumerate(invocations)]


def serial(records: list[Any]) -> bool:
    """A run is serial when each actor's statements form one contiguous block of the executed order."""
    actors = [r.actor for r in records if getattr(r, "outcome", None) == "ok"]
    blocks = sum(1 for i, a in enumerate(actors) if i == 0 or actors[i - 1] != a)
    return blocks <= len(set(actors))


def reservations_observation(obs_id: str, build: str, result: Any, initial: dict[str, list[dict[str, Any]]],
                             invocations: list[dict[str, Any]]) -> dict[str, Any]:
    mutant = build[len("mutant_"):] if build.startswith("mutant_") else None
    return {"obs_id": obs_id, "case": "reservations", "build": build,
            "role": "mutant" if mutant else ROLE[build], "mutant": mutant,
            "schedule_class": "serial" if serial(result.trace.records) else "concurrent",
            "sound": result.trace.outcome == "completed" and bool((result.corroboration or {}).get("ok")),
            "initial": {**initial, "calls": []},
            "final": {**result.final_state, "calls": reservation_calls(invocations, result.trace.raw_results)},
            "oracle_violated": bool(result.violations)}


def chancy_jobs(jobs_detail: list[dict[str, Any]], raw_results: dict[str, Any]) -> list[dict[str, Any]]:
    cancelled = {int(c["n"]) for r in raw_results.values() if isinstance(r, dict) for c in r.get("cancelled", [])
                 if c.get("rowcount") == 1}
    execs: dict[int, int] = {}
    succ: dict[int, int] = {}
    for r in raw_results.values():
        if isinstance(r, dict):
            for n in r.get("handled", []):
                execs[int(n)] = execs.get(int(n), 0) + 1
            for n in r.get("succeeded", []):
                succ[int(n)] = succ.get(int(n), 0) + 1
    return [{"id": int(j["n"]), "n": int(j["n"]), "state": j["state"], "attempts": j["attempts"],
             "max_attempts": j["max_attempts"], "has_claim_id": j["has_claim_id"], "completed": bool(j["completed"]),
             "errors": j["errors"] or 0, "taken": bool(j["taken"]), "cancelled": int(j["n"]) in cancelled,
             "executions": execs.get(int(j["n"]), 0), "successes": succ.get(int(j["n"]), 0)}
            for j in jobs_detail]


def chancy_serial(coordinator: dict[str, Any]) -> bool:
    """Serial-equivalent: no cancel statement is granted between a claim and the next completion flush (every
    cancel lands while no job is being worked on)."""
    statements = coordinator.get("statements", {})
    working = 0
    for d in coordinator.get("decisions", []):
        text = statements.get(d["statement_id"], d.get("sql", ""))
        if CLAIM_RE.search(text):
            working += 1
        elif FLUSH_RE.search(text):
            working = max(0, working - 1)
        elif CANCEL_RE.search(text) and working:
            return False
    return True


def chancy_observation(obs_id: str, build: str, role: str, mutant: str | None, report: dict[str, Any]
                       ) -> dict[str, Any]:
    run = report.get("run") or {}
    final = (run.get("final_state") or {}).get("jobs_detail") or []
    co = report.get("case_oracle") or {}
    verdict = co.get("verdict")
    return {"obs_id": obs_id, "case": "chancy-2895c4a", "build": build, "role": role, "mutant": mutant,
            "schedule_class": "serial" if chancy_serial(run.get("coordinator") or {}) else "concurrent",
            "sound": report.get("verdict") == "SOUND",
            "initial": {"jobs": []},
            "final": {"jobs": chancy_jobs(final, run.get("raw_results") or {})},
            "oracle_violated": None if verdict in (None, "NO_VERDICT") else verdict == "VIOLATED"}
