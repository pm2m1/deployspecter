"""Rejects or compiles a TestProposal. This is the trust boundary for generated specs.

A proposal cannot carry SQL. Each call names a statement extracted from the
submitted source, and BEGIN/COMMIT placement comes from the source's transaction
blocks. A proposal therefore cannot introduce a defect the code does not have
by writing a different statement; it can still choose inputs and data flow,
which are checked against the extracted flow where that is statically known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    Actor,
    ActorProposal,
    ExecutableSpec,
    InvariantProposal,
    ParamBinding,
    Step,
    TestProposal,
)
from .source_model import ExtractedStatement, FunctionModel, SourceModel

MAX_ACTORS = 3
MAX_STEPS_PER_ACTOR = 16
MAX_SEED_ROWS = 10
_ACTOR_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,30}$")
_MAX_ACTOR_ID = 31
_ALLOWED_STATEMENTS = {"SELECT", "INSERT", "UPDATE", "DELETE"}

Catalog = dict[str, set[str]]


def actor_ids(names: list[str]) -> list[str]:
    """Map display names to unique identifiers, deterministically and in order.

    Valid names are kept as they are, so a name that needs normalizing can never take
    the identifier of one that does not.
    """
    taken = {name for name in names if _ACTOR_ID_RE.match(name)}
    ids: list[str] = []
    for position, name in enumerate(names, start=1):
        if _ACTOR_ID_RE.match(name):
            ids.append(name)
            continue
        base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or f"actor_{position}"
        if not base[0].isalpha():
            base = f"a_{base}"
        base = base[:_MAX_ACTOR_ID]
        candidate, suffix = base, 2
        while candidate in taken:
            tail = f"_{suffix}"
            candidate = base[: _MAX_ACTOR_ID - len(tail)] + tail
            suffix += 1
        taken.add(candidate)
        ids.append(candidate)
    return ids


class SpecRejected(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class CompileReport:
    spec: ExecutableSpec
    dropped_invariants: list[str] = field(default_factory=list)
    unverified_flows: list[str] = field(default_factory=list)
    renamed_actors: dict[str, str] = field(default_factory=dict)


def compile_proposal(
    proposal: TestProposal, source: SourceModel, catalog: Catalog, generator: str
) -> CompileReport:
    reasons: list[str] = []
    if not 2 <= len(proposal.actors) <= MAX_ACTORS:
        reasons.append(f"need 2..{MAX_ACTORS} actors, got {len(proposal.actors)}")
    names = [a.name for a in proposal.actors]
    if len(set(names)) != len(names):
        reasons.append(f"duplicate actor names: {names}")
    if len(proposal.seed) > MAX_SEED_ROWS:
        reasons.append(f"too many seed rows ({len(proposal.seed)} > {MAX_SEED_ROWS})")
    for row in proposal.seed:
        if row.table not in catalog:
            reasons.append(f"seed: unknown table {row.table!r}")
            continue
        for value in row.values:
            if value.column not in catalog[row.table]:
                reasons.append(f"seed: unknown column {row.table}.{value.column}")

    unverified: list[str] = []
    actors: list[Actor] = []
    ids = actor_ids(names)
    renamed = {name: new for name, new in zip(names, ids, strict=True) if name != new}
    for proposal_actor, actor_id in zip(proposal.actors, ids, strict=True):
        actor = _compile_actor(proposal_actor, actor_id, source, reasons, unverified)
        if actor is not None:
            actors.append(actor)
    if reasons:
        raise SpecRejected(reasons)

    kept: list[InvariantProposal] = []
    dropped: list[str] = []
    for inv in proposal.invariants:
        problem = _invariant_problem(inv, catalog)
        if problem:
            dropped.append(f"{inv.id}: {problem}")
        else:
            kept.append(inv)
    spec = ExecutableSpec(
        name=proposal.name, generator=generator, seed=proposal.seed, actors=actors,
        invariants=kept, rationale=proposal.rationale,
    )
    return CompileReport(spec, dropped, unverified, renamed)


def _compile_actor(
    proposal: ActorProposal, actor_id: str, source: SourceModel, reasons: list[str], unverified: list[str]
) -> Actor | None:
    where = f"actor {proposal.name!r}"
    fn = source.functions.get(proposal.function)
    if fn is None:
        reasons.append(f"{where}: unknown function {proposal.function!r}")
        return None
    index_of = {s.statement_id: i for i, s in enumerate(fn.statements)}
    start = len(reasons)
    bound: set[str] = set()
    chosen: list[tuple[int, ExtractedStatement]] = []
    for call in proposal.calls:
        if call.statement_id not in index_of:
            reasons.append(f"{where}: statement {call.statement_id!r} is not in {fn.ref}")
            continue
        st = fn.statements[index_of[call.statement_id]]
        first_word = st.sql.split(None, 1)[0].upper() if st.sql.strip() else ""
        if first_word not in _ALLOWED_STATEMENTS or ";" in st.sql:
            reasons.append(f"{where}: {st.statement_id} is not a single SELECT/INSERT/UPDATE/DELETE statement")
            continue
        chosen.append((index_of[call.statement_id], st))
        _check_params(where, call.params, st, bound, reasons, unverified)
        if call.bind_as:
            if call.bind_as in bound:
                reasons.append(f"{where}: bind name {call.bind_as!r} reused")
            bound.add(call.bind_as)
    _check_path(where, fn, chosen, reasons)
    if len(reasons) > start or not chosen:
        if not chosen and len(reasons) == start:
            reasons.append(f"{where}: no statements")
        return None

    steps = _with_transaction_boundaries(proposal, chosen)
    if len(steps) > MAX_STEPS_PER_ACTOR:
        reasons.append(f"{where}: {len(steps)} steps exceeds {MAX_STEPS_PER_ACTOR}")
        return None
    return Actor(name=actor_id, function=fn.ref, steps=steps)


def _check_path(where, fn: FunctionModel, chosen, reasons: list[str]) -> None:
    """The call list must be a path the function can actually take."""
    seen: set[int] = set()
    prev = -1
    for idx, st in chosen:
        if idx <= prev and not (st.in_loop and fn.statements[prev].in_loop):
            reasons.append(f"{where}: {st.statement_id} out of source order")
        for skipped in range(prev + 1, idx):
            other = fn.statements[skipped]
            if not (other.conditional or other.in_loop):
                reasons.append(f"{where}: skips unconditional statement {other.statement_id}")
        prev = idx
        seen.add(idx)
    for i, st in enumerate(fn.statements):
        if i > prev and not (st.conditional or st.in_loop):
            reasons.append(f"{where}: omits unconditional statement {st.statement_id}")


def _check_params(where, params: list[ParamBinding], st: ExtractedStatement, bound, reasons, unverified) -> None:
    names = [p.name for p in params]
    if sorted(names) != sorted(st.placeholders):
        reasons.append(f"{where}: {st.statement_id} needs params {st.placeholders}, got {names}")
        return
    for p in params:
        label = f"{where}: {st.statement_id} param {p.name!r}"
        if p.source == "row_ref":
            if p.bind not in bound:
                reasons.append(f"{label}: bind {p.bind!r} is not produced by an earlier statement")
            if not p.column:
                reasons.append(f"{label}: row_ref needs a column")
            if p.transform in {"add", "multiply"} and p.operand is None:
                reasons.append(f"{label}: transform {p.transform} needs an operand")
        kind, detail = st.flows.get(p.name, ("unknown", ""))
        if kind == "row" and (p.source != "row_ref" or p.column != detail or p.transform != "none"):
            reasons.append(f"{label}: source code reads row[{detail!r}]; binding disagrees")
        elif kind == "arg" and p.source != "literal":
            reasons.append(f"{label}: source passes function argument {detail!r}; use a literal")
        elif kind == "const" and (p.source != "literal" or p.value != detail):
            reasons.append(f"{label}: source passes constant {detail!r}; binding disagrees")
        elif kind == "unknown":
            unverified.append(f"{st.statement_id}:{p.name}")


def _with_transaction_boundaries(proposal: ActorProposal, chosen) -> list[Step]:
    steps: list[Step] = []
    current: int | None = None
    prev = -1
    calls = [c for c in proposal.calls]
    for call, (idx, st) in zip(calls, chosen, strict=True):
        new_iteration = idx <= prev
        if st.txn_block != current or (new_iteration and st.txn_in_loop and current is not None):
            if current is not None:
                steps.append(Step(kind="commit"))
            if st.txn_block is not None:
                steps.append(Step(kind="begin"))
            current = st.txn_block
        steps.append(Step(
            kind="sql", sql=st.sql, statement_id=st.statement_id,
            params={p.name: p for p in call.params}, bind_as=call.bind_as,
            removable=st.conditional or st.in_loop,
        ))
        prev = idx
    if current is not None:
        steps.append(Step(kind="commit"))
    return steps


def _invariant_problem(inv: InvariantProposal, catalog: Catalog) -> str | None:
    columns = catalog.get(inv.table)
    if columns is None:
        return f"unknown table {inv.table!r}"
    for col in (inv.key_column, inv.column, inv.other_column):
        if col and col not in columns:
            return f"unknown column {inv.table}.{col}"
    if inv.kind in {"column_equals", "column_in"} and not inv.expected:
        return f"{inv.kind} needs expected values"
    if inv.kind not in {"row_exists", "row_absent"} and not inv.column:
        return f"{inv.kind} needs a column"
    if inv.kind == "column_equals_column" and not inv.other_column:
        return "column_equals_column needs other_column"
    return None
