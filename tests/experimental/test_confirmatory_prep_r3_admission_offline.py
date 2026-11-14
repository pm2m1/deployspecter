"""Offline tests of revision candidate r3's proposal-admission rule (#24a;
research_tools/confirmatory_prep/prep_r3.py). Synthetic replies and a SIMULATION integration only: no model request,
database, container or real case."""

from __future__ import annotations

import inspect
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "confirmatory_prep"))
import prep  # noqa: E402
import prep_r2  # noqa: E402
import prep_r3 as r3  # noqa: E402

K, S, R = 6, 2, 2
H = {"R1": "R1", "R2": "R2"}


def w(n, flag=False):
    return {"jobs": [{"n": n}], "flag": flag}


def validate(p):
    if not isinstance(p, dict) or set(p) != {"jobs", "flag"}:
        return ["shape"]
    return [] if all(0 <= j.get("n", -1) <= 50 for j in p["jobs"]) else ["n out of range"]


def ok(i, proposals):
    return {"index": i, "status": "ok", "proposals": proposals}


class Sim:
    SIMULATION = True

    def run(self, handle, wl, seed):
        return {"verdict": "SOUND", "clauses": {"T": "VIOLATED" if handle == "R1" and wl["flag"] else "HOLDS"}}


def run_admitted(adm):
    e = prep_r2.R2Executor(integration=Sim(), oracle_targets={"T"}, journal_sha={"M": adm["journal_sha256"]},
                           simulation=True)
    return e.run_arm("c", "M", adm["admitted"], H)


def test_more_than_k_valid_admits_the_first_k_and_counts_overflow():
    a = r3.admit_replies([ok(0, [w(i) for i in range(6)]), ok(1, [w(i) for i in range(6, 12)])], R, validate)
    assert a["admitted"] == [w(i) for i in range(6)]
    want = {"generated": 12, "valid": 12, "distinct_valid": 12, "admitted": 6, "overflow": 6, "duplicate": 0,
            "invalid": 0}
    assert {k: a["proposals"][k] for k in want} == want
    assert r3.executed_counts(a, run_admitted(a))["executed"] == 6


def test_exactly_k_valid():
    a = r3.admit_replies([ok(0, [w(i) for i in range(6)]), ok(1, [{"bad": 1}] * 6)], R, validate)
    assert a["proposals"]["admitted"] == 6 and a["proposals"]["overflow"] == 0 and a["proposals"]["invalid"] == 6
    assert a["slots"] == {"slots": 6, "filled_first_time": 6, "filled_by_repetition": 0, "unfilled_refused": 0}


def test_fewer_than_k_distinct_fill_by_repetition_with_equal_budgets():
    a = r3.admit_replies([ok(0, [w(1), w(2)]), ok(1, [w(3), w(1)])], R, validate)
    assert a["proposals"]["distinct_valid"] == 3 and a["proposals"]["duplicate"] == 1
    assert a["slots"]["filled_by_repetition"] == 3
    runs = run_admitted(a)
    assert sum(r["slot"] == "R1" for r in runs) == K * S == sum(r["slot"] == "R2" for r in runs)
    assert r3.executed_counts(a, runs)["executed"] == 3


def test_all_invalid_leaves_every_slot_unfilled_and_refused():
    a = r3.admit_replies([ok(0, [{"bad": i} for i in range(6)]), ok(1, [w(99)] * 6)], R, validate)
    assert a["admitted"] == [] and a["slots"]["unfilled_refused"] == K and a["proposals"]["validity_rate"] == 0.0
    assert a["proposals"]["invalid_by_reason"] == {"shape": 6, "n out of range": 6}
    cell = prep.score_cell(run_admitted(a), {"T"}, K, S)
    assert cell["defective"]["REFUSED"] == K * S and cell["primary"]["censored"]


def test_all_duplicates_count_as_valid_but_admit_one():
    a = r3.admit_replies([ok(0, [w(7)] * 6), ok(1, [w(7)] * 6)], R, validate)
    p = a["proposals"]
    assert (p["generated"], p["valid"], p["distinct_valid"], p["duplicate"], p["admitted"]) == (12, 12, 1, 11, 1)
    assert p["validity_rate"] == 1.0 and a["slots"]["filled_by_repetition"] == K - 1


def test_cross_request_duplicates_point_to_the_first_occurrence():
    a = r3.admit_replies([ok(0, [w(1), w(2)]), ok(1, [{"flag": False, "jobs": [{"n": 2}]}, w(3)])], R, validate)
    dup = [e for e in a["log"] if e["outcome"] == "duplicate"]
    assert len(dup) == 1 and dup[0]["request"] == 1 and dup[0]["duplicate_of"] == {"request": 0, "position": 1}
    assert a["admitted"] == [w(1), w(2), w(3)]  # key order doesn't make a different workload


