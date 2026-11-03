"""Operation-specific argument validation for registry-authorized workloads. Research only, offline and pure.

The third of five separate stages, each with its own output:

1. Structural validity (``workload_spec``): the document is a well-formed version 2 specification.
2. Registry authorization (``registry``): it names registered entrypoints, versions, roles and argument names.
3. **Argument-value validation (this module)**: each value satisfies its operation's declared constraints.
4. Execution authorization, not implemented: trusted dispatch resolves operation references to code, after checking
   each operation's source fingerprint, and decides whether to run.
5. Independent correctness evaluation, not implemented here: oracles judge outcomes, and never see stages 1 to 4.

Constraints come from a ``ContractSet``: trusted configuration, pinned by its own digest, bound to one registry's
digest, and checked for complete coverage of that registry. The vocabulary is closed and enumerated below; anything
else is refused when the contract set is built. There is no expression language, no regular expression supplied by
configuration, and no coercion or repair of values. Nothing is imported, executed, read from a database or network,
or compared with an oracle.
"""

from __future__ import annotations

import hashlib
import json
import math
import string
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from . import workload_spec as ws
from .registry import AuthorizedWorkload, Registry

CONTRACTS_VERSION = 1
MAX_ALLOWED_VALUES = 32
MAX_PREDICATES = 8

Identifier = Annotated[str, Field(pattern=ws.IDENTIFIER)]
VersionLabel = Annotated[str, Field(pattern=ws.VERSION_LABEL)]

