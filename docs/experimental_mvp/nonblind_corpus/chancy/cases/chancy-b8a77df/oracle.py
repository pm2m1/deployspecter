"""ds-nbc/1 oracle for case chancy-b8a77df (NONBLIND development; developer-written from the documented contract).

Sources of the property (never the fix's code):
* chancy 0.26.0 ``CHANGELOG.md`` (entry added in ``4fe6633``, "Changelog entry for the lock ordering fix (#89)"):
  "Pushing jobs with unique keys and the worker's batched job updates now lock rows in the same order, fixing a
  deadlock between overlapping pushes and updates that could fail either side (@AudeCstg, #89)."
* The maintainers' regression tests added with the fix, ``tests/regressions/test_89.py``:
  - ``test_regression_89``: a transaction locking the jobs' rows as the worker's update batch does, overlapping a
    ``push_many`` of the same unique keys in the opposite order, must raise no ``DeadlockDetected`` ("here or in the
    push task"), and the push must return the existing jobs' references (``assert await push == references``);
  - ``test_regression_89_sync_push_preserves_order``: the references come back in the caller's order.

Properties, over the actors' results and the host's independent final-state read of a SOUND run:
* **D1 no deadlock, either side:** no statement of the worker, ``seed`` or ``repush`` failed with SQLSTATE ``40P01``
  (deadlock_detected), and neither ``push_many`` raised it.
* **D2 references in the caller's order:** each completed ``push_many`` returned one reference per requested job, and
  the final row each reference names carries the requested job's ``n`` at that position.
* **NO_VERDICT** when an actor's result is missing, the final state has no job rows, or a push failed with anything
  other than a deadlock (not this property's question).
"""

from __future__ import annotations

from typing import Any

DEADLOCK = "40P01"
ACTORS = ("worker", "seed", "repush")


def evaluate(final_state: dict[str, Any], raw_results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"D1": [], "D2": [], "no_verdict": []}
    res = {a: raw_results.get(a) for a in ACTORS}
    for a, r in res.items():
        if not isinstance(r, dict):
            out["no_verdict"].append(f"{a}: no result")
    rows = {j["id"]: j for j in (final_state.get("jobs_detail") or [])}
    if not rows:
        out["no_verdict"].append("no job rows in the final state")
    if out["no_verdict"]:
        return {**out, "verdict": "NO_VERDICT"}
    for a, r in res.items():
        for e in r.get("op_errors") or []:
            if e.get("sqlstate") == DEADLOCK:
                out["D1"].append(f"{a}: a statement failed with deadlock_detected: {e.get('sql_head')}")
    for a in ("seed", "repush"):
        r = res[a]
        err = r.get("error")
        if err:
            if err.get("sqlstate") == DEADLOCK:
                out["D1"].append(f"{a}: push_many raised {err.get('class')} ({DEADLOCK})")
            else:
                out["no_verdict"].append(f"{a}: push_many raised {err.get('class')} ({err.get('sqlstate')})")
            continue
        want = [int(q["n"]) for q in r.get("requested") or []]
        refs = r.get("refs") or []
        if len(refs) != len(want):
            out["D2"].append(f"{a}: {len(refs)} references for {len(want)} jobs")
            continue
        for i, (n, ref) in enumerate(zip(want, refs, strict=True)):
            row = rows.get(ref.get("id"))
            if row is None or int(row["n"]) != n:
                out["D2"].append(f"{a}[{i}]: the reference names {None if row is None else row['n']}, not n={n}")
    verdict = "VIOLATED" if out["D1"] or out["D2"] else "NO_VERDICT" if out["no_verdict"] else "HOLDS"
    return {**out, "verdict": verdict}


def check(final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
    return evaluate(final_state, raw_results)["verdict"]
