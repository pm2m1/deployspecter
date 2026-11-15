"""Solo r3 design: the maintainer-test adapter on the four NONBLIND development cases, and the automatic oracles.

The captured final states (``fixtures/solo/captured``) come from live RA-DET runs of 2026-11-16 through
``research_tools/solo/capture_final_states.py``; ``docs/experimental_mvp/solo/DEV_CASE_CHECK.md`` records them.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "solo"))
import auto_oracles as ao  # noqa: E402
import maintainer_oracle as mo  # noqa: E402

SPECS = REPO / "docs/experimental_mvp/solo/maintainer_specs"
FIX = Path(__file__).resolve().parent / "fixtures" / "solo"
TEST_FILES = {
    "saq-c570ba4": "saq-c570ba4__tests__test_queue.py.txt",
    "chancy-2895c4a": "chancy-2895c4a__tests__plugins__test_recovery.py.txt",
    "pgqueuer-8dfadf4": "pgqueuer-8dfadf4__test__integration__test_persistence_queries_sql.py.txt",
    "saq-addee31": None,
}


def _spec(case):
    return json.loads((SPECS / f"{case}.json").read_text())


def _test_file(case):
    f = TEST_FILES[case]
    return (FIX / "upstream_tests" / f).read_bytes() if f else b""


def _captured(case):
    out = []
    for p in sorted((FIX / "captured" / case).glob("*.json")):
        d = json.loads(p.read_text())
        out.append((p.name.split("__")[-2], d))
    return out


def test_upstream_test_files_are_the_fix_commits_blobs():
    for case, f in TEST_FILES.items():
        if f:
            assert mo.git_blob_id(_test_file(case)) == _spec(case)["upstream"]["test_blob"]


@pytest.mark.parametrize("case", ["saq-c570ba4", "saq-addee31"])
def test_two_saq_cases_are_refused_and_logged(case):
    log = []
    with pytest.raises(mo.Refused):
        mo.compile_oracle(_spec(case), _test_file(case), log)
    assert log and log[-1]["case_id"] == case and log[-1]["admitted"] is False
    if case == "saq-c570ba4":
        assert "in_memory_return_value" in log[-1]["reason"]
    else:
        assert "no regression test" in log[-1]["reason"]


@pytest.mark.parametrize("case", ["chancy-2895c4a", "pgqueuer-8dfadf4"])
def test_reproduced_cases_are_detected_through_their_maintainer_tests(case):
    oracle = mo.compile_oracle(_spec(case), _test_file(case))
    runs = _captured(case)
    r1 = [oracle.check(d["final_state"], d["raw_results"]) for slot, d in runs if slot == "R1"]
    assert r1 and "VIOLATED" in r1, "the defective revision must be detected"


@pytest.mark.parametrize("case", ["chancy-2895c4a", "pgqueuer-8dfadf4"])
def test_controls_hold(case):
    oracle = mo.compile_oracle(_spec(case), _test_file(case))
    r2 = [oracle.check(d["final_state"], d["raw_results"]) for slot, d in _captured(case) if slot == "R2"]
    assert len(r2) >= 8 and set(r2) == {"HOLDS"}


@pytest.mark.parametrize("case", ["chancy-2895c4a", "pgqueuer-8dfadf4"])
def test_maintainer_oracle_agrees_with_the_case_oracle_on_every_captured_run(case):
    oracle = mo.compile_oracle(_spec(case), _test_file(case))
    for _, d in _captured(case):
        assert oracle.check(d["final_state"], d["raw_results"]) == d["case_oracle_verdict"]


def test_a_spec_that_drops_an_assertion_is_refused():
    spec = _spec("chancy-2895c4a")
    spec["assertions"] = [a for a in spec["assertions"] if a["line"] != 167]
    assert any("167" in p for p in mo.check_spec(spec, _test_file("chancy-2895c4a")))
    with pytest.raises(mo.Refused):
        mo.compile_oracle(spec, _test_file("chancy-2895c4a"))


def test_a_spec_must_account_for_every_changed_test_function():
    spec = _spec("pgqueuer-8dfadf4")
    del spec["upstream"]["unused_test_functions"]["test_cancel_removes_failed_job"]
    assert any("changed test functions" in p for p in mo.check_spec(spec, _test_file("pgqueuer-8dfadf4")))


def test_a_spec_for_another_file_is_refused():
    assert any("blob id" in p for p in mo.check_spec(_spec("chancy-2895c4a"), _test_file("pgqueuer-8dfadf4")))


def test_precondition_gives_no_verdict():
    oracle = mo.compile_oracle(_spec("chancy-2895c4a"), _test_file("chancy-2895c4a"))
    _, d = _captured("chancy-2895c4a")[0]
    rr = copy.deepcopy(d["raw_results"])
    rr["push"]["cancelled"] = []
    assert oracle.check(d["final_state"], rr) == "NO_VERDICT"


def test_predicates_are_not_python():
    with pytest.raises(ValueError):
        mo.evaluate_predicate({"eval": "__import__('os')"}, {})


# ---- automatic oracles ----

BASE = {"verdict": "SOUND", "final_state": {"t": [{"id": 1, "v": "a", "updated_at": "x"}]},
        "raw_results": {"w": {"ok": True}}, "trace": ["SELECT 1", "UPDATE t SET v = 'a'"]}


def _m(**kw):
    return {**copy.deepcopy(BASE), **kw}


def test_differential_verdicts():
    changed_trace = ["SELECT 1", "UPDATE t SET v = 'a' WHERE TRUE"]
    assert ao.run_verdict(BASE, _m()) == "UNREACHABLE"
    assert ao.run_verdict(BASE, _m(trace=changed_trace)) == "HOLDS"
    assert ao.run_verdict(BASE, _m(trace=changed_trace, final_state={"t": [{"id": 1, "v": "b"}]})) == "VIOLATED"
    assert ao.run_verdict(BASE, _m(reached=True, final_state={"t": [{"id": 1, "v": "a", "updated_at": "y"}]})) \
        == "HOLDS", "volatile timestamps are ignored"
    assert ao.run_verdict(BASE, _m(verdict="REFUSED", reached=True)) == "NO_VERDICT"


def test_mutant_classification():
    differs = _m(reached=True, final_state={"t": []})
    same = _m(reached=True)
    pairs = [
        {"mutant_id": "k", "schedule": "serial", "base": BASE, "mutant": same},
        {"mutant_id": "k", "schedule": 1, "base": BASE, "mutant": differs},
        {"mutant_id": "e", "schedule": "serial", "base": BASE, "mutant": same},
        {"mutant_id": "e", "schedule": 1, "base": BASE, "mutant": same},
        {"mutant_id": "u", "schedule": "serial", "base": BASE, "mutant": _m()},
        {"mutant_id": "u", "schedule": 1, "base": BASE, "mutant": _m()},
        {"mutant_id": "s", "schedule": "serial", "base": BASE, "mutant": differs},
    ]
    out = ao.classify(pairs)
    assert out["k"]["verdict"] == "VIOLATED" and not out["k"]["serial_killed"] and out["k"]["serial_reachable"]
    assert out["e"]["verdict"] == "EQUIVALENT-SUSPECT"
    assert out["u"]["verdict"] == "UNREACHABLE" and not out["u"]["serial_reachable"]
    assert out["s"]["verdict"] == "VIOLATED" and out["s"]["serial_killed"]
