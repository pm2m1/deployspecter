"""EXPERIMENTAL, NONBLIND: arms B1-B5 on the trusted reservations fixture (Gate 2A's synthetic application, run by
the MVP's stepping engine through ``planning_adapter``; nothing here is Gate 2A or A2 evidence).

Every workload is a WorkloadSpec v2 document judged by the unmodified chain (``planning_adapter.plan``): parse,
authorize, argument validation, plan identity, state planning, catalog verification, Phase 9B bridge. Only a
``bridged`` document runs; every other outcome is recorded with its stage and code.

* B1: Track A's document and the five release-path documents (``research_tools/mvp_release_evidence.py``).
* B2: a grid of two-actor documents (reserve/release by two holders) x held reservations x event capacity, in greedy
  pairwise-coverage order.
* B3: Muse's planner proposals (``muse_apps`` target ``planner``), mapped field-for-field.
* B4/B5: three fixed actor sets; the *state* differs. B4: a deterministic grid of states in greedy order. B5: Muse
  proposes the state only. Both become the same state goals and pass the same chain, so the constructor is constant.
"""

from __future__ import annotations

import copy
import itertools
import json
from typing import Any

from pydantic import BaseModel

from ...research.track_a import scenario as sc
from . import greedy


def I(v: int) -> dict[str, Any]:  # noqa: N802, E743
    return {"type": "int", "value": v}


def S(v: str) -> dict[str, Any]:  # noqa: N802
    return {"type": "str", "value": v}


def release(name: str, holder: str) -> dict[str, Any]:
    return {"name": name, "entrypoint": "release_seats", "application_version": "v1", "role": "request",
            "arguments": [{"name": "holder", "value": S(holder)}]}


def reserve(name: str, holder: str, qty: int) -> dict[str, Any]:
    return {"name": name, "entrypoint": "reserve_seats", "application_version": "v1", "role": "request",
            "arguments": [{"name": "event_id", "value": I(1)}, {"name": "holder", "value": S(holder)},
                          {"name": "qty", "value": I(qty)}]}


def document(name: str, actors: list[dict[str, Any]], held: dict[str, list[int]], *, capacity: int = 10,
             reserved: int | None = None, author: str = "mvp_release_evidence",
             origin: str = "hand_written") -> dict[str, Any]:
    """As ``mvp_release_evidence.document`` (identical for its defaults), with the capacity and the declared
    ``reserved`` as parameters. ``reserved`` defaults to the held seats; a different value is passed through as
    proposed, for the chain and the harness to judge."""
    d = copy.deepcopy(sc.DOCUMENT)
    d["name"], d["actors"] = name, actors
    d["provenance"] = {"origin": origin, "author": author}
    total = sum(q for qs in held.values() for q in qs) if reserved is None else reserved
    d["state_goals"][0]["constraints"][1:] = [{"field": "capacity", "op": "eq", "value": I(capacity)},
                                              {"field": "reserved", "op": "eq", "value": I(total)}]
    for holder, qtys in held.items():
        for i, q in enumerate(qtys):
            d["state_goals"].append({"entity": "reservation", "label": f"{holder}_{i}", "count": 1, "constraints": [
                {"field": "event_id", "op": "eq", "value": I(1)}, {"field": "holder", "op": "eq", "value": S(holder)},
                {"field": "qty", "op": "eq", "value": I(q)}]})
    return d


RELEASE_CASES = {
    "duplicate_release_one_held": document("dup_one", [release("a", "jack"), release("b", "jack")], {"jack": [4]}),
    "duplicate_release_two_held": document("dup_two", [release("a", "jack"), release("b", "jack")], {"jack": [4, 4]}),
    "independent_releases": document("indep", [release("a", "jack"), release("b", "jill")], {"jack": [3], "jill": [2]}),
    "reserve_vs_release": document("res_rel", [reserve("a", "jack", 2), release("b", "jack")], {}),
    "unknown_holder": document("unknown", [release("a", "nobody"), release("b", "jack")], {"jack": [1]}),
}


def curated() -> list[tuple[str, dict[str, Any]]]:
    """B1."""
    return [("curated#0", copy.deepcopy(sc.DOCUMENT))] + [(f"curated#{i + 1}", copy.deepcopy(d))
                                                          for i, d in enumerate(RELEASE_CASES.values())]


ACTOR_MENU = {"reserve_jack_2": ("reserve", "jack", 2), "reserve_jill_2": ("reserve", "jill", 2),
              "reserve_jack_3": ("reserve", "jack", 3), "release_jack": ("release", "jack", None),
              "release_jill": ("release", "jill", None)}
HELD_MENU = {"none": {}, "jack_2": {"jack": [2]}, "jack_2_2": {"jack": [2, 2]},
             "jack_2_jill_3": {"jack": [2], "jill": [3]}}


def _actor(name: str, item: tuple[str, str, int | None]) -> dict[str, Any]:
    kind, holder, qty = item
    return reserve(name, holder, qty) if kind == "reserve" else release(name, holder)


