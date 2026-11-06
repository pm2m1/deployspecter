"""EXPERIMENTAL: PostgreSQL's jsonb text normalization, reproduced exactly, for server-log corroboration.

PostgreSQL 16 logs a bind parameter through its type's output function. When the type is jsonb, the logged text
is ``jsonb_in`` then ``jsonb_out`` of what the client sent (``corroboration/jsonb_logging_probe_1``):
* keys ordered by (byte length, bytes);
* a duplicate key keeps its last value;
* insignificant whitespace dropped, with ", " and ": " separators;
* numbers printed by ``numeric_out``: ``1e2`` → ``100``, ``1.50e1`` → ``15.0``, ``-0`` → ``0``;
* strings printed with jsonb's escapes (``\\uXXXX`` input is decoded).

``normalize(text)`` returns that output text, or None when ``jsonb_in`` would reject the text (malformed, NaN or
Infinity, ``\\u0000``, unpaired surrogates, unescaped control characters).
Checked against PostgreSQL's own output: ``tests/experimental/fixtures/pg16_jsonb_out.json``.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any


class _Reject(ValueError):
    pass


def _reject_constant(name: str) -> Any:
    raise _Reject(name)


def _number(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise _Reject(text) from exc


def _numeric_out(d: Decimal) -> str:
    s = format(d, "f")
    return s[1:] if s.startswith("-") and d == 0 else s  # numeric has no negative zero


def _string(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if o == 0 or 0xD800 <= o <= 0xDFFF:
            raise _Reject("\\u0000 or an unpaired surrogate")
        out.append({'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r",
                    "\t": "\\t"}.get(ch) or (f"\\u{o:04x}" if o < 0x20 else ch))
    return '"' + "".join(out) + '"'


def _key(k: str) -> tuple[int, bytes]:
    b = k.encode("utf-8")
    return (len(b), b)


def _out(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, Decimal):
        return _numeric_out(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, list):
        return "[" + ", ".join(_out(x) for x in v) + "]"
    return "{" + ", ".join(f"{_string(k)}: {_out(v[k])}" for k in sorted(v, key=_key)) + "}"


def normalize(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    try:
        v = json.loads(text, object_pairs_hook=dict, parse_int=_number, parse_float=_number,
                       parse_constant=_reject_constant)  # dict(pairs): a duplicate key keeps its last value
        return _out(v)
    except (_Reject, ValueError, RecursionError):
        return None


def is_container(text: str) -> bool:
    """A JSON object or array at top level: the only shapes whose normalization the corroborator relies on (scalar
    JSON texts overlap other types' output, e.g. ' 5 ' logged as an integer)."""
    n = normalize(text)
    return n is not None and n[:1] in ("{", "[")
