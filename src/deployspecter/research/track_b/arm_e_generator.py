"""Arm E's deterministic generator: a pure, unfrozen prototype of ``ARM_E_BASELINE_SPEC_PROPOSAL.md``.

Not frozen, not adopted, and never used in a protocol evaluation. It reads only a trusted v2 Registry and Contracts,
a pinned goal policy and hand-written A translations. It can't see requirements, oracles, labels, schedules or fixes,
and imports nothing that carries them. No database, model or network.

Units come from three sources, all v2 documents passed through the frozen stages 1 to 3:

* ``A``: application tests that can be written as one v2 document (translations supplied, frozen with E).
* ``B``: per entrypoint, two actors with identical arguments: a canonical tuple, then one argument at a time over its
  contract's boundary values.
* ``pair``: unordered entrypoint pairs, self-pairs only when ``max_instances >= 2``, with three argument assignments.

The arm a unit belongs to is recorded here, by the generator. The document's ``provenance`` is never read for it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from deployspecter.live import argument_validation as av
from deployspecter.live import registry as rg
from deployspecter.live import state_planning as sp
from deployspecter.live import workload_identity as wi
from deployspecter.live import workload_spec as ws

GENERATOR_VERSION = "arm-e-generator/0-prototype"
ORDER_DOMAIN = "deployspecter-live-protocol-v1:E-order"
MAX_UNITS = 10_000  # a safety ceiling only: reaching it blocks E and R for the application; nothing is truncated
FILL = {"digits": "0", "alnum": "a", "alnum_underscore_dash": "a", "lower_identifier": "a", "printable_ascii": "a",
        None: "a"}
Source = Literal["A", "B", "pair"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GoalReference(_Strict):
    """An argument whose value names a stored key: the generator writes one ``eq`` goal for it."""

    entrypoint: str
    argument: str
    entity: str
    field: str


class GoalPolicy(_Strict):
    """Harness-written from the schema and registration only, pinned, and frozen before any held-out Muse output."""

    policy_version: Literal[1] = 1
    application: str
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    references: tuple[GoalReference, ...] = ()

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


class ATranslation(_Strict):
    """One adapted application test. ``document`` is None when it can't be written as one v2 document."""

    test_name: str
    document: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class GeneratorOptions:
    """Pure, default-off policy variants, for evaluation only. The defaults reproduce the 0-prototype exactly.

    * ``repair_requires``: when a B variation sets an argument that a contract ``requires`` predicate ties to another,
      add the other at its canonical value, instead of generating a unit stage 3 will reject. [comparator]
    * ``single_instance_b``: ``pair_with_partner`` gives an entrypoint with ``max_instances = 1`` B units whose second
      actor is the first other entrypoint's canonical call, instead of two identical actors it can't have. [comparator]
    """

    repair_requires: bool = False
    single_instance_b: Literal["reject", "pair_with_partner"] = "reject"

    def label(self) -> str | None:
        return None if self == GeneratorOptions() else json.dumps(asdict(self), sort_keys=True)


@dataclass(frozen=True)
class Unit:
    unit_id: str
    source: Source
    label: str
    document: dict[str, Any]
    status: Literal["valid", "generation_rejected"]
    stage: str | None = None
    codes: tuple[str, ...] = ()
    validated: av.ValidatedWorkload | None = None
    plan_identity: str | None = None


@dataclass
class GenerationResult:
    units: list[Unit] = field(default_factory=list)
    blocked_a_tests: list[tuple[str, str]] = field(default_factory=list)  # (test name, reason): not expressible
    ceiling_reached: bool = False
    candidates_counted: int = 0

    @property
    def valid(self) -> list[Unit]:
        return [u for u in self.units if u.status == "valid"]

    @property
    def rejected(self) -> list[Unit]:
        return [u for u in self.units if u.status == "generation_rejected"]

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for unit in self.units:
            row = out.setdefault(unit.source, {"generated": 0, "valid": 0, "generation_rejected": 0})
            row["generated"] += 1
            row[unit.status] += 1
        out["A_not_expressible"] = {"blocked": len(self.blocked_a_tests)}
        return out


