"""Validated-plan identity and deduplication. Research only, offline and pure.

Three identities, kept apart:

A. **Full-record identity** (``workload_spec.digest``): every field of the specification as written, including what
   it says about itself. It identifies a document.
B. **Validated-plan identity** (this module): what a successfully validated workload asks the harness to do, bound to
   the trusted registry and contract digests. Explanatory and self-reported fields are left out.
C. **Executed-scenario identity**: specified in ``WORKLOAD_IDENTITY.md``, not implemented. Equal plan identities do
   **not** imply equal executed scenarios: the code behind an operation reference, the constructed seed state, the
   database configuration and the schedule can all differ.

The plan identity is conservative. Where removing a field hasn't been shown to preserve meaning, the field stays, so
two plans that might behave differently never share an identity. Some plans that would behave the same will get
different identities; that's the accepted cost.

Nothing here reads a database, network, oracle or model-provided digest, imports code, or changes its inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import registry as rg
from . import workload_spec as ws
from .argument_validation import Contracts, ValidatedWorkload, check_validated

PLAN_IDENTITY_VERSION = 1
DOMAIN = b"deployspecter:validated-plan-identity:v1\x00"
PREFIX = "vplan1:"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PlanIdentity(_Strict):
    value: str = Field(pattern=r"^vplan1:[0-9a-f]{64}$")
    identity_version: Literal[1] = 1
    registry_digest: str
    contracts_digest: str


class IdentityResult(_Strict):
    supported: bool
    identity: PlanIdentity | None = None
    reason: str | None = None


class _Unsupported(Exception):
    pass


def _encode(kind: str, value: Any) -> list[str]:
    """A type tag and an exact text form. No two different typed values share an encoding."""
    if kind == "int" and type(value) is int:
        return ["int", str(value)]
    if kind == "float" and type(value) is float:
        if not math.isfinite(value) or (value == 0.0 and math.copysign(1.0, value) < 0):
            raise _Unsupported("a non-finite float or negative zero has no plan identity")
        return ["float", value.hex()]  # exact, and the same in every language with C99 hex floats
    if kind == "bool" and type(value) is bool:
        return ["bool", "true" if value else "false"]
    if kind == "str" and type(value) is str:
        return ["str", value]  # compared as code points; no Unicode normalization, so é and e+◌́ stay distinct
    if kind == "null" and value is None:
        return ["null"]
    raise _Unsupported(f"value tagged {kind!r} holds {type(value).__name__}")


def _goal(goal: ws.StateGoal) -> dict[str, Any]:
    constraints = []
    for constraint in goal.constraints:
        value = [] if constraint.value is None else _encode(constraint.value.type, ws.plain_value(constraint.value))
        constraints.append([constraint.field, constraint.op, *value])
    return {
        "entity": goal.entity,
        "label": goal.label,  # kept: dropping it hasn't been shown to preserve meaning
        "count": str(goal.count),
        "constraints": sorted(constraints),  # a conjunction
    }


def plan_fields(validated: ValidatedWorkload) -> dict[str, Any]:
    """The exact content the plan identity hashes. Every leaf is a string, so canonical JSON is unambiguous."""
    workload = validated.authorized
    actors = []
    for actor in workload.actors:
        arguments = sorted([argument.name, *_encode(argument.type, argument.value)] for argument in actor.arguments)
        actors.append({
            "name": actor.actor,  # kept: actors are referred to and scheduled by name
            "entrypoint": actor.entrypoint,
            "operation": actor.operation,
            "application_version": actor.application_version,
            "role": actor.role,
            "arguments": arguments,  # keyword arguments: order means nothing; missing and null stay distinct
        })
    return {
        "identity_version": str(PLAN_IDENTITY_VERSION),
        "registry_digest": workload.registry_digest,
        "contracts_digest": validated.contracts_digest,
        "application": workload.application,
        "actors": sorted(actors, key=lambda a: a["name"]),
        "state_goals": sorted((_goal(g) for g in workload.state_goals), key=lambda g: g["label"]),
    }


def canonical_plan_json(validated: ValidatedWorkload) -> str:
    return json.dumps(plan_fields(validated), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validated_plan_identity(validated: ValidatedWorkload, *, registry: rg.Registry,
                            contracts: Contracts) -> IdentityResult:
    """The plan identity, or an explicit reason none can be given.

    Nothing about the input is trusted as given: stages 1, 2 and 3 are run again on its content, against the trusted
    registry and contract set passed here, and the result must reproduce the input exactly.
    """
    if not isinstance(validated, ValidatedWorkload):
        raise TypeError("plan identity takes a ValidatedWorkload")
    if not isinstance(registry, rg.Registry):
        raise TypeError("plan identity takes the trusted Registry it was authorized against")
    if not isinstance(contracts, Contracts):
        raise TypeError("plan identity takes the trusted Contracts it was validated against")
    problem = check_validated(validated, registry=registry, contracts=contracts)
    if problem:
        return IdentityResult(supported=False, reason=problem)
    try:
        text = canonical_plan_json(validated)
    except _Unsupported as exc:
        return IdentityResult(supported=False, reason=str(exc))
    digest = hashlib.sha256(DOMAIN + text.encode("utf-8")).hexdigest()
    return IdentityResult(supported=True, identity=PlanIdentity(
        value=PREFIX + digest, registry_digest=validated.authorized.registry_digest,
        contracts_digest=validated.contracts_digest,
    ))


class PlanGroup(_Strict):
    identity: str
    members: tuple[int, ...]  # input positions, ascending; the first is the representative


class UnsupportedMember(_Strict):
    index: int
    reason: str


class DedupResult(_Strict):
    groups: tuple[PlanGroup, ...]
    unsupported: tuple[UnsupportedMember, ...]


def deduplicate(workloads: Sequence[ValidatedWorkload], *, registry: rg.Registry,
                contracts: Contracts) -> DedupResult:
    """Groups workloads by plan identity, in order of first appearance. Reads its inputs; never changes them.

    A workload without a plan identity is never merged with anything: it's listed as unsupported instead.

    This is a reporting view only. It knows nothing of evaluation arms, attempts, budgets or replicates, and it never
    removes, replaces or reassigns a proposal: every input position appears exactly once in the result. Arm-level
    accounting belongs to the evaluation harness, under protocol v1.2, and must count every proposal as generated.
    """
    groups: dict[str, list[int]] = {}
    unsupported: list[UnsupportedMember] = []
    for index, workload in enumerate(workloads):
        result = validated_plan_identity(workload, registry=registry, contracts=contracts)
        if not result.supported:
            unsupported.append(UnsupportedMember(index=index, reason=result.reason or "unsupported"))
            continue
        groups.setdefault(result.identity.value, []).append(index)
    ordered = sorted(groups.items(), key=lambda item: item[1][0])
    return DedupResult(
        groups=tuple(PlanGroup(identity=identity, members=tuple(members)) for identity, members in ordered),
        unsupported=tuple(unsupported),
    )
