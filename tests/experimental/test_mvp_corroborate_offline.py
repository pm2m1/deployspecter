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


# ------------------------------------------------------------------ typed date/time, interval and UUID values

import datetime as dt  # noqa: E402
import uuid  # noqa: E402

from deployspecter.experimental.stepping import sanitize  # noqa: E402

UTC = dt.UTC
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INSTANT = dt.datetime(2026, 11, 5, 8, 30, 59, 357123, tzinfo=UTC)


@pytest.mark.parametrize("value, logged, verdict", [
    # timestamptz: the server prints the instant in the session's TimeZone; equal instants match
    (INSTANT, "'2026-11-05 08:30:59.357123+00'", True),
    (INSTANT, "'2026-11-05 14:00:59.357123+05:30'", True),
    (INSTANT.astimezone(IST), "'2026-11-05 08:30:59.357123+00'", True),
    (INSTANT, "'2026-11-05 08:30:59.357124+00'", False),  # one microsecond apart
    (INSTANT, "'2026-11-05 08:30:59.357123+05:30'", False),  # same wall clock, a different instant
    (INSTANT, "'2026-11-05 08:30:59.357123'", False),  # no zone: a timestamp, not a timestamptz
    (INSTANT, "'infinity'", None),  # not reconciled: UNVERIFIED, never accepted
    (INSTANT, "NULL", False),
    # timestamp: a wall-clock value, every field equal and no zone
    (dt.datetime(2026, 11, 5, 8, 30, 59, 7), "'2026-11-05 08:30:59.000007'", True),
    (dt.datetime(2026, 11, 5, 8, 30, 59, 7), "'2026-11-05 08:30:59.000008'", False),
    (dt.datetime(2026, 11, 5, 8, 30, 59, 7), "'2026-11-05 08:30:59.000007+00'", False),
    (dt.datetime(2026, 11, 5, 8, 30, 59), "'2026-11-05 08:30:59'", True),
    # date and time
    (dt.date(2026, 11, 5), "'2026-11-05'", True), (dt.date(2026, 11, 5), "'2026-11-06'", False),
    (dt.time(8, 30, 59, 357000), "'08:30:59.357'", True), (dt.time(8, 30, 59, 357000), "'08:30:59.358'", False),
    (dt.time(8, 30, tzinfo=dt.timezone(dt.timedelta(hours=-3))), "'08:30:00-03'", True),
    (dt.time(8, 30, tzinfo=dt.timezone(dt.timedelta(hours=-3))), "'11:30:00+00'", False),  # timetz keeps its offset
    # interval, component-wise as PostgreSQL keeps it
    (dt.timedelta(seconds=30), "'00:00:30'", True),
    (dt.timedelta(days=1, seconds=3723, microseconds=5), "'1 day 01:02:03.000005'", True),
    (dt.timedelta(microseconds=-1), "'-1 days +23:59:59.999999'", True),
    (dt.timedelta(days=1), "'24:00:00'", False),  # equal length, a different interval
    (dt.timedelta(days=30), "'1 mon'", None),  # months: not reconciled
    (dt.timedelta(seconds=30), "'PT30S'", None),  # another IntervalStyle: not reconciled
    (dt.timedelta(0), "'00:00:00'", True),
    # uuid: canonical text, by digest
    (uuid.UUID("03fbb261-c406-2a37-a654-f9643fe1ba94"), "'03fbb261-c406-2a37-a654-f9643fe1ba94'", True),
    (uuid.UUID("03fbb261-c406-2a37-a654-f9643fe1ba94"), "'03fbb261-c406-2a37-a654-f9643fe1ba95'", False),
    # arrays of them
    ([dt.timedelta(0), dt.timedelta(seconds=90)], "'{00:00:00,00:01:30}'", True),
    ([dt.timedelta(days=1)], "'{\"1 day\"}'", True),
    ([dt.timedelta(0), dt.timedelta(seconds=91)], "'{00:00:00,00:01:30}'", False),
])
def test_date_time_interval_and_uuid_values_are_compared_exactly(value, logged, verdict):
    assert _value_matches(logged, sanitize(value)) is verdict


def test_typed_values_keep_no_raw_uuid_and_exact_instants():
    s = sanitize(uuid.UUID("03fbb261-c406-2a37-a654-f9643fe1ba94"))
    assert set(s) == {"uuid_sha256"} and "03fbb261" not in str(s)
    assert sanitize(INSTANT) == sanitize(INSTANT.astimezone(IST))  # one instant, one value
    assert sanitize(dt.datetime(2026, 1, 1)) != sanitize(dt.datetime(2026, 1, 1, tzinfo=UTC))


from psycopg.types.json import Json, Jsonb  # noqa: E402


@pytest.mark.parametrize("value, logged, verdict", [
    (Json({"a": 1, "b": [1, 2]}), """'{"a": 1, "b": [1, 2]}'""", True),
    (Jsonb({"b": [1, 2], "a": 1}), """'{"a": 1, "b": [1, 2]}'""", True),  # jsonb's normalized output, same value
    (Json({"a": 1}), """'{"a": 2}'""", False),
    (Json({"a": 1}), """'{"a": 1, "x": null}'""", False),
    (Json({"q": "it's"}), """'{"q": "it''s"}'""", True),
    (Json({"a": 1}), "NULL", False), (Json({"a": 1}), "'not json'", False),
])
def test_json_values_are_compared_by_value(value, logged, verdict):
    assert _value_matches(logged, sanitize(value)) is verdict


U = "03fbb261-c406-2a37-a654-f9643fe1ba94"


@pytest.mark.parametrize("sent, logged, verdict", [
    (U.replace("-", ""), f"'{U}'", True),  # a 32-hex string the server coerced to uuid
    (U, f"'{U}'", True),
    (U.replace("-", "")[:-1] + "5", f"'{U}'", None),  # uuid-shaped but not the same: UNVERIFIED, never accepted
    ("hello", f"'{U}'", None),
    ("hello", "'world'", False),  # an ordinary string that differs: a mismatch
])
def test_strings_coerced_to_uuid_by_the_server(sent, logged, verdict):
    assert _value_matches(logged, sanitize(sent)) is verdict
