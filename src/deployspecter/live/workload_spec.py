"""Workload specification, schema version 2: frozen. Research only; the release doesn't use it.

A specification is declarative data. It names registered entrypoints by identifier, gives typed arguments, selects
application versions and roles, and may state logical goals about the starting state. It has no field that can carry
Python, SQL, a shell command, an import path or a function. Unknown fields are refused at every level.

What this module does and doesn't decide:

* It checks **structure**: types, bounds, identifiers, uniqueness and total size.
* It does **not** decide whether an entrypoint exists, whether an argument is acceptable for it, or whether a goal is
  feasible. Those belong to the registry, the argument validator and state construction, which are later steps. A
  string argument that looks like SQL is valid structure here, and stays inert data: nothing in this module, or in
  compiling a specification, ever interprets an argument's text.
* It never sees requirements, oracles, labels or schedules, and imports nothing that could.

Version 1 documents (``live/workload.py``) are upgraded explicitly by ``upgrade_v1``. ``parse_workload`` accepts
either version and returns version 2.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:  # the version 1 module imports the live engine, so it's loaded only when upgrading
    from .workload import WorkloadProposal

SCHEMA_VERSION = 2
SCHEMA_ID = "urn:deployspecter:workload-spec:2"
SUPPORTED_VERSIONS = (1, 2)

MAX_ACTORS = 4
MIN_ACTORS = 2
MAX_ARGUMENTS = 16
MAX_STATE_GOALS = 12
MAX_GOAL_CONSTRAINTS = 16
MAX_GOAL_COUNT = 20
MAX_TOTAL_GOAL_ROWS = 64
MAX_SOURCE_REFS = 24
MAX_STRING_VALUE = 128
MAX_DOCUMENT_BYTES = 32_768

# Lower-case identifiers with no dot, colon, slash or bracket, so none can be read as a module path or an expression.
IDENTIFIER = r"^[a-z][a-z0-9_]{0,47}$"
ACTOR_NAME = r"^[A-Za-z][A-Za-z0-9_]{0,30}$"  # the engine's own rule for actor names
VERSION_LABEL = r"^[a-z0-9][a-z0-9._-]{0,31}$"
# Relative, "/"-separated segments that each start with a letter, digit or underscore: no leading "/", no empty, "."
# or ".." segment, no trailing "/", no backslash.
SOURCE_PATH = r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$"
# The text policy, one rule for every string in the version 2 contract: rationale, notes, claims, string arguments,
# names, labels, registry descriptions and contract values. Refused code points:
#   U+0000-U+001F (C0, so newline and tab too), U+007F (DEL), U+0080-U+009F (C1, including CSI U+009B);
#   U+061C, U+200E, U+200F (directional marks); U+202A-U+202E (embeddings and overrides);
#   U+2066-U+2069 (isolates); U+2028, U+2029 (line and paragraph separators, which renderers treat as line breaks);
#   U+D800-U+DFFF (unpaired surrogates: not text at all, and impossible to encode as UTF-8).
# Everything else is kept, including every script, combining marks, emoji and other supplementary characters, and the
# joiners U+200C and U+200D that several scripts need. The pattern expresses all of it except surrogates, which a
# JSON Schema pattern can't state exactly; ``text_problems`` checks those, in Python, before anything else runs.
# Renderers must still escape untrusted text: this rule removes control characters, not every misleading string.
SAFE_TEXT = r"^[^\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]*$"
_FORBIDDEN_RANGES = (
    (0x0000, 0x001F), (0x007F, 0x009F), (0x061C, 0x061C), (0x200E, 0x200F), (0x2028, 0x2029), (0x202A, 0x202E),
    (0x2066, 0x2069), (0xD800, 0xDFFF),
)
INT64 = 2**63


def forbidden_code_point(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _FORBIDDEN_RANGES)


def safe_repr(text: str, limit: int = 60) -> str:
    """Untrusted text made safe to put in a message: refused and unprintable code points become escapes."""
    shown = "".join(f"\\u{ord(c):04x}" if forbidden_code_point(c) or not c.isprintable() else c for c in text[:limit])
    return f"'{shown}'" + ("..." if len(text) > limit else "")


def text_problems(value: Any, limit: int = 20) -> list[str]:
    """Every key and string anywhere in a decoded JSON value that breaks the text policy, by path, in document order.

    It runs before measuring, encoding or validating, so a lone surrogate is reported, never raised as an encoding
    error. It walks with an explicit stack, so no nesting depth can make it raise. Nothing is replaced, removed or
    escaped into a different value: offending text is refused.
    """
    found: list[str] = []
    stack: list[tuple[Any, str]] = [(value, "$")]
    while stack and len(found) < limit:
        item, path = stack.pop()
        if isinstance(item, str):
            bad = sorted({ord(c) for c in item if forbidden_code_point(c)})
            if bad:
                shown = ", ".join(f"U+{code:04X}" for code in bad[:4])
                found.append(f"{path}: text contains refused code points ({shown})")
        elif isinstance(item, dict):
            children = []
            for key, child in item.items():
                if not isinstance(key, str):
                    found.append(f"{path}: an object key isn't a string")
                    continue
                key_path = f"{path}.{safe_repr(key, 40)}"
                if any(forbidden_code_point(c) for c in key):
                    found.append(f"{key_path}: key contains refused code points")
                children.append((child, key_path))
            stack.extend(reversed(children))
        elif isinstance(item, (list, tuple)):
            stack.extend(reversed([(child, f"{path}[{index}]") for index, child in enumerate(item)]))
    return found[:limit]


Identifier = Annotated[str, Field(pattern=IDENTIFIER)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ------------------------------------------------------------------ typed values


class IntValue(_Strict):
    type: Literal["int"]
    value: StrictInt = Field(ge=-INT64, lt=INT64)


class FloatValue(_Strict):
    """A finite IEEE-754 double. Written with a fraction or exponent, so an integer can never pass as a float.

    Negative zero is refused, because it equals zero but serializes differently. Different decimal texts that round to
    the same double (``0.1`` and ``0.10000000000000001``) are the same value, and canonicalize to the shortest form.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    type: Literal["float"]
    value: StrictFloat

    @field_validator("value", mode="before")
    @classmethod
    def _a_real_float(cls, value: Any) -> Any:
        if type(value) is not float:
            raise ValueError("a float value must be written with a fraction or exponent, such as 1.0")
        if value == 0.0 and math.copysign(1.0, value) < 0:
            raise ValueError("negative zero isn't allowed")
        return value


