"""NONBLIND development oracle for case pgqueuer-8dfadf4 (ds-nbc/1). Developer-written, from PGQueuer's documented
contract; it does not read or mention the fix's code.

Property H1 ("concurrency_limit is a global cap on running jobs"). Sources, all at the buggy parent f258cbc:
* docs/guides/concurrency-control.md: "Concurrency limits are enforced **globally** at the database level via the
  dequeue SQL query, not per-worker. If you set `concurrency_limit=5`, at most 5 jobs run across your entire fleet."
  and "Setting `concurrency_limit=1` guarantees that only one job of this entrypoint runs at a time across all
  workers."
* pgqueuer/core/qm.py, ``QueueManager.entrypoint`` docstring: "``concurrency_limit=0`` means unlimited; ``1``
  serialises dispatch. Limits are enforced at the database level across all workers."
* docs/guides/job-cancellation.md: "Cancellations are best-effort and may not halt a job already underway." So a
  handler that keeps running after a cancel request is documented behaviour, and it still counts as running.
* The maintainers' test added with the fix (test/integration/test_persistence_queries_sql.py,
  ``test_cancel_keeps_picked_row_until_worker_logs``) asserts that, after the running job of a
  ``concurrency_limit=1`` entrypoint is cancelled, a dequeue returns nothing until its worker logs the outcome.

Decided from the final state alone (the trusted handler's ``ds_pgq_runs`` rows, ordered by their sequence, which is
the order the statements executed) and the workers' results (the limit each one registered):
* an execution is the rows sharing one ``exec`` token: ``start``, any ``step`` rows, ``end``;
* H1 is VIOLATED when, at some row, more executions have started and not yet ended than the registered limit;
* NO_VERDICT when the evidence is incomplete: no runs table, no execution, workers that disagree on the limit, or an
  execution without both its ``start`` and ``end``.
A limit of 0 is unlimited, so H1 holds trivially.
"""

from __future__ import annotations

from typing import Any


def explain(final_state: dict[str, Any], raw_results: dict[str, Any]) -> dict[str, Any]:
    tables = final_state.get("tables") or []
    if "ds_pgq_runs" not in tables:
        return {"verdict": "NO_VERDICT", "why": "the trusted handler's table is missing"}
    limits = {r.get("concurrency_limit") for r in raw_results.values()
              if isinstance(r, dict) and "worker" in r and "concurrency_limit" in r}
    if len(limits) != 1:
        return {"verdict": "NO_VERDICT", "why": f"the workers' registered limits aren't one value: {sorted(limits)}"}
    (limit,) = limits
    runs = sorted(final_state.get("runs") or [], key=lambda r: r["seq"])
    if not runs:
        return {"verdict": "NO_VERDICT", "why": "no handler execution was recorded", "limit": limit}
    phases: dict[str, list[str]] = {}
    for r in runs:
        phases.setdefault(r["exec"], []).append(r["phase"])
    broken = {e: p for e, p in phases.items() if p[:1] != ["start"] or p[-1:] != ["end"] or
              p.count("start") != 1 or p.count("end") != 1}
    active: list[str] = []
    peak, at = 0, None
    for r in runs:
        if r["phase"] == "start":
            active.append(r["exec"])
        elif r["phase"] == "end" and r["exec"] in active:
            active.remove(r["exec"])
        if len(active) > peak:
            peak, at = len(active), {"seq": r["seq"], "running": list(active)}
    execs = {e: {"job_id": next(r["job_id"] for r in runs if r["exec"] == e),
                 "n": next(r["n"] for r in runs if r["exec"] == e)} for e in phases}
    out = {"limit": limit, "executions": execs, "peak_running": peak, "peak_at": at, "broken": broken}
    if limit > 0 and peak > limit:
        return {"verdict": "VIOLATED", "why": f"{peak} executions ran at once under concurrency_limit={limit}", **out}
    if broken:
        return {"verdict": "NO_VERDICT", "why": "an execution lacks its start or end", **out}
    return {"verdict": "HOLDS", "why": f"at most {peak} at once under concurrency_limit={limit}", **out}


def check(final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
    return explain(final_state, raw_results)["verdict"]