# ------------------------------------------------------------------ boundary values


def _dedupe(values: Iterable[tuple[str, Any]]) -> list[tuple[str, Any]]:
    seen, out = set(), []
    for kind, value in values:
        key = (kind, json.dumps(value))
        if key not in seen:
            seen.add(key)
            out.append((kind, value))
    return out


def boundary_values(rule: av.ArgumentRule | None, types: tuple[str, ...]) -> list[tuple[str, Any]]:
    """Typed boundary values in registered-type order. The first is the argument's canonical value."""
    values: list[tuple[str, Any]] = []
    for kind in types:
        constraint = rule.for_type(kind) if rule is not None else None
        if kind == "int":
            if constraint is not None and constraint.allowed:
                values += [("int", v) for v in constraint.allowed]
                continue
            lo = constraint.minimum if constraint is not None and constraint.minimum is not None else -1
            hi = constraint.maximum if constraint is not None and constraint.maximum is not None else 1
            values += [("int", v) for v in (lo, lo + 1, hi - 1, hi, 0) if lo <= v <= hi]
        elif kind == "float":
            lo = constraint.minimum if constraint is not None and constraint.minimum is not None else -1.0
            hi = constraint.maximum if constraint is not None and constraint.maximum is not None else 1.0
            values += [("float", float(v)) for v in (lo, hi, 0.0) if lo <= v <= hi]
        elif kind == "str":
            if constraint is not None and constraint.allowed:
                values += [("str", v) for v in constraint.allowed]
                continue
            short = constraint.min_length if constraint is not None else 0
            long = constraint.max_length if constraint is not None else ws.MAX_STRING_VALUE
            if constraint is not None and constraint.charset == "lower_identifier":
                short = max(short, 1)
            fill = FILL[constraint.charset if constraint is not None else None]
            values += [("str", fill * short), ("str", fill * long)]
        elif kind == "bool":
            allowed = constraint.allowed if constraint is not None and constraint.allowed else [False, True]
            values += [("bool", v) for v in allowed]
        elif kind == "null":
            values.append(("null", None))
    return _dedupe(values)


def _signature(entry: rg.EntrypointRegistration, contract: av.OperationContract | None):
    rules = {r.name: r for r in contract.arguments} if contract is not None else {}
    return [(a.name, a.required, boundary_values(rules.get(a.name), tuple(a.types))) for a in entry.arguments]


def _canonical(signature) -> dict[str, tuple[str, Any]]:
    return {name: values[0] for name, required, values in signature if required and values}


def _first_other(signature) -> dict[str, tuple[str, Any]]:
    out = {}
    for name, required, values in signature:
        if required and values:
            out[name] = values[1] if len(values) > 1 else values[0]
    return out


# ------------------------------------------------------------------ documents


def _actor(name: str, entry: rg.EntrypointRegistration, version: str, args: dict[str, tuple[str, Any]]):
    arguments = [{"name": k, "value": {"type": "null"} if t == "null" else {"type": t, "value": v}}
                 for k, (t, v) in args.items()]
    return {"name": name, "entrypoint": entry.id, "application_version": version, "role": entry.roles[0],
            "arguments": arguments}


def _goals(actors: list[dict[str, Any]], policy: GoalPolicy) -> list[dict[str, Any]]:
    refs = {(r.entrypoint, r.argument): (r.entity, r.field) for r in policy.references}
    keys: list[tuple[str, str, str]] = []
    for actor in actors:
        for argument in actor["arguments"]:
            target = refs.get((actor["entrypoint"], argument["name"]))
            if target is None or argument["value"]["type"] == "null":
                continue
            key = (*target, json.dumps(argument["value"], sort_keys=True))
            if key not in keys:
                keys.append(key)
    return [{"entity": entity, "label": f"ref_{n}", "count": 1,
             "constraints": [{"field": field_, "op": "eq", "value": json.loads(value)}]}
            for n, (entity, field_, value) in enumerate(keys)]


