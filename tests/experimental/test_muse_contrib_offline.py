"""EXPERIMENTAL, offline: arms B1-B5 inputs, validation, selection, prompts and the comparison rule."""

from __future__ import annotations

import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools"))
sys.path.insert(0, str(ROOT / "research_tools" / "muse_contrib"))
import analyze as an  # noqa: E402
import chancy_runner as cr  # noqa: E402
import experiment as ex  # noqa: E402
import mvp_release_evidence as ev  # noqa: E402

from deployspecter.experimental import planning_adapter as pa  # noqa: E402
from deployspecter.experimental.muse_contrib import chancy as ch  # noqa: E402
from deployspecter.experimental.muse_contrib import greedy  # noqa: E402
from deployspecter.experimental.muse_contrib import prompts as P  # noqa: E402
from deployspecter.experimental.muse_contrib import reservations as rs  # noqa: E402


def test_chancy_validation_is_strict_and_repairs_nothing():
    ok = {"rationale": "x", "jobs": 2, "cancel": [1], "max_attempts": 2, "workers": 2}
    assert ch.validate(ok) == ({"ns": [0, 1], "cancel": [1], "max_attempts": 2, "workers": 2}, [])
    for bad in ({**ok, "jobs": 0}, {**ok, "cancel": [2]}, {**ok, "cancel": [1, 1]}, {**ok, "max_attempts": 4},
                {**ok, "workers": 3}, {"jobs": 1}):
        w, reasons = ch.validate(bad)
        assert w is None and reasons


def test_greedy_order_is_deterministic_and_covers_pairs_fast():
    g = ch.grid()
    assert len(g) == 84 and ch.systematic(5) == ch.systematic(5)
    first = ch.systematic(10)
    covered = set().union(*(greedy.pairs(ch.features(w)) for w in first))
    later = set().union(*(greedy.pairs(ch.features(w)) for w in g[:10]))
    assert len(covered) > len(later)  # greedy covers more pairs than grid order


def test_reservations_curated_is_the_release_evidence_and_everything_deterministic_bridges():
    assert rs.RELEASE_CASES == ev.CASES
    docs = rs.curated() + rs.systematic(6)
    for s in rs.ACTOR_SETS:
        docs += [(f"{s}{i}", rs.state_to_document(s, f"{s}{i}", st, origin="deterministic_baseline", author="t"))
                 for i, st in enumerate(rs.deterministic_states(s, 3))]
    assert all(pa.plan(d).outcome == "bridged" for _, d in docs)


def test_a_state_proposal_is_renamed_never_repaired():
    p = {"rationale": "x", "capacity": 3, "reserved": 9, "reservations": [{"holder": "jack", "qty": 2}]}
    d = rs.state_to_document("two_reserves", "t", p, origin="model_proposal", author="muse")
    consts = {c["field"]: c["value"]["value"] for c in d["state_goals"][0]["constraints"]}
    assert consts["reserved"] == 9 and consts["capacity"] == 3  # an inconsistent state is passed on as proposed
    assert d["actors"] == rs.ACTOR_SETS["two_reserves"]


LEAKS = ["2895c4a", "80f570f", "claim_id is not distinct", "fence", "abl_", "mc_claim", "mc_cancel", "lock_row",
         "for update", "corrected", "mutant", "h1", "h2", "r5", "release_seats defect", "overwrite"]


@pytest.mark.parametrize("label,name,n,_p", ex.CALLS)
def test_prompts_carry_no_solution_metadata(label, name, n, _p):
    instructions, text, _ = P.catalog(name, n)
    low = (instructions + text).lower()
    hits = [t for t in LEAKS if t in low and not (t == "for update" and name == "reservations_planner")]
    assert hits == [], (label, hits)


def test_reservations_planner_prompt_names_only_reviewed_statements():
    # the planner context lists each handler's captured statements; the lockable ones appear without FOR UPDATE
    _i, text, _ = P.catalog("reservations_planner", 2)
    assert "FOR UPDATE" not in text


