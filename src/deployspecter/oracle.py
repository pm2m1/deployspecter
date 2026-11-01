"""Independent correctness oracle.

Verdicts come from a hand-written reference model evaluated over the committed
event history and the final database state. No test generator sees these
requirements, and an LLM invariant can never produce a true_violation. Writes
count only if they affected rows and were committed, so transaction conflicts
and errors stay distinct from data corruption.
"""

from __future__ import annotations

import re
from typing import Any

from .models import (
    ExecutionTrace,
    InvariantProposal,
    OracleVerdict,
    Requirement,
    StepRecord,
    Violation,
)

STANDARD_ASSUMPTIONS = [
    "Scheduling is at statement and transaction boundaries only; PostgreSQL-internal "
    "interleavings are not controlled.",
    "Each schedule starts from freshly rebuilt schema and seed state.",
    "A passing result covers only the schedules executed with these inputs; it is not a "
    "proof of correctness.",
]


def _same(expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is actual
    try:
        return abs(float(expected) - float(actual)) < 1e-6
    except (TypeError, ValueError):
        return str(expected) == str(actual)


def _committed_writes(trace: ExecutionTrace, req: Requirement) -> list[tuple[StepRecord, Any, Any]]:
    """Authoritative writes that took effect, in commit order, as (record, key, value)."""
    writers = {w.function: w for w in req.writers}
    out: list[tuple[StepRecord, Any, Any]] = []
    for rec in trace.records:
        decl = writers.get(rec.function)
        if (
            decl is None or rec.kind != "sql" or rec.status != "ok" or not rec.committed
            or not rec.rowcount or decl.key_param not in rec.params
        ):
            continue
        if decl.value_param and decl.value_param not in rec.params:
            continue
        value = rec.params[decl.value_param] if decl.value_param else None
        if decl.value_transform == "lower" and value is not None:
            value = str(value).lower()
        out.append((rec, rec.params[decl.key_param], value))
    out.sort(key=lambda w: (w[0].durable_commit_seq or 0, w[0].seq))
    return out


def _find_row(rows: list[dict[str, Any]], column: str, key: Any) -> dict[str, Any] | None:
    return next((r for r in rows if _same(r.get(column), key)), None)


def _later_writers(trace: ExecutionTrace, req: Requirement, after_commit: int) -> list[StepRecord]:
    table = re.compile(rf"\b{re.escape(req.table)}\b", re.I)
    return [
        r for r in trace.records
        if r.kind == "sql" and r.committed and r.rowcount and (r.durable_commit_seq or 0) > after_commit
        and r.sql and table.search(r.sql) and not r.sql.lstrip().upper().startswith("SELECT")
    ]


def _check_authoritative(trace: ExecutionTrace, req: Requirement) -> tuple[list[Violation], bool]:
    writes = _committed_writes(trace, req)
    last: dict[str, tuple[StepRecord, Any, Any]] = {str(k): (r, k, v) for r, k, v in writes}
    rows = trace.final_state.get(req.table, [])
    violations = []
    for rec, key, value in last.values():
        row = _find_row(rows, req.key_column, key)
        actual = row.get(req.column) if row else "<row missing>"
        if row is None or not _same(value, actual):
            later = _later_writers(trace, req, rec.durable_commit_seq or 0)
            culprit = f"; later committed write by {later[-1].actor} (step {later[-1].seq})" if later else ""
            violations.append(Violation(
                requirement_id=req.id, requirement=req.text, table=req.table, key=key,
                column=req.column, expected=value, actual=actual,
                evidence=[rec.seq, *(r.seq for r in later)],
                explanation=(
                    f"{rec.actor} committed {req.column}={value!r} at step {rec.seq}, "
                    f"but the final value is {actual!r}{culprit}."
                ),
            ))
    return violations, bool(writes)


def _check_tombstone(trace: ExecutionTrace, req: Requirement) -> tuple[list[Violation], bool]:
    writes = _committed_writes(trace, req)
    rows = trace.final_state.get(req.table, [])
    violations = []
    for rec, key, _ in writes:
        row = _find_row(rows, req.key_column, key)
        if row is not None and row.get(req.column) is None:
            later = _later_writers(trace, req, rec.durable_commit_seq or 0)
            violations.append(Violation(
                requirement_id=req.id, requirement=req.text, table=req.table, key=key,
                column=req.column, expected="row absent or tombstoned", actual=row,
                evidence=[rec.seq, *(r.seq for r in later)],
                explanation=f"{rec.actor} committed a delete at step {rec.seq}, but the row is live again.",
            ))
    return violations, bool(writes)


def _check_delta_sum(trace: ExecutionTrace, req: Requirement) -> tuple[list[Violation], bool]:
    writes = _committed_writes(trace, req)
    totals: dict[str, tuple[Any, float, list[int]]] = {}
    for rec, key, delta in writes:
        _, total, seqs = totals.get(str(key), (key, 0.0, []))
        totals[str(key)] = (key, total + float(delta), [*seqs, rec.seq])
    rows = trace.final_state.get(req.table, [])
    initial = trace.initial_state.get(req.table, [])
    violations, checked = [], False
    for key, total, seqs in totals.values():
        row, start = _find_row(rows, req.key_column, key), _find_row(initial, req.key_column, key)
        if not row or not start or row.get(req.column) is None or start.get(req.base_column) is None:
            continue  # target column not populated yet: requirement says nothing about this row
        base = float(start[req.base_column]) * req.base_multiplier
        if start.get(req.column) is not None and not _same(start[req.column], base):
            continue  # seed already disagrees with the reference model, so it cannot judge this row
        checked = True
        expected = base + total
        if not _same(expected, row[req.column]):
            violations.append(Violation(
                requirement_id=req.id, requirement=req.text, table=req.table, key=key,
                column=req.column, expected=expected, actual=row[req.column], evidence=seqs,
                explanation=(
                    f"Starting value plus committed deltas gives {expected}, "
                    f"but the final value is {row[req.column]}."
                ),
            ))
    return violations, checked


def _pairs(rows: list[dict[str, Any]], req: Requirement) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(tuple(str(row[c]) for c in req.key_columns), []).append(row)
    return grouped


def _replay_replica(trace: ExecutionTrace, req: Requirement):
    """Replays committed source operations in commit order; never reads the trigger under test."""
    source = {
        pair: {float(r[req.column]) for r in rows}
        for pair, rows in _pairs(trace.initial_state.get(req.source_table or "", []), req).items()
    }
    replica = {
        pair: float(rows[0][req.column])
        for pair, rows in _pairs(trace.initial_state.get(req.table, []), req).items()
    }
    ops = {op.function: op for op in req.source_ops}
    touched: dict[tuple[str, ...], list[int]] = {}
    records = sorted(
        (r for r in trace.records if r.function in ops and r.kind == "sql" and r.status == "ok" and r.committed),
        key=lambda r: (r.durable_commit_seq or 0, r.seq),
    )
    for rec in records:
        decl = ops[rec.function]
        value = {name: rec.params.get(param) for name, param in decl.params.items()}
        if decl.op == "backfill":
            low, high = float(value["first_user_id"]), float(value["last_user_id"])
            for pair, levels in source.items():
                if low <= float(pair[0]) <= high and pair not in replica and levels:
                    replica[pair] = min(levels)
                    touched.setdefault(pair, []).append(rec.seq)
            continue
        if not rec.rowcount:
            continue
        pair = tuple(str(value[c]) for c in req.key_columns)
        level = float(value["access_level"])
        levels = source.setdefault(pair, set())
        touched.setdefault(pair, []).append(rec.seq)
        if decl.op == "insert":
            levels.add(level)
            replica[pair] = level
        elif decl.op == "update":
            levels.discard(level)
            levels.add(float(value["new_level"]))
            replica[pair] = float(value["new_level"])
        else:
            levels.discard(level)
            if levels:
                replica[pair] = min(levels)
            else:
                replica.pop(pair, None)
    return replica, touched


def _replica_violation(req: Requirement, pair, expected, actual, evidence, explanation) -> Violation:
    return Violation(
        requirement_id=req.id, requirement=req.text, table=req.table, key=list(pair), column=req.column,
        expected=expected, actual=actual, evidence=evidence, explanation=explanation,
    )


def _check_replica_retained(trace: ExecutionTrace, req: Requirement) -> tuple[list[Violation], bool]:
    expected, touched = _replay_replica(trace, req)
    actual = _pairs(trace.final_state.get(req.table, []), req)
    source = _pairs(trace.final_state.get(req.source_table or "", []), req)
    violations = [
        _replica_violation(
            req, pair, "row present", "<row missing>", touched.get(pair, []),
            f"Pair {list(pair)} has {len(source[pair])} source row(s), but its destination row is gone.",
        )
        for pair in sorted(source) if pair in expected and pair not in actual
    ]
    return violations, bool(touched)


_CHECKS = {
    "replica_pair_retained": _check_replica_retained,
    "authoritative_write_preserved": _check_authoritative,
    "tombstone_preserved": _check_tombstone,
    "committed_delta_sum": _check_delta_sum,
}


def evaluate(requirements: list[Requirement], trace: ExecutionTrace) -> OracleVerdict:
    violations: list[Violation] = []
    checked: list[str] = []
    vacuous: list[str] = []
    assumptions = list(STANDARD_ASSUMPTIONS)
    for req in requirements:
        found, exercised = _CHECKS[req.kind](trace, req)
        violations.extend(found)
        (checked if exercised else vacuous).append(req.id)
        assumptions.extend(a for a in req.assumptions if a not in assumptions)
    if violations:
        classification = "true_violation"
    elif trace.outcome in {"execution_error", "timeout", "stalled"}:
        classification = "execution_error"
    elif trace.outcome == "transaction_conflict":
        classification = "transaction_conflict"
    elif not checked:
        classification = "vacuous"
    else:
        classification = "no_violation"
    return OracleVerdict(
        classification=classification, violations=violations, checked=checked,
        vacuous=vacuous, assumptions=assumptions,
    )


def invariant_holds(inv: InvariantProposal, state: dict[str, list[dict[str, Any]]]) -> bool:
    """Evaluate one candidate invariant. A failure is only a hypothesis to investigate."""
    row = _find_row(state.get(inv.table, []), inv.key_column, inv.key_value)
    if inv.kind == "row_exists":
        return row is not None
    if inv.kind == "row_absent":
        return row is None
    if row is None:
        return False
    actual = row.get(inv.column or "")
    if inv.kind == "column_is_null":
        return actual is None
    if inv.kind == "column_not_null":
        return actual is not None
    if inv.kind in {"column_equals", "column_in"}:
        return any(_same(e, actual) for e in inv.expected)
    return _same(actual, row.get(inv.other_column or ""))