def _document(application: str, name: str, actors: list[dict[str, Any]], goals: list[dict[str, Any]]):
    return {"schema_version": 2, "name": name, "application": application,
            "provenance": {"origin": "deterministic_baseline", "author": "arm_e_generator"},
            "actors": actors, "state_goals": goals}


def _unit_id(source: str, label: str, document: dict[str, Any], options: str | None = None) -> str:
    inputs = {"generator": GENERATOR_VERSION, "source": source, "label": label,
              "actors": document["actors"], "state_goals": document["state_goals"]}
    if options is not None:
        inputs["options"] = options
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def _validate(source: Source, label: str, document: dict[str, Any], registry, contracts,
              options: str | None = None) -> Unit:
    unit_id = _unit_id(source, label, document, options)
    try:
        spec = ws.parse_workload(document)
    except ValueError as exc:
        return Unit(unit_id, source, label, document, "generation_rejected", "parse", (str(exc)[:120],))
    authorized = rg.authorize(spec, registry)
    if not authorized.accepted:
        return Unit(unit_id, source, label, document, "generation_rejected", "authorize",
                    tuple(sorted({r.code for r in authorized.rejections})))
    validated = av.validate_arguments(authorized.workload, registry=registry, contracts=contracts)
    if not validated.accepted:
        return Unit(unit_id, source, label, document, "generation_rejected", "validate_arguments",
                    tuple(sorted({r.code for r in validated.rejections})))
    identity = wi.validated_plan_identity(validated.workload, registry=registry, contracts=contracts)
    return Unit(unit_id, source, label, document, "valid", validated=validated.workload,
                plan_identity=identity.identity.value if identity.supported else None)


def _repair(args: dict[str, tuple[str, Any]], contract: av.OperationContract | None, sig) -> dict:
    if contract is None:
        return args
    canonical = {name: values[0] for name, _required, values in sig if values}
    out = dict(args)
    for predicate in contract.predicates:
        if predicate.op == "requires" and out.get(predicate.if_present, ("null", None))[0] != "null" \
                and predicate.then_present not in out and predicate.then_present in canonical:
            out[predicate.then_present] = canonical[predicate.then_present]
    return out


def candidates(registry: rg.Registry, contracts: av.Contracts, version: str, policy: GoalPolicy,
               a_translations: Iterable[ATranslation] = (), options: GeneratorOptions | None = None,
               ) -> tuple[list[tuple[Source, str, dict]], list]:
    """Every candidate document, in generation order, before validation. Pure and exhaustive."""
    if policy.registry_digest != registry.digest or policy.application != registry.application:
        raise ValueError("the goal policy is pinned to a different registry")
    out: list[tuple[Source, str, dict]] = []
    blocked = []
    for test in a_translations:
        if test.document is None:
            blocked.append((test.test_name, test.reason or "not expressible as one v2 document"))
        else:
            out.append(("A", f"a:{test.test_name}", json.loads(json.dumps(test.document))))
    entries = [registry.lookup(i) for i in registry.ids() if version in registry.lookup(i).versions]
    sigs = {e.id: _signature(e, contracts.lookup(e.id, version)) for e in entries}
    options = options or GeneratorOptions()
    for entry in entries:
        sig = sigs[entry.id]
        partner = next((e for e in entries if e.id != entry.id), None)
        base = _canonical(sig)
        tuples = [("canonical", base)]
        for name, required, values in sig:
            for value in values:
                if required and value == base.get(name):
                    continue
                tuples.append((f"{name}={value[0]}:{json.dumps(value[1])}", {**base, name: value}))
        for label, args in tuples:
            if options.repair_requires:
                args = _repair(args, contracts.lookup(entry.id, version), sig)
            second = _actor("b", entry, version, args)
            if entry.max_instances < 2 and options.single_instance_b == "pair_with_partner" and partner is not None:
                second = _actor("b", partner, version, _canonical(sigs[partner.id]))
            actors = [_actor("a", entry, version, args), second]
            out.append(("B", f"b:{entry.id}:{label}", _document(registry.application, "arm_e_b", actors,
                                                                _goals(actors, policy))))
    for i, first in enumerate(entries):
        for second in entries[i:]:
            if first.id == second.id and first.max_instances < 2:
                continue
            a_args = _canonical(sigs[first.id])
            b_canon = _canonical(sigs[second.id])
            types = {a.name: set(a.types) for a in second.arguments}
            collide = {k: (a_args[k] if k in a_args and a_args[k][0] in types.get(k, set()) else v)
                       for k, v in b_canon.items()}
            for label, b_args in (("canonical", b_canon), ("collide", collide),
                                  ("differ", _first_other(sigs[second.id]))):
                actors = [_actor("a", first, version, a_args), _actor("b", second, version, b_args)]
                out.append(("pair", f"pair:{first.id}+{second.id}:{label}",
                            _document(registry.application, "arm_e_pair", actors, _goals(actors, policy))))
    return out, blocked


