"""EXPERIMENTAL checkers for historical cases (docs/experimental_mvp/historical/*/CASE.md), each frozen before the case
runs. Pure functions over what the host reads independently after a run; corroboration is checked separately.
"""

from __future__ import annotations

from typing import Any


def chancy_2895c4a(jobs: list[dict[str, Any]], results: dict[str, Any], *, has_claim_column: bool) -> dict[str, Any]:
    """H1/H2 of ``historical/chancy_2895c4a/CASE.md``, with the H0 sanity check (no verdict when it fails)."""
    cancelled = {int(c["n"]) for r in results.values() if isinstance(r, dict) for c in r.get("cancelled", [])
                 if c.get("rowcount") == 1}
    runs: dict[int, int] = {}
    for r in results.values():
        if isinstance(r, dict) and "handled" in r:
            for n in r["handled"]:
                runs[int(n)] = runs.get(int(n), 0) + 1
    out: dict[str, Any] = {"H0": [], "H1": [], "H2": [], "cancelled": sorted(cancelled),
                           "executions": {str(k): v for k, v in sorted(runs.items())}}
    final = {int(j["n"]): j for j in jobs}
    for n in sorted(cancelled):
        j = final.get(n)
        if j is None:
            out["H0"].append(f"n={n}: the cancelled job is missing")
            continue
        if runs.get(n, 0) != 1:
            out["H0"].append(f"n={n}: its handler ran {runs.get(n, 0)} times, not once: the race wasn't exercised")
        if j["state"] != "failed":
            out["H1"].append(f"n={n}: cancelled (1 row) but ends {j['state']} (attempts {j['attempts']})")
        if has_claim_column and (j["has_claim_id"] or not j["completed"]):
            out["H2"].append(f"n={n}: claim_id set {j['has_claim_id']}, completed_at set {j['completed']}")
    if not cancelled:
        out["H0"].append("no cancel updated a row")
    verdict = "NO_VERDICT" if out["H0"] else ("VIOLATED" if out["H1"] or out["H2"] else "HOLDS")
    out["verdict"] = verdict
    out["H2_applicable"] = has_claim_column
    return out
