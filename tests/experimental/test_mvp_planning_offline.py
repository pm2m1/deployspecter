"""EXPERIMENTAL planning adapter, offline: WorkloadSpec v2 through the unmodified typed chain; explicit outcomes."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from deployspecter.experimental import planning_adapter as pa
from deployspecter.research.track_a import pipeline as tp
from deployspecter.research.track_a import scenario as sc


def T(kind, value=None):  # noqa: N802
    return {"type": kind} if kind == "null" else {"type": kind, "value": value}


def doc(change):
    d = copy.deepcopy(sc.DOCUMENT)
    change(d)
    return d


def goal(**values):
    def change(d):
        d["state_goals"][0]["constraints"] = [{"field": k, "op": "eq", "value": v} for k, v in values.items()]
    return change


class TestPlanOutcomes:
    def test_the_track_a_document_bridges_to_the_same_digests_as_track_a(self):
        p = pa.plan(sc.DOCUMENT)
        chain = tp.run_offline_chain()
        assert p.outcome == "bridged"
        assert p.digests["bridge_typed"] == chain.digests["bridge_typed"]
        assert p.digests["request"] == chain.digests["request"]
        assert pa.typed_rows(p) == [("events", {"capacity": 3, "id": 1, "reserved": 0})]

    @pytest.mark.parametrize("change, outcome, stage, code", [
        (lambda d: d.update(schema_version=7), "invalid", "parse", None),
        (lambda d: d["actors"][0].update(entrypoint="drop_everything"), "invalid", "authorize", "unknown_entrypoint"),
        (lambda d: d["actors"][0]["arguments"][2].update(value=T("int", 11)), "invalid", "validate_arguments",
         "above_maximum"),
        (lambda d: d["actors"][0]["arguments"].append({"name": "lock_row", "value": T("bool", True)}), "invalid",
         "authorize", "unknown_argument"),
        (goal(id=T("int", 1), capacity=T("int", 2**40), reserved=T("int", 0)), "infeasible", "plan_state",
         "outside_type_range"),
        (lambda d: d["state_goals"][0].update(count=2), "infeasible", "plan_state", "duplicate_key_within_goal"),
        (lambda d: d["state_goals"][0].update(entity="invoice"), "unsupported", "plan_state", "unknown_entity"),
        (goal(id=T("int", 1), capacity=T("null"), reserved=T("int", 0)), "unsupported", "plan_state",
         "null_comparison"),
        (goal(id=T("int", 1), capacity=T("float", 3.0), reserved=T("int", 0)), "unsupported", "plan_state",
         "cast_dependent"),
    ])
    def test_each_failure_is_an_explicit_outcome_at_its_stage(self, change, outcome, stage, code):
        p = pa.plan(doc(change))
        assert (p.outcome, p.stage) == (outcome, stage)
        if code is not None:
            detail = p.detail[0] if isinstance(p.detail, list) else p.detail
            assert detail["code"] == code
        with pytest.raises(pa.AdapterRefused):
            pa.typed_rows(p)

    def test_negative_zero_is_refused_by_workloadspec_itself(self):
        p = pa.plan(doc(goal(id=T("int", 1), capacity=T("float", -0.0), reserved=T("int", 0))))
        assert (p.outcome, p.stage) == ("invalid", "parse") and "negative zero" in p.detail[0]

    def test_the_largest_integer_the_column_holds_is_kept_exactly(self):
        p = pa.plan(doc(goal(id=T("int", 1), capacity=T("int", 2**31 - 1), reserved=T("int", 0))))
        rows = pa.typed_rows(p)
        assert rows[0][1]["capacity"] == 2147483647 and type(rows[0][1]["capacity"]) is int

    @pytest.mark.parametrize("change, outcome", [
        (lambda s: s.update(attributes=[a for a in s["attributes"] if a["attname"] != "reserved"]), "invalid"),
        (lambda s: s["constraints"].append({"oid": 31009, "conname": "cap_chk", "contype": "c", "conrelid": sc.EVENTS,
                                            "conkey": [2], "condeferrable": False, "condeferred": False,
                                            "convalidated": True}), "unsupported"),
        (lambda s: s["coverage"].update(constraints=False), "indeterminate"),
    ])
    def test_a_catalog_that_disagrees_stops_the_chain_before_the_bridge(self, change, outcome):
        snap = sc.catalog_snapshot_fixture()
        change(snap)
        p = pa.plan(sc.DOCUMENT, snapshot=snap)
        assert (p.outcome, p.stage) == (outcome, "verify_context")
        assert "bridge" not in p.stages


def fake_plan(*values):
    row = SimpleNamespace(table="events", values=[SimpleNamespace(column=c, type=t, value=v) for c, t, v in values])
    return pa.Plan("bridged", "bridge", bridged=SimpleNamespace(representation=SimpleNamespace(rows=[row])))


class TestTypedRows:
    def test_exact_values_and_null_pass_through(self):
        assert pa.typed_rows(fake_plan(("id", "int", 2**62), ("capacity", "null", None))) == [
            ("events", {"id": 2**62, "capacity": None})]

    @pytest.mark.parametrize("values", [
        [("id", "decimal", "1")],  # a tag the representation doesn't define
        [("id", "int", True)],  # a bool isn't an int here
        [("id", "int", 1.0)],  # nor is a float
        [("id", "float", float("nan"))],
        [("id", "null", 0)],
        [("owner", "str", "x")],  # not a context column
    ])
    def test_anything_it_cant_keep_exact_is_refused(self, values):
        with pytest.raises(pa.AdapterRefused):
            pa.typed_rows(fake_plan(*values))

    def test_a_row_in_a_table_the_context_doesnt_describe_is_refused(self):
        p = fake_plan(("id", "int", 1))
        p.bridged.representation.rows[0].table = "accounts"
        with pytest.raises(pa.AdapterRefused):
            pa.typed_rows(p)


class TestTrustedHandlers:
    def test_handlers_come_only_from_the_pinned_registry_entrypoints(self):
        fx = pa.PlannedReservations(pa.plan(sc.DOCUMENT))
        reg = fx.registry("buggy")
        assert sorted(reg) == [("v1", "release_seats"), ("v1", "reserve_seats")]
        assert reg[("v1", "reserve_seats")].fn.keywords == {"lock_row": False}
        assert fx.registry("fixed")[("v1", "reserve_seats")].fn.keywords == {"lock_row": True}

    def test_an_edited_application_source_is_refused(self, monkeypatch):
        monkeypatch.setitem(pa.PINNED_SHA256, "live_app.reservations", "0" * 64)
        with pytest.raises(pa.AdapterRefused, match="isn't the reviewed one"):
            pa.PlannedReservations(pa.plan(sc.DOCUMENT))

    def test_an_operation_outside_the_trusted_set_is_refused(self, monkeypatch):
        monkeypatch.setattr(pa, "TRUSTED_OPERATIONS", {"live_app.reservations:reserve_seats"})
        fx = pa.PlannedReservations(pa.plan(sc.DOCUMENT))
        with pytest.raises(pa.AdapterRefused, match="release_seats"):
            fx.registry("buggy")

    def test_an_untrusted_module_is_never_imported(self):
        with pytest.raises(pa.AdapterRefused):
            pa.load_trusted("os")