def test_missing_failed_and_provider_failure_requests():
    a = r3.admit_replies([ok(0, [w(1)])], R, validate)  # request 1 never recorded
    assert a["requests"] == {"planned": 2, "attempted": 1, "failed": 1, "by_status": {"ok": 1, "missing": 1}}
    mismatch = {"status": "provider_model_mismatch", "usable": False, "reply_text": json.dumps({"workloads": [w(5)]})}
    recs = [r3.reply_record(0, None), r3.reply_record(1, mismatch)]
    assert recs[0]["status"] == "refused_before_sending" and recs[1]["proposals"] == [w(5)]
    b = r3.admit_replies(recs, R, validate)
    assert b["proposals"]["generated"] == 1 and b["proposals"]["invalid_by_reason"] == {"provider failure": 1}
    assert b["admitted"] == [] and b["requests"]["failed"] == 2
    failed = r3.reply_record(0, {"status": "request_failed", "usable": False})
    garbled = r3.reply_record(1, {"status": "ok", "usable": True, "reply_text": "not json"})
    assert (failed["status"], garbled["status"]) == ("request_failed", "unparseable_reply")
    c = r3.admit_replies([failed, garbled], R, validate)
    assert c["proposals"]["generated"] == 0 and c["proposals"]["validity_rate"] is None


def test_ordering_is_stable_and_follows_request_index_then_reply_order():
    reps = [ok(0, [w(i) for i in range(4)]), ok(1, [w(i) for i in range(10, 14)])]
    base = r3.admit_replies(reps, R, validate)
    for seed in range(5):
        shuffled = reps[:]
        random.Random(seed).shuffle(shuffled)
        assert r3.admit_replies(shuffled, R, validate) == base
    assert base["admitted"] == [w(0), w(1), w(2), w(3), w(10), w(11)]
    reordered = r3.admit_replies([ok(0, [w(3), w(2), w(1), w(0)]), reps[1]], R, validate)
    assert reordered["admitted"][:4] == [w(3), w(2), w(1), w(0)]  # the reply's own order is kept


def test_validity_denominator_counts_every_generated_proposal():
    a = r3.admit_replies([ok(0, [w(1), {"bad": 1}, w(1)]), ok(1, [w(99), w(2), w(3)])], R, validate)
    p = a["proposals"]
    assert p["generated"] == 6 == p["valid"] + p["invalid"]
    assert p["valid"] == p["distinct_valid"] + p["duplicate"] and p["distinct_valid"] == p["admitted"] + p["overflow"]
    assert p["validity_rate"] == round(4 / 6, 4)


def test_no_outcome_dependent_replacement_or_repeated_requests():
    a = r3.admit_replies([ok(0, [w(1), w(2, True)])], R, validate)
    e = prep_r2.R2Executor(integration=Sim(), oracle_targets={"T"}, journal_sha={"M": a["journal_sha256"]},
                           simulation=True)
    with pytest.raises(SystemExit, match="admission journal"):
        e.run_arm("c", "M", [w(2, True), w(1)], H)  # reordered
    with pytest.raises(SystemExit, match="admission journal"):
        e.run_arm("c", "M", [w(1), w(3)], H)  # replaced
    with pytest.raises(SystemExit, match="executed workloads differ"):
        r3.executed_counts(a, [{"workload_sha256": prep.sha(w(1))}])
    with pytest.raises(SystemExit, match="never repeated or replaced"):
        r3.admit_replies([ok(0, [w(1)]), ok(0, [w(2)])], R, validate)
    with pytest.raises(SystemExit, match="outside the plan"):
        r3.admit_replies([ok(2, [w(1)])], R, validate)  # a third request beyond R
    plan = prep_r2.RequestPlan("c", "M", r=R)
    plan.begin()
    plan.start_runs()
    with pytest.raises(SystemExit, match="outcome-dependent"):
        plan.begin()


def test_admission_sees_no_labels_and_its_record_leaks_none():
    assert set(inspect.signature(r3.admit_replies).parameters) == {"replies", "r", "validate", "k"}
    labels = [{"case_id": "c-7f3a", "repository": "https://example.invalid/x", "buggy_commit": "a" * 40,
               "fixed_commit": "b1c2d3e4f5" + "0" * 30, "fix_paths": ["pkg/worker.py"], "defect_summary": "x",
               "keywords": ["stranded"], "exposure": "none"}]
    a = r3.admit_replies([ok(0, [w(1), w(2)]), ok(1, [w(3)])], R, validate)
    assert prep.leak_check_inputs(labels, {"admitted": a["admitted"], "log": a["log"]}) == []


def test_k_is_fixed_and_real_execution_stays_disabled():
    with pytest.raises(SystemExit, match="K = 6 and S = 2"):
        r3.admit_replies([], R, validate, k=4)
    with pytest.raises(SystemExit, match="real confirmatory execution is not enabled"):
        prep_r2.R2Executor(integration=Sim(), oracle_targets={"T"}, journal_sha={}, real=True, simulation=True)