class BoolValue(_Strict):
    type: Literal["bool"]
    value: StrictBool


class StrValue(_Strict):
    type: Literal["str"]
    value: StrictStr = Field(max_length=MAX_STRING_VALUE, pattern=SAFE_TEXT)


class NullValue(_Strict):
    type: Literal["null"]


TypedValue = Annotated[IntValue | FloatValue | BoolValue | StrValue | NullValue, Field(discriminator="type")]


def plain_value(typed: Any) -> Any:
    """The Python value a typed value stands for. ``None`` for null."""
    return None if isinstance(typed, NullValue) else typed.value


# ------------------------------------------------------------------ the specification


class Provenance(_Strict):
    """What the document says about where it came from. Self-reported, so never authoritative.

    It grants no trust and no authority to run anything, and it doesn't decide an evaluation arm. Arm membership and who
    produced a specification are recorded by the harness that requested it, never taken from this field.
    """

    origin: Literal["hand_written", "synthetic", "deterministic_baseline", "model_proposal"]
    author: Identifier
    generator_version: str | None = Field(default=None, pattern=VERSION_LABEL)
    note: str = Field(default="", max_length=400, pattern=SAFE_TEXT)


class Argument(_Strict):
    name: Identifier
    value: TypedValue


class ActorSpec(_Strict):
    name: str = Field(pattern=ACTOR_NAME)
    entrypoint: Identifier
    application_version: str = Field(pattern=VERSION_LABEL)
    role: Literal["request", "background_job"]
    arguments: list[Argument] = Field(default_factory=list, max_length=MAX_ARGUMENTS)

    @model_validator(mode="after")
    def _unique_arguments(self) -> ActorSpec:
        names = [a.name for a in self.arguments]
        if len(set(names)) != len(names):
            raise ValueError(f"actor {self.name!r}: repeated argument name")
        return self


