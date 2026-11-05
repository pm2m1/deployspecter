"""EXPERIMENTAL, narrow and explicitly scoped: a constructed initial state for a real application, Chancy 0.26.0.

The workload is a list of job numbers. It becomes state goals, deterministic candidate rows of ``chancy_jobs`` (typed:
uuid, text, varchar, jsonb, integer, timestamptz, NULL), and an exact construction in the live schema Chancy's own
migrations created. The rows are then verified independently, and Chancy's unmodified worker, in the isolated
runner, must consume exactly that state.

**Why not Phase 9B here.** ``chancy_jobs`` has four ``jsonb`` columns (kwargs, limits, meta, errors). Phase 9B's
matcher compares a stored ``to_jsonb`` value (a parsed object) with the requested value (a string) and reports the
state IMPOSSIBLE (fixtures ``json_refused_by_phase9b`` and ``jsonb_refused_by_phase9b``). This is
recorded (``docs/experimental_mvp/state_construction``) as a BLOCKED limit of the Phase 9B constructor. It is not
worked around: the rule that picks the constructor is fixed **before** construction (``constructor_for``). Any
json or jsonb value → the trigger-free direct constructor. Everything else → Phase 9B.

**The direct constructor's scope** (``preconditions``, checked on the live catalog, refused otherwise):
* the target table's columns and types are exactly the pinned Chancy 0.26.0 (schema v8) ``chancy_jobs`` columns;
* no user trigger, rule or policy on the table, and no foreign key from it, so insertion order can't matter and
  nothing but the inserted rows can appear;
* every NOT NULL column without a default is given a value.

Under these, one transaction of plain INSERTs either fails or yields exactly the requested rows. ``typed.verify``
re-reads them exactly on its own session, and the run is bound to the digest of that verified state.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .typed import TV, TypedRefused, json_exact

FUNC = "deployspecter.experimental.thirdparty.chancy_adapter.ds_job"  # the adapter's trusted handler
QUEUE = "ds_q"  # the adapter's queue
EPOCH = "2026-01-01T00:00:00+00:00"  # fixed, in the past: deterministic, and runnable at once
NAMESPACE = uuid.UUID("6f1c2a4e-9d3b-5e7f-8a1b-2c3d4e5f6a7b")
JOBS_TABLE = "chancy_jobs"
# chancy_jobs after Chancy 0.26.0's migrations v1..v8, from the pinned sdist's migration sources: v1 creates it,
# v5 turns kwargs, limits, meta and errors into jsonb, v8 adds claim_id. (A first pin from v1 and v8 alone was wrong;
# the live-catalog precondition refused it: attempt 02 in docs/experimental_mvp/state_construction.)
PINNED_COLUMNS = {
    "id": "uuid", "queue": "text", "func": "text", "kwargs": "jsonb", "limits": "jsonb", "meta": "jsonb",
    "state": "character varying", "priority": "integer", "attempts": "integer", "max_attempts": "integer",
    "taken_by": "text", "created_at": "timestamp with time zone", "started_at": "timestamp with time zone",
    "completed_at": "timestamp with time zone", "scheduled_at": "timestamp with time zone", "unique_key": "text",
    "errors": "jsonb", "claim_id": "uuid",
}
STATES = ("pending", "succeeded")  # what a goal may ask for: runnable, or already finished (a negative control)


def validate(workload: Any) -> list[str]:
    """Deterministic. ``{"jobs": [{"n": int 0..999, "state": "pending"|"succeeded"}, ...]}``, 1..8 jobs, n unique."""
    if not isinstance(workload, dict) or set(workload) != {"jobs"} or not isinstance(workload["jobs"], list):
        return ['shape: exactly {"jobs": [...]}']
    jobs, out = workload["jobs"], []
    if not 1 <= len(jobs) <= 8:
        out.append("jobs: 1..8")
    seen = set()
    for i, j in enumerate(jobs):
        if not isinstance(j, dict) or set(j) != {"n", "state"}:
            out.append(f"jobs[{i}]: exactly n and state")
            continue
        if not isinstance(j["n"], int) or isinstance(j["n"], bool) or not 0 <= j["n"] <= 999:
            out.append(f"jobs[{i}].n: an int in 0..999")
        elif j["n"] in seen:
            out.append(f"jobs[{i}].n: duplicate")
        else:
            seen.add(j["n"])
        if j["state"] not in STATES:
            out.append(f"jobs[{i}].state: one of {STATES}")
    if jobs and not any(isinstance(j, dict) and j.get("state") == "pending" for j in jobs):
        out.append("at least one pending job")
    return out


def goals(workload: dict[str, Any]) -> list[dict[str, Any]]:
    """One goal per job: a ``chancy_jobs`` row for the trusted handler, in the given state."""
    return [{"entity": JOBS_TABLE, "func": FUNC, "queue": QUEUE, "kwargs": {"n": j["n"]}, "state": j["state"],
             "attempts": 0 if j["state"] == "pending" else 1, "max_attempts": 1, "priority": 0}
            for j in workload["jobs"]]


def job_id(n: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"ds_sc:chancy:{n}"))


def rows(gs: list[dict[str, Any]]) -> list[tuple[str, dict[str, TV]]]:
    """Deterministic candidate rows: every column explicit (no clock, no random default), exact JSON text."""
    out = []
    for g in gs:
        n = g["kwargs"]["n"]
        finished = g["state"] == "succeeded"
        out.append((JOBS_TABLE, {
            "id": TV("uuid", job_id(n)), "queue": TV("str", g["queue"]), "func": TV("str", g["func"]),
            "kwargs": TV("jsonb", json.dumps(g["kwargs"], sort_keys=True)), "limits": TV("jsonb", "[]"),
            "meta": TV("jsonb", "{}"), "state": TV("str", g["state"]), "priority": TV("int", g["priority"]),
            "attempts": TV("int", g["attempts"]), "max_attempts": TV("int", g["max_attempts"]),
            "taken_by": TV("null", None), "created_at": TV("timestamptz", EPOCH),
            "started_at": TV("timestamptz", EPOCH) if finished else TV("null", None),
            "completed_at": TV("timestamptz", EPOCH) if finished else TV("null", None),
            "scheduled_at": TV("timestamptz", EPOCH), "unique_key": TV("null", None), "errors": TV("jsonb", "[]"),
            "claim_id": TV("null", None)}))
    return out


def constructor_for(rows_: list[tuple[str, dict[str, TV]]]) -> str:
    """Fixed before construction: Phase 9B unless a value is json or jsonb (which its matcher can't compare)."""
    return "direct_trigger_free" if any(tv.tag in ("json", "jsonb") for _, r in rows_ for tv in r.values()) \
        else "phase9b"


def preconditions(conn: Any, schema: str, rows_: list[tuple[str, dict[str, TV]]]) -> list[str]:
    """The direct constructor's scope, on the live catalog. Empty means in scope."""
    from .typed import check_value, columns
    problems = []
    cols = columns(conn, schema, JOBS_TABLE)
    if {c: v["data_type"] for c, v in cols.items()} != PINNED_COLUMNS:
        problems.append(f"{JOBS_TABLE}'s columns aren't the pinned Chancy 0.26.0 v8 ones: {sorted(cols)}")
        return problems
    rel = f"{schema}.{JOBS_TABLE}"
    q = {"user triggers": "SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal",
         "rules": "SELECT count(*) FROM pg_rewrite WHERE ev_class = %s::regclass AND rulename <> '_RETURN'",
         "policies": "SELECT count(*) FROM pg_policy WHERE polrelid = %s::regclass",
         "foreign keys": "SELECT count(*) FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'f'",
         "deferrable constraints": "SELECT count(*) FROM pg_constraint WHERE conrelid = %s::regclass AND "
                                   "condeferrable"}
    for what, sql_ in q.items():
        if (k := conn.execute(sql_, (rel,)).fetchone()[0]) != 0:
            problems.append(f"{k} {what} on {rel}")
    required = {r[0] for r in conn.execute(
        "SELECT column_name::text FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "AND is_nullable = 'NO' AND column_default IS NULL", (schema, JOBS_TABLE)).fetchall()}
    for table, vals in rows_:
        if table != JOBS_TABLE:
            problems.append(f"a row for {table}, outside scope")
            continue
        if missing := sorted(required - set(vals)):
            problems.append(f"required column(s) {missing} not given")
        for c, tv in vals.items():
            try:
                check_value(tv, cols[c])
            except (TypedRefused, KeyError, ValueError) as exc:
                problems.append(f"{c}: {exc}")
    return problems


def oracle(workload: dict[str, Any], final_jobs: list[dict[str, Any]], handled: list[int]) -> list[str]:
    """Independent of the constructor and of Chancy's code, from Chancy's documented job states and the workload.

    C1 exactly the constructed jobs exist (no job lost, none added);
    C2 each pending job reached 'succeeded', with a completion time, after exactly one attempt;
    C3 the handler ran exactly once for each pending job, and never for a job constructed as already succeeded;
    C4 each job kept its constructed queue, func and kwargs; a job constructed as succeeded is unchanged."""
    out = []
    want = {job_id(j["n"]): j for j in workload["jobs"]}
    got = {j["id"]: j for j in final_jobs}
    if set(got) != set(want):
        out.append(f"C1 jobs {sorted(got)} in the database, {sorted(want)} constructed")
    pending = sorted(j["n"] for j in workload["jobs"] if j["state"] == "pending")
    for i, j in want.items():
        g = got.get(i)
        if g is None:
            continue
        if j["state"] == "pending" and (g["state"] != "succeeded" or g["attempts"] != 1 or not g["completed"]):
            out.append(f"C2 job n={j['n']}: {g['state']} after {g['attempts']} attempt(s), completed={g['completed']}")
        if j["state"] == "succeeded" and (g["state"] != "succeeded" or g["attempts"] != 1):
            out.append(f"C4 job n={j['n']} was constructed succeeded and changed to {g['state']}/{g['attempts']}")
        if (g["queue"], g["func"]) != (QUEUE, FUNC) or not json_exact(json.loads(g["kwargs"]), {"n": j["n"]}):
            out.append(f"C4 job n={j['n']}: queue/func/kwargs changed to {(g['queue'], g['func'], g['kwargs'])}")
    if sorted(handled) != pending:
        out.append(f"C3 the handler ran for {sorted(handled)}, the pending jobs are {pending}")
    return out
