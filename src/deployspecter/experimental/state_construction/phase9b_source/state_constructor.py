"""Finds an order of a proposal's own seed rows that produces the state those rows ask for. No model is involved.

A proposal's seed list says two things: rows it wants to exist (the requested state) and an order to insert them in (a
physical plan). Triggers can make that plan fail or overwrite a requested row even though another order works. This
module keeps the requested rows fixed and searches only physical plans over them:

* a row may be inserted, in any order the dependencies allow;
* a row may be left out if an identical row is already present when its turn comes.

It adds no value and no statement, never alters a trigger or constraint, and reads nothing but the seed rows and the
database's own catalogs. It is given no requirement, oracle, test actor or failing schedule, and it could not use one.

Each search runs in one transaction that is rolled back, using savepoints to share prefixes. Sequences do not roll back,
so a candidate's state counts only after a clean rebuild replays it, and only that rebuild can make it acceptable.
``CONSTRUCTED_EQUIVALENT`` means every valid plan found gives one identical state and no assumption was needed. Anything
weaker is reported as such. ``IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS`` means only that no such plan exists, not that no
database operations could produce the state.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from psycopg import sql
from pydantic import BaseModel, Field

from .config import redact
from .database import RUN_SCHEMA, Sandbox, SetupError
from .models import SeedRow, TestProposal
from .seed_feasibility import (
    SeedContext,
    _key,
    _read_state,
    _row_dict,
    _same,
    check_seed,
    proposal_sha256,
    seed_sha256,
)
from .source_model import SourceModel
from .triggers import TriggerInfo, fired_closure
from .validation import MAX_SEED_ROWS

Classification = Literal[
    "ORIGINAL_VALID", "RECONCILED_EQUIVALENT", "CONSTRUCTED_EQUIVALENT", "CONSTRUCTED_WITH_ASSUMPTIONS",
    "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS", "UNSUPPORTED_OR_UNKNOWN", "SEARCH_BUDGET_EXHAUSTED",
]
PROVEN: set[str] = {"ORIGINAL_VALID", "RECONCILED_EQUIVALENT", "CONSTRUCTED_EQUIVALENT"}
VERIFY_SCHEMA = "ds_construct_verify"
_MAX_ALTERNATIVES = 24


@dataclass(frozen=True)
class ConstructionBudget:
    max_rows: int = MAX_SEED_ROWS
    max_operations: int = 2000  # inserts tried during the search
    max_candidates: int = 64  # complete plans evaluated
    max_rebuilds: int = 24  # clean replays
    max_seconds: float = 15.0
    statement_timeout_ms: int = 5000


class PlanStep(BaseModel):
    row_index: int
    table: str
    action: Literal["insert", "already_present"]
    state_after: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class Alternative(BaseModel):
    sequence: list[str]
    reason: str


class ConstructionReport(BaseModel):
    classification: Classification
    explanation: str
    setup_sql_sha256: str
    requested_rows: list[dict[str, Any]]
    original_order: list[int]
    requested_seed_sha256: str
    effective_seed: list[SeedRow] | None = None
    effective_seed_sha256: str | None = None
    plan: list[PlanStep] = Field(default_factory=list)
    reordered: bool = False
    dependency_evidence: list[dict[str, Any]] = Field(default_factory=list)
    trigger_evidence: list[dict[str, Any]] = Field(default_factory=list)
    final_snapshot: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    final_sequences: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    independent_verification: dict[str, Any] | None = None
    rejected_alternatives: list[Alternative] = Field(default_factory=list)
    rejected_alternatives_total: int = 0
    failed_inserts: dict[str, int] = Field(default_factory=dict)
    valid_plans_found: int = 0
    distinct_final_states: int = 0
    search_exhaustive: bool = False
    budget_used: dict[str, Any] = Field(default_factory=dict)


def _matches(requested: dict[str, Any], row: dict[str, Any]) -> bool:
    return all(column in row and _same(row[column], value) for column, value in requested.items())


def assign_rows(seed: list[SeedRow], state: dict[str, list[dict[str, Any]]]) -> tuple[list[int], dict[int, dict]]:
    """Gives each requested row its own existing row (bipartite matching). Returns unmatched rows and the assignment."""
    owner: dict[tuple[str, int], int] = {}

    def place(index: int, seen: set[tuple[str, int]]) -> bool:
        requested = _row_dict(seed[index])
        for position, row in enumerate(state.get(seed[index].table, [])):
            slot = (seed[index].table, position)
            if slot in seen or not _matches(requested, row):
                continue
            seen.add(slot)
            if slot not in owner or place(owner[slot], seen):
                owner[slot] = index
                return True
        return False

    unmatched = [i for i in range(len(seed)) if not place(i, set())]
    return unmatched, {i: state[t][p] for (t, p), i in owner.items()}


def _state_sha(state: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _read_sequences(sandbox: Sandbox) -> dict[str, Any]:
    rows = sandbox.admin.execute(
        "SELECT sequencename::text, last_value FROM pg_sequences WHERE schemaname = %s ORDER BY 1", (RUN_SCHEMA,)
    ).fetchall()
    return {name: value for name, value in rows}


def _column_facts(sandbox: Sandbox, table: str) -> dict[str, dict[str, Any]]:
    rows = sandbox.admin.execute(
        """SELECT column_name::text, column_default IS NOT NULL, is_identity = 'YES', is_generated = 'ALWAYS',
                  is_nullable = 'YES', data_type::text
           FROM information_schema.columns WHERE table_schema = %s AND table_name = %s""",
        (RUN_SCHEMA, table),
    ).fetchall()
    return {r[0]: {"default": r[1], "identity": r[2], "generated": r[3], "nullable": r[4], "type": r[5]} for r in rows}


def _hard_edges(sandbox: Sandbox, seed: list[SeedRow], context: SeedContext) -> list[dict[str, Any]]:
    """A parent seed row must precede its child for a single-column, non-deferrable key no trigger can satisfy first."""
    rows = sandbox.admin.execute(
        """SELECT con.conname::text, ch.relname::text, ca.attname::text, pa_rel.relname::text, pa.attname::text
           FROM pg_constraint con
           JOIN pg_class ch ON ch.oid = con.conrelid
           JOIN pg_namespace n ON n.oid = ch.relnamespace
           JOIN pg_class pa_rel ON pa_rel.oid = con.confrelid
           JOIN pg_attribute ca ON ca.attrelid = con.conrelid AND ca.attnum = con.conkey[1]
           JOIN pg_attribute pa ON pa.attrelid = con.confrelid AND pa.attnum = con.confkey[1]
           WHERE con.contype = 'f' AND NOT con.condeferrable AND array_length(con.conkey, 1) = 1 AND n.nspname = %s""",
        (RUN_SCHEMA,),
    ).fetchall()
    written = {target.table for t in context.triggers if t.fires for target in t.targets}
    edges = []
    for name, child_table, child_column, parent_table, parent_column in rows:
        if parent_table in written:
            continue
        for child_index, child in enumerate(seed):
            value = _row_dict(child).get(child_column)
            if child.table != child_table or value is None:
                continue
            for parent_index, parent in enumerate(seed):
                if parent.table == parent_table and parent_index != child_index and \
                        _same(_row_dict(parent).get(parent_column), value):
                    edges.append({"constraint": name, "parent_row": parent_index, "child_row": child_index})
    return edges


def _trigger_evidence(triggers: list[TriggerInfo]) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name, "table": t.table, "timing": t.timing, "events": t.events, "level": t.level,
            "fires": t.fires, "deferrable": t.deferrable, "effect": t.effect, "unknown_reasons": t.unknown_reasons,
            "targets": [x.model_dump() for x in t.targets],
            "function_definition_sha256": hashlib.sha256(t.function_definition.encode()).hexdigest(),
        }
        for t in triggers
    ]


def _fired_by_seed(seed: list[SeedRow], context: SeedContext) -> list[TriggerInfo]:
    found: dict[tuple[str, str], TriggerInfo] = {}
    for table in {row.table for row in seed}:
        for trigger in fired_closure(context.triggers, RUN_SCHEMA, table, "INSERT"):
            found[(trigger.table, trigger.name)] = trigger
    return sorted(found.values(), key=lambda t: (t.table, t.name))


def _static_conflicts(seed: list[SeedRow], context: SeedContext) -> list[str]:
    """Two requested rows with one primary key can never both exist, whatever the order."""
    problems = []
    for first in range(len(seed)):
        for second in range(first + 1, len(seed)):
            a, b = seed[first], seed[second]
            info = context.schema.get(a.table)
            if a.table != b.table or info is None or not info.primary_key:
                continue
            left, right = _row_dict(a), _row_dict(b)
            if all(c in left and c in right and _same(left[c], right[c]) for c in info.primary_key):
                problems.append(f"seed rows {first} and {second} ask for the same {a.table} primary key")
    return problems


def _order_sensitive_reads(source: SourceModel | None) -> list[str]:
    """Reads with no ORDER BY can see a different first row when the insertion order changes."""
    if source is None:
        return []
    found = []
    for function in source.functions.values():
        for statement in function.statements:
            text = statement.sql.upper()
            if text.lstrip().startswith(("SELECT", "WITH")) and "ORDER BY" not in text:
                found.append(statement.statement_id)
    return found


@dataclass
class _Candidate:
    steps: list[tuple[int, str]]
    leaf_sha: str


class _Search:
    def __init__(self, sandbox, seed, budget: ConstructionBudget, edges) -> None:
        self.sandbox, self.seed, self.budget = sandbox, seed, budget
        self.parents: dict[int, set[int]] = {}
        for edge in edges:
            self.parents.setdefault(edge["child_row"], set()).add(edge["parent_row"])
        self.operations = 0
        self.leaves = 0
        self.started = time.perf_counter()
        self.stopped_by: str | None = None
        self.visited: set[tuple[str, frozenset[int]]] = set()
        self.pruned_duplicates = 0
        self.candidates: list[_Candidate] = []
        self.rejected: list[Alternative] = []
        self.rejected_total = 0
        self.failed_inserts: dict[str, int] = {}

    def _spent(self) -> bool:
        if self.stopped_by:
            return True
        if self.operations >= self.budget.max_operations:
            self.stopped_by = "max_operations"
        elif self.leaves >= self.budget.max_candidates:
            self.stopped_by = "max_candidates"
        elif time.perf_counter() - self.started > self.budget.max_seconds:
            self.stopped_by = "max_seconds"
        return bool(self.stopped_by)

    def _label(self, steps: list[tuple[int, str]]) -> list[str]:
        return [f"{'insert' if a == 'insert' else 'already present'}: row {i} ({self.seed[i].table})" for i, a in steps]

    def _reject(self, steps, reason: str) -> None:
        self.rejected_total += 1
        if len(self.rejected) < _MAX_ALTERNATIVES:
            self.rejected.append(Alternative(sequence=self._label(steps), reason=reason))

    def _leaf(self, steps, depth) -> None:
        self.leaves += 1
        admin = self.sandbox.admin
        name = f"ds_leaf{depth}"
        admin.execute(f"SAVEPOINT {name}")
        try:
            admin.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except psycopg.Error as exc:
            admin.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._reject(steps, f"a deferred constraint failed: {redact(str(exc).splitlines()[0])}")
            return
        state, _ = _read_state(self.sandbox)
        admin.execute(f"ROLLBACK TO SAVEPOINT {name}")
        unmatched, _ = assign_rows(self.seed, state)
        if unmatched:
            detail = "; ".join(
                f"row {i} asks for {_row_dict(self.seed[i])} in {self.seed[i].table}, which holds "
                f"{state.get(self.seed[i].table, [])[:3]}" for i in unmatched[:3]
            )
            self._reject(steps, f"requested state not reached: {detail}")
            return
        self.candidates.append(_Candidate(list(steps), _state_sha(state)))

    def run(self, steps, remaining: frozenset[int], state, depth=0) -> None:
        if self._spent():
            return
        if not remaining:
            self._leaf(steps, depth)
            return
        key = (_state_sha(state), remaining)
        if key in self.visited:
            self.pruned_duplicates += 1
            return
        self.visited.add(key)
        admin = self.sandbox.admin
        for index in sorted(remaining):
            if self._spent():
                return
            if self.parents.get(index, set()) & remaining:
                continue  # a required parent seed row has not been placed yet
            row = self.seed[index]
            requested = _row_dict(row)
            if any(_matches(requested, existing) for existing in state.get(row.table, [])):
                self.run([*steps, (index, "already_present")], remaining - {index}, state, depth + 1)
            name = f"ds_step{depth}"
            admin.execute(f"SAVEPOINT {name}")
            self.operations += 1
            try:
                self.sandbox._insert_seed(row)
            except psycopg.Error as exc:
                admin.execute(f"ROLLBACK TO SAVEPOINT {name}")
                label = f"row {index}: {exc.sqlstate} {exc.diag.constraint_name or ''}".strip()
                self.failed_inserts[label] = self.failed_inserts.get(label, 0) + 1
                if exc.sqlstate == "57014":
                    self.stopped_by = "statement_timeout"
                self._reject([*steps, (index, "insert")], redact(str(exc).strip().splitlines()[0]))
                continue
            after, _ = _read_state(self.sandbox)
            self.run([*steps, (index, "insert")], remaining - {index}, after, depth + 1)
            admin.execute(f"ROLLBACK TO SAVEPOINT {name}")


def _effective(seed: list[SeedRow], steps: list[tuple[int, str]]) -> list[SeedRow]:
    return [seed[i] for i, action in steps if action == "insert"]


def _replay(sandbox: Sandbox, setup_sql: str, seed, steps) -> tuple[list[PlanStep], dict, dict] | str:
    """Rebuilds from nothing, recording the state after each step, then commits through the normal path."""
    sandbox.reset(setup_sql, [])
    plan = []
    try:
        with sandbox.admin.transaction():
            for index, action in steps:
                if action == "insert":
                    sandbox._insert_seed(seed[index])
                state, _ = _read_state(sandbox)
                plan.append(PlanStep(row_index=index, table=seed[index].table, action=action, state_after=state))
            raise psycopg.Rollback()
    except psycopg.Error as exc:
        return redact(str(exc).strip().splitlines()[0])
    try:
        sandbox.reset(setup_sql, _effective(seed, steps))
    except SetupError as exc:
        return redact(str(exc).splitlines()[0])
    state, whole = _read_state(sandbox)
    if not whole:
        return "a table exceeded the read bound"
    return plan, state, _read_sequences(sandbox)


def verify_independently(settings, setup_sql: str, requested: list[SeedRow], effective: list[SeedRow]) -> dict:
    """Plain psycopg in its own schema: none of the search, the sandbox or its comparison helpers is used."""
    problems: list[str] = []
    with psycopg.connect(settings.test_dsn, autocommit=True, connect_timeout=5) as conn:
        if not conn.info.dbname.endswith("_test"):
            raise RuntimeError("the independent check runs only on the test database")
        conn.execute(f"DROP SCHEMA IF EXISTS {VERIFY_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {VERIFY_SCHEMA}")
        try:
            conn.execute(f"SET search_path = {VERIFY_SCHEMA}")
            with conn.transaction():
                conn.execute(setup_sql)
                for row in effective:
                    names = [v.column for v in row.values]
                    conn.execute(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            sql.Identifier(row.table), sql.SQL(", ").join(map(sql.Identifier, names)),
                            sql.SQL(", ").join(sql.Placeholder() * len(names)),
                        ),
                        [v.value for v in row.values],
                    )
            for index, row in enumerate(requested):
                types = dict(conn.execute(
                    """SELECT column_name::text, data_type::text FROM information_schema.columns
                       WHERE table_schema = %s AND table_name = %s""", (VERIFY_SCHEMA, row.table),
                ).fetchall())
                where = sql.SQL(" AND ").join(
                    sql.SQL("{} IS NOT DISTINCT FROM %s::{}").format(sql.Identifier(v.column), sql.SQL(types[v.column]))
                    for v in row.values
                )
                query = sql.SQL("SELECT count(*) FROM {} WHERE {}").format(sql.Identifier(row.table), where)
                found = conn.execute(query, [v.value for v in row.values]).fetchone()[0]
                if found < 1:
                    problems.append(f"requested row {index} is not in {row.table}")
            tables = [r[0] for r in conn.execute(
                "SELECT tablename::text FROM pg_tables WHERE schemaname = %s ORDER BY 1", (VERIFY_SCHEMA,)
            ).fetchall()]
            snapshot = {}
            for table in tables:
                rows = conn.execute(
                    sql.SQL("SELECT to_jsonb(t) FROM {} t").format(sql.Identifier(table))
                ).fetchall()
                snapshot[table] = sorted((r[0] for r in rows), key=lambda r: json.dumps(r, sort_keys=True))
        except psycopg.Error as exc:
            return {"verified": False, "problems": [redact(str(exc).splitlines()[0])], "snapshot_sha256": None}
        finally:
            conn.execute(f"DROP SCHEMA IF EXISTS {VERIFY_SCHEMA} CASCADE")
    return {"verified": not problems, "problems": problems, "snapshot_sha256": _state_sha(snapshot)}


def _assumptions_for_plan(sandbox, context, seed, steps, final, reordered, source) -> list[str]:
    assumptions = []
    inserted = {i for i, a in steps if a == "insert"}
    _, assigned = assign_rows(seed, final)
    for index, row in enumerate(seed):
        if index in inserted:
            continue
        facts = _column_facts(sandbox, row.table)
        requested = _row_dict(row)
        for column, column_facts in facts.items():
            if column not in requested and (column_facts["default"] or column_facts["identity"]):
                assumptions.append(
                    f"row {index} was not inserted and omits {row.table}.{column}, which has a default; the value "
                    f"present is {assigned.get(index, {}).get(column)!r}, and its default was not compared"
                )
        on_table = [
            t.name for t in context.triggers if t.table == row.table and t.fires and "INSERT" in t.events
        ]
        if on_table:
            assumptions.append(
                f"row {index} was not inserted, and {row.table} has INSERT triggers ({', '.join(on_table)}) that an "
                "explicit insert would have fired"
            )
    claimed = {_key(r) for r in assigned.values()}
    named = {row.table for row in seed}
    for table in sorted(named):
        extra = [r for r in final.get(table, []) if _key(r) not in claimed]
        if extra:
            assumptions.append(f"{table} also holds {len(extra)} row(s) the proposal did not list: {extra[:3]}")
    if reordered and (reads := _order_sensitive_reads(source)):
        assumptions.append(
            f"insertion order changed, and these source reads have no ORDER BY: {', '.join(reads[:5])}"
        )
    return assumptions


def _original_order_state(sandbox: Sandbox, setup_sql: str, seed: list[SeedRow]) -> tuple[dict, dict]:
    """The original order with its failing rows dropped: what those rows do when the order isn't changed."""
    sandbox.reset(setup_sql, [])
    admin = sandbox.admin
    with admin.transaction():
        for row in seed:
            try:
                with admin.transaction():
                    sandbox._insert_seed(row)
            except psycopg.Error:
                continue
        state, _ = _read_state(sandbox)
        sequences = _read_sequences(sandbox)
        raise psycopg.Rollback()
    return state, sequences