class GoalConstraint(_Strict):
    field: Identifier
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null"]
    value: TypedValue | None = None

    @model_validator(mode="after")
    def _value_matches_operator(self) -> GoalConstraint:
        if self.op in {"is_null", "not_null"} and self.value is not None:
            raise ValueError(f"{self.op} takes no value")
        if self.op not in {"is_null", "not_null"} and self.value is None:
            raise ValueError(f"{self.op} needs a value")
        return self


class StateGoal(_Strict):
    """A logical statement about the starting state. Never seed rows, and never a requirement or oracle."""

    entity: Identifier
    label: Identifier
    count: StrictInt = Field(default=1, ge=0, le=MAX_GOAL_COUNT)
    constraints: list[GoalConstraint] = Field(default_factory=list, max_length=MAX_GOAL_CONSTRAINTS)


class SourceRef(_Strict):
    """Lines in a registered source file the proposal points to, with an inert explanation."""

    path: str = Field(max_length=200, pattern=SOURCE_PATH)
    start_line: StrictInt = Field(ge=1)
    end_line: StrictInt = Field(ge=1)
    claim: str = Field(max_length=400, pattern=SAFE_TEXT)

    @model_validator(mode="after")
    def _ordered(self) -> SourceRef:
        if self.end_line < self.start_line:
            raise ValueError("end_line comes before start_line")
        if any(part in {"", ".", ".."} for part in self.path.split("/")):  # also excluded by the pattern
            raise ValueError("source path must be relative, inside the registered tree")
        return self


class WorkloadSpec(_Strict):
    schema_version: Literal[2]
    name: Identifier
    application: Identifier
    provenance: Provenance
    actors: list[ActorSpec] = Field(min_length=MIN_ACTORS, max_length=MAX_ACTORS)
    state_goals: list[StateGoal] = Field(default_factory=list, max_length=MAX_STATE_GOALS)
    source_refs: list[SourceRef] = Field(default_factory=list, max_length=MAX_SOURCE_REFS)
    rationale: str = Field(default="", max_length=800, pattern=SAFE_TEXT)  # one paragraph, no line breaks

    @model_validator(mode="after")
    def _whole_document(self) -> WorkloadSpec:
        actors = [a.name for a in self.actors]
        if len(set(actors)) != len(actors):
            raise ValueError("repeated actor name")
        labels = [g.label for g in self.state_goals]
        if len(set(labels)) != len(labels):
            raise ValueError("repeated state-goal label")
        if sum(g.count for g in self.state_goals) > MAX_TOTAL_GOAL_ROWS:
            raise ValueError(f"state goals ask for more than {MAX_TOTAL_GOAL_ROWS} rows in total")
        return self


# ------------------------------------------------------------------ parsing, versions and canonical form


class UnsupportedSchemaVersion(ValueError):
    pass


