"""Track A: the independent state check, exact initial-state verification, and evidence binding. Research-only.

Nothing here opens a connection. ``independent_state_check`` takes a connection-like object (a recording fake in
the offline tests). Everything else is pure and works on plain records.

One limit is recorded rather than hidden: Gate 2A's parent read (``Sandbox.snapshot``) masks timestamps as
``"<timestamp>"``. The common standard allows no masking, so this check is exact only for schemas without time types.
The reservations schema has none, and ``check_no_time_types`` enforces that from the pinned snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import pipeline as p

SQL_TYPES = {"integer": "int4", "bigint": "int8", "smallint": "int2", "text": "text", "varchar": "varchar",
             "double precision": "float8", "boolean": "bool"}
TIME_TYPES = {"timestamp", "timestamptz", "date", "time", "timetz", "interval"}
NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SCHEMA = "ds_run"


class BindingError(ValueError):
    """Evidence that doesn't bind: a digest, run or result that can't be tied to the prepared chain."""


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def scenario_digest(scenario: Any) -> str:
    """Of a LiveScenario: name, setup SQL, seed rows and actors (entrypoint and every kwarg)."""
    return canonical_digest({
        "name": scenario.name, "setup_sql_sha256": hashlib.sha256(scenario.setup_sql.encode()).hexdigest(),
        "seed": [r.model_dump() for r in scenario.seed],
        "actors": [{"name": a.name, "entrypoint": a.entrypoint, "kwargs": dict(a.kwargs), "autocommit": a.autocommit,
                    "isolation": None if a.isolation is None else str(a.isolation)} for a in scenario.actors]})


def check_no_time_types(snapshot: dict[str, Any]) -> list[str]:
    names = {t["oid"]: t["typname"] for t in snapshot["types"]}
    return sorted(f"{a['attrelid']}.{a['attname']}" for a in snapshot["attributes"]
                  if not a["attisdropped"] and names.get(a["atttypid"]) in TIME_TYPES)


# ------------------------------------------------------------------ independent state check (OP-6)


def independent_statements(expected: dict[str, list[dict[str, tuple[str, Any]]]],
                           column_types: dict[tuple[str, str], str]) -> list[tuple[str, tuple]]:
    """Fixed templates, filled only with reviewed table and column names and typed parameters."""
    statements: list[tuple[str, tuple]] = [("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", ())]
    for table in sorted(expected):
        if not NAME.match(table):
            raise BindingError(f"table name {table!r} isn't a plain name")
        statements.append((f'SELECT count(*) FROM {SCHEMA}."{table}"', ()))
        statements.append((f"SELECT md5(coalesce(string_agg(t::text, E'\\n' ORDER BY t::text), '')) "
                           f'FROM {SCHEMA}."{table}" t', ()))
        for row in expected[table]:
            parts, params = [], []
            for column in sorted(row):
                if not NAME.match(column):
                    raise BindingError(f"column name {column!r} isn't a plain name")
                kind, value = row[column]
                sql_type = SQL_TYPES.get(column_types.get((table, column), ""))
                if sql_type is None:
                    raise BindingError(f"{table}.{column} has no reviewed SQL type")
                parts.append(f'"{column}" IS NOT DISTINCT FROM %s::{sql_type}')
                params.append(value)
            statements.append((f'SELECT count(*) FROM {SCHEMA}."{table}" WHERE ' + " AND ".join(parts), tuple(params)))
    statements.append(("COMMIT", ()))
    return statements


@dataclass
class IndependentCheck:
    counts: dict[str, int]
    digests: dict[str, str]
    row_matches: list[int]
    statements: list[str] = field(default_factory=list)


def independent_state_check(conn: Any, expected: dict[str, list[dict[str, tuple[str, Any]]]],
                            column_types: dict[tuple[str, str], str]) -> IndependentCheck:
    out = IndependentCheck({}, {}, [])
    for text, params in independent_statements(expected, column_types):
        out.statements.append(text)
        cursor = conn.execute(text, params) if params else conn.execute(text)
        if text.startswith("SELECT count(*)") and " WHERE " not in text:
            out.counts[text.split('"')[1]] = cursor.fetchone()[0]
        elif text.startswith("SELECT md5"):
            out.digests[text.split('"')[1]] = cursor.fetchone()[0]
        elif text.startswith("SELECT count(*)"):
            out.row_matches.append(cursor.fetchone()[0])
    return out


def judge_independent(check: IndependentCheck, expected: dict[str, list[dict[str, tuple[str, Any]]]]) -> list[str]:
    problems = []
    for table, rows in expected.items():
        if check.counts.get(table) != len(rows):
            problems.append(f"{table}: {check.counts.get(table)} rows, expected exactly {len(rows)}")
    flat = [r for t in sorted(expected) for r in expected[t]]
    for row, matches in zip(flat, check.row_matches, strict=False):
        if matches != sum(1 for other in flat if other == row):
            problems.append(f"requested row {row} matched {matches} rows")
    if len(check.row_matches) != len(flat):
        problems.append("not every requested row was checked")
    return problems


# ------------------------------------------------------------------ exact initial states across runs


def verify_initial_states(expected: dict[str, list[dict[str, tuple[str, Any]]]], initial_states: list[dict],
                          independent: list[IndependentCheck]) -> dict[str, Any]:
    """Every run's parent-read initial state exact, all identical (the repeat), and independently confirmed."""
    problems = [f"run {i}: {x}" for i, state in enumerate(initial_states)
                for x in p.check_initial_state(expected, state)]
    digests = {canonical_digest(s) for s in initial_states}
    if len(digests) != 1:
        problems.append(f"{len(digests)} distinct initial states across runs")
    server = {json.dumps(c.digests, sort_keys=True) for c in independent}
    if not independent:
        problems.append("no independent check")
    if len(server) > 1:
        problems.append("independent server digests differ between runs")
    problems += [f"independent {i}: {x}" for i, c in enumerate(independent) for x in judge_independent(c, expected)]
    return {"problems": problems, "verified_initial_digest": next(iter(digests)) if len(digests) == 1 else None}


