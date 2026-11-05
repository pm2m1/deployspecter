"""EXPERIMENTAL corroboration, offline: how logged bind values are compared with the trace's sanitized values."""

from __future__ import annotations

import hashlib

import pytest

from deployspecter.experimental.corroborate import _array_elements, _value_matches, bind


def digest(s: str) -> dict:
    return {"str": len(s), "sha256": hashlib.sha256(s.encode()).hexdigest()[:12]}


def test_array_literals_are_split_like_postgresql_prints_them():
    assert _array_elements("{1,2,NULL}") == ["1", "2", None]
    assert _array_elements('{"a b","c\\"d",e}') == ["a b", 'c"d', "e"]
    assert _array_elements("{}") == []
    assert _array_elements("{{1}}") is None and _array_elements("1") is None


@pytest.mark.parametrize("logged, want, verdict", [
    ("'{1,2}'", [1, 2], True), ("'{1,3}'", [1, 2], False), ("'{1}'", [1, 2], False),
    ("'{ds_job,\"x y\"}'", [digest("ds_job"), digest("x y")], True), ("'{NULL}'", [None], True),
    ("'{{1}}'", [[1]], None),  # nested: not compared, counted as unchecked
    ("'\\x6869'", {"bytes": 2, "sha256": hashlib.sha256(b"hi").hexdigest()[:12]}, True),
    ("'\\x6870'", {"bytes": 2, "sha256": hashlib.sha256(b"hi").hexdigest()[:12]}, False),
    ("'7'", 7, True), ("NULL", None, True), ("'t'", True, True), ("'2026-11-05'", {"type": "date"}, None),
])
def test_logged_values_are_compared_exactly_or_counted_unchecked(logged, want, verdict):
    assert _value_matches(logged, want) is verdict


def test_named_and_positional_placeholders_map_to_server_numbers():
    assert bind("SELECT %(a)s, %(b)s, %(a)s", {"a": 1, "b": 2}) == ("SELECT $1, $2, $1", [1, 2])
    assert bind("SELECT %s LIKE 'x%%'", [3]) == ("SELECT $1 LIKE 'x%'", [3])
    assert bind("SELECT 1 %% 2", None) == ("SELECT 1 %% 2", [])  # no parameters: psycopg sends it unchanged
