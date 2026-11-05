"""EXPERIMENTAL, offline: exact comparison of composite-array and float8 parameters against what PostgreSQL 16 logs.

The fixture holds parameter text captured verbatim from the server's csvlog (`fixtures/pg16_typed_params.json`), next
to the trace's sanitized form of the same Python values. Every genuine pair must compare True; every mutation must
compare False; anything malformed must be None (UNVERIFIED, counted, never accepted).
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from deployspecter.experimental.corroborate import _array_elements, _record_fields, _value_matches

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "pg16_typed_params.json").read_text())
VECTORS = {v["name"]: v for v in FIXTURE["vectors"]}


@pytest.mark.parametrize("name", sorted(VECTORS))
def test_every_captured_value_compares_exactly(name):
    v = VECTORS[name]
    assert _value_matches(v["logged"], v["sanitized"]) is True


def mutations(rec_array):
    """Each single-field change of every record: an int, a string digest, NULL<->value, json, a timestamp."""
    for i, rec in enumerate(rec_array):
        for k, f in enumerate(rec):
            m = copy.deepcopy(rec_array)
            if isinstance(f, bool) or f is None:
                m[i][k] = {"str": 0, "sha256": "e3b0c44298fc"}
            elif isinstance(f, int):
                m[i][k] = f + 1
            elif "str" in f:
                m[i][k] = {"str": f["str"], "sha256": "000000000000"}
            elif "json_sha256" in f:
                m[i][k] = {"json_sha256": "000000000000"}
            elif "timestamptz_us" in f:
                m[i][k] = {"timestamptz_us": f["timestamptz_us"] + 1}
            yield f"rec{i}.field{k}", m


@pytest.mark.parametrize("name", ["procrastinate_defer_shape", "adversarial_records"])
def test_any_single_field_change_in_a_record_is_a_mismatch(name):
    v = VECTORS[name]
    for label, m in mutations(v["sanitized"]):
        assert _value_matches(v["logged"], m) is False, label


def test_record_arity_and_count_must_match():
    v = VECTORS["adversarial_records"]
    short = [rec[:-1] for rec in v["sanitized"]]
    assert _value_matches(v["logged"], short) is False
    assert _value_matches(v["logged"], v["sanitized"][:1]) is False


def test_null_and_the_string_null_are_different():
    v = VECTORS["text_array_with_braces"]
    swapped = copy.deepcopy(v["sanitized"])
    swapped[2], swapped[3] = swapped[3], swapped[2]
    assert _value_matches(v["logged"], swapped) is False
    assert _record_fields('(,NULL,"")') == [None, "NULL", ""]  # record_in: only an empty unquoted field is NULL


@pytest.mark.parametrize("logged", [
    "'{\"(a,b\"}'",          # an unterminated record quote
    "'{{1,2},{3,4}}'",       # a nested array
    "'{\"(a,(b))\"}'",       # an unquoted parenthesis inside a record
    "'[1:2]={\"(a)\",\"(b)\"}'",  # a dimension decoration
    "'{\"(a)\",\"(b)\"'",    # an unterminated array
    "'(a,b)'",               # a record where an array is expected
])
def test_malformed_or_unknown_literals_are_never_accepted(logged):
    assert _value_matches(logged, [[{"str": 1, "sha256": "ca978112ca1b"}], [{"str": 1, "sha256": "3e23e8160039"}]]) \
        in (None, False)


def test_float_comparison_is_bit_exact():
    nextup = math.nextafter(30.0, math.inf)
    assert _value_matches("'30'", 30.0) is True and _value_matches("'30'", nextup) is False
    assert _value_matches("'-0'", -0.0) is True and _value_matches("'-0'", 0.0) is False
    assert _value_matches("'0'", -0.0) is False
    assert _value_matches("'NaN'", float("nan")) is True and _value_matches("'NaN'", 0.0) is False
    assert _value_matches("'Infinity'", float("-inf")) is False
    assert _value_matches("'abc'", 1.0) is False and _value_matches("NULL", 1.0) is False
    assert _value_matches("'0.1'", 0.1) is True and _value_matches("'0.10000000000000002'", 0.1) is False


def test_the_old_one_dimensional_arrays_still_compare_as_before():
    assert _array_elements('{a,"b c",NULL,"NULL"}') == ["a", "b c", None, "NULL"]
    assert _array_elements("{}") == []
    assert _array_elements("{a,,b}") is None  # an empty unquoted element isn't valid array syntax
