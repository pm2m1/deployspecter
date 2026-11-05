"""EXPERIMENTAL checker for docs/experimental_mvp/simultaneous/PROPERTIES.md (frozen before the first run).

Pure functions over what the host reads independently after a run:
* the final ``chancy_jobs`` rows (``jobs_detail``);
* every actor's result (``results``, keyed by actor);
* the scenario's declared expectations (``scenario``).

No database, no trace: corroboration is checked separately, and a run it refuses gets no verdict here.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

CLASSES = {"P1": "H", "P2": "H", "P3": "H", "P4": "H", "P5": "X", "P6": "H", "P7": "G|X", "P8": "H"}
JOBS_DETAIL_SQL = ("SELECT id::text AS id, queue, state, attempts, max_attempts, (kwargs->>'n')::int AS n, "
                   "{claim} AS has_claim_id, completed_at IS NOT NULL AS completed, "
                   "json_array_length(errors) AS errors, taken_by IS NOT NULL AS taken FROM {schema}.chancy_jobs "
                   "ORDER BY (kwargs->>'n')::int, id")


def _versions(results: dict[str, Any], key: str) -> dict[int, list[str]]:
    """n -> versions of the workers that ran it (once per execution)."""
    out: dict[int, list[str]] = {}
    for r in results.values():
        if isinstance(r, dict) and key in r:
            for n in r[key]:
                out.setdefault(int(n), []).append(str(r.get("version")))
    return out


def check(jobs: list[dict[str, Any]], results: dict[str, Any], scenario: dict[str, Any],
          *, quiescent: bool) -> dict[str, Any]:
    pushed = [p for r in results.values() if isinstance(r, dict) for p in r.get("pushed", [])]
    cancelled = {int(c["n"]) for r in results.values() if isinstance(r, dict) for c in r.get("cancelled", [])
                 if c.get("rowcount") == 1}
    runs = _versions(results, "handled")
    ok_runs = _versions(results, "succeeded")
    pushed_by = {int(p["n"]): p["version"] for p in pushed}
    by_n = Counter(j["n"] for j in jobs)
    final = {j["n"]: j for j in jobs}
    v: dict[str, list[str]] = {k: [] for k in CLASSES}
    ids = [p["id"] for p in pushed]
    if len(set(ids)) != len(ids):
        v["P1"].append(f"duplicate pushed ids: {sorted(i for i, c in Counter(ids).items() if c > 1)}")
    for p in pushed:
        if by_n.get(int(p["n"]), 0) != 1 or final[int(p["n"])]["id"] != p["id"]:
            v["P1"].append(f"job n={p['n']} isn't present exactly once with its pushed id")
    for n, j in final.items():
        execs = len(runs.get(n, []))
        if n not in cancelled and (execs != j["attempts"] or execs > j["max_attempts"]):
            v["P2"].append(f"n={n}: {execs} executions {runs.get(n, [])}, attempts {j['attempts']}, "
                           f"max {j['max_attempts']}")
        if n in ok_runs and n not in cancelled and j["state"] != "succeeded":
            v["P3"].append(f"n={n}: its handler succeeded ({ok_runs[n]}) but it ends {j['state']}")
        if j["state"] == "succeeded" and (j["attempts"] < 1 or not j["completed"]):
            v["P4"].append(f"n={n}: succeeded with attempts {j['attempts']}, completed {j['completed']}")
        if j["state"] == "failed" and n not in cancelled and j["attempts"] != j["max_attempts"]:
            v["P4"].append(f"n={n}: failed after {j['attempts']} of {j['max_attempts']} attempts, not cancelled")
        if n in pushed_by and n not in cancelled and not runs.get(n):
            crossing = scenario.get("consumer_of", {}).get(str(n))
            if crossing and crossing != pushed_by[n]:
                v["P5"].append(f"n={n}: pushed by {pushed_by[n]}, never executed by {crossing}")
        if quiescent and j["state"] in ("pending", "running", "retrying"):
            v["P6"].append(f"n={n}: {j['state']} after a quiescent completion")
        if n in cancelled and j["state"] != "failed":
            v["P7"].append(f"n={n}: cancelled (1 row) but ends {j['state']} after executions {runs.get(n, [])}")
        if n in scenario.get("retry", []):
            failures = len(runs.get(n, [])) - len(ok_runs.get(n, []))
            if j["errors"] != failures or j["attempts"] != len(runs.get(n, [])) or \
                    (j["state"] == "succeeded") != bool(ok_runs.get(n)):
                v["P8"].append(f"n={n}: attempts {j['attempts']}, errors {j['errors']}, runs {runs.get(n, [])}, "
                               f"successes {ok_runs.get(n, [])}, state {j['state']}")
    return {"violations": {k: x for k, x in v.items() if x}, "classes": CLASSES,
            "not_evaluated": [] if quiescent else ["P6"],
            "executions": {str(n): runs.get(n, []) for n in sorted(final)},
            "cancelled": sorted(cancelled), "quiescent": quiescent}
