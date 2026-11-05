"""EXPERIMENTAL checker for docs/experimental_mvp/rolling_upgrade/procrastinate_3.3_3.4/CASE.md (frozen before any run).

Pure functions over what the host reads independently after the final phase (``procrastinate_jobs`` and
``procrastinate_events`` rows) and over every phase's actor results (``results``: actor name -> result dict).
Corroboration and isolation are checked separately; a refused phase gives the case no verdict.
"""

from __future__ import annotations

from typing import Any

STATE_SQL = {
    "jobs": ("SELECT id, task_name, (args->>'n')::int AS n, status::text AS status, attempts, queue_name "
             "FROM public.procrastinate_jobs ORDER BY id"),
    "events": "SELECT job_id, type::text AS type FROM public.procrastinate_events ORDER BY id",
}


def check(jobs: list[dict[str, Any]], events: list[dict[str, Any]], results: dict[str, Any], *,
          post_applied_before: set[int], quiescent: bool) -> dict[str, Any]:
    """``post_applied_before``: n of the admin retries that ran after the ``post`` migration (R6)."""
    pushed = {int(p["n"]): p for r in results.values() if isinstance(r, dict) for p in r.get("pushed", [])}
    execs: dict[int, list[str]] = {}
    ok: dict[int, list[str]] = {}
    for r in results.values():
        if isinstance(r, dict) and "handled" in r:
            for n in r["handled"]:
                execs.setdefault(int(n), []).append(r["version"])
            for n in r.get("succeeded", []):
                ok.setdefault(int(n), []).append(r["version"])
    admin = [a for r in results.values() if isinstance(r, dict) for a in r.get("retried", [])]
    admin_n: dict[int, int] = {}
    for a in admin:
        if a.get("ok"):
            admin_n[int(a["n"])] = admin_n.get(int(a["n"]), 0) + 1
    by_n = {int(j["n"]): j for j in jobs}
    ev: dict[int, list[str]] = {}
    for e in events:
        ev.setdefault(int(e["job_id"]), []).append(e["type"])
    v: dict[str, list[str]] = {k: [] for k in ("R1", "R2", "R3", "R4", "R5", "R6")}
    for n, p in sorted(pushed.items()):
        j = by_n.get(n)
        if j is None:
            v["R1"].append(f"n={n}: deferred ({p['version']}) but missing")
            continue
        runs = len(execs.get(n, []))
        if quiescent and j["status"] not in ("succeeded", "failed"):
            v["R1"].append(f"n={n}: {j['status']} after a quiescent completion")
        bound = int(p.get("max_attempts", 0)) + 1 + admin_n.get(n, 0) * (int(p.get("max_attempts", 0)) + 1)
        if runs > bound:
            v["R2"].append(f"n={n}: {runs} executions, more than the bound {bound}")
        if runs and runs != j["attempts"] + 1:
            v["R2"].append(f"n={n}: {runs} executions but attempts {j['attempts']}")
        if (j["status"] == "succeeded") != bool(ok.get(n)):
            v["R3"].append(f"n={n}: status {j['status']}, successful executions {ok.get(n, [])}")
        if quiescent and not runs:
            v["R4"].append(f"n={n}: deferred by {p['version']} and never executed")
        types = ev.get(int(j["id"]), [])
        if not types or types[0] != "deferred":
            v["R6"].append(f"n={n}: events {types} don't start with 'deferred'")
        if j["status"] in ("succeeded", "failed") and types and types[-1] != j["status"]:
            v["R6"].append(f"n={n}: last event {types[-1]!r}, status {j['status']}")
        if "retried" in types and n not in post_applied_before:
            v["R6"].append(f"n={n}: a 'retried' event without a post-migration admin retry")
    for a in admin:
        if not a.get("ok"):
            v["R5"].append(f"admin retry of n={a['n']} failed: {a.get('error')}")
        elif not (a.get("before", {}).get("status") == "failed" and a.get("after", {}).get("status") == "todo"
                  and a["after"].get("attempts") == a["before"].get("attempts", -9) + 1):
            v["R5"].append(f"admin retry of n={a['n']}: before {a.get('before')}, after {a.get('after')}")
    return {"violations": {k: x for k, x in v.items() if x}, "quiescent": quiescent,
            "executions": {str(n): execs.get(n, []) for n in sorted(pushed)},
            "events": {str(n): ev.get(int(by_n[n]["id"]), []) for n in sorted(pushed) if n in by_n},
            "admin_retries": admin}