class FakeClient:
    model, reasoning_effort, store, estimated_usd = "fake", "medium", False, 0.0

    def generate(self, *a, **k):
        raise AssertionError("must not be called")


def test_a_call_with_a_changed_prompt_is_refused_before_any_request(tmp_path):
    with pytest.raises(RuntimeError, match="refused"):
        P.call(FakeClient(), "chancy_workloads", 6, label="x", purpose="t", ledger=tmp_path / "l.jsonl",
               frozen_sha256="0" * 64)
    assert not (tmp_path / "l.jsonl").exists()


def test_selection_takes_the_first_k_distinct_valid_in_order(tmp_path):
    rows = [{"label": "c#0", "valid": False, "reasons": ["x"], "workload": None},
            {"label": "c#1", "valid": True, "reasons": [], "workload": {"a": 1}},
            {"label": "c#2", "valid": True, "reasons": [], "workload": {"a": 1}},
            {"label": "c#3", "valid": True, "reasons": [], "workload": {"a": 2}},
            {"label": "c#4", "valid": True, "reasons": [], "workload": {"a": 3}}]
    (tmp_path / "c.validated.json").write_text(json.dumps(rows))
    got, stats = ex.selected(tmp_path, "c", 2, "workload")
    assert got == [("c#1", {"a": 1}), ("c#3", {"a": 2})]
    assert stats == {"returned": 5, "valid": 4, "invalid": 1, "duplicate": 1, "call_failed": 0}
    assert ex.selected(tmp_path, "missing", 2, "workload")[1]["call_failed"] == 1


def row(arm, rep, det, first):
    return {"arm": arm, "replicate": rep, "detected": det, "first_detection_schedule": first}


def test_the_comparison_rule():
    assert an.compare([row("B1", 0, True, 3), row("B3", 0, True, 1), row("B3", 1, True, 2)], "B3",
                      ["B1"])["verdict"] == "improved"
    assert an.compare([row("B1", 0, True, 3), row("B3", 0, True, 1), row("B3", 1, False, None)], "B3",
                      ["B1"])["verdict"] == "underperformed"
    assert an.compare([row("B1", 0, True, 3), row("B2", 0, True, 1), row("B3", 0, True, 2)], "B3",
                      ["B1", "B2"])["verdict"] == "matched"
    assert an.compare([row("B1", 0, False, None), row("B3", 0, False, None)], "B3", ["B1"])["verdict"] == "matched"
    assert an.compare([row("B1", 0, False, None), row("B3", 0, True, 4)], "B3", ["B1"])["verdict"] == "improved"
    assert an.compare([row("B3", 0, True, 4)], "B3", ["B1"])["verdict"] == "not answerable"


def test_chancy_spec_runs_a_second_worker_in_its_own_process():
    s = cr.spec_for(ch.workload(2, [0, 1], 1, 2))
    assert sorted(s["processes"]) == ["A", "B"] and s["covers"] == [0, 1]
    assert [a["actor"] for a in s["processes"]["B"]] == ["worker2"]
    assert cr.spec_for(ch.workload(1, [0], 1, 1))["processes"].keys() == {"A"}


SDISTS = Path(os.environ.get("DS_THIRDPARTY_SDIST_DIR", "/nonexistent"))


@pytest.mark.skipif(not (SDISTS / cr.FIX).exists(), reason="set DS_THIRDPARTY_SDIST_DIR")
def test_each_new_mutant_is_one_exact_substring_of_the_pinned_fix_tree():
    with tarfile.open(SDISTS / cr.FIX) as tf:
        for mid, (rel, old, new, _why) in cr.NEW_MUTANTS.items():
            text = tf.extractfile(rel).read().decode()
            assert text.count(old) == 1 and old != new, mid
    cr.register_mutants()
    for mid in cr.NEW_MUTANTS:
        assert cr.ri.mutant_declaration(SDISTS / cr.FIX, mid, cr.FIX_BASE).startswith(f"{mid}:{cr.FIX_BASE}:")
