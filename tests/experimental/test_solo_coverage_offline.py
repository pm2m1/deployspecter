"""Solo r3 design, spec format 2: the uses_deployspecter_code flag, mechanical verification of not-expressible
reasons, the per-case coverage record, and the oracle_depends_on_tool refusal (offline; captured fixtures)."""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "solo"))
import maintainer_oracle as mo  # noqa: E402

SPECS = REPO / "docs/experimental_mvp/solo/maintainer_specs"
FIX = Path(__file__).resolve().parent / "fixtures" / "solo"
FILES = {
    "saq-c570ba4": "saq-c570ba4__tests__test_queue.py.txt",
    "chancy-2895c4a": "chancy-2895c4a__tests__plugins__test_recovery.py.txt",
    "pgqueuer-8dfadf4": "pgqueuer-8dfadf4__test__integration__test_persistence_queries_sql.py.txt",
}


def spec(case, v1=False):
    base = FIX / "specs_v1" if v1 else SPECS
    return json.loads((base / f"{case}.json").read_text())


def upstream_file(case):
    return (FIX / "upstream_tests" / FILES[case]).read_bytes()


def captured(case):
    paths = sorted((FIX / "captured" / case).glob("*.json"))
    return [(p.name.split("__")[-2], json.loads(p.read_text())) for p in paths]


def line(s, n):
    return next(a for a in s["assertions"] if a["line"] == n)


def test_admitted_specs_are_format_2():
    for case in ("chancy-2895c4a", "pgqueuer-8dfadf4", "saq-c570ba4", "saq-addee31"):
        assert spec(case)["format"] == "ds-maintainer-spec/2"


@pytest.mark.parametrize("case", ["chancy-2895c4a", "pgqueuer-8dfadf4"])
def test_format_1_specs_are_still_read(case):
    s = spec(case, v1=True)
    assert s["format"] == "ds-maintainer-spec/1"
    o = mo.compile_oracle(s, upstream_file(case))
    for _, d in captured(case):
        assert o.check(d["final_state"], d["raw_results"]) == d["case_oracle_verdict"]
    assert o.coverage["expressed_without_deployspecter_code"] is None, "format 1 has no flags: unknown"


def test_missing_flag_is_refused():
    s = spec("chancy-2895c4a")
    del line(s, 164)["uses_deployspecter_code"]
    assert any("uses_deployspecter_code" in p for p in mo.check_spec(s, upstream_file("chancy-2895c4a")))
    with pytest.raises(mo.Refused):
        mo.compile_oracle(s, upstream_file("chancy-2895c4a"))


def test_false_flag_on_a_predicate_that_reads_deployspecter_data_is_refused():
    s = spec("pgqueuer-8dfadf4")
    line(s, 258)["uses_deployspecter_code"] = False
    problems = mo.check_spec(s, upstream_file("pgqueuer-8dfadf4"))
    assert any("line 258" in p and "final_state.runs" in p for p in problems)


def test_reads_deployspecter_resolves_bound_rows_and_the_workload_record():
    s = spec("pgqueuer-8dfadf4")
    ds = mo.reads_deployspecter(line(s, 258)["predicate"], s["sources"])
    assert ds and {p.split(".")[1] for p in ds} == {"runs"}
    for n in (253, 254, 264, 265):
        assert mo.reads_deployspecter(line(s, n)["predicate"], s["sources"]) == [], n
    worker = {"exists": {"rows": {"path": "raw_results.w1.handled"}, "as": "h"}}
    assert mo.reads_deployspecter(worker, s["sources"]) == ["raw_results.w1.handled"]


@pytest.mark.parametrize(("case", "ln", "reason"), [
    ("chancy-2895c4a", 167, "mock"), ("chancy-2895c4a", 168, "mock"),
    ("pgqueuer-8dfadf4", 259, "exception"),
    ("pgqueuer-8dfadf4", 249, "in_memory_return_value"), ("pgqueuer-8dfadf4", 271, "in_memory_return_value"),
    ("pgqueuer-8dfadf4", 273, "in_memory_return_value"), ("saq-c570ba4", 583, "in_memory_return_value"),
])
def test_mechanical_reasons_that_verify(case, ln, reason):
    s = spec(case)
    fn = next(iter(mo._functions(upstream_file(case).decode(), s["upstream"]["test_functions"]).values()))
    assert mo.verify_reason(fn, ln, reason)[0] == "verified"


