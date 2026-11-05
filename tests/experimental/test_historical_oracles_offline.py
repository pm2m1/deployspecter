"""EXPERIMENTAL, offline: vectors for the chancy-2895c4a historical case checker."""

from __future__ import annotations

from deployspecter.experimental import historical_oracles as ho


def job(state, attempts=1, claim=False, completed=True):
    return {"n": 0, "state": state, "attempts": attempts, "has_claim_id": claim, "completed": completed}


def results(rowcount=1, handled=(0,)):
    return {"push": {"cancelled": [{"n": 0, "rowcount": rowcount}]}, "worker": {"handled": list(handled)}}


def test_an_overwritten_cancel_violates_h1():
    out = ho.chancy_2895c4a([job("succeeded")], results(), has_claim_column=False)
    assert out["verdict"] == "VIOLATED" and out["H1"] and not out["H2"]


def test_a_fenced_cancel_holds_on_the_fixed_schema():
    out = ho.chancy_2895c4a([job("failed", attempts=0)], results(), has_claim_column=True)
    assert out["verdict"] == "HOLDS"


def test_a_cancel_that_left_its_claim_violates_h2():
    out = ho.chancy_2895c4a([job("failed", claim=True)], results(), has_claim_column=True)
    assert out["verdict"] == "VIOLATED" and out["H2"] and not out["H1"]


def test_no_verdict_when_the_race_wasnt_exercised():
    assert ho.chancy_2895c4a([job("failed")], results(handled=()), has_claim_column=True)["verdict"] == "NO_VERDICT"
    assert ho.chancy_2895c4a([job("failed")], results(rowcount=0), has_claim_column=True)["verdict"] == "NO_VERDICT"
