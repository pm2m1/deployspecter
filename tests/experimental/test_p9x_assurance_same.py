"""EXPERIMENTAL ds-p9x/1.1 assurance A1: the ``_same`` substitution.

* **Non-jsonb equivalence (property-based):** for every pair of values that the constructor can compare without a
  requested jsonb value, ``same_v1`` returns exactly what the original ``_same`` returns. That covers ints, bools,
  floats including -0.0, NaN and infinities, None, strings (numeric-looking, uuid-, timestamp- and JSON-looking),
  Decimal, and the lists and dicts ``to_jsonb`` produces for array and record columns.
* **jsonb semantics:** when a requested value is a ``JsonbText``, the result is ``jsonb_v1.equal`` against its
  parsed value, in either argument position.
* **The v1 defect, fixed in 1.1:** a stored text equal to a jsonb text is compared by the original rule.
"""

from __future__ import annotations

import decimal
import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from deployspecter.experimental.phase9b_ext import jsonb_v1, loader
from deployspecter.models import ColumnValue, SeedRow

M = loader.load()
ORIGINAL = loader._original_same
J = loader.JsonbText

numeric_text = st.from_regex(r"\A-?[0-9]{1,20}(\.[0-9]{1,10})?([eE][+-]?[0-9]{1,3})?\Z")
json_looking = st.sampled_from(['{"n": 0}', "{}", "[]", "null", "true", "false", '"x"', "0", "1", "-0", "1.0", "NaN",
                                "Infinity", '{"a": 1}', "[1, 2]"])
special = st.sampled_from(["", " ", "nan", "inf", "-inf", "Infinity", "1_000", "0x10", "١٢٣", "２", "None", "True",
                           "2026-01-01T00:00:00+00:00", "2026-01-01 00:00:00", "6f1c2a4e-9d3b-5e7f-8a1b-2c3d4e5f6a7b"])
scalars = st.one_of(
    st.none(), st.booleans(), st.integers(), st.integers(min_value=-(2**70), max_value=2**70),
    st.floats(allow_nan=True, allow_infinity=True), st.sampled_from([0.0, -0.0, math.inf, -math.inf, math.nan]),
    st.decimals(allow_nan=True, allow_infinity=True), st.text(max_size=20), numeric_text, json_looking, special,
    st.uuids().map(str))
values = st.recursive(scalars, lambda c: st.one_of(st.lists(c, max_size=4),
                                                   st.dictionaries(st.text(max_size=5), c, max_size=4)), max_leaves=12)


def _same_or_both_raise(a, b):
    try:
        want = ("ok", ORIGINAL(a, b))
    except Exception as exc:  # noqa: BLE001 - equivalence includes raising the same way
        want = ("raise", type(exc))
    try:
        got = ("ok", loader.same_v1(a, b))
    except Exception as exc:  # noqa: BLE001
        got = ("raise", type(exc))
    return want, got


@settings(max_examples=6000, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(values, values)
def test_non_jsonb_comparisons_are_exactly_the_original(a, b):
    want, got = _same_or_both_raise(a, b)
    assert want == got


@pytest.mark.parametrize("a,b", [
    (0.0, -0.0), (math.nan, math.nan), (math.inf, "inf"), (1, True), (0, False), (True, 1), (None, "None"),
    (decimal.Decimal("1.0"), 1), ("1.0", 1), ("1e2", 100), ("0x10", 16), ('{"n": 0}', '{"n": 0}'),
    ({"n": 0}, '{"n": 0}'), ([1, 2], "[1, 2]"), ("null", None), ("", None)])
def test_named_non_jsonb_edge_cases_are_the_original(a, b):
    assert loader.same_v1(a, b) == ORIGINAL(a, b) and loader.same_v1(b, a) == ORIGINAL(b, a)


def test_the_v1_defect_is_gone_stored_text_equal_to_a_jsonb_text():
    # state_constructor.py:489 compares stored now[c] with stored before[c]; both plain strings from a text column
    stored = '{"n": 0}'
    assert loader.same_v1(stored, '{"n": 0}') is True  # the original rule: equal text
    assert loader.same_v1('"x"', "x") is False  # a text column holding '"x"' vs requested plain 'x'


jsonb_values = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(min_value=-(2**53 - 1), max_value=2**53 - 1),
              st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=8)),
    lambda c: st.one_of(st.lists(c, max_size=4),
                        st.dictionaries(st.text(alphabet=st.characters(blacklist_categories=("Cs",),
                                                                       blacklist_characters="\x00"), max_size=4),
                                        c, max_size=4)), max_leaves=10)


