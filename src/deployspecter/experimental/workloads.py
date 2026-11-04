"""EXPERIMENTAL workload proposals: one versioned schema, one deterministic validator, three sources.

A proposal names allowlisted handlers, the app version that invokes each, their arguments, the initial state and the
migration phase. It carries no SQL and no code, and nothing in it is executed until ``validate`` accepts it; accepted
proposals become invocations of the trusted registry only.

Sources (not the frozen protocol's A to G/R arms; named here only for this prototype):

    curated     hand-written application-style tests
    systematic  deterministic, derived from the registry's handlers and the schema's boundary values
    muse        Meta Muse proposals, through the same schema and validator
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .apps import reservations_mixed as app

SCHEMA_VERSION = "ds-mvp-workload/1"
ACTOR = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
HOLDER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RANGES = {"event_id": (1, 1000), "qty": (1, 100), "batch": (1, 100), "reservation_id": (1, 10000)}
MAX_INVOCATIONS, MAX_EVENTS, MAX_RESERVATIONS = 4, 10, 20


class Arg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["int", "str"]
    int_value: int | None
    str_value: str | None


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str
    app_version: str
    handler: str
    args: list[Arg]


class SeedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    capacity: int
    reserved: int
    reserved_v2: int | None


class SeedReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    event_id: int
    holder: str
    qty: int


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    migration_phase: str
    rationale: str
    invocations: list[Invocation]
    seed_events: list[SeedEvent]
    seed_reservations: list[SeedReservation]
    invariant_hypotheses: list[str]


@dataclass
class Workload:
    source: str
    label: str
    phase: str
    invocations: list[dict[str, Any]]  # {actor, app_version, handler, args}
    seed: dict[str, list[dict[str, Any]]]
    hypotheses: list[str] = field(default_factory=list)


@dataclass
class Rejection:
    source: str
    label: str
    reasons: list[str]


def validate(p: Proposal | dict[str, Any], *, source: str, label: str) -> Workload | Rejection:
    """Deterministic: the same proposal always gets the same verdict, with every reason."""
    if isinstance(p, dict):
        try:
            p = Proposal.model_validate(p)
        except Exception as exc:  # pydantic's message names fields, never executes anything
            return Rejection(source, label, [f"schema: {str(exc)[:300]}"])
    reasons: list[str] = []
    if len(p.rationale) > 2000 or any(len(h) > 500 for h in p.invariant_hypotheses):
        reasons.append("rationale or hypotheses too long")
    if p.schema_version != SCHEMA_VERSION:
        reasons.append(f"schema_version must be {SCHEMA_VERSION}")
    if p.migration_phase not in app.PHASES:
        reasons.append(f"unsupported migration phase {p.migration_phase!r}")
    handlers = app.registry("buggy")  # the builds differ only in the backfill's body, not its signature
    if not 1 <= len(p.invocations) <= MAX_INVOCATIONS:
        reasons.append(f"1 to {MAX_INVOCATIONS} invocations")
    actors = [i.actor for i in p.invocations]
    if len(set(actors)) != len(actors) or not all(ACTOR.match(a) for a in actors):
        reasons.append("actor names must be unique lowercase identifiers")
    if not 1 <= len(p.seed_events) <= MAX_EVENTS or len(p.seed_reservations) > MAX_RESERVATIONS:
        reasons.append(f"1 to {MAX_EVENTS} events and at most {MAX_RESERVATIONS} reservations")
    events = {e.id: e for e in p.seed_events}
    if len(events) != len(p.seed_events):
        reasons.append("duplicate event ids")
    sums: dict[int, int] = {}
    for e in p.seed_events:
        if not (1 <= e.id <= 1000 and 0 <= e.capacity <= 1000 and 0 <= e.reserved <= e.capacity):
            reasons.append(f"event {e.id}: needs 1 <= id <= 1000 and 0 <= reserved <= capacity <= 1000")
        if e.reserved_v2 is not None and e.reserved_v2 != e.reserved:
            reasons.append(f"event {e.id}: an initial reserved_v2 must equal reserved (the state must be consistent)")
    rids = set()
    for r in p.seed_reservations:
        if r.id in rids or not 1 <= r.id <= 10000:
            reasons.append(f"reservation {r.id}: duplicate or out of range")
        rids.add(r.id)
        if r.event_id not in events:
            reasons.append(f"reservation {r.id}: references event {r.event_id}, which isn't seeded")
        if not HOLDER.match(r.holder) or not 1 <= r.qty <= 100:
            reasons.append(f"reservation {r.id}: holder must be an identifier and 1 <= qty <= 100")
        sums[r.event_id] = sums.get(r.event_id, 0) + r.qty
    for e in p.seed_events:
        if sums.get(e.id, 0) != e.reserved:
            reasons.append(f"event {e.id}: reserved {e.reserved} must equal its seeded reservations "
                           f"({sums.get(e.id, 0)})")
    invocations = []
    for inv in p.invocations:
        h = handlers.get((inv.app_version, inv.handler))
        if h is None:
            reasons.append(f"{inv.actor}: unknown handler {inv.app_version}.{inv.handler}")
            continue
        given = {a.name: a for a in inv.args}
        if len(given) != len(inv.args) or set(given) != set(h.args):
            reasons.append(f"{inv.actor}: {inv.app_version}.{inv.handler} takes exactly {sorted(h.args)}")
            continue
        args: dict[str, Any] = {}
        for name, typ in h.args.items():
            a = given[name]
            if typ is int:
                if a.kind != "int" or a.int_value is None or a.str_value is not None or isinstance(a.int_value,
                                                                                                    bool):
                    reasons.append(f"{inv.actor}: {name} must be an integer")
                    continue
                lo, hi = RANGES[name]
                if not lo <= a.int_value <= hi:
                    reasons.append(f"{inv.actor}: {name} must be in [{lo}, {hi}]")
                args[name] = a.int_value
            else:
                if a.kind != "str" or a.str_value is None or a.int_value is not None or not HOLDER.match(
                        a.str_value):
                    reasons.append(f"{inv.actor}: {name} must be an identifier string")
                    continue
                args[name] = a.str_value
        if "event_id" in args and args["event_id"] not in events:
            reasons.append(f"{inv.actor}: event {args['event_id']} isn't seeded")
        if "reservation_id" in args and args["reservation_id"] not in rids:
            reasons.append(f"{inv.actor}: reservation {args['reservation_id']} isn't seeded")
        invocations.append({"actor": inv.actor, "app_version": inv.app_version, "handler": inv.handler,
                            "args": args})
    if reasons:
        return Rejection(source, label, reasons)
    seed = {"events": [e.model_dump() for e in p.seed_events],
            "reservations": [r.model_dump() for r in p.seed_reservations]}
    return Workload(source, label, p.migration_phase, invocations, seed, list(p.invariant_hypotheses))


# ------------------------------------------------------------------ deterministic sources


def _prop(invocations: list[tuple[str, str, str, dict[str, Any]]], events: list[dict[str, Any]],
          reservations: list[dict[str, Any]] = ()) -> dict[str, Any]:
    def arg(k: str, v: Any) -> dict[str, Any]:
        return {"name": k, "kind": "int" if isinstance(v, int) else "str",
                "int_value": v if isinstance(v, int) else None, "str_value": v if isinstance(v, str) else None}
    return {"schema_version": SCHEMA_VERSION, "migration_phase": "expand", "rationale": "deterministic",
            "invocations": [{"actor": a, "app_version": v, "handler": h, "args": [arg(k, x) for k, x in args.items()]}
                            for a, v, h, args in invocations],
            "seed_events": [{"reserved_v2": None, **e} for e in events],
            "seed_reservations": list(reservations), "invariant_hypotheses": []}


def curated() -> list[tuple[str, dict[str, Any]]]:
    """Application-style tests, the kind a team would write: each handler alone, and the obvious pairs."""
    ev = [{"id": 1, "capacity": 5, "reserved": 0}]
    ev_held = [{"id": 1, "capacity": 5, "reserved": 2}]
    held = [{"id": 1, "event_id": 1, "holder": "carol", "qty": 2}]
    return [
        ("v1 reserve alone", _prop([("a", "v1", "reserve", {"event_id": 1, "holder": "alice", "qty": 2})], ev)),
        ("v2 reserve alone", _prop([("a", "v2", "reserve", {"event_id": 1, "holder": "alice", "qty": 2})], ev)),
        ("backfill alone", _prop([("j", "job", "backfill_v2", {"batch": 10})], ev_held, held)),
        ("v1 and v2 reserve", _prop([("a", "v1", "reserve", {"event_id": 1, "holder": "alice", "qty": 2}),
                                     ("b", "v2", "reserve", {"event_id": 1, "holder": "bob", "qty": 2})], ev)),
        ("v1 release alone", _prop([("a", "v1", "release", {"reservation_id": 1})], ev_held, held)),
    ]


def systematic(limit: int) -> list[tuple[str, dict[str, Any]]]:
    """Every pair of distinct registry handlers, in a fixed order, with boundary values from the schema: one event at
    capacity 3, one seeded reservation of 1 (so releases have something to release), qty 1 then 2."""
    handlers = sorted(app.registry("buggy"))
    events = [{"id": 1, "capacity": 3, "reserved": 1}]
    held = [{"id": 1, "event_id": 1, "holder": "seed", "qty": 1}]

    def args(name: str, qty: int) -> dict[str, Any]:
        return {"reserve": {"event_id": 1, "holder": f"h{qty}", "qty": qty}, "release": {"reservation_id": 1},
                "backfill_v2": {"batch": 10}}[name]

    out = []
    for (v1, h1), (v2, h2) in itertools.combinations(handlers, 2):
        out.append((f"{v1}.{h1} + {v2}.{h2}", _prop([("a", v1, h1, args(h1, 1)), ("b", v2, h2, args(h2, 2))],
                                                    events, held)))
        if len(out) >= limit:
            break
    return out
