"""The typed format a future Muse Spark workload driver would produce. No model is called here.

A proposal can only name things a person registered: entrypoints, argument shapes, version labels, logical entities
and source files. It has no field that can hold SQL or Python, and each string argument must match a pattern or list
the registration declares, so model text cannot reach ``execute`` or ``eval``. Seed goals describe a state, not how to
create it; a deterministic constructor (Phase 9B's, or another) must turn them into rows, and may report that it cannot.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .runner import LiveActor

MAX_ACTORS = 4
MAX_SEED_GOALS = 12
MAX_CONSTRAINTS = 24
MAX_STRING_ARGUMENT = 128
_IDENTIFIER = r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$"

Scalar = str | int | float | bool | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Argument(_Strict):
    name: str = Field(pattern=_IDENTIFIER)
    value: Scalar


class WorkloadActor(_Strict):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,30}$")
    entrypoint: str = Field(pattern=_IDENTIFIER)
    arguments: list[Argument] = Field(default_factory=list, max_length=16)
    application_version: str = Field(pattern=_IDENTIFIER)
    role: Literal["request", "background_job"] = "request"


class FieldGoal(_Strict):
    field: str = Field(pattern=_IDENTIFIER)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null"]
    value: Scalar = None


class SeedGoal(_Strict):
    """A logical statement about the starting state, for example "one event with no free seats"."""

    entity: str = Field(pattern=_IDENTIFIER)
    label: str = Field(pattern=_IDENTIFIER)
    count: int = Field(default=1, ge=0, le=20)
    fields: list[FieldGoal] = Field(default_factory=list, max_length=16)


class SourceConstraint(_Strict):
    """Why the proposal thinks something matters, tied to lines the validator can check exist."""

    path: str = Field(pattern=r"^[A-Za-z0-9_./-]{1,200}$")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    claim: str = Field(max_length=400)


class WorkloadProposal(_Strict):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=_IDENTIFIER)
    application: str = Field(pattern=_IDENTIFIER)
    rationale: str = Field(default="", max_length=800)
    actors: list[WorkloadActor] = Field(min_length=2, max_length=MAX_ACTORS)
    seed_goals: list[SeedGoal] = Field(default_factory=list, max_length=MAX_SEED_GOALS)
    constraints: list[SourceConstraint] = Field(default_factory=list, max_length=MAX_CONSTRAINTS)


# -------------------------------------------------------------------- the registration a person writes


@dataclass(frozen=True)
class ArgumentSpec:
    kind: Literal["int", "float", "bool", "str"]
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None
    choices: tuple[Scalar, ...] = ()


@dataclass(frozen=True)
class EntrypointSpec:
    function: Callable[..., Any]
    arguments: dict[str, ArgumentSpec]
    versions: frozenset[str]
    role: Literal["request", "background_job"] = "request"
    autocommit: bool = False


@dataclass(frozen=True)
class AppRegistry:
    application: str
    entrypoints: dict[str, EntrypointSpec]
    entities: dict[str, frozenset[str]]
    # path -> line count and SHA-256 of the file as pinned at registration.
    sources: dict[str, tuple[int, str]] = field(default_factory=dict)


def source_entry(text: str) -> tuple[int, str]:
    return len(text.splitlines()), hashlib.sha256(text.encode()).hexdigest()


class WorkloadRejected(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _argument_problem(where: str, spec: ArgumentSpec, value: Scalar) -> str | None:
    expected = {"int": int, "float": (int, float), "bool": bool, "str": str}[spec.kind]
    if value is None and not spec.required:
        return None
    if not isinstance(value, expected) or (spec.kind != "bool" and isinstance(value, bool)):
        return f"{where}: expected {spec.kind}, got {type(value).__name__}"
    if spec.choices and value not in spec.choices:
        return f"{where}: not one of the registered choices"
    if spec.kind in {"int", "float"}:
        if spec.minimum is not None and value < spec.minimum:
            return f"{where}: below minimum {spec.minimum}"
        if spec.maximum is not None and value > spec.maximum:
            return f"{where}: above maximum {spec.maximum}"
    if spec.kind == "str":
        if len(value) > MAX_STRING_ARGUMENT:
            return f"{where}: longer than {MAX_STRING_ARGUMENT} characters"
        if not spec.choices and (not spec.pattern or not re.fullmatch(spec.pattern, value)):
            return f"{where}: does not match the registered pattern"
    return None


def validate_workload(proposal: WorkloadProposal, registry: AppRegistry) -> None:
    """Refuses anything outside the registration, and lists every reason rather than the first."""
    reasons: list[str] = []
    if proposal.application != registry.application:
        reasons.append(f"application {proposal.application!r} is not {registry.application!r}")
    names = [a.name for a in proposal.actors]
    if len(set(names)) != len(names):
        reasons.append(f"duplicate actor names: {names}")
    for actor in proposal.actors:
        where = f"actor {actor.name!r}"
        spec = registry.entrypoints.get(actor.entrypoint)
        if spec is None:
            reasons.append(f"{where}: entrypoint {actor.entrypoint!r} is not registered")
            continue
        if actor.application_version not in spec.versions:
            reasons.append(f"{where}: version {actor.application_version!r} is not registered")
        if actor.role != spec.role:
            reasons.append(f"{where}: {actor.entrypoint} is registered as {spec.role}, not {actor.role}")
        given = [a.name for a in actor.arguments]
        if len(set(given)) != len(given):
            reasons.append(f"{where}: repeated argument")
        for argument in actor.arguments:
            argument_spec = spec.arguments.get(argument.name)
            if argument_spec is None:
                reasons.append(f"{where}: unknown argument {argument.name!r}")
                continue
            problem = _argument_problem(f"{where}: argument {argument.name!r}", argument_spec, argument.value)
            if problem:
                reasons.append(problem)
        for name, argument_spec in spec.arguments.items():
            if argument_spec.required and name not in given:
                reasons.append(f"{where}: missing argument {name!r}")
    for goal in proposal.seed_goals:
        fields = registry.entities.get(goal.entity)
        if fields is None:
            reasons.append(f"seed goal {goal.label!r}: unknown entity {goal.entity!r}")
            continue
        for item in goal.fields:
            if item.field not in fields:
                reasons.append(f"seed goal {goal.label!r}: unknown field {goal.entity}.{item.field}")
            if item.op in {"is_null", "not_null"} and item.value is not None:
                reasons.append(f"seed goal {goal.label!r}: {item.op} takes no value")
    for constraint in proposal.constraints:
        entry = registry.sources.get(constraint.path)
        if entry is None:
            reasons.append(f"constraint: {constraint.path!r} is not a registered source")
        elif not 1 <= constraint.start_line <= constraint.end_line <= entry[0]:
            reasons.append(f"constraint: lines {constraint.start_line}-{constraint.end_line} of {constraint.path}")
    if reasons:
        raise WorkloadRejected(reasons)


def compile_workload(proposal: WorkloadProposal, registry: AppRegistry) -> tuple[list[LiveActor], list[SeedGoal]]:
    """Registered functions with validated arguments. Seed goals come back unresolved, for a constructor."""
    validate_workload(proposal, registry)
    actors = [
        LiveActor(
            name=actor.name, entrypoint=registry.entrypoints[actor.entrypoint].function,
            kwargs={a.name: a.value for a in actor.arguments},
            autocommit=registry.entrypoints[actor.entrypoint].autocommit,
        )
        for actor in proposal.actors
    ]
    return actors, list(proposal.seed_goals)