@pytest.mark.parametrize(("case", "ln", "reason"), [
    ("chancy-2895c4a", 150, "mock"),                       # an assert on state, not a mock call
    ("chancy-2895c4a", 146, "in_memory_return_value"),     # the assertion is itself a call
    ("pgqueuer-8dfadf4", 256, "exception"),                # not inside pytest.raises
    ("pgqueuer-8dfadf4", 257, "in_memory_return_value"),   # reads job_log_history() in the assertion
])
def test_mechanical_reasons_that_fail_are_refused(case, ln, reason):
    s = spec(case)
    line(s, ln)["reason"] = reason
    problems = mo.check_spec(s, upstream_file(case))
    assert any(f"line {ln}" in p and "mechanical verification" in p for p in problems)
    with pytest.raises(mo.Refused):
        mo.compile_oracle(s, upstream_file(case))


def test_judgement_reasons_are_reported_not_refused():
    s = spec("chancy-2895c4a")
    fn = next(iter(mo._functions(upstream_file("chancy-2895c4a").decode(), s["upstream"]["test_functions"]).values()))
    assert mo.verify_reason(fn, 139, "schema_added_by_fix")[0] == "investigator_judgement"


def test_coverage_records_for_the_development_cases():
    chancy = mo.compile_oracle(spec("chancy-2895c4a"), upstream_file("chancy-2895c4a")).coverage
    assert chancy == {"case_id": "chancy-2895c4a", "format": "ds-maintainer-spec/2", "total": 8, "expressed": 2,
                      "expressed_without_deployspecter_code": 2, "expressed_with_deployspecter_code": 0,
                      "not_expressible_by_reason": {"intermediate_state": 1, "mock": 2, "not_in_final_state": 1,
                                                    "schema_added_by_fix": 2},
                      "mechanically_verified": 2, "verification_failed": 0, "investigator_judgement": 4,
                      "strict_subset": False}
    pgq = mo.compile_oracle(spec("pgqueuer-8dfadf4"), upstream_file("pgqueuer-8dfadf4")).coverage
    assert pgq == {"case_id": "pgqueuer-8dfadf4", "format": "ds-maintainer-spec/2", "total": 11, "expressed": 5,
                   "expressed_without_deployspecter_code": 4, "expressed_with_deployspecter_code": 1,
                   "not_expressible_by_reason": {"exception": 1, "in_memory_return_value": 3, "intermediate_state": 2},
                   "mechanically_verified": 4, "verification_failed": 0, "investigator_judgement": 2,
                   "strict_subset": False}


def test_refused_cases_still_get_a_coverage_record():
    log = []
    with pytest.raises(mo.Refused):
        mo.compile_oracle(spec("saq-c570ba4"), upstream_file("saq-c570ba4"), log)
    assert log[-1]["coverage"]["mechanically_verified"] == 1 and log[-1]["coverage"]["expressed"] == 0


def test_a_case_whose_expressed_assertions_all_use_deployspecter_code_is_refused():
    s = spec("pgqueuer-8dfadf4")
    for n in (253, 254, 264, 265):
        a = line(s, n)
        for k in ("predicate", "uses_deployspecter_code"):
            a.pop(k)
        a.update(kind="not_expressible", reason="not_in_final_state")
    log = []
    with pytest.raises(mo.Refused, match="oracle_depends_on_tool"):
        mo.compile_oracle(s, upstream_file("pgqueuer-8dfadf4"), log)
    assert log[-1]["admitted"] is False and log[-1]["reason"].startswith("oracle_depends_on_tool")


def test_detections_are_attributed_to_tool_code_or_not():
    pgq = mo.compile_oracle(spec("pgqueuer-8dfadf4"), upstream_file("pgqueuer-8dfadf4"))
    r1 = [pgq.evaluate(d["final_state"], d["raw_results"]) for slot, d in captured("pgqueuer-8dfadf4") if slot == "R1"]
    violated = [r for r in r1 if r["verdict"] == "VIOLATED"]
    assert violated and all(r["detected_only_through_deployspecter_code"] for r in violated)
    chancy = mo.compile_oracle(spec("chancy-2895c4a"), upstream_file("chancy-2895c4a"))
    r1 = [chancy.evaluate(d["final_state"], d["raw_results"]) for slot, d in captured("chancy-2895c4a") if slot == "R1"]
    assert r1 and all(r["verdict"] == "VIOLATED" and r["failed_without_deployspecter_code"] == [164] for r in r1)


def test_specs_unchanged_by_evaluation():
    s = spec("chancy-2895c4a")
    before = copy.deepcopy(s)
    mo.compile_oracle(s, upstream_file("chancy-2895c4a")).evaluate({}, {})
    assert s == before