def _changes_from_reordering(
    sandbox, setup_sql, seed, final, sequences, context: SeedContext,
) -> list[str]:
    """What a reorder changed beyond the rows the proposal asked for. Each one is a change to intended state."""
    baseline, baseline_sequences = _original_order_state(sandbox, setup_sql, seed)
    named = {row.table for row in seed}
    changes = [
        f"compared with the original order, reordering changed {table}, which the proposal did not list"
        for table in sorted(final) if table not in named and final[table] != baseline.get(table)
    ]
    _, assigned = assign_rows(seed, final)
    for index, row in enumerate(seed):
        keys = context.schema[row.table].primary_key
        now = assigned.get(index)
        if not keys or now is None:
            continue
        before = next(
            (r for r in baseline.get(row.table, []) if all(_same(r.get(k), now.get(k)) for k in keys)), None
        )
        if before is None:
            continue
        requested = _row_dict(row)
        facts = _column_facts(sandbox, row.table)  # generated columns follow from this row's own columns
        changed = sorted(
            c for c in now
            if c not in requested and not facts.get(c, {}).get("generated") and not _same(now[c], before.get(c))
        )
        if changed:
            changes.append(
                f"compared with the original order, reordering changed {row.table}.{', '.join(changed)} on a row the "
                "proposal listed without those columns"
            )
    if sequences != baseline_sequences:
        changes.append("compared with the original order, reordering changed sequence values")
    return changes