@settings(max_examples=3000, deadline=None)
@given(jsonb_values, values)
def test_a_requested_jsonb_value_compares_by_jsonb_v1_in_either_position(v, stored):
    text = jsonb_v1.canonical(v)
    req = J(text)
    assert req.parsed == v or (isinstance(v, float) and math.isnan(v))
    assert loader.same_v1(stored, req) == jsonb_v1.equal(stored, v)
    assert loader.same_v1(req, stored) == jsonb_v1.equal(stored, v)
    assert loader.same_v1(v, req) is True  # its own parsed value matches
    assert loader.same_v1(req, J(text)) is True


@pytest.mark.parametrize("requested,stored,want", [
    ('{"a": [1, true, null], "bb": {"c": "x"}}', {"bb": {"c": "x"}, "a": [1, True, None]}, True),  # key order
    ('{"a": 1}', {"a": 1, "b": 2}, False), ("[1, 2]", [2, 1], False), ("[]", {}, False), ("{}", [], False),
    ('"x"', "x", True), ('"1"', 1, False), ("1", "1", False), ("1", 1, True), ("1", True, False),
    ("true", True, True), ("true", "true", False), ("true", 1, False), ("false", 0, False),
    ("null", None, True),  # attribution can't tell JSON null from SQL NULL; the database readback does
    (str(2**53 - 1), 2**53 - 1, True), (str(-(2**53 - 1)), -(2**53 - 1), True), ("1", 1.0, False),
    ('{"a": 1}', '{"a": 1}', False),  # the stored value is a JSON *string*, not an object
    ('[[[{"x": [null]}]]]', [[[{"x": [None]}]]], True)])
def test_adversarial_jsonb_attribution(requested, stored, want):
    assert loader.same_v1(stored, J(requested)) is want


@pytest.mark.parametrize("text,why", [('{"a": 1, "a": 2}', "duplicate"), (str(2**53), "exceeds"),
                                      (str(-(2**53)), "exceeds"), ("1.5", "non-integer"), ("2e3", "non-integer"),
                                      ("NaN", "not JSON"), ('{"a": [1,]}', "malformed")])
def test_a_jsonb_marker_cant_be_made_for_a_value_outside_jsonb_1(text, why):
    with pytest.raises(jsonb_v1.JsonbRefused, match=why):
        J(text)


def test_seed_rows_carry_markers_and_the_ambiguity_refusal_remains():
    seed, registered = loader.seed_rows([("t", {"id": 1, "d": '{"n": 0}', "s": "plain"})], {0: {"d"}})
    vals = {v.column: v.value for v in seed[0].values}
    assert isinstance(vals["d"], J) and not isinstance(vals["s"], J) and set(registered) == {'{"n": 0}'}
    with pytest.raises(loader.ExtensionRefused, match="ambiguous"):
        loader.seed_rows([("t", {"d": "{}", "s": "{}"})], {0: {"d"}})


def test_a_lost_marker_is_refused_before_construction():
    seed, registered = loader.seed_rows([("t", {"d": '{"n": 0}'})], {0: {"d"}})
    copied = [r.model_copy(deep=True) for r in seed]  # a deep copy keeps the marker (rebuilt through __new__)
    assert isinstance(copied[0].values[0].value, J)
    plain = [SeedRow.model_construct(table=r.table, values=[ColumnValue.model_construct(column=v.column,
                                                                                         value=str(v.value))
                                                             for v in r.values]) for r in seed]  # a lost marker
    with pytest.raises(loader.ExtensionRefused, match="markers"):
        loader.construct(None, "", plain, registered)


def test_the_substitution_reached_both_modules():
    assert M.seed_feasibility._same is loader.same_v1 and M.state_constructor._same is loader.same_v1


def test_v1_text_keyed_logic_diverged_from_the_original_on_stored_vs_stored_text():
    """The superseded v1 rule (verbatim from fc4afb4, with its registry passed explicitly) diverges from the original
    on two *stored* text values that equal a registered jsonb text, as at state_constructor.py:489."""
    def same_v1_v1(a, b, registered):
        for stored, requested in ((a, b), (b, a)):
            if isinstance(requested, str) and requested in registered:
                if isinstance(stored, str) and stored in registered:
                    return stored == requested
                return jsonb_v1.equal(stored, registered[requested])
        return ORIGINAL(a, b)
    registered = {'"x"': "x"}
    assert ORIGINAL("x", '"x"') is False and same_v1_v1("x", '"x"', registered) is True  # the v1 divergence
    assert loader.same_v1("x", '"x"') is ORIGINAL("x", '"x"')  # v1.1: the original
