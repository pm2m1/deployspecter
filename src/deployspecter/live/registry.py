"""Trusted entrypoint registry for version 2 workload specifications. Research only, and database-free.

A registry is built only from configuration plus a ``TrustedRegistration`` that harness code constructs itself: who
registered the entrypoints, the configuration's label, and the digest the configuration must have. The JSON alone
proves nothing; its own ``registered_by`` must agree with the trusted object, and registration provenance is taken
from that object.
It maps registered identifiers to operation references, version labels, roles, argument contracts and instance
limits. ``authorize`` checks an already parsed ``WorkloadSpec`` against it and returns either an authorized workload
or a structured rejection listing every reason.

Boundaries:

* **Nothing is imported or executed.** An operation reference is an opaque, trusted string. Resolving it to code is a
  later, separate step, never driven by proposal text.
* **Proposal text can't register anything.** Only registry configuration defines entrypoints; a specification can only
  name them.
* **Structure and authorization stay separate.** Structural validation is ``workload_spec``'s job. ``authorize`` still
  re-validates the specification it's given, because a pydantic object can be built without validation
  (``model_construct``, ``model_copy``), so its class proves nothing. This module checks names, versions, roles,
  argument names, declared argument types and counts. Argument values
  (ranges, patterns, choices) are the operation-specific validator's job, a later step, and are reported as unchecked.
* **Who registered an entrypoint** comes from the registry configuration's ``registered_by``. A specification's own
  ``provenance`` is carried through, labelled self-reported, and decides nothing.
* No oracle, requirement, schedule, database or network is used.

These checks are against mistakes and forged data objects, not against hostile code in the same process: such code
could replace these functions themselves.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from . import workload_spec as ws

REGISTRY_VERSION = 1
MAX_ENTRYPOINTS = 64
# A dotted module path and a function name, for the harness to resolve later. Stored as text, never imported here.
OPERATION_REF = r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*:[a-z_][a-z0-9_]*$"

Identifier = Annotated[str, Field(pattern=ws.IDENTIFIER)]
VersionLabel = Annotated[str, Field(pattern=ws.VERSION_LABEL)]
ValueType = Literal["int", "float", "bool", "str", "null"]
Role = Literal["request", "background_job"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _unique(values: list[Any], what: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"repeated {what}")


class ArgumentContract(_Strict):
    """Which value types an argument may have. Ranges and patterns belong to the later argument validator."""

    name: Identifier
    types: list[ValueType] = Field(min_length=1, max_length=5)
    required: StrictBool = True

    @model_validator(mode="after")
    def _unique_types(self) -> ArgumentContract:
        _unique(self.types, f"type in argument {self.name!r}")
        if self.required and self.types == ["null"]:
            raise ValueError(f"argument {self.name!r} is required but may only be null")
        return self


class EntrypointRegistration(_Strict):
    id: Identifier
    operation: str = Field(pattern=OPERATION_REF, max_length=200)
    versions: list[VersionLabel] = Field(min_length=1, max_length=16)
    roles: list[Role] = Field(min_length=1, max_length=2)
    arguments: list[ArgumentContract] = Field(default_factory=list, max_length=ws.MAX_ARGUMENTS)
    max_instances: StrictInt = Field(ge=1, le=ws.MAX_ACTORS)
    description: str = Field(default="", max_length=200, pattern=ws.SAFE_TEXT)

    @model_validator(mode="after")
    def _unique_parts(self) -> EntrypointRegistration:
        _unique(self.versions, f"version in {self.id!r}")
        _unique(self.roles, f"role in {self.id!r}")
        _unique([a.name for a in self.arguments], f"argument in {self.id!r}")
        return self


class RegisteredBy(_Strict):
    """Who registered these entrypoints, as the trusted harness configuration states it."""

    registrar: Identifier
    source: Literal["harness_config"]
    config_label: Identifier


class RegistryConfig(_Strict):
    registry_version: Literal[1]
    application: Identifier
    registered_by: RegisteredBy
    min_actors: StrictInt = Field(ge=ws.MIN_ACTORS, le=ws.MAX_ACTORS)
    max_actors: StrictInt = Field(ge=ws.MIN_ACTORS, le=ws.MAX_ACTORS)
    entrypoints: list[EntrypointRegistration] = Field(min_length=1, max_length=MAX_ENTRYPOINTS)

    @field_validator("registry_version", mode="before")
    @classmethod
    def _an_integer_version(cls, value: Any) -> Any:
        if type(value) is not int:  # True == 1 in Python, so Literal[1] alone would accept it
            raise ValueError("registry_version must be the integer 1")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> RegistryConfig:
        if self.min_actors > self.max_actors:
            raise ValueError("min_actors is greater than max_actors")
        _unique([e.id for e in self.entrypoints], "entrypoint id")
        operations = [e.operation for e in self.entrypoints]
        if len(set(operations)) != len(operations):
            raise ValueError("ambiguous: one operation registered under more than one id")
        return self


class TrustedRegistration(_Strict):
    """Constructed by harness code from its own configuration, never parsed from a proposal or the registry JSON.

    The pinned digest must come from somewhere other than the configuration it checks, for example a reviewed,
    committed file, so that changing the configuration alone can't change what is trusted.
    """

    registrar: Identifier
    config_label: Identifier
    pinned_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegistryError(ValueError):
    """The registry configuration itself is invalid, forged or not the one the harness pinned."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class Registry:
    """An immutable, validated registry. Build one with ``build_registry`` or ``load_registry_json``."""

    def __init__(self, config: RegistryConfig, trusted: TrustedRegistration) -> None:
        self.config = config
        self._by_id = {e.id: e for e in config.entrypoints}
        self.digest = registry_digest(config)
        self.trusted = trusted

    @property
    def application(self) -> str:
        return self.config.application

    @property
    def registered_by(self) -> RegisteredBy:
        """From the trusted object. The configuration's own field has already been checked against it."""
        return RegisteredBy(registrar=self.trusted.registrar, source="harness_config",
                            config_label=self.trusted.config_label)

    def lookup(self, entrypoint_id: str) -> EntrypointRegistration | None:
        """Exact match only. No prefix, case-folding or fuzzy matching."""
        return self._by_id.get(entrypoint_id)

    def ids(self) -> list[str]:
        return sorted(self._by_id)


