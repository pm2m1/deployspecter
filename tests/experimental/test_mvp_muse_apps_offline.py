"""EXPERIMENTAL Muse targets, offline: what the validators accept, what they refuse, and the hypothesis grammar."""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("sqlalchemy")

from deployspecter.experimental import muse_apps as ma  # noqa: E402
from deployspecter.experimental import planning_adapter as pa  # noqa: E402


def ledger_ok():
    return copy.deepcopy(ma.ledger_curated()[0][1])


class TestLedgerValidator:
    def test_curated_and_systematic_proposals_are_valid(self):
        for label, p in ma.ledger_curated() + ma.ledger_systematic(10):
            assert isinstance(ma.validate_ledger(p, source="t", label=label), ma.LedgerWorkload), label

    @pytest.mark.parametrize("change, reason", [
        (lambda p: p["invocations"][0].update(handler="drop_all"), "unknown handler"),
        (lambda p: p["invocations"][0]["args"][1].update(value=501), "outside"),
        (lambda p: p["invocations"][0]["args"][0].update(value=3), "isn't a seeded account"),
        (lambda p: p["invocations"][0]["args"].append({"name": "lock", "value": 1}), "arguments must be exactly"),
        (lambda p: p["invocations"][1].update(actor="a"), "unique"),
        (lambda p: p.update(invocations=p["invocations"][:1]), "invocations"),
        (lambda p: p["seed_accounts"].append({"id": 1, "opening": 5}), "distinct ids"),
        (lambda p: p["invariant_hypotheses"][0].update(column="password"), "hypothesis"),
        (lambda p: p.update(sql="DELETE FROM accounts"), "shape"),  # no free-form field reaches anything
    ])
    def test_a_bad_proposal_is_rejected_whole_and_not_repaired(self, change, reason):
        p = ledger_ok()
        change(p)
        out = ma.validate_ledger(p, source="t", label="x")
        if reason == "shape":
            # unknown keys are ignored by the model, never executed; the rest still validates
            assert isinstance(out, ma.LedgerWorkload) and "sql" not in out.model_dump()
            return
        assert isinstance(out, list) and any(reason in r for r in out), out

    def test_a_transfer_to_itself_is_rejected(self):
        p = copy.deepcopy(ma.ledger_curated()[2][1])
        p["invocations"][0]["args"] = [{"name": "src", "value": 1}, {"name": "dst", "value": 1},
                                       {"name": "amount", "value": 5}]
        assert any("differ" in r for r in ma.validate_ledger(p, source="t", label="x"))


H = {"table": "accounts", "column": "balance", "bound": None, "base_column": None, "child_table": None,
     "child_fk": None, "child_column": None, "statement": ""}
INITIAL = {"accounts": [{"id": 1, "owner": "o", "opening": 100, "balance": 100}], "entries": []}
LOST = {"accounts": [{"id": 1, "owner": "o", "opening": 100, "balance": 40}],
        "entries": [{"id": 1, "account_id": 1, "delta": -70}, {"id": 2, "account_id": 1, "delta": -60}]}


class TestHypotheses:
    def test_the_ledger_identity_catches_the_lost_update(self):
        h = {**H, "kind": "equals_base_plus_child_sum", "base_column": "opening", "child_table": "entries",
             "child_fk": "account_id", "child_column": "delta"}
        assert ma.hypothesis_problem(h) is None
        assert ma.evaluate_hypothesis(h, INITIAL, INITIAL) and not ma.evaluate_hypothesis(h, INITIAL, LOST)

    def test_no_overdraft_doesnt_see_this_lost_update(self):
        h = {**H, "kind": "column_at_least", "bound": 0}
        assert ma.evaluate_hypothesis(h, INITIAL, LOST)

    def test_conservation_is_false_for_a_correct_withdrawal(self):
        h = {**H, "kind": "column_sum_unchanged"}
        ok = {"accounts": [{"id": 1, "owner": "o", "opening": 100, "balance": 30}],
              "entries": [{"id": 1, "account_id": 1, "delta": -70}]}
        assert not ma.evaluate_hypothesis(h, INITIAL, ok)  # a hypothesis is checked, never trusted

    def test_a_hypothesis_naming_an_unknown_column_is_rejected(self):
        assert ma.hypothesis_problem({**H, "kind": "column_sum_unchanged", "column": "secret"})


def planner_ok():
    return {"name": "two_buyers", "rationale": "r", "actors": [
        {"name": n, "entrypoint": "reserve_seats", "arguments": [
            {"name": "event_id", "kind": "int", "int_value": 1, "str_value": None},
            {"name": "holder", "kind": "str", "int_value": None, "str_value": n + "x"},
            {"name": "qty", "kind": "int", "int_value": 2, "str_value": None}]} for n in ("a", "b")],
        "state_goals": [{"entity": "event", "label": "e1", "count": 1, "constraints": [
            {"field": "id", "op": "eq", "kind": "int", "int_value": 1, "str_value": None},
            {"field": "capacity", "op": "eq", "kind": "int", "int_value": 3, "str_value": None},
            {"field": "reserved", "op": "eq", "kind": "int", "int_value": 0, "str_value": None}]}]}


class TestPlannerMapping:
    def test_a_proposal_maps_to_a_workloadspec_v2_document_the_chain_bridges(self):
        doc = ma.to_workload_spec(planner_ok())
        assert doc["schema_version"] == 2 and doc["provenance"]["origin"] == "model_proposal"
        assert pa.plan(doc).outcome == "bridged"

    def test_the_mapping_repairs_nothing(self):
        p = planner_ok()
        p["actors"][0]["arguments"][2]["int_value"] = 50
        p["state_goals"][0]["constraints"][1].update(kind="str", int_value=None, str_value="three")
        doc = ma.to_workload_spec(p)
        assert doc["actors"][0]["arguments"][2]["value"] == {"type": "int", "value": 50}
        assert pa.plan(doc).outcome == "invalid"  # the frozen chain refuses it, at argument validation

    def test_is_null_carries_no_value(self):
        p = planner_ok()
        p["state_goals"][0]["constraints"].append({"field": "reserved", "op": "is_null", "kind": "none",
                                                   "int_value": None, "str_value": None})
        assert ma.to_workload_spec(p)["state_goals"][0]["constraints"][-1] == {"field": "reserved", "op": "is_null"}

    def test_the_prompts_carry_no_fix_and_no_oracle(self):
        for target in ("ledger", "planner"):
            text = ma.prompt(target, 3).lower()
            for leak in ("lock_row", "with_for_update", "for update", "buggy", "fixed", "lost update", "oracle",
                         "check_invariants", "r1 ", "l1 "):
                assert leak not in text, (target, leak)
