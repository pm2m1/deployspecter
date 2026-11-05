"""EXPERIMENTAL state construction, offline: typed exactness, the constructor's order, the pinned Phase 9B sources,
and the narrow Chancy path's workload, rows and oracle. No database."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import uuid

import pytest

from deployspecter.experimental.state_construction import chancy, phase9b, pipeline, typed
from deployspecter.experimental.state_construction.typed import TV

INT8 = {"data_type": "bigint", "is_nullable": "YES", "char_max": None}
NOT_NULL_TEXT = {"data_type": "text", "is_nullable": "NO", "char_max": None}


class TestTypedValues:
    @pytest.mark.parametrize("tv, column", [
        (TV("int", 2**63 - 1), INT8), (TV("int", -(2**63)), INT8), (TV("null", None), INT8),
        (TV("float", -0.0), {"data_type": "double precision", "is_nullable": "NO", "char_max": None}),
        (TV("str", "NULL"), NOT_NULL_TEXT), (TV("str", ""), NOT_NULL_TEXT),
        (TV("uuid", str(uuid.uuid5(uuid.NAMESPACE_DNS, "x"))), {"data_type": "uuid", "is_nullable": "NO",
                                                                "char_max": None}),
        (TV("timestamptz", "2026-01-01T00:00:00+05:30"), {"data_type": "timestamp with time zone",
                                                          "is_nullable": "NO", "char_max": None}),
        (TV("jsonb", '{"n": 1, "x": [true, null, 1.5]}'), {"data_type": "jsonb", "is_nullable": "NO",
                                                          "char_max": None}),
    ])
    def test_values_that_fit_their_tag_and_column_are_accepted(self, tv, column):
        typed.check_value(tv, column)

    @pytest.mark.parametrize("tv, column, why", [
        (TV("int", 2**63), INT8, "outside"), (TV("bool", True), INT8, "bool value for a bigint"),
        (TV("int", True), INT8, "holding bool"), (TV("int", 1.0), INT8, "holding float"),
        (TV("null", None), NOT_NULL_TEXT, "NOT NULL"), (TV("null", 0), INT8, "null tag with a value"),
        (TV("str", "a\x00b"), NOT_NULL_TEXT, "NUL"), (TV("decimal", "1.0"), INT8, "unsupported tag"),
        (TV("str", "x" * 26), {"data_type": "character varying", "is_nullable": "NO", "char_max": 25}, "26"),
        (TV("uuid", "0B0A8C1E-6F43-5D2E-9A1B-3C4D5E6F7A8B"), {"data_type": "uuid", "is_nullable": "NO",
                                                              "char_max": None}, "canonical"),
        (TV("timestamptz", "2026-01-01T00:00:00"), {"data_type": "timestamp with time zone", "is_nullable": "NO",
                                                    "char_max": None}, "offset"),
        (TV("json", '{"n": NaN}'), {"data_type": "json", "is_nullable": "NO", "char_max": None}, "NaN"),
        (TV("jsonb", '{"n": 1}'), {"data_type": "json", "is_nullable": "NO", "char_max": None}, "jsonb value"),
    ])
    def test_anything_that_cant_stay_exact_is_refused(self, tv, column, why):
        with pytest.raises(typed.TypedRefused, match=why):
            typed.check_value(tv, column)

    def test_equality_is_exact(self):
        assert typed.same(TV("float", -0.0), -0.0) and not typed.same(TV("float", -0.0), 0.0)
        assert not typed.same(TV("float", 0.0), -0.0)
        assert typed.same(TV("float", float("nan")), float("nan")) and not typed.same(TV("float", float("nan")), 1.0)
        assert typed.same(TV("float", 5e-324), 5e-324) and not typed.same(TV("float", 5e-324), 0.0)
        assert not typed.same(TV("str", "NULL"), None) and not typed.same(TV("null", None), "NULL")
        assert typed.same(TV("null", None), None)
        assert not typed.same(TV("int", 1), True) and not typed.same(TV("bool", True), 1)
        assert not typed.same(TV("int", 1), 1.0) and not typed.same(TV("float", 1.0), 1)
        u = uuid.uuid4()
        assert typed.same(TV("uuid", str(u)), u) and not typed.same(TV("uuid", str(u)), str(u))
        at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        assert typed.same(TV("timestamptz", "2026-01-01T05:30:00+05:30"), at)  # the same instant
        assert not typed.same(TV("timestamptz", "2026-01-01T00:00:00.000001+00:00"), at)
        assert not typed.same(TV("timestamptz", "2026-01-01T00:00:00+00:00"), at.replace(tzinfo=None))
        assert typed.same(TV("json", '{"n":1}'), {"n": 1}, '{"n":1}')  # json is kept verbatim: text, byte for byte
        assert not typed.same(TV("json", '{"n":1}'), {"n": 1}, '{"n": 1}')
        assert not typed.same(TV("json", "{}"), None, None)  # NULL isn't the JSON text

    def test_jsonb_compares_parsed_values_with_their_json_types(self):
        assert typed.same(TV("jsonb", '{"b": 2, "a": 1}'), {}, '{"a": 1, "b": 2}')
        for want, got in (('{"n": 1}', '{"n": 1.0}'), ('{"n": 1}', '{"n": true}'), ('{"n": null}', '{}'),
                          ('[1, 2]', '[2, 1]'), ('{"n": "1"}', '{"n": 1}'), ('{"n": -0.0}', '{"n": 0.0}')):
            assert not typed.same(TV("jsonb", want), {}, got), (want, got)

    def test_negative_zero_and_nan_are_recorded_distinctly(self):
        assert TV("float", -0.0).to_dict() == {"tag": "float", "value": "-0.0"}
        assert TV("float", float("nan")).to_dict() == {"tag": "float", "value": "nan"}


class TestConstructorOrder:
    SEED = [{"table": "a", "values": [{"column": "id", "value": 1}]},
            {"table": "b", "values": [{"column": "id", "value": 1}]},
            {"table": "a", "values": [{"column": "id", "value": True}]}]

    def test_original_valid_keeps_the_bridge_order(self):
        assert pipeline.constructor_order({"classification": "ORIGINAL_VALID"}, self.SEED) == [0, 1, 2]

    def test_constructed_follows_the_plan_and_leaves_out_rows_already_present(self):
        plan = [{"row_index": 2, "action": "insert"}, {"row_index": 0, "action": "already_present"},
                {"row_index": 1, "action": "insert"}]
        assert pipeline.constructor_order({"classification": "CONSTRUCTED_EQUIVALENT", "plan": plan},
                                          self.SEED) == [2, 1]

    def test_reconciled_is_matched_by_exact_json_so_1_and_true_differ(self):
        report = {"classification": "RECONCILED_EQUIVALENT", "effective_seed": [self.SEED[0], self.SEED[2]]}
        assert pipeline.constructor_order(report, self.SEED) == [0, 2]
        bad = {"classification": "RECONCILED_EQUIVALENT",
               "effective_seed": [{"table": "a", "values": [{"column": "id", "value": 1.0}]}]}
        with pytest.raises(phase9b.ConstructorRefused, match="subsequence"):
            pipeline.constructor_order(bad, self.SEED)

    @pytest.mark.parametrize("cls", ["CONSTRUCTED_WITH_ASSUMPTIONS", "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS",
                                     "UNSUPPORTED_OR_UNKNOWN", "SEARCH_BUDGET_EXHAUSTED"])
    def test_anything_not_proven_authorizes_nothing(self, cls):
        with pytest.raises(phase9b.ConstructorRefused, match="isn't proven"):
            pipeline.constructor_order({"classification": cls}, self.SEED)
        assert cls not in phase9b.PROVEN


class TestPinnedPhase9B:
    def test_the_vendored_sources_are_the_pinned_bytes(self):
        for name, pin in phase9b.PINNED.items():
            assert hashlib.sha256((phase9b.SOURCE_DIR / name).read_bytes()).hexdigest() == pin

    def test_it_loads_under_a_private_package_without_replacing_deployspecter_modules(self):
        m = phase9b.load()
        assert m.state_constructor.__name__ == "deployspecter._phase9b.state_constructor"
        assert "deployspecter.state_constructor" not in sys.modules
        assert set(m.state_constructor.PROVEN) == set(phase9b.PROVEN)
        import deployspecter.database as db
        assert m.state_constructor.Sandbox is db.Sandbox  # its relative imports are this branch's root modules

    def test_an_edited_source_is_refused(self, monkeypatch):
        saved = {k: v for k, v in sys.modules.items() if k.startswith(phase9b.PACKAGE)}
        for k in saved:
            del sys.modules[k]
        monkeypatch.setitem(phase9b.PINNED, "triggers.py", "0" * 64)
        try:
            with pytest.raises(phase9b.ConstructorRefused, match="isn't the pinned"):
                phase9b.load()
        finally:
            for k in [k for k in sys.modules if k.startswith(phase9b.PACKAGE)]:
                del sys.modules[k]
            sys.modules.update(saved)

    def test_the_protected_port_is_refused_before_any_connection(self):
        with pytest.raises(phase9b.ConstructorRefused, match="protected"):
            phase9b.settings(55432, "u", "p")


class TestChancyPath:
    W = {"jobs": [{"n": 0, "state": "pending"}, {"n": 5, "state": "pending"}, {"n": 9, "state": "succeeded"}]}

    @pytest.mark.parametrize("w, why", [
        ({"jobs": []}, "1..8"), ({"jobs": [{"n": 1, "state": "pending"}] * 2}, "duplicate"),
        ({"jobs": [{"n": True, "state": "pending"}]}, "an int"), ({"jobs": [{"n": 1, "state": "running"}]}, "one of"),
        ({"jobs": [{"n": 1, "state": "succeeded"}]}, "at least one pending"),
        ({"jobs": [{"n": 1, "state": "pending", "func": "os.system"}]}, "exactly n and state"),
        ({"jobs": [], "sql": "DROP"}, "shape"),
    ])
    def test_the_workload_is_validated_deterministically(self, w, why):
        assert any(why in p for p in chancy.validate(w))

    def test_rows_are_deterministic_explicit_and_typed(self):
        assert chancy.validate(self.W) == []
        a, b = chancy.rows(chancy.goals(self.W)), chancy.rows(chancy.goals(json.loads(json.dumps(self.W))))
        assert a == b
        for _, r in a:
            assert set(r) == set(chancy.PINNED_COLUMNS)  # every column explicit: no clock or random default
        assert a[0][1]["id"] == TV("uuid", chancy.job_id(0)) and chancy.job_id(0) == chancy.job_id(0)
        assert a[2][1]["state"] == TV("str", "succeeded") and a[2][1]["completed_at"].tag == "timestamptz"
        assert a[0][1]["kwargs"] == TV("jsonb", '{"n": 0}')

    def test_the_constructor_choice_is_fixed_by_the_value_types(self):
        assert chancy.constructor_for(chancy.rows(chancy.goals(self.W))) == "direct_trigger_free"
        assert chancy.constructor_for([("t", {"id": TV("int", 1)})]) == "phase9b"

    def final(self, **change):
        jobs = [{"id": chancy.job_id(n), "queue": chancy.QUEUE, "func": chancy.FUNC, "kwargs": json.dumps({"n": n}),
                 "state": "succeeded", "attempts": 1, "completed": True} for n in (0, 5, 9)]
        for n, fields in change.items():
            jobs[[0, 5, 9].index(int(n[1:]))].update(fields)
        return jobs

    def test_the_oracle_accepts_exactly_the_documented_outcome(self):
        assert chancy.oracle(self.W, self.final(), [5, 0]) == []

    @pytest.mark.parametrize("final_change, handled, code", [
        ({}, [0, 5, 9], "C3"), ({}, [0], "C3"), ({}, [0, 0, 5], "C3"),
        ({"n5": {"state": "failed"}}, [0, 5], "C2"), ({"n0": {"attempts": 2}}, [0, 5], "C2"),
        ({"n9": {"attempts": 2}}, [0, 5], "C4"), ({"n0": {"kwargs": '{"n": 0.0}'}}, [0, 5], "C4"),
    ])
    def test_the_oracle_catches_each_deviation(self, final_change, handled, code):
        assert any(v.startswith(code) for v in chancy.oracle(self.W, self.final(**final_change), handled))

    def test_a_lost_or_extra_job_is_c1(self):
        lost = self.final()[:2]
        assert any(v.startswith("C1") for v in chancy.oracle(self.W, lost, [0, 5]))
        extra = self.final() + [{**self.final()[0], "id": str(uuid.uuid4())}]
        assert any(v.startswith("C1") for v in chancy.oracle(self.W, extra, [0, 5]))