def generate(registry: rg.Registry, contracts: av.Contracts, version: str, policy: GoalPolicy,
             a_translations: Iterable[ATranslation] = (), max_units: int = MAX_UNITS,
             options: GeneratorOptions | None = None) -> GenerationResult:
    options = options or GeneratorOptions()
    found, blocked = candidates(registry, contracts, version, policy, a_translations, options)
    result = GenerationResult(blocked_a_tests=blocked, candidates_counted=len(found))
    if len(found) > max_units:
        result.ceiling_reached = True  # E and R blocked for this application; no unit is kept, none truncated
        return result
    result.units = [_validate(source, label, doc, registry, contracts, options.label())
                    for source, label, doc in found]
    return result


# ------------------------------------------------------------------ order, allocation and R


def seeded_order(units: Iterable[Unit], seed: int, owner_repo: str) -> list[Unit]:
    if seed not in (1, 2, 3):
        raise ValueError("deterministic replicates use seeds 1, 2 and 3")
    key = lambda u: hashlib.sha256(f"{ORDER_DOMAIN}:{owner_repo}:{seed}:{u.unit_id}".encode()).hexdigest()  # noqa: E731
    return sorted(units, key=key)


def round_robin(order: list[str], tree_runs: dict[str, int], max_runs: int) -> tuple[list[str], int]:
    """Run order for one replicate. ``tree_runs`` is each unit's runs until its decision tree is exhausted.

    Units missing from ``tree_runs``, or with 0, couldn't be constructed or verified: they get no run and aren't
    replaced. Returns the run order and the number of runs left unused, which is reported and never moved.
    """
    left = {u: tree_runs.get(u, 0) for u in order}
    runs: list[str] = []
    while len(runs) < max_runs and any(left[u] > 0 for u in order):
        for unit in order:
            if len(runs) >= max_runs:
                break
            if left[unit] > 0:
                runs.append(unit)
                left[unit] -= 1
    return runs, max_runs - len(runs)


def r_state_requirements(verified: dict[str, str | None]) -> dict[str, tuple[str, str]]:
    """For each E unit, what R must see before running: E's verified state digest, or a block."""
    return {unit: ("required_digest", digest) if digest else ("blocked", "E's state wasn't verified")
            for unit, digest in verified.items()}


def planning_use(units: Iterable[Unit], *, registry, contracts, context, trusted,
                 budget: sp.PlanningBudget | None = None) -> list[dict[str, Any]]:
    """The shared planner's outcome and counters per valid unit, as D would get. Descriptive only."""
    rows = []
    for unit in units:
        if unit.validated is None:
            continue
        result = sp.plan_state(unit.validated, registry=registry, contracts=contracts, context=context,
                               trusted=trusted, budget=budget)
        rows.append({"unit_id": unit.unit_id, "label": unit.label, "outcome": result.outcome,
                     "code": getattr(result, "code", None), **result.counters.model_dump()})
    return rows