# Fixed character sets, by name. Configuration chooses one; it can't define its own.
_LOWER = frozenset(string.ascii_lowercase)
CHARSETS: dict[str, frozenset[str]] = {
    "digits": frozenset(string.digits),
    "alnum": frozenset(string.ascii_letters + string.digits),
    "alnum_underscore_dash": frozenset(string.ascii_letters + string.digits + "_-"),
    "lower_identifier": frozenset(string.ascii_lowercase + string.digits + "_"),  # and must start with a letter
    "printable_ascii": frozenset(chr(c) for c in range(0x20, 0x7F)),
}
Charset = Literal["digits", "alnum", "alnum_underscore_dash", "lower_identifier", "printable_ascii"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _unique(values: list[Any], what: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"repeated {what}")


# ------------------------------------------------------------------ the closed constraint vocabulary


class IntConstraint(_Strict):
    kind: Literal["int"]
    minimum: StrictInt | None = Field(default=None, ge=-ws.INT64, lt=ws.INT64)
    maximum: StrictInt | None = Field(default=None, ge=-ws.INT64, lt=ws.INT64)
    allowed: list[StrictInt] | None = Field(default=None, min_length=1, max_length=MAX_ALLOWED_VALUES)

    @model_validator(mode="after")
    def _sound(self) -> IntConstraint:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum is greater than maximum")
        if self.allowed is not None:
            _unique(self.allowed, "allowed value")
        return self


class FloatConstraint(_Strict):
    """Finite bounds only. No allowed-value list, because comparing floats for equality is fragile."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    kind: Literal["float"]
    minimum: StrictFloat | None = None
    maximum: StrictFloat | None = None

    @model_validator(mode="after")
    def _sound(self) -> FloatConstraint:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum is greater than maximum")
        return self


class StrConstraint(_Strict):
    kind: Literal["str"]
    min_length: StrictInt = Field(default=0, ge=0, le=ws.MAX_STRING_VALUE)
    max_length: StrictInt = Field(default=ws.MAX_STRING_VALUE, ge=0, le=ws.MAX_STRING_VALUE)
    charset: Charset | None = None
    allowed: list[StrictStr] | None = Field(default=None, min_length=1, max_length=MAX_ALLOWED_VALUES)

    @model_validator(mode="after")
    def _sound(self) -> StrConstraint:
        if self.min_length > self.max_length:
            raise ValueError("min_length is greater than max_length")
        if self.allowed is not None:
            _unique(self.allowed, "allowed value")
            for value in self.allowed:
                if _string_problem(value, self) is not None:
                    raise ValueError(f"allowed value {value!r} breaks the constraint's own length or charset")
        return self


class BoolConstraint(_Strict):
    kind: Literal["bool"]
    allowed: list[StrictBool] | None = Field(default=None, min_length=1, max_length=2)

    @model_validator(mode="after")
    def _sound(self) -> BoolConstraint:
        if self.allowed is not None:
            _unique(self.allowed, "allowed value")
        return self


ValueConstraint = Annotated[IntConstraint | FloatConstraint | StrConstraint | BoolConstraint,
                            Field(discriminator="kind")]


class ArgumentRule(_Strict):
    name: Identifier
    constraints: list[ValueConstraint] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _one_per_type(self) -> ArgumentRule:
        _unique([c.kind for c in self.constraints], f"constraint type for {self.name!r}")
        return self

    def for_type(self, kind: str) -> Any:
        return next((c for c in self.constraints if c.kind == kind), None)


# Cross-field predicates, within one actor's arguments. Exactly these four; no other operator exists.


class LessThan(_Strict):
    op: Literal["less_than"]
    left: Identifier
    right: Identifier


class LessOrEqual(_Strict):
    op: Literal["less_or_equal"]
    left: Identifier
    right: Identifier


class Requires(_Strict):
    """If ``if_present`` has a non-null value, ``then_present`` must have one too."""

    op: Literal["requires"]
    if_present: Identifier
    then_present: Identifier


class Excludes(_Strict):
    """``left`` and ``right`` may not both have non-null values."""

    op: Literal["excludes"]
    left: Identifier
    right: Identifier


Predicate = Annotated[LessThan | LessOrEqual | Requires | Excludes, Field(discriminator="op")]
PREDICATE_OPS = ("less_than", "less_or_equal", "requires", "excludes")


def _predicate_fields(predicate: Any) -> tuple[str, str]:
    if isinstance(predicate, Requires):
        return predicate.if_present, predicate.then_present
    return predicate.left, predicate.right


class OperationContract(_Strict):
    entrypoint: Identifier
    versions: list[VersionLabel] = Field(min_length=1, max_length=16)
    roles: list[Literal["request", "background_job"]] = Field(min_length=1, max_length=2)
    arguments: list[ArgumentRule] = Field(default_factory=list, max_length=ws.MAX_ARGUMENTS)
    predicates: list[Predicate] = Field(default_factory=list, max_length=MAX_PREDICATES)

    @model_validator(mode="after")
    def _sound(self) -> OperationContract:
        _unique(self.versions, f"version in {self.entrypoint!r}")
        _unique(self.roles, f"role in {self.entrypoint!r}")
        _unique([a.name for a in self.arguments], f"argument rule in {self.entrypoint!r}")
        names = {a.name for a in self.arguments}
        for predicate in self.predicates:
            for field in _predicate_fields(predicate):
                if field not in names:
                    raise ValueError(f"{predicate.op} in {self.entrypoint!r} names unknown argument {field!r}")
            if len(set(_predicate_fields(predicate))) == 1:
                raise ValueError(f"{predicate.op} in {self.entrypoint!r} compares an argument with itself")
        return self


class ContractSet(_Strict):
    contracts_version: StrictInt
    application: Identifier
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contracts: list[OperationContract] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _sound(self) -> ContractSet:
        if self.contracts_version != CONTRACTS_VERSION or type(self.contracts_version) is not int:
            raise ValueError(f"contracts_version must be the integer {CONTRACTS_VERSION}")
        seen = [(c.entrypoint, v) for c in self.contracts for v in c.versions]
        _unique(seen, "entrypoint and version pair (ambiguous contracts)")
        return self


class TrustedContracts(_Strict):
    """Built by harness code. The pinned digest must come from a source separate from the contract set itself."""

    reviewer: Identifier
    pinned_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class Contracts:
    """A validated contract set, bound to one registry."""

    def __init__(self, config: ContractSet, registry: Registry, trusted: TrustedContracts) -> None:
        self.config = config
        self.registry_digest = registry.digest
        self.digest = contracts_digest(config)
        self.trusted = trusted
        self._index = {(c.entrypoint, v): c for c in config.contracts for v in c.versions}
        self.registered_types = {
            (entry_id, a.name): tuple(a.types)
            for entry_id in registry.ids() for a in registry.lookup(entry_id).arguments
        }

    def lookup(self, entrypoint: str, version: str) -> OperationContract | None:
        return self._index.get((entrypoint, version))


def contracts_digest(config: ContractSet) -> str:
    data = config.model_dump(mode="json")
    for contract in data["contracts"]:
        contract["versions"].sort()
        contract["roles"].sort()
        contract["arguments"].sort(key=lambda a: a["name"])
        for argument in contract["arguments"]:
            argument["constraints"].sort(key=lambda c: c["kind"])
    data["contracts"].sort(key=lambda c: (c["entrypoint"], c["versions"]))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_contracts(config: Any, *, registry: Registry, trusted: TrustedContracts) -> Contracts:
    """Validate a contract set against its registry. Incomplete or inconsistent coverage fails closed."""
    if not isinstance(trusted, TrustedContracts):
        raise ContractError(["a TrustedContracts built by harness code is required"])
    if not isinstance(registry, Registry):
        raise ContractError(["contracts are checked against a built Registry"])
    try:
        parsed = config if isinstance(config, ContractSet) else ContractSet.model_validate(config)
    except ValueError as exc:
        raise ContractError(_errors(exc)) from exc
    problems: list[str] = []
    if contracts_digest(parsed) != trusted.pinned_digest:
        problems.append("contract set digest isn't the pinned one")
    if parsed.registry_digest != registry.digest:
        problems.append("contract set is for a different registry")
    if parsed.application != registry.application:
        problems.append("contract set is for a different application")
    problems.extend(_coverage_problems(parsed, registry))
    if problems:
        raise ContractError(problems)
    return Contracts(parsed, registry, trusted)


def _coverage_problems(config: ContractSet, registry: Registry) -> list[str]:
    problems: list[str] = []
    covered = {(c.entrypoint, v) for c in config.contracts for v in c.versions}
    for entry_id in registry.ids():
        entry = registry.lookup(entry_id)
        for version in entry.versions:
            if (entry_id, version) not in covered:
                problems.append(f"no contract for {entry_id!r} version {version!r}")
    for contract in config.contracts:
        entry = registry.lookup(contract.entrypoint)
        if entry is None:
            problems.append(f"contract for unregistered entrypoint {contract.entrypoint!r}")
            continue
        where = contract.entrypoint
        problems += [f"{where}: version {v!r} isn't registered" for v in contract.versions if v not in entry.versions]
        problems += [f"{where}: role {r!r} isn't registered" for r in contract.roles if r not in entry.roles]
        registered = {a.name: a for a in entry.arguments}
        rules = {a.name: a for a in contract.arguments}
        problems += [f"{where}: no rule for argument {n!r}" for n in sorted(set(registered) - set(rules))]
        problems += [f"{where}: rule for unregistered argument {n!r}" for n in sorted(set(rules) - set(registered))]
        for name in sorted(set(registered) & set(rules)):
            types = set(registered[name].types)
            kinds = {c.kind for c in rules[name].constraints}
            problems += [f"{where}: {name!r} has no {k} constraint" for k in sorted(types - kinds - {"null"})]
            problems += [f"{where}: {name!r} constrains unregistered type {k}" for k in sorted(kinds - types)]
        for predicate in contract.predicates:
            left, right = _predicate_fields(predicate)
            if predicate.op in {"less_than", "less_or_equal"} and left in registered and right in registered:
                problems.extend(_comparison_problems(where, predicate.op, left, right, registered))
    return problems


def _comparison_problems(where: str, op: str, left: str, right: str, registered: dict[str, Any]) -> list[str]:
    """Both sides must have the same single numeric type.

    Mixed int and float comparisons are refused: Python compares them exactly (``2**53 + 1 > 2.0**53``), but anything
    that converts the integer to a float first gets the opposite answer, so the rule would depend on the implementation.
    """
    kinds = [set(registered[field].types) - {"null"} for field in (left, right)]
    for field, kind in zip((left, right), kinds, strict=True):
        if not kind <= {"int", "float"}:
            return [f"{where}: {op} needs numeric arguments, {field!r} isn't"]
    if len(kinds[0]) != 1 or kinds[0] != kinds[1]:
        return [f"{where}: {op} compares {left!r} and {right!r}, which must share one numeric type"]
    return []


def _errors(exc: Exception) -> list[str]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors()]
    return [str(exc)]


# ------------------------------------------------------------------ validation


ArgumentRejectionCode = Literal[
    "registry_mismatch", "no_contract", "role_not_covered", "type_not_covered", "null_not_allowed",
    "wrong_python_type", "non_finite", "below_minimum", "above_maximum", "too_short", "too_long",
    "character_not_permitted", "not_in_allowed_values", "cross_field_violated",
]


class ArgumentRejection(_Strict):
    code: ArgumentRejectionCode
    actor: str | None = None
    entrypoint: str | None = None
    argument: str | None = None
    predicate: str | None = None
    detail: str


class ValidatedWorkload(_Strict):
    """A workload whose argument values satisfy their contracts. Still not authorized to run, and not judged correct."""

    authorized: AuthorizedWorkload
    contracts_digest: str
    argument_values_validated: Literal[True] = True
    execution_authorized: Literal[False] = False
    correctness_evaluated: Literal[False] = False


class ValidationResult(_Strict):
    accepted: bool
    workload: ValidatedWorkload | None = None
    rejections: tuple[ArgumentRejection, ...] = ()


def _string_problem(value: str, rule: StrConstraint) -> tuple[str, str] | None:
    if len(value) < rule.min_length:
        return "too_short", f"{len(value)} characters, at least {rule.min_length}"
    if len(value) > rule.max_length:
        return "too_long", f"{len(value)} characters, at most {rule.max_length}"
    if rule.charset is not None:
        allowed = CHARSETS[rule.charset]
        bad = sorted({c for c in value if c not in allowed})
        if bad or (rule.charset == "lower_identifier" and value and value[0] not in _LOWER):
            shown = ", ".join(repr(c) for c in bad[:3]) or "the first character"
            return "character_not_permitted", f"{shown} not permitted by charset {rule.charset}"
    return None


_PYTHON_TYPES = {"int": int, "float": float, "str": str, "bool": bool}


def _value_problems(kind: str, value: Any, rule: Any) -> list[tuple[str, str]]:
    """Every problem with one non-null value. Its type tag is trusted only after its Python type agrees with it."""
    if type(value) is not _PYTHON_TYPES[kind]:
        return [("wrong_python_type", f"tagged {kind} but holds {type(value).__name__}")]
    if kind == "float" and not math.isfinite(value):
        return [("non_finite", "not a finite number")]
    found: list[tuple[str, str]] = []
    if kind in {"int", "float"}:
        if rule.minimum is not None and value < rule.minimum:
            found.append(("below_minimum", f"{value!r} is below {rule.minimum!r}"))
        if rule.maximum is not None and value > rule.maximum:
            found.append(("above_maximum", f"{value!r} is above {rule.maximum!r}"))
    if kind == "str":
        problem = _string_problem(value, rule)
        if problem is not None:
            found.append(problem)
    allowed = getattr(rule, "allowed", None)
    if allowed is not None and value not in allowed:
        found.append(("not_in_allowed_values", f"{value!r} isn't one of the allowed values"))
    return found


def validate_arguments(workload: AuthorizedWorkload, contracts: Contracts) -> ValidationResult:
    """Check every argument of an authorized workload. Deterministic; lists every problem; changes nothing."""
    if not isinstance(workload, AuthorizedWorkload):
        raise TypeError("validate_arguments takes a registry-authorized workload")
    if not isinstance(contracts, Contracts):
        raise TypeError("validate_arguments takes contracts built against the registry")
    rejections: list[ArgumentRejection] = []
    if workload.registry_digest != contracts.registry_digest:
        rejections.append(ArgumentRejection(code="registry_mismatch",
                                            detail="the workload was authorized by a different registry"))
        return ValidationResult(accepted=False, rejections=tuple(rejections))
    for actor in workload.actors:
        contract = contracts.lookup(actor.entrypoint, actor.application_version)

        def reject(code: str, detail: str, argument: str | None = None, predicate: str | None = None,
                   _actor: Any = actor) -> None:
            rejections.append(ArgumentRejection(code=code, actor=_actor.actor, entrypoint=_actor.entrypoint,
                                                argument=argument, predicate=predicate, detail=detail))

        if contract is None:
            reject("no_contract", f"no contract for version {actor.application_version!r}")
            continue
        if actor.role not in contract.roles:
            reject("role_not_covered", f"contract doesn't cover role {actor.role!r}")
            continue
        rules = {r.name: r for r in contract.arguments}
        present: dict[str, Any] = {}
        for argument in actor.arguments:
            rule = rules.get(argument.name)
            if argument.type == "null":
                registered = contracts_registered_types(contracts, actor.entrypoint, argument.name)
                if "null" not in registered:
                    reject("null_not_allowed", "null isn't a registered type", argument.name)
                continue
            constraint = rule.for_type(argument.type) if rule else None
            if constraint is None:
                reject("type_not_covered", f"no {argument.type} constraint", argument.name)
                continue
            problems = _value_problems(argument.type, argument.value, constraint)
            for code, detail in problems:
                reject(code, detail, argument.name)
            if not problems:
                present[argument.name] = argument.value
        for predicate in contract.predicates:
            detail = _predicate_violation(predicate, present)
            if detail is not None:
                reject("cross_field_violated", detail, predicate=predicate.op)
    if rejections:
        return ValidationResult(accepted=False, rejections=tuple(rejections))
    return ValidationResult(accepted=True, workload=ValidatedWorkload(authorized=workload,
                                                                      contracts_digest=contracts.digest))


def contracts_registered_types(contracts: Contracts, entrypoint: str, argument: str) -> set[str]:
    return set(contracts.registered_types.get((entrypoint, argument), ()))


def _predicate_violation(predicate: Any, present: dict[str, Any]) -> str | None:
    """Only values that passed their own checks take part. A missing or null value never satisfies a comparison."""
    left, right = _predicate_fields(predicate)
    if isinstance(predicate, Requires):
        if left in present and right not in present:
            return f"{left!r} needs {right!r}"
        return None
    if isinstance(predicate, Excludes):
        if left in present and right in present:
            return f"{left!r} and {right!r} can't both be given"
        return None
    if left not in present or right not in present:
        return None  # a comparison applies only when both values are given; "requires" states presence
    if isinstance(predicate, LessThan) and not present[left] < present[right]:
        return f"{left!r} must be less than {right!r}"
    if isinstance(predicate, LessOrEqual) and not present[left] <= present[right]:
        return f"{left!r} must be at most {right!r}"
    return None
