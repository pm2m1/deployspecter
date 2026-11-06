"""EXPERIMENTAL ds-p9x/1 jsonb type contract (version ``jsonb/1``), for Phase 9B construction.

A requested jsonb value is JSON **text**. It is accepted only if all of these hold:
* **Syntax:** it parses as one JSON value under RFC 8259, with nothing left over.
* **Object keys:** no object repeats a key. PostgreSQL's jsonb keeps the last duplicate silently, so the writer's
  intended meaning is unknowable, and the value is refused.
* **Numbers:** only integer literals (no fraction, no exponent) with ``|n| <= 2**53 - 1`` are allowed. The readback
  paths (``to_jsonb`` through psycopg, Python's ``json``) turn non-integer numbers into binary floats, so a fraction
  or exponent could not be verified exactly. Such numbers are **refused**, not rounded.
* **Non-finite numbers:** ``NaN``, ``Infinity`` and ``-Infinity`` are not JSON. They are refused.
* **Strings:** ``\\u0000`` and unpaired surrogates are refused (PostgreSQL's jsonb rejects them); anything else is
  kept exactly.
* **Size:** at most 64 KiB of text, nested at most 32 deep.

**Normalization.** Applied only where jsonb semantics justify it: insignificant whitespace is dropped, and keys are
ordered by (UTF-8 length, bytes), PostgreSQL's jsonb storage order. ``canonical()`` computes that text. The writer
then cross-checks it against PostgreSQL's own ``%s::jsonb::text`` for the bound value; a disagreement is a
refusal.

**SQL NULL and JSON null** are different values. SQL NULL is the typed value ``null``, with no JSON text. JSON
null is the jsonb text ``null``. ``to_jsonb(row)`` shows both as JSON null, so Phase 9B's row attribution can't
tell them apart. ``readback`` decides it on the database instead, with ``IS NULL`` and ``jsonb_typeof``.

**Never executable.** The value only ever travels as a bound parameter, cast to jsonb in SQL. It is never
interpolated into a statement.
"""

from __future__ import annotations

import json
import re
from typing import Any

VERSION = "jsonb/1"
MAX_TEXT = 64 * 1024
MAX_DEPTH = 32
MAX_INT = 2**53 - 1
_INT_LITERAL = re.compile(r"-?(0|[1-9][0-9]*)\Z")


class JsonbRefused(ValueError):
    """A jsonb value outside contract jsonb/1. Raised before any construction."""


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for k, v in pairs:
        if k in seen:
            raise JsonbRefused(f"duplicate object key {k!r}: jsonb would keep only the last, so the meaning is unknown")
        seen[k] = v
    return seen


def _refuse_float(text: str) -> Any:
    raise JsonbRefused(f"non-integer JSON number {text!r}: it can't be verified exactly through the readback paths")


def _int(text: str) -> int:
    if not _INT_LITERAL.match(text):
        raise JsonbRefused(f"number {text!r} isn't a plain integer literal")
    n = int(text)
    if abs(n) > MAX_INT:
        raise JsonbRefused(f"integer {text} exceeds 2**53 - 1")
    return n


def _refuse_constant(name: str) -> Any:
    raise JsonbRefused(f"{name} is not JSON")


def _check_strings_and_depth(v: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise JsonbRefused(f"nested deeper than {MAX_DEPTH}")
    if isinstance(v, str):
        if "\x00" in v:
            raise JsonbRefused("\\u0000 in a string: PostgreSQL's jsonb rejects it")
        try:
            v.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise JsonbRefused("an unpaired surrogate in a string") from exc
    elif isinstance(v, dict):
        for k, x in v.items():
            _check_strings_and_depth(k, depth + 1)
            _check_strings_and_depth(x, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _check_strings_and_depth(x, depth + 1)


def parse(text: Any) -> Any:
    """The JSON value, or JsonbRefused. ``text`` must be a str of JSON text."""
    if not isinstance(text, str):
        raise JsonbRefused(f"jsonb text must be a str, not {type(text).__name__}")
    if len(text.encode("utf-8", "surrogatepass")) > MAX_TEXT:
        raise JsonbRefused(f"longer than {MAX_TEXT} bytes")
    try:
        v = json.loads(text, object_pairs_hook=_no_duplicates, parse_float=_refuse_float, parse_int=_int,
                       parse_constant=_refuse_constant)
    except JsonbRefused:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise JsonbRefused(f"malformed JSON: {exc}") from exc
    _check_strings_and_depth(v)
    return v


def _key(k: str) -> tuple[int, bytes]:
    b = k.encode("utf-8")
    return (len(b), b)


def _string(s: str) -> str:
    # PostgreSQL's escape_json: \" \\ \b \f \n \r \t, other C0 controls as \u00xx, everything else verbatim
    out = []
    for ch in s:
        o = ord(ch)
        out.append({'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r",
                    "\t": "\\t"}.get(ch) or (f"\\u{o:04x}" if o < 0x20 else ch))
    return '"' + "".join(out) + '"'


def canonical(v: Any) -> str:
    """PostgreSQL's jsonb output text for a parsed jsonb/1 value."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, list):
        return "[" + ", ".join(canonical(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_string(k)}: {canonical(v[k])}" for k in sorted(v, key=_key)) + "}"
    raise JsonbRefused(f"not a jsonb/1 value: {type(v).__name__}")


def equal(a: Any, b: Any) -> bool:
    """Type-exact JSON equality. bool, int and float are never equal to one another; floats never match under
    jsonb/1 (a float in a readback means a value outside the contract); objects compare by key set."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, float) or isinstance(b, float):
        return False
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return False


def typeof(v: Any) -> str:
    """``jsonb_typeof`` of a parsed value."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "number"
    return {str: "string", list: "array", dict: "object"}[type(v)]


def accept(text: str, pg_canonical: str | None = None) -> str:
    """The canonical text of an accepted value. With ``pg_canonical`` (PostgreSQL's own ``::jsonb::text`` of the bound
    text), the two normalizations must agree exactly, otherwise the value is refused as ambiguous."""
    v = parse(text)
    c = canonical(v)
    if pg_canonical is not None and pg_canonical != c:
        raise JsonbRefused(f"PostgreSQL normalizes this value to {pg_canonical!r}, jsonb/1 to {c!r}")
    return c


def readback(requested: str | None, is_null: bool, text: str | None, pg_type: str | None) -> list[str]:
    """Compare one cell read back from the database. ``requested`` None means SQL NULL; otherwise canonical text.
    Returns the problems (empty = equal under jsonb/1)."""
    if requested is None:
        return [] if is_null else [f"SQL NULL requested, the column holds jsonb {text!r}"]
    if is_null:
        return [f"jsonb {requested!r} requested, the column is SQL NULL"]
    want = parse(requested)
    try:
        got = parse(text)
    except JsonbRefused as exc:
        return [f"the stored value is outside jsonb/1: {exc}"]
    p = []
    if text != requested:
        p.append(f"stored text {text!r} differs from the canonical {requested!r}")
    if pg_type != typeof(want):
        p.append(f"jsonb_typeof is {pg_type}, requested {typeof(want)}")
    if not equal(want, got):
        p.append("the stored value isn't equal to the requested one")
    return p