def _grid() -> list[dict[str, Any]]:
    out = []
    for x, y in itertools.combinations_with_replacement(sorted(ACTOR_MENU), 2):
        for held_name in HELD_MENU:
            for capacity in (4, 10):
                out.append({"a": x, "b": y, "held": held_name, "capacity": capacity})
    return out


def _features(g: dict[str, Any]) -> tuple[Any, ...]:
    a, b = ACTOR_MENU[g["a"]], ACTOR_MENU[g["b"]]
    holders = set(HELD_MENU[g["held"]])
    return (a[0], b[0], a[1] == b[1], g["held"], g["capacity"], a[1] in holders or b[1] in holders)


def systematic(limit: int) -> list[tuple[str, dict[str, Any]]]:
    """B2."""
    out = []
    for i, g in enumerate(greedy.order(_grid(), _features, limit)):
        d = document(f"grid_{i}", [_actor("a", ACTOR_MENU[g["a"]]), _actor("b", ACTOR_MENU[g["b"]])],
                     HELD_MENU[g["held"]], capacity=g["capacity"], author="ds_mc_grid", origin="deterministic_baseline")
        out.append((f"systematic#{i}", d))
    return out


# ------------------------------------------------------------------------------------------------ B4 / B5

ACTOR_SETS = {"two_reserves": [reserve("a", "jack", 2), reserve("b", "jill", 2)],
              "two_releases_same_holder": [release("a", "jack"), release("b", "jack")],
              "reserve_and_release": [reserve("a", "jack", 2), release("b", "jack")]}


class HeldP(BaseModel):
    holder: str
    qty: int


class StateProposal(BaseModel):
    rationale: str
    capacity: int
    reserved: int
    reservations: list[HeldP]


class StateProposalSet(BaseModel):
    proposals: list[StateProposal]


STATE_INSTRUCTIONS = ("You choose the initial database state for a concurrency test of a small Python reservations "
                      "service. The test's invocations are fixed and given; a deterministic scheduler will run them "
                      "concurrently and try different interleavings of their database operations, and an "
                      "independent checker then inspects the final state. Propose initial states likely to expose "
                      "data-integrity problems under those invocations. You cannot write SQL or code, or change the "
                      "invocations. The state is checked by a deterministic planner and refused if it can't be "
                      "built exactly. Return JSON only.")


def state_context(actor_set: str) -> dict[str, Any]:
    entities = sc.context_config(registry_digest="", schema_digest="")["entities"]
    return {"invocations": [{"actor": a["name"], "handler": a["entrypoint"],
                             "args": {g["name"]: g["value"]["value"] for g in a["arguments"]}}
                            for a in ACTOR_SETS[actor_set]],
            "entities": entities,
            "state_shape": "one event with id 1 (its capacity and its reserved counter) and its reservations, each a "
                           "holder and a qty; reservation ids are assigned by the planner",
            "constraints": {"capacity": "1..20", "reserved": "0..40", "reservations": "0..4 items",
                            "holder": "a lowercase identifier", "qty": "1..10"}}


def state_prompt(actor_set: str, n: int) -> str:
    return (f"Propose exactly {n} different initial states.\n\nContext (JSON):\n"
            + json.dumps(state_context(actor_set), indent=1, sort_keys=True, default=str))


def state_to_document(actor_set: str, label: str, p: Any, *, origin: str, author: str) -> dict[str, Any]:
    """A state proposal (B4 or B5) → the same WorkloadSpec v2 document builder: renames only, repairs nothing."""
    prop = StateProposal.model_validate(p)
    held: dict[str, list[int]] = {}
    for r in prop.reservations:
        held.setdefault(r.holder, []).append(r.qty)
    return document(label, copy.deepcopy(ACTOR_SETS[actor_set]), held, capacity=prop.capacity,
                    reserved=prop.reserved, author=author, origin=origin)


STATE_GRID_HELD = {"none": [], "jack_2": [("jack", 2)], "jack_2_2": [("jack", 2), ("jack", 2)],
                   "jack_1_jill_3": [("jack", 1), ("jill", 3)]}


def deterministic_states(actor_set: str, limit: int) -> list[dict[str, Any]]:
    """B4: capacity x held reservations (reserved = their seats), greedy pairwise order over (held shape, capacity,
    whether an invocation's holder holds something, whether the held seats fill the capacity)."""
    holders = {g["value"]["value"] for a in ACTOR_SETS[actor_set] for g in a["arguments"] if g["name"] == "holder"}
    grid = []
    for held_name, held in STATE_GRID_HELD.items():
        for capacity in (2, 3, 4, 10):
            seats = sum(q for _, q in held)
            if seats > capacity:
                continue
            grid.append({"rationale": f"grid {held_name} capacity {capacity}", "capacity": capacity,
                         "reserved": seats, "reservations": [{"holder": h, "qty": q} for h, q in held],
                         "_f": (held_name, capacity, bool(holders & {h for h, _ in held}), seats == capacity)})
    picked = greedy.order(grid, lambda g: g["_f"], limit)
    return [{k: v for k, v in g.items() if k != "_f"} for g in picked]
