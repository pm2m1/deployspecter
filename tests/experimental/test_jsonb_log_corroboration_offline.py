"""EXPERIMENTAL, offline: jsonb-typed parameters that PostgreSQL logs normalized are compared exactly under jsonb
rules; nothing else changes. Vectors: PostgreSQL 16's own jsonb output (fixtures/pg16_jsonb_out.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deployspecter.experimental import corroborate as co
from deployspecter.experimental import jsonb_text as jt
from deployspecter.experimental import stepping as st

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "pg16_jsonb_out.json").read_text())


@pytest.mark.parametrize("v", FIXTURE["vectors"], ids=lambda v: repr(v["input"])[:40])
def test_normalization_is_postgresqls_own(v):
    assert jt.normalize(v["input"]) == v["output"]


def lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@pytest.mark.parametrize("sent", ['{"b": 1, "a": 2}', '{"a":1,"a":2}', '{"n": 1.50e1, "m": 1e-3, "z": -0}',
                                  '[3, {"z": true, "y": null}]', '{"é": 1, "e": 2}'])
def test_a_str_logged_jsonb_normalized_matches(sent):
    want = st.sanitize(sent)
    assert "jsonb_sha256" in want
    assert co._value_matches(lit(jt.normalize(sent)), want) is True
    assert co._value_matches(lit(sent), want) is True  # verbatim (text or json parameter), as before


def test_a_different_value_or_a_non_fixpoint_is_still_a_mismatch():
    want = st.sanitize('{"b": 1, "a": 2}')
    assert co._value_matches(lit('{"a": 2, "b": 2}'), want) is False  # another value
    assert co._value_matches(lit('{"a": 2, "b": 1.0}'), want) is False  # 1 vs 1.0: numeric scale differs
    assert co._value_matches(lit('{"b":1,"a":2}'), want) is False  # not jsonb output, and not the sent text
    assert co._value_matches(lit('{"a": "2", "b": 1}'), want) is False  # a string where a number was sent
    assert co._value_matches("NULL", want) is False


def test_strings_that_need_nothing_new_keep_their_old_trace_form():
    for s in ("hello", "5", " 5 ", '"x"', "1e2", '{"a": 2, "b": 1}', "[1, 2]", "{not json}", ""):
        assert set(st.sanitize(s)) == {"str", "sha256"}, s
    # a scalar JSON text normalized by another type's output (' 5 ' logged as the integer 5) is not accepted
    assert co._value_matches(lit("5"), st.sanitize(" 5 ")) is False