def construct_state(
    sandbox: Sandbox, setup_sql: str, seed: list[SeedRow], context: SeedContext,
    source: SourceModel | None = None, budget: ConstructionBudget | None = None,
) -> ConstructionReport:
    budget = budget or ConstructionBudget()
    report = ConstructionReport(
        classification="UNSUPPORTED_OR_UNKNOWN", explanation="",
        setup_sql_sha256=hashlib.sha256(setup_sql.encode()).hexdigest(),
        requested_rows=[{"row_index": i, "table": r.table, "columns": _row_dict(r)} for i, r in enumerate(seed)],
        original_order=list(range(len(seed))), requested_seed_sha256=seed_sha256(seed),
    )
    fired = _fired_by_seed(seed, context)
    report.trigger_evidence = _trigger_evidence(fired)

    def finish(classification: Classification, explanation: str) -> ConstructionReport:
        report.classification, report.explanation = classification, explanation
        sandbox.reset(setup_sql, [])
        return report

    def accept_as_is(classification: Classification, effective, state, explanation) -> ConstructionReport:
        report.effective_seed, report.effective_seed_sha256 = effective, seed_sha256(effective)
        report.final_snapshot = state
        return finish(classification, explanation)

    original = check_seed(sandbox, setup_sql, seed, context, "strict")
    if original.feasible and not assign_rows(seed, original.final_state)[0]:
        return accept_as_is("ORIGINAL_VALID", seed, original.final_state, "The original sequence reaches the state.")
    reconciled = check_seed(sandbox, setup_sql, seed, context, "reconcile")
    if reconciled.feasible and reconciled.transformations and not assign_rows(seed, reconciled.final_state)[0]:
        return accept_as_is(
            "RECONCILED_EQUIVALENT", reconciled.effective_seed, reconciled.final_state,
            "The original order works once a row identical to a trigger's output is skipped.",
        )
    if structural := [d for d in original.diagnostics if d.stage == "structural"]:
        return finish("IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS", f"Not expressible here: {structural[0].message}")
    if conflicts := _static_conflicts(seed, context):
        return finish("IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS", "; ".join(conflicts))
    before_tables = {t.table for t in context.triggers if t.timing == "BEFORE" and t.fires}
    for diagnostic in original.diagnostics:
        order_independent = diagnostic.category == "TYPE_MISMATCH" or (
            diagnostic.category in {"CHECK_CONSTRAINT_FAILURE", "NOT_NULL_FAILURE"}
            and diagnostic.table not in before_tables
        )
        if order_independent and not diagnostic.raised_inside_trigger:
            return finish(
                "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS",
                f"Seed row {diagnostic.row_index} fails in any order: {diagnostic.message}",
            )
    if len(seed) > budget.max_rows:
        return finish("SEARCH_BUDGET_EXHAUSTED", f"{len(seed)} seed rows exceed the limit of {budget.max_rows}.")
    if unknown := [t.name for t in fired if t.effect == "unknown"]:
        return finish(
            "UNSUPPORTED_OR_UNKNOWN", f"Triggers with unknown effects fire on these rows: {', '.join(unknown)}."
        )
    outside = sorted({
        f"{x.schema_name}.{x.table}" for t in fired for x in t.targets if x.schema_name != RUN_SCHEMA
    })
    if outside:
        return finish("UNSUPPORTED_OR_UNKNOWN", f"Triggers write outside the observed schema: {', '.join(outside)}.")
    if ruled := sorted({r.table for r in seed} & context.tables_with_rules):
        return finish("UNSUPPORTED_OR_UNKNOWN", f"Rewrite rules apply to: {', '.join(ruled)}.")

    edges = _hard_edges(sandbox, seed, context)
    report.dependency_evidence = edges
    sandbox.reset(setup_sql, [])
    search = _Search(sandbox, seed, budget, edges)
    admin = sandbox.admin
    with admin.transaction():
        admin.execute(f"SET LOCAL statement_timeout = {int(budget.statement_timeout_ms)}")
        state, whole = _read_state(sandbox)
        if whole:
            search.run([], frozenset(range(len(seed))), state)
        else:
            search.stopped_by = "read_bound"
        raise psycopg.Rollback()
    report.rejected_alternatives = search.rejected
    report.rejected_alternatives_total = search.rejected_total
    report.failed_inserts = search.failed_inserts
    report.valid_plans_found = len(search.candidates)
    report.search_exhaustive = search.stopped_by is None
    report.budget_used = {
        "operations": search.operations, "plans_evaluated": search.leaves,
        "duplicate_states_pruned": search.pruned_duplicates, "stopped_by": search.stopped_by,
        "seconds": round(time.perf_counter() - search.started, 4),
        "limits": {
            "max_rows": budget.max_rows, "max_operations": budget.max_operations,
            "max_candidates": budget.max_candidates, "max_rebuilds": budget.max_rebuilds,
            "max_seconds": budget.max_seconds,
        },
    }
    if not search.candidates:
        if search.stopped_by:
            return finish(
                "SEARCH_BUDGET_EXHAUSTED",
                f"No valid plan found before {search.stopped_by} was reached: {search.operations} inserts, "
                f"{search.leaves} complete plans. The state may still be reachable.",
            )
        if deferred := [t.name for t in fired if t.deferrable]:
            return finish(
                "UNSUPPORTED_OR_UNKNOWN",
                f"No plan worked, but deferred triggers run only at commit ({', '.join(deferred)}), and this search "
                "cannot leave a row out for one of them to produce.",
            )
        return finish(
            "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS",
            f"Every order of these rows was tried ({search.rejected_total} alternatives rejected) and none reaches the "
            "requested state. Other operations were not considered.",
        )

    def preference(candidate: _Candidate) -> tuple:
        inserted = [i for i, a in candidate.steps if a == "insert"]
        inversions = sum(inserted[x] > inserted[y] for x in range(len(inserted)) for y in range(x + 1, len(inserted)))
        return len(inserted), inversions, candidate.steps

    ordered = sorted(search.candidates, key=preference)
    rebuilds = 0
    outcomes: list[tuple[_Candidate, list[PlanStep], dict, dict]] = []
    for candidate in ordered:
        if rebuilds >= budget.max_rebuilds:
            break
        rebuilds += 2
        replayed = _replay(sandbox, setup_sql, seed, candidate.steps)
        if isinstance(replayed, str):
            search.rejected_total += 1
            report.rejected_alternatives.append(Alternative(
                sequence=search._label(candidate.steps), reason=f"failed on a clean rebuild: {replayed}",
            ))
            continue
        plan, final, sequences = replayed
        unmatched, _ = assign_rows(seed, final)
        if unmatched:
            search.rejected_total += 1
            report.rejected_alternatives.append(Alternative(
                sequence=search._label(candidate.steps), reason=f"rows {unmatched} are absent after a clean rebuild",
            ))
            continue
        outcomes.append((candidate, plan, final, sequences))
    report.rejected_alternatives_total = search.rejected_total
    report.budget_used["rebuilds"] = rebuilds
    if not outcomes:
        return finish(
            "UNSUPPORTED_OR_UNKNOWN" if rebuilds >= budget.max_rebuilds else "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS",
            "No candidate plan survived a clean rebuild.",
        )
    best, plan, final, sequences = outcomes[0]
    # The same plan again: a state that changes between identical runs can't be called equivalent to anything.
    repeat = _replay(sandbox, setup_sql, seed, best.steps)
    if isinstance(repeat, str) or repeat[1] != final or repeat[2] != sequences:
        return finish(
            "UNSUPPORTED_OR_UNKNOWN",
            "The same plan gave a different state when repeated, so the state is not deterministic (for example, it "
            "may use the clock or random values).",
        )
    effective = _effective(seed, best.steps)
    inserted_order = [i for i, a in best.steps if a == "insert"]
    report.reordered = inserted_order != sorted(inserted_order)
    report.plan, report.final_snapshot, report.final_sequences = plan, final, sequences
    report.effective_seed, report.effective_seed_sha256 = effective, seed_sha256(effective)
    if any(row not in seed for row in effective):
        raise AssertionError("a constructed seed may contain only rows from the proposal")
    verification = verify_independently(sandbox.settings, setup_sql, seed, effective)
    report.independent_verification = verification
    verification["snapshot_matches_rebuild"] = verification["snapshot_sha256"] == _state_sha(final)
    if not verification["verified"] or not verification["snapshot_matches_rebuild"]:
        return finish("UNSUPPORTED_OR_UNKNOWN", f"The independent check disagreed: {verification}.")

    distinct = {_state_sha({"tables": o[2], "sequences": o[3]}) for o in outcomes}
    report.distinct_final_states = len(distinct)
    assumptions = _assumptions_for_plan(sandbox, context, seed, best.steps, final, report.reordered, source)
    if report.reordered:
        assumptions.extend(_changes_from_reordering(sandbox, setup_sql, seed, final, sequences, context))
    if search.stopped_by:
        assumptions.append(f"the search stopped at {search.stopped_by}, so other plans may reach a different state")
    if len(outcomes) < len(ordered):
        assumptions.append("not every valid plan was rebuilt, so other plans may reach a different state")
    if len(distinct) > 1:
        tables = sorted({
            table for o in outcomes for table in o[2] if o[2].get(table) != final.get(table)
        })
        where = f"tables {tables}" if tables else "sequence values"
        assumptions.append(
            f"{len(distinct)} valid plans end in different states, differing in {where}; choosing this plan assumes "
            "the test does not depend on that difference"
        )
    report.assumptions = assumptions
    report.evidence = [
        f"PostgreSQL reached every requested row after a clean rebuild of {len(effective)} insert(s).",
        f"{len(outcomes)} valid plan(s) rebuilt, ending in {len(distinct)} distinct state(s), sequences included.",
        "The same plan gave the same state when repeated.",
        "A separate plain-SQL check in its own schema found every requested row and the same snapshot.",
    ]
    if assumptions:
        return finish("CONSTRUCTED_WITH_ASSUMPTIONS", "A plan reaches the rows, but equivalence rests on assumptions.")
    return finish("CONSTRUCTED_EQUIVALENT", "A plan reaches the requested state, and every valid plan agrees on it.")


def construction_audit(proposal: TestProposal, report: ConstructionReport) -> dict[str, Any]:
    """Everything a reviewer needs to rebuild the state without trusting the proposal's explanation."""
    effective = None
    if report.effective_seed is not None:
        effective = proposal.model_copy(update={"seed": report.effective_seed})
    return {
        "mode": "construct",
        "classification": report.classification,
        "raw_proposal": proposal.model_dump(),
        "raw_proposal_sha256": proposal_sha256(proposal),
        "construction": report.model_dump(),
        "effective_proposal_sha256": proposal_sha256(effective) if effective else None,
    }
