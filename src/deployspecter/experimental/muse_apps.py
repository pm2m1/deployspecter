"""EXPERIMENTAL: Meta Muse proposals for the SQLAlchemy ledger and for WorkloadSpec v2 on the planner path.

Muse sees what DeploySpecter can capture about each trusted application, and nothing about its defect: handler
names, argument types and ranges, the exact statements each handler may send (as captured from the build under test),
and the schema. It never sees docstrings, fixes, the oracle or any result. It returns JSON only. Nothing it returns
is executed except through a deterministic validator and the trusted registry; no generated SQL or code ever runs.

* ``ledger``: invocations of the ORM handlers, a seed of accounts, and invariant hypotheses in a small fixed grammar
  that ``evaluate_hypothesis`` checks on final states. A hypothesis is data: it is evaluated, never trusted.
* ``planner``: actors and relational state goals for Gate 2A's synthetic reservations application. Each proposal is
  mapped field-for-field into a WorkloadSpec v2 document, which the unmodified chain then judges
  (``planning_adapter.plan``). The mapping only renames fields; it repairs nothing.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from ..meta_client import PRICING_NOTE, MetaClient, strict_json_schema
from ..research.track_a import scenario as sc
from .apps import ledger_orm as L

PROMPT_VERSION = "mvp-muse-apps-1"
COMMON = ("A deterministic scheduler will run your invocations concurrently and try different interleavings of "
          "their database operations; an independent checker then inspects the final state. You may only invoke the "
          "listed handlers with the listed argument types; you cannot write SQL or code. Every proposal must satisfy "
          "the stated constraints exactly, or it is rejected. Return JSON only.")
INSTRUCTIONS = {
    "ledger": "You design concurrency test workloads for a small Python service built on an ORM. " + COMMON
              + " Also state invariant hypotheses, in the given grammar, that a correct service should keep.",
    "planner": "You design concurrency test workloads, including the initial database state they need, for a small "
               "Python service. " + COMMON,
}


# ------------------------------------------------------------------ the ledger (SQLAlchemy) target

LEDGER_RANGES = {"account_id": (1, 4), "src": (1, 4), "dst": (1, 4), "amount": (1, 500), "opening": (0, 1000)}
LEDGER_MAX_INVOCATIONS = 3


class IntArg(BaseModel):
    name: str
    value: int


class LedgerInvocation(BaseModel):
    actor: str
    handler: str
    args: list[IntArg]


class SeedAccount(BaseModel):
    id: int
    opening: int


class Hypothesis(BaseModel):
    kind: Literal["column_at_least", "equals_base_plus_child_sum", "column_sum_unchanged", "child_sum_equals"]
    table: str
    column: str
    bound: int | None
    base_column: str | None
    child_table: str | None
    child_fk: str | None
    child_column: str | None
    statement: str


class LedgerProposal(BaseModel):
    rationale: str
    invocations: list[LedgerInvocation]
    seed_accounts: list[SeedAccount]
    invariant_hypotheses: list[Hypothesis]


class LedgerSet(BaseModel):
    proposals: list[LedgerProposal]


def ledger_context() -> dict[str, Any]:
    handlers = [{"app_version": v, "handler": n, "args": {k: "int" for k in h.args},
                 "may_send_sql": sorted(h.allowed_sql)} for (v, n), h in sorted(L.registry("buggy").items())]
    return {"schema_ddl": " ".join(L.DDL.split()), "handlers": handlers,
            "constraints": {
                "invocations": f"2 to {LEDGER_MAX_INVOCATIONS}, each with a unique lowercase actor name",
                "argument_ranges": {k: list(v) for k, v in LEDGER_RANGES.items() if k != "opening"},
                "transfer": "src and dst differ",
                "seed": "1 to 4 accounts with distinct ids in 1..4 and opening in 0..1000; each starts with "
                        "balance = opening and no entries; invocations may only reference seeded accounts",
                "hypotheses": "1 to 4, each one of: column_at_least(table, column, bound); "
                              "equals_base_plus_child_sum(table, column, base_column, child_table, child_fk, "
                              "child_column); column_sum_unchanged(table, column); child_sum_equals(child_table, "
                              "child_column, bound). Unused fields are null; statement is one sentence."}}


class LedgerWorkload(BaseModel):
    label: str
    source: str
    invocations: list[dict[str, Any]]
    seed: dict[str, list[dict[str, Any]]]
    hypotheses: list[dict[str, Any]]


def validate_ledger(p: Any, *, source: str, label: str) -> LedgerWorkload | list[str]:
    """Deterministic. A list of reasons means the proposal is rejected as a whole; nothing is repaired."""
    try:
        prop = LedgerProposal.model_validate(p)
    except Exception as exc:
        return [f"shape: {str(exc)[:200]}"]
    reasons: list[str] = []
    registry = L.registry("buggy")
    ids = [a.id for a in prop.seed_accounts]
    if not 1 <= len(ids) <= 4 or len(set(ids)) != len(ids) or not all(1 <= i <= 4 for i in ids):
        reasons.append("seed: 1 to 4 accounts with distinct ids in 1..4")
    if not all(0 <= a.opening <= 1000 for a in prop.seed_accounts):
        reasons.append("seed: opening outside 0..1000")
    if not 2 <= len(prop.invocations) <= LEDGER_MAX_INVOCATIONS:
        reasons.append(f"invocations: 2 to {LEDGER_MAX_INVOCATIONS}")
    actors = [i.actor for i in prop.invocations]
    if len(set(actors)) != len(actors) or not all(a.isascii() and a.isidentifier() and a.islower() for a in actors):
        reasons.append("actors: unique lowercase names")
    invocations = []
    for inv in prop.invocations:
        h = registry.get(("v1", inv.handler))
        if h is None:
            reasons.append(f"{inv.actor}: unknown handler {inv.handler!r}")
            continue
        args = {a.name: a.value for a in inv.args}
        if len(args) != len(inv.args) or set(args) != set(h.args):
            reasons.append(f"{inv.actor}: arguments must be exactly {sorted(h.args)}")
            continue
        for k, v in args.items():
            lo, hi = LEDGER_RANGES[k]
            if not lo <= v <= hi:
                reasons.append(f"{inv.actor}: {k}={v} outside {lo}..{hi}")
            if k in ("account_id", "src", "dst") and v not in ids:
                reasons.append(f"{inv.actor}: {k}={v} isn't a seeded account")
        if inv.handler == "transfer" and args.get("src") == args.get("dst"):
            reasons.append(f"{inv.actor}: transfer src and dst must differ")
        invocations.append({"actor": inv.actor, "app_version": "v1", "handler": inv.handler, "args": args})
    hyps = []
    if not 1 <= len(prop.invariant_hypotheses) <= 4:
        reasons.append("hypotheses: 1 to 4")
    for h in prop.invariant_hypotheses:
        problem = hypothesis_problem(h.model_dump())
        if problem:
            reasons.append(f"hypothesis: {problem}")
        hyps.append(h.model_dump())
    if reasons:
        return reasons
    seed = {"accounts": [{"id": a.id, "owner": f"owner{a.id}", "opening": a.opening, "balance": a.opening}
                         for a in sorted(prop.seed_accounts, key=lambda a: a.id)]}
    return LedgerWorkload(label=label, source=source, invocations=invocations, seed=seed, hypotheses=hyps)


COLUMNS = {"accounts": {"id", "owner", "opening", "balance"}, "entries": {"id", "account_id", "delta"}}


def hypothesis_problem(h: dict[str, Any]) -> str | None:
    def col(t: Any, c: Any) -> bool:
        return t in COLUMNS and c in COLUMNS[t] and c not in ("owner",)
    k = h["kind"]
    if k == "column_at_least" and not (col(h["table"], h["column"]) and isinstance(h["bound"], int)):
        return "column_at_least needs a numeric table.column and an int bound"
    if k == "equals_base_plus_child_sum" and not (
            col(h["table"], h["column"]) and col(h["table"], h["base_column"])
            and col(h["child_table"], h["child_fk"]) and col(h["child_table"], h["child_column"])):
        return "equals_base_plus_child_sum names a column that doesn't exist"
    if k == "column_sum_unchanged" and not col(h["table"], h["column"]):
        return "column_sum_unchanged names a column that doesn't exist"
    if k == "child_sum_equals" and not (col(h["child_table"], h["child_column"]) and isinstance(h["bound"], int)):
        return "child_sum_equals needs child_table.child_column and an int bound"
    return None


def evaluate_hypothesis(h: dict[str, Any], initial: dict[str, list[dict[str, Any]]],
                        final: dict[str, list[dict[str, Any]]]) -> bool:
    """True when the final state satisfies the hypothesis. Pure; a fixed grammar, no generated code."""
    k = h["kind"]
    if k == "column_at_least":
        return all(r[h["column"]] >= h["bound"] for r in final[h["table"]])
    if k == "column_sum_unchanged":
        return sum(r[h["column"]] for r in final[h["table"]]) == sum(r[h["column"]] for r in initial[h["table"]])
    if k == "child_sum_equals":
        return sum(r[h["child_column"]] for r in final[h["child_table"]]) == h["bound"]
    sums: dict[Any, int] = {}
    for r in final[h["child_table"]]:
        sums[r[h["child_fk"]]] = sums.get(r[h["child_fk"]], 0) + r[h["child_column"]]
    return all(r[h["column"]] == r[h["base_column"]] + sums.get(r["id"], 0) for r in final[h["table"]])


def ledger_curated() -> list[tuple[str, dict[str, Any]]]:
    def prop(invs: list[tuple[str, str, dict[str, int]]], openings: dict[int, int]) -> dict[str, Any]:
        return {"rationale": "hand-written", "seed_accounts": [{"id": i, "opening": o} for i, o in openings.items()],
                "invocations": [{"actor": a, "handler": h, "args": [{"name": k, "value": v} for k, v in args.items()]}
                                for a, h, args in invs],
                "invariant_hypotheses": [{"kind": "column_at_least", "table": "accounts", "column": "balance",
                                          "bound": 0, "base_column": None, "child_table": None, "child_fk": None,
                                          "child_column": None, "statement": "no overdraft"}]}
    return [
        ("curated#0", prop([("a", "withdraw", {"account_id": 1, "amount": 70}),
                            ("b", "withdraw", {"account_id": 1, "amount": 60})], {1: 100})),
        ("curated#1", prop([("a", "deposit", {"account_id": 1, "amount": 10}),
                            ("b", "deposit", {"account_id": 1, "amount": 20})], {1: 0})),
        ("curated#2", prop([("a", "transfer", {"src": 1, "dst": 2, "amount": 30}),
                            ("b", "transfer", {"src": 2, "dst": 1, "amount": 10})], {1: 100, 2: 100})),
        ("curated#3", prop([("a", "withdraw", {"account_id": 1, "amount": 50}),
                            ("b", "deposit", {"account_id": 2, "amount": 50})], {1: 100, 2: 0})),
    ]


def ledger_systematic(limit: int) -> list[tuple[str, dict[str, Any]]]:
    """Every ordered pair of handlers, on one fixed two-account seed, in a fixed order."""
    args = {"withdraw": {"account_id": 1, "amount": 60}, "deposit": {"account_id": 1, "amount": 25},
            "transfer": {"src": 1, "dst": 2, "amount": 40}}
    names = sorted(args)
    no_overdraft = {"kind": "column_at_least", "table": "accounts", "column": "balance", "bound": 0,
                    "base_column": None, "child_table": None, "child_fk": None, "child_column": None,
                    "statement": "no overdraft"}

    def inv(actor: str, h: str) -> dict[str, Any]:
        return {"actor": actor, "handler": h, "args": [{"name": k, "value": v} for k, v in args[h].items()]}
    out = []
    for i, x in enumerate(names):
        for y in names[i:]:
            out.append((f"systematic#{len(out)}", {
                "rationale": "pairwise enumeration",
                "seed_accounts": [{"id": 1, "opening": 100}, {"id": 2, "opening": 0}],
                "invocations": [inv("a", x), inv("b", y)], "invariant_hypotheses": [no_overdraft]}))
    return out[:limit]


# ------------------------------------------------------------------ the planner (WorkloadSpec v2) target


class TypedArg(BaseModel):
    name: str
    kind: Literal["int", "str"]
    int_value: int | None
    str_value: str | None


class PlannerActor(BaseModel):
    name: str
    entrypoint: str
    arguments: list[TypedArg]


class GoalConstraintP(BaseModel):
    field: str
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null"]
    kind: Literal["int", "str", "none"]
    int_value: int | None
    str_value: str | None


class GoalP(BaseModel):
    entity: str
    label: str
    count: int
    constraints: list[GoalConstraintP]


class PlannerProposal(BaseModel):
    name: str
    rationale: str
    actors: list[PlannerActor]
    state_goals: list[GoalP]


class PlannerSet(BaseModel):
    proposals: list[PlannerProposal]


def planner_context() -> dict[str, Any]:
    from .planning_adapter import ALLOWED_SQL
    return {"registry": sc.REGISTRY_CONFIG["entrypoints"], "argument_contracts": sc.CONTRACTS_CONFIG["contracts"],
            "may_send_sql": ALLOWED_SQL,
            "entities": sc.context_config(registry_digest="", schema_digest="")["entities"],
            "constraints": {"actors": "2 to 3 actors with unique names matching ^[a-z][a-z0-9_]{0,15}$, application "
                                      "version v1, role request",
                            "state_goals": "1 to 4 goals; entity is one listed; count 1 to 3; each constraint is a "
                                           "field, an op and a typed value (kind none only with is_null/not_null)",
                            "name": "a lowercase identifier"}}


def to_workload_spec(p: Any) -> dict[str, Any]:
    """Field-for-field into a WorkloadSpec v2 document. Renames only; the frozen chain decides validity."""
    prop = PlannerProposal.model_validate(p)

    def typed(kind: str, i: int | None, s: str | None) -> dict[str, Any] | None:
        return None if kind == "none" else {"type": kind, "value": i if kind == "int" else s}
    return {"schema_version": 2, "name": prop.name, "application": sc.APPLICATION, "rationale": prop.rationale,
            "provenance": {"origin": "model_proposal", "author": "muse"},
            "actors": [{"name": a.name, "entrypoint": a.entrypoint, "application_version": "v1", "role": "request",
                        "arguments": [{"name": g.name, "value": typed(g.kind, g.int_value, g.str_value)}
                                      for g in a.arguments]} for a in prop.actors],
            "state_goals": [{"entity": g.entity, "label": g.label, "count": g.count,
                             "constraints": [{k: v for k, v in {"field": c.field, "op": c.op,
                                                                "value": typed(c.kind, c.int_value, c.str_value)}
                                              .items() if v is not None} for c in g.constraints]}
                            for g in prop.state_goals]}


def planner_curated() -> list[tuple[str, dict[str, Any]]]:
    return [("curated#0", json.loads(json.dumps(sc.DOCUMENT)))]


def planner_systematic(limit: int) -> list[tuple[str, dict[str, Any]]]:
    """Two reservations against one event, over a grid of capacities and quantities."""
    out = []
    for capacity in (2, 3, 4, 5):
        for qty in (1, 2, 3):
            d = json.loads(json.dumps(sc.DOCUMENT))
            d["name"] = f"grid_c{capacity}_q{qty}"
            d["provenance"] = {"origin": "deterministic_baseline", "author": "mvp_grid"}
            for actor in d["actors"]:
                actor["arguments"][2]["value"] = {"type": "int", "value": qty}
            d["state_goals"][0]["constraints"][1]["value"] = {"type": "int", "value": capacity}
            out.append((f"systematic#{len(out)}", d))
    return out[:limit]


# ------------------------------------------------------------------ the request

TARGETS = {"ledger": (ledger_context, LedgerSet), "planner": (planner_context, PlannerSet)}


def prompt(target: str, n: int) -> str:
    return (f"Propose exactly {n} different workloads.\n\nContext (JSON):\n"
            + json.dumps(TARGETS[target][0](), indent=1, sort_keys=True, default=str))


def prompt_hash(target: str, n: int) -> str:
    return hashlib.sha256((PROMPT_VERSION + INSTRUCTIONS[target] + prompt(target, n)).encode()).hexdigest()


def propose(client: MetaClient, target: str, *, n: int, ledger: Path, call_label: str) -> list[tuple[str, Any]]:
    """One request; the raw proposals (still to be validated); one ledger entry with usage and estimated cost."""
    schema_model = TARGETS[target][1]
    entry: dict[str, Any] = {"call": call_label, "target": target,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": client.model,
                             "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_hash(target, n),
                             "reasoning_effort": client.reasoning_effort, "store": client.store,
                             "requested_proposals": n, "pricing_note": PRICING_NOTE}
    try:
        reply = client.generate(INSTRUCTIONS[target], prompt(target, n), f"ds_mvp_{target}",
                                strict_json_schema(schema_model), max_output_tokens=16000)
    except Exception as exc:
        entry.update(error=type(exc).__name__, detail=str(exc)[:300])
        _append(ledger, entry)
        raise
    entry.update(status=reply.status, latency_s=reply.latency_seconds, input_tokens=reply.input_tokens,
                 output_tokens=reply.output_tokens, reasoning_tokens=reply.reasoning_tokens,
                 estimated_usd=reply.approx_cost_usd, cumulative_estimated_usd=round(client.estimated_usd, 6))
    try:
        raw = json.loads(reply.text).get("proposals", [])
    except ValueError:
        raw, entry["parse_error"] = [], True
    entry["proposals_returned"] = len(raw)
    entry["raw_reply_sha256"] = hashlib.sha256(reply.text.encode()).hexdigest()
    (ledger.parent / f"{call_label}.reply.json").write_text(reply.text)  # kept at once: a later failure can't lose it
    _append(ledger, entry)
    return [(f"{call_label}#{i}", p) for i, p in enumerate(raw)]


def _append(ledger: Path, entry: dict[str, Any]) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