def _canonical_config(config: RegistryConfig) -> dict[str, Any]:
    data = config.model_dump(mode="json")
    for entry in data["entrypoints"]:
        entry["versions"].sort()
        entry["roles"].sort()
        for argument in entry["arguments"]:
            argument["types"].sort()
        entry["arguments"].sort(key=lambda a: a["name"])
    data["entrypoints"].sort(key=lambda e: e["id"])
    return data


def registry_digest(config: RegistryConfig) -> str:
    text = json.dumps(_canonical_config(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _errors(exc: Exception) -> list[str]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors()]
    return [str(exc)]


def revalidate_model(model_type: type[BaseModel], value: Any) -> Any:
    """A fresh, fully validated copy. An instance of the right class is dumped and validated again, never trusted."""
    document = value.model_dump(mode="json", warnings="error") if isinstance(value, BaseModel) else value
    problems = ws.text_problems(document)
    if problems:
        raise ValueError("; ".join(problems))
    return model_type.model_validate(document)


def build_registry(config: Any, *, trusted: TrustedRegistration) -> Registry:
    """Validate configuration against what the harness trusts. Any disagreement fails closed."""
    try:
        trusted = revalidate_model(TrustedRegistration, trusted) if isinstance(trusted, TrustedRegistration) else None
    except ValueError:
        trusted = None
    if trusted is None:
        raise RegistryError(["a TrustedRegistration built by harness code is required"])
    try:
        parsed = revalidate_model(RegistryConfig, config)
    except Exception as exc:  # a forged instance may fail to serialize in any of several ways
        raise RegistryError(_errors(exc)) from exc
    digest = registry_digest(parsed)
    problems = []
    if digest != trusted.pinned_digest:
        problems.append(f"registry digest {digest[:16]} isn't the pinned {trusted.pinned_digest[:16]}")
    claimed = parsed.registered_by
    if (claimed.registrar, claimed.config_label) != (trusted.registrar, trusted.config_label):
        problems.append("the configuration's registered_by doesn't match the trusted registration")
    if problems:
        raise RegistryError(problems)
    return Registry(parsed, trusted)


def verify_registry(registry: Registry) -> None:
    """Check a ``Registry`` object is what ``build_registry`` makes from its own configuration and trust object.

    ``Registry`` is a plain class whose attributes can be reassigned, so stages that rely on it rebuild it and compare.
    Raises ``RegistryError`` on any difference.
    """
    if not isinstance(registry, Registry):
        raise RegistryError(["not a Registry"])
    rebuilt = build_registry(registry.config, trusted=registry.trusted)
    if (rebuilt.config, rebuilt.digest, rebuilt._by_id) != (registry.config, registry.digest, registry._by_id):
        raise RegistryError(["the Registry object differs from what its configuration builds"])


def load_registry_json(text: str, *, trusted: TrustedRegistration) -> Registry:
    if not isinstance(text, str):
        raise RegistryError(["registry configuration must be given as text"])
    try:
        document = json.loads(text, object_pairs_hook=ws._refuse_duplicate_keys, parse_constant=ws._refuse_constant)
    except RecursionError as exc:
        raise RegistryError(["registry JSON is nested too deeply to decode"]) from exc
    except ValueError as exc:
        raise RegistryError([f"not valid registry JSON: {exc}"]) from exc
    return build_registry(document, trusted=trusted)


# ------------------------------------------------------------------ authorization


RejectionCode = Literal[
    "structure_invalid", "not_genuine_authorization", "application_mismatch", "too_few_actors", "too_many_actors",
    "unknown_entrypoint", "version_not_registered",
    "role_not_registered", "unknown_argument", "missing_argument", "argument_type_not_registered",
    "instance_limit_exceeded",
]


class Rejection(_Strict):
    """Structured fields for comparison; ``detail`` is for people and is derived from the same facts."""

    code: RejectionCode
    actor: str | None = None
    entrypoint: str | None = None
    argument: str | None = None
    detail: str


class AuthorizedArgument(_Strict):
    name: str
    type: ValueType
    value: Any


class AuthorizedActor(_Strict):
    actor: str
    entrypoint: str
    operation: str
    application_version: str
    role: Role
    arguments: tuple[AuthorizedArgument, ...]
    # Names and declared types are checked here. Values aren't; that's the argument validator's job.
    argument_values_validated: Literal[False] = False


class AuthorizedWorkload(_Strict):
    spec_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    application: str
    registered_by: RegisteredBy
    actors: tuple[AuthorizedActor, ...]
    state_goals: tuple[ws.StateGoal, ...]
    # Carried through for analysis. What the proposal says about itself; it decides nothing here.
    self_reported_provenance: ws.Provenance
    # Registry authorization alone never makes a workload runnable.
    execution_ready: Literal[False] = False


class AuthorizationResult(_Strict):
    accepted: bool
    workload: AuthorizedWorkload | None = None
    rejections: tuple[Rejection, ...] = ()


def _structure_problems(spec: ws.WorkloadSpec) -> tuple[ws.WorkloadSpec | None, list[str]]:
    """Validate a specification object again from its own content, with the real parser.

    Returns the freshly parsed specification, or the reasons it isn't one. The object must equal its re-parsed self,
    so content that validation would have changed or refused can't pass as already validated.
    """
    try:
        # Without defaults, so the re-parsed document is never larger than the one first parsed, and the size limit
        # can't refuse here what it accepted there.
        document = spec.model_dump(mode="json", warnings="error", exclude_defaults=True)
    except Exception as exc:  # a forged object may fail to serialize in any of several ways
        return None, [f"the specification object can't be read back as data ({type(exc).__name__})"]
    try:
        again = ws.parse_workload(document)
    except ws.SpecRejected as exc:
        return None, exc.reasons
    except ws.UnsupportedSchemaVersion as exc:
        return None, [str(exc)]
    if again != spec:
        return None, ["the specification object differs from its own validated content"]
    return again, []


def authorize(spec: ws.WorkloadSpec, registry: Registry) -> AuthorizationResult:
    """Check a specification against the registry. Deterministic, and lists every reason.

    The specification is validated again first: an object of the right class proves nothing. The registry is rebuilt
    from its configuration and trust object and must match, or ``RegistryError`` is raised.
    """
    if not isinstance(spec, ws.WorkloadSpec):
        raise TypeError("authorize takes a parsed WorkloadSpec; parse and validate its structure first")
    if not isinstance(registry, Registry):
        raise TypeError("authorize takes a Registry built from trusted configuration")
    verify_registry(registry)
    checked, problems = _structure_problems(spec)
    if checked is None:
        return AuthorizationResult(accepted=False, rejections=tuple(
            Rejection(code="structure_invalid", detail=reason) for reason in problems))
    spec = checked
    config = registry.config
    rejections: list[Rejection] = []
    if spec.application != registry.application:
        rejections.append(Rejection(code="application_mismatch",
                                    detail=f"{spec.application!r} isn't {registry.application!r}"))
    count = len(spec.actors)
    if count < config.min_actors:
        rejections.append(Rejection(code="too_few_actors", detail=f"{count} actors, at least {config.min_actors}"))
    if count > config.max_actors:
        rejections.append(Rejection(code="too_many_actors", detail=f"{count} actors, at most {config.max_actors}"))
    actors: list[AuthorizedActor] = []
    instances: dict[str, int] = {}
    for actor in sorted(spec.actors, key=lambda a: a.name):
        entry = registry.lookup(actor.entrypoint)
        if entry is None:
            rejections.append(Rejection(code="unknown_entrypoint", actor=actor.name, entrypoint=actor.entrypoint,
                                        detail=f"{actor.entrypoint!r} isn't registered"))
            continue
        instances[entry.id] = instances.get(entry.id, 0) + 1
        problems = _actor_problems(actor, entry)
        rejections.extend(problems)
        if not problems:
            actors.append(AuthorizedActor(
                actor=actor.name, entrypoint=entry.id, operation=entry.operation,
                application_version=actor.application_version, role=actor.role,
                arguments=tuple(
                    AuthorizedArgument(name=a.name, type=a.value.type, value=ws.plain_value(a.value))
                    for a in sorted(actor.arguments, key=lambda a: a.name)
                ),
            ))
    for entrypoint_id in sorted(instances):
        limit = registry.lookup(entrypoint_id).max_instances
        if instances[entrypoint_id] > limit:
            rejections.append(Rejection(code="instance_limit_exceeded", entrypoint=entrypoint_id,
                                        detail=f"{entrypoint_id!r} used {instances[entrypoint_id]} times, at most "
                                               f"{limit}"))
    if rejections:
        return AuthorizationResult(accepted=False, rejections=tuple(rejections))
    return AuthorizationResult(accepted=True, workload=AuthorizedWorkload(
        spec_digest=ws.digest(spec), registry_digest=registry.digest, application=registry.application,
        registered_by=registry.registered_by, actors=tuple(actors),
        state_goals=tuple(sorted(spec.state_goals, key=lambda g: g.label)), self_reported_provenance=spec.provenance,
    ))


def _actor_problems(actor: ws.ActorSpec, entry: EntrypointRegistration) -> list[Rejection]:
    found: list[Rejection] = []

    def reject(code: RejectionCode, detail: str, argument: str | None = None) -> None:
        found.append(Rejection(code=code, actor=actor.name, entrypoint=entry.id, argument=argument, detail=detail))

    if actor.application_version not in entry.versions:
        reject("version_not_registered", f"{entry.id!r} has no version {actor.application_version!r}")
    if actor.role not in entry.roles:
        reject("role_not_registered", f"{entry.id!r} isn't registered as {actor.role!r}")
    contracts = {c.name: c for c in entry.arguments}
    given = {a.name: a for a in actor.arguments}
    for name in sorted(set(given) - set(contracts)):
        reject("unknown_argument", f"{entry.id!r} has no argument {name!r}", name)
    for name in sorted(n for n, c in contracts.items() if c.required and n not in given):
        reject("missing_argument", f"{entry.id!r} needs argument {name!r}", name)
    for name in sorted(set(given) & set(contracts)):
        kind = given[name].value.type
        if kind not in contracts[name].types:
            reject("argument_type_not_registered", f"{name!r} may be {contracts[name].types}, not {kind!r}", name)
    return found


def _document_value(kind: str, value: Any) -> dict[str, Any]:
    return {"type": "null"} if kind == "null" else {"type": kind, "value": value}


def check_authorized(workload: AuthorizedWorkload, registry: Registry) -> tuple[Rejection, ...]:
    """Whether an ``AuthorizedWorkload`` object is genuine ``authorize`` output for this registry. Empty if it is.

    The object holds no copy of its specification, so a document is rebuilt from what it does hold, parsed, and
    authorized again. Unless the result equals the object exactly, it wasn't produced by ``authorize``: a forged
    operation reference, entrypoint, version, role, actor name, argument, state goal, application or registration,
    or reordered content, all fail. The full-record digest can't be re-derived without the original document, so only
    its form is checked; nothing downstream uses it.
    """
    if not isinstance(workload, AuthorizedWorkload):
        raise TypeError("check_authorized takes an AuthorizedWorkload")
    verify_registry(registry)

    def refuse(code: RejectionCode, detail: str) -> tuple[Rejection, ...]:
        return (Rejection(code=code, detail=detail),)

    try:
        # The shortest valid name, no explanatory fields and no defaults, so this document is never larger than the
        # specification that was authorized, and the size limit can't refuse it here.
        document = {
            "schema_version": 2, "name": "r", "application": workload.application,
            "provenance": workload.self_reported_provenance.model_dump(mode="json", warnings="error",
                                                                       exclude_defaults=True),
            "actors": [{
                "name": actor.actor, "entrypoint": actor.entrypoint,
                "application_version": actor.application_version, "role": actor.role,
                "arguments": [{"name": a.name, "value": _document_value(a.type, a.value)} for a in actor.arguments],
            } for actor in workload.actors],
            "state_goals": [goal.model_dump(mode="json", warnings="error", exclude_defaults=True)
                            for goal in workload.state_goals],
        }
        spec_digest = workload.spec_digest
    except Exception as exc:  # a forged object may lack fields or hold the wrong kinds of value
        return refuse("not_genuine_authorization", f"the workload object can't be read back ({type(exc).__name__})")
    if type(spec_digest) is not str or len(spec_digest) != 64 or set(spec_digest) - set("0123456789abcdef"):
        return refuse("not_genuine_authorization", "the full-record digest isn't 64 lower-case hex digits")
    try:
        spec = ws.parse_workload(document)
    except (ws.SpecRejected, ws.UnsupportedSchemaVersion) as exc:
        return tuple(Rejection(code="structure_invalid", detail=reason)
                     for reason in getattr(exc, "reasons", [str(exc)]))
    result = authorize(spec, registry)
    if not result.accepted:
        return result.rejections
    if result.workload.model_copy(update={"spec_digest": spec_digest}) != workload:
        return refuse("not_genuine_authorization", "the workload isn't what the registry authorizes for its content")
    return ()