class SpecRejected(ValueError):
    """The document isn't a valid specification. ``reasons`` lists every structural problem found."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _reasons(exc: Exception) -> list[str]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors()]
    return [str(exc)]


def _version_of(document: Any) -> int:
    if not isinstance(document, dict):
        raise SpecRejected(["the document must be a JSON object"])
    if "schema_version" not in document:
        raise UnsupportedSchemaVersion("schema_version is required, and must be stated explicitly")
    version = document["schema_version"]
    if type(version) is not int or version not in SUPPORTED_VERSIONS:  # rejects True, "2" and 2.0
        raise UnsupportedSchemaVersion(f"unsupported schema_version {version!r}; supported: {SUPPORTED_VERSIONS}")
    return version


def parse_workload(document: Any, *, v1_provenance: Provenance | None = None) -> WorkloadSpec:
    """Validate a decoded JSON document of either version and return version 2.

    A version 1 document carries no provenance, so the caller must say where it came from. Nothing is guessed.
    The text policy is checked first, over every key and string, so malformed text is a ``SpecRejected``.
    """
    problems = text_problems(document)
    if problems:
        raise SpecRejected(problems)
    version = _version_of(document)
    size = document_size(document)
    if size > MAX_DOCUMENT_BYTES:
        raise SpecRejected([f"document is {size} bytes, more than {MAX_DOCUMENT_BYTES}"])
    try:
        if version == 1:
            if v1_provenance is None:
                raise SpecRejected(["a version 1 document needs its provenance stated by the caller"])
            from .workload import WorkloadProposal

            return upgrade_v1(WorkloadProposal.model_validate(document), v1_provenance)
        return WorkloadSpec.model_validate(document)
    except SpecRejected:
        raise
    except ValueError as exc:
        raise SpecRejected(_reasons(exc)) from exc


def document_size(document: Any) -> int:
    """Bytes of the document as compact UTF-8 JSON: the same measure however the original text was written."""
    try:
        text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_no_default)
    except RecursionError as exc:
        raise SpecRejected(["the document is nested too deeply to measure"]) from exc
    except (TypeError, ValueError) as exc:
        raise SpecRejected([f"the document can't be measured as JSON: {exc}"]) from exc
    return len(text.encode("utf-8"))


def _no_default(value: Any) -> Any:
    raise TypeError(f"{type(value).__name__} isn't a JSON value")


def parse_workload_json(text: str, **kwargs: Any) -> WorkloadSpec:
    if not isinstance(text, str):
        raise SpecRejected(["the document must be given as text"])
    # A coarse bound before decoding, so a huge text is never parsed. The exact limit applies to the decoded document.
    # "surrogatepass" only measures: a lone surrogate in the text is refused after decoding, by the text policy.
    if len(text.encode("utf-8", "surrogatepass")) > 6 * MAX_DOCUMENT_BYTES:
        raise SpecRejected([f"document text is far larger than {MAX_DOCUMENT_BYTES} bytes"])
    try:
        document = json.loads(text, parse_constant=_refuse_constant, object_pairs_hook=_refuse_duplicate_keys)
    except RecursionError as exc:
        raise SpecRejected(["the JSON text is nested too deeply to decode"]) from exc
    except ValueError as exc:
        raise SpecRejected([f"not valid JSON: {exc}"]) from exc
    return parse_workload(document, **kwargs)


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} isn't allowed")


def _refuse_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON parsers usually keep the last of two equal keys. Here that ambiguity is refused."""
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"repeated key {safe_repr(repeated[0], 40)}")
    return dict(pairs)


