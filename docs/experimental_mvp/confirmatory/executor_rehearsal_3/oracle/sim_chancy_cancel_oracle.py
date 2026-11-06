# SIMULATION: synthetic cases, mock approvals and a simulated executor. This is an orchestration rehearsal, not an evaluation; nothing here is evidence about DeploySpecter or Muse.
"""SIMULATION oracle (developer-written, NOT independent): Chancy's documented cancel semantics. A job whose
cancel updated its row (rowcount 1) ends in state 'failed'. No verdict when no cancel updated a row."""


def check(final_state, raw_results):
    cancelled = {int(c["n"]) for r in raw_results.values() if isinstance(r, dict) for c in r.get("cancelled", [])
                 if c.get("rowcount") == 1}
    if not cancelled:
        return "NO_VERDICT"
    final = {int(j["n"]): j for j in final_state.get("jobs_detail", [])}
    return "VIOLATED" if any(final.get(n, {}).get("state") != "failed" for n in cancelled) else "HOLDS"