# ------------------------------------------------------------------ binding


def run_record(trace: Any, *, variant: str, scenario_sha: str, oracle: Any) -> dict[str, Any]:
    """The facts acceptance needs from one supervised trace. ``oracle`` maps a final state to violation strings."""
    violations = oracle(trace.final_state)
    return {
        "variant": variant, "scenario_digest": scenario_sha, "run_id": trace.evidence.run_id,
        "supervised": trace.evidence.supervised, "scoreable": trace.scoreable,
        "flags": [f.kind for f in trace.evidence.flags], "opaque_steps": trace.opaque_steps,
        "refusals": len(trace.refusals), "outcome": trace.outcome, "inconclusive": trace.inconclusive,
        "initial_digest": canonical_digest(trace.initial_state), "final_digest": canonical_digest(trace.final_state),
        "chosen_schedule": list(trace.chosen_schedule), "fingerprint": trace.fingerprint(),
        "requirement_ids": sorted({v.split()[0] for v in violations}), "violations": violations,
    }


def bind(*, chain: p.OfflineChain, prepared: dict[str, str], runs: list[dict[str, Any]],
         verified_initial_digest: str | None) -> str:
    """One digest over everything, after checking each run belongs to a prepared scenario and state."""
    for run in runs:
        if prepared.get(run["variant"]) != run["scenario_digest"]:
            raise BindingError(f"run {run['run_id']} isn't from the prepared {run['variant']} scenario")
        if run["initial_digest"] != verified_initial_digest:
            raise BindingError(f"run {run['run_id']} started from an unverified state")
        if not run["run_id"]:
            raise BindingError("a run has no run id")
    return canonical_digest({"chain": chain.digests, "prepared": prepared, "verified_initial": verified_initial_digest,
                             "runs": [{k: r[k] for k in ("variant", "run_id", "initial_digest", "final_digest",
                                                         "fingerprint", "requirement_ids")} for r in runs]})