def _typed(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": value}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "str", "value": value}


def upgrade_v1(proposal: WorkloadProposal, provenance: Provenance) -> WorkloadSpec:
    """Version 1 to version 2. Strict: anything version 2 can't express faithfully is refused, not rewritten.

    Version 1 allowed dots, hyphens and capitals in identifiers, and untyped values. Values get the type of the JSON
    value they already had. An identifier that doesn't meet version 2's rule is refused rather than renamed, because a
    rename could point at a different registered entrypoint.
    """
    document = {
        "schema_version": 2,
        "name": proposal.name,
        "application": proposal.application,
        "provenance": provenance.model_dump(),
        "rationale": proposal.rationale,
        "actors": [
            {
                "name": actor.name, "entrypoint": actor.entrypoint, "application_version": actor.application_version,
                "role": actor.role,
                "arguments": [{"name": a.name, "value": _typed(a.value)} for a in actor.arguments],
            }
            for actor in proposal.actors
        ],
        "state_goals": [
            {
                "entity": goal.entity, "label": goal.label, "count": goal.count,
                "constraints": [
                    {"field": f.field, "op": f.op,
                     **({} if f.op in {"is_null", "not_null"} else {"value": _typed(f.value)})}
                    for f in goal.fields
                ],
            }
            for goal in proposal.seed_goals
        ],
        "source_refs": [
            {"path": c.path, "start_line": c.start_line, "end_line": c.end_line, "claim": c.claim}
            for c in proposal.constraints
        ],
    }
    try:
        spec = WorkloadSpec.model_validate(document)
    except ValueError as exc:
        raise SpecRejected(["can't be upgraded to version 2 without changing its meaning", *_reasons(exc)]) from exc
    # Typed values make the version 2 form larger. It must fit version 2's own limit, or later stages, which
    # re-validate the version 2 form, would refuse what this accepted.
    size = document_size(spec.model_dump(mode="json", exclude_defaults=True))
    if size > MAX_DOCUMENT_BYTES:
        raise SpecRejected([f"upgraded to version 2 it would be {size} bytes, more than {MAX_DOCUMENT_BYTES}"])
    return spec


def canonical(spec: WorkloadSpec) -> dict[str, Any]:
    """A normalized form: only collections whose order carries no meaning are sorted.

    * Actors: the engine refers to actors by name, and its schedules choose among them by name.
    * Arguments: they become keyword arguments.
    * State goals: a set of facts about the starting state; construction decides physical order itself.
    * Constraints within a goal: a conjunction.
    * Source references: citations.

    Nothing else is reordered, and no value is changed.
    """
    data = spec.model_dump(mode="json", exclude_none=True)
    for actor in data["actors"]:
        actor["arguments"].sort(key=lambda a: a["name"])
    data["actors"].sort(key=lambda a: a["name"])
    for goal in data["state_goals"]:
        goal["constraints"].sort(key=lambda c: (c["field"], c["op"], json.dumps(c.get("value"), sort_keys=True)))
    data["state_goals"].sort(key=lambda g: g["label"])
    data["source_refs"].sort(key=lambda r: (r["path"], r["start_line"], r["end_line"], r["claim"]))
    return data


def canonical_json(spec: WorkloadSpec) -> str:
    """Deterministic bytes: sorted keys, no insignificant whitespace, UTF-8 kept as text."""
    return json.dumps(canonical(spec), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(spec: WorkloadSpec) -> str:
    """A full-record digest: every field, including provenance, rationale and source references.

    It identifies a document. It isn't an execution-semantic digest, which would cover only what changes execution
    (application, actors, entrypoints, versions, roles, arguments and state goals), so two proposals that differ only
    in their explanation would share it. That digest is a later step, and must not be inferred from this one.
    """
    return hashlib.sha256(canonical_json(spec).encode()).hexdigest()


def json_schema() -> dict[str, Any]:
    """The frozen JSON Schema. Necessary, not sufficient: uniqueness and cross-field rules live in the validators."""
    schema = WorkloadSpec.model_json_schema(mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "x-status": "frozen",
        "x-not-enforced-by-schema": [
            "unique actor names, argument names and state-goal labels",
            "is_null and not_null take no value; other operators need one",
            "end_line is not before start_line",
            "float values are written with a fraction or exponent, and aren't negative zero",
            f"state goals total at most {MAX_TOTAL_GOAL_ROWS} rows",
            f"the whole document is at most {MAX_DOCUMENT_BYTES} bytes as compact UTF-8 JSON",
            "no repeated keys in the JSON text",
            "no NaN or Infinity anywhere",
            "no unpaired surrogate code points (U+D800 to U+DFFF) in any key or string; the text pattern covers the"
            " rest of the text policy but can't state this exactly",
            "object keys follow the same text policy as string values",
        ],
        **schema,
    }

