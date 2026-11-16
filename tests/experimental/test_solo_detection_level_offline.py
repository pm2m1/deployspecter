"""Solo r3 design: the detection-level sensitivity analysis (offline; synthetic records and the captured development
fixtures). A detection counts only if at least one VIOLATED assertion has uses_deployspecter_code false."""

import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "solo"))
sys.path.insert(0, str(REPO / "research_tools"))
import detection_level as dl  # noqa: E402
import maintainer_oracle as mo  # noqa: E402
import paired_eval as pe  # noqa: E402

SPECS = REPO / "docs/experimental_mvp/solo/maintainer_specs"
FIX = Path(__file__).resolve().parent / "fixtures" / "solo"
FILES = {
    "chancy-2895c4a": "chancy-2895c4a__tests__plugins__test_recovery.py.txt",
    "pgqueuer-8dfadf4": "pgqueuer-8dfadf4__test__integration__test_persistence_queries_sql.py.txt",
}


def oracle(case):
    spec = json.loads((SPECS / f"{case}.json").read_text())
    return mo.compile_oracle(spec, (FIX / "upstream_tests" / FILES[case]).read_bytes())


def V(*flags):
    return {"status": "VIOLATED",
            "violated_assertions": [{"line": 100 + i, "uses_deployspecter_code": f} for i, f in enumerate(flags)]}


H = {"status": "HOLDS", "violated_assertions": []}


def run(schedules, case="c1", arm="A", run_id="r0", fixed=False):
    sched = [dict(s, schedule=i) for i, s in enumerate(schedules, 1)]
    first = next((s["schedule"] for s in sched if s["status"] == "VIOLATED"), None)
    return {"record_format": dl.FORMAT, "case": case, "arm": arm, "run_id": run_id, "schedules_used": len(sched),
            "detected": first is not None, "first_detection_schedule": first, "wall_seconds": None,
            "invalid_proposals": 0, "refused": False, "fixed_version_violation": fixed, "tokens_in": 0,
            "tokens_out": 0, "est_usd": 0, "schedule_budget": 12, "defective_schedules": sched}


# ---------------------------------------------------------------------------------------------- per-run attribution

def test_detection_supported_only_by_tool_code_is_excluded():
    r = dl.annotate(dl.validate(run([V(True, True)])))
    assert r["detected"] and r["first_detection_schedule"] == 1
    assert r["detection_violated_with_deployspecter_code"] == [100, 101]
    assert r["detection_violated_without_deployspecter_code"] == []
    assert r["detected_without_deployspecter_code"] is False
    assert r["first_detection_schedule_without_deployspecter_code"] is None
    v = dl.detection_level_view([r])[0]
    assert v["detected"] is False and v["first_detection_schedule"] is None
    pe.validate(v)


def test_mixed_detection_is_kept():
    r = dl.annotate(dl.validate(run([H, V(True, False)])))
    assert r["detection_violated_with_deployspecter_code"] == [100]
    assert r["detection_violated_without_deployspecter_code"] == [101]
    assert r["first_detection_schedule_without_deployspecter_code"] == 2
    v = dl.detection_level_view([r])[0]
    assert v["detected"] and v["first_detection_schedule"] == 2


def test_detection_with_only_non_tool_assertions_is_kept():
    r = dl.annotate(dl.validate(run([V(False)])))
    assert r["detected_without_deployspecter_code"] is True
    assert dl.detection_level_view([r])[0]["first_detection_schedule"] == 1


def test_a_later_non_tool_violation_moves_the_detection_later():
    r = dl.annotate(run([V(True), H, V(False, True)]))
    assert r["first_detection_schedule"] == 1
    assert r["detection_violated_with_deployspecter_code"] == [100]
    assert r["first_detection_schedule_without_deployspecter_code"] == 3


def test_corrected_revision_violation_is_kept_in_the_view():
    v = dl.detection_level_view([run([V(True)], fixed=True)])[0]
    assert v["fixed_version_violation"] is True and v["detected"] is False


@pytest.mark.parametrize("mutate, match", [
    (lambda r: r.update(first_detection_schedule=2, schedules_used=2,
                        defective_schedules=[dict(V(False), schedule=1), dict(V(False), schedule=2)]),
     "first VIOLATED"),
    (lambda r: r["defective_schedules"][0]["violated_assertions"][0].update(uses_deployspecter_code=None),
     "boolean"),
    (lambda r: r["defective_schedules"][0].update(violated_assertions=[]), "non-empty exactly when VIOLATED"),
    (lambda r: r.update(schedules_used=2), "exactly schedules_used"),
    (lambda r: r["defective_schedules"][0].update(schedule=2), "numbered"),
    (lambda r: r.pop("record_format"), "record_format"),
])
def test_inconsistent_records_are_refused(mutate, match):
    r = run([V(False)])
    mutate(r)
    with pytest.raises(dl.Refused, match=match):
        dl.validate(r)


def test_schedule_entry_comes_from_the_oracle_record():
    rec = {"verdict": "VIOLATED", "violated_assertions": [{"line": 7, "uses_deployspecter_code": False}]}
    assert dl.schedule_entry(1, "VIOLATED", rec)["violated_assertions"] == rec["violated_assertions"]
    assert dl.schedule_entry(2, "INCOMPLETE", rec)["violated_assertions"] == []
    with pytest.raises(dl.Refused, match="maintainer oracle says"):
        dl.schedule_entry(1, "VIOLATED", {"verdict": "HOLDS", "violated_assertions": []})


# ---------------------------------------------------------------------------------------------- the analysis

def test_holm():
    assert dl.holm([0.01, 0.04]) == [0.02, 0.04]
    assert dl.holm([0.04, 0.01]) == [0.04, 0.02]
    assert dl.holm([0.03, 0.02]) == [0.04, 0.04]
    assert dl.holm([0.9, 0.8]) == [1.0, 1.0]


def _arms(n_cases, muse_flags):
    rows = []
    for i in range(n_cases):
        rows.append(run([V(muse_flags[i % len(muse_flags)])], case=f"c{i}", arm="RA-MD"))
        rows.append(run([H], case=f"c{i}", arm="RA-DET-G"))
        rows.append(run([V(False)] if i == 0 else [H], case=f"c{i}", arm="RA-DET"))
    return rows


def test_analysis_reports_both_counts_per_arm_and_says_descriptive():
    out = dl.analyse(_arms(4, [True, False]))
    assert out["per_arm"]["RA-MD"] == {"defect_cases": 4, "detected_main": 4, "detected_detection_level": 2,
                                       "detected_only_through_deployspecter_code": 2,
                                       "cases_detected_only_through_deployspecter_code": ["c0", "c2"]}
    assert out["per_arm"]["RA-DET"]["detected_main"] == out["per_arm"]["RA-DET"]["detected_detection_level"] == 1
    assert out["main"]["RA-MD vs RA-DET-G"]["mcnemar"]["b_only_RA-MD"] == 4
    assert out["detection_level"]["RA-MD vs RA-DET-G"]["mcnemar"]["b_only_RA-MD"] == 2
    assert set(out["main"]) == set(out["detection_level"]) == {"RA-MD vs RA-DET-G", "RA-MD vs RA-DET"}
    assert all("p_holm" in c["mcnemar"] for v in ("main", "detection_level") for c in out[v].values())
    assert "Descriptive only: 4 paired defect cases, fewer than 10." in out["detection_level_label"]
    assert "does not replace the main result" in out["detection_level_label"]


def test_analysis_with_ten_cases_is_not_labelled_descriptive():
    out = dl.analyse(_arms(10, [False]))
    assert "Descriptive only" not in out["detection_level_label"]
    assert out["detection_level"]["RA-MD vs RA-DET-G"]["verdict"] == "RA-MD better"


def test_cli_writes_the_report(tmp_path):
    p = tmp_path / "runs.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in _arms(3, [True])))
    assert dl.main(["--runs", str(p), "--out", str(tmp_path / "out")]) == 0
    rep = json.loads((tmp_path / "out" / "detection_level.json").read_text())
    assert rep["per_arm"]["RA-MD"]["detected_detection_level"] == 0
    bad = tmp_path / "bad.jsonl"
    r = run([V(False)])
    r["defective_schedules"][0]["violated_assertions"][0]["uses_deployspecter_code"] = None
    bad.write_text(json.dumps(r) + "\n")
    assert dl.main(["--runs", str(bad), "--out", str(tmp_path / "x")]) == 2


# ---------------------------------------------------------------------------------------------- development cases

def dev_runs(case):
    """RA-DET run records rebuilt from the captured oracle calls (one record per run index; R1 = defective revision,
    R2 = corrected). The live check found 0 mismatched parameters, so every captured VIOLATED counts as SOUND."""
    o = oracle(case)
    by = defaultdict(lambda: {"R1": [], "R2": []})
    for p in sorted((FIX / "captured" / case).glob("*.json")):
        _, _, arm, r, rev, s = p.stem.split("__")
        d = json.loads(p.read_text())
        by[r][rev].append((int(s[1:]), o.evaluate(d["final_state"], d["raw_results"])))
    rows = []
    for r, revs in sorted(by.items()):
        sched = [dl.schedule_entry(i, rec["verdict"], rec)
                 for i, (_, rec) in enumerate(sorted(revs["R1"], key=lambda x: x[0]), 1)]
        first = next((s["schedule"] for s in sched if s["status"] == "VIOLATED"), None)
        rows.append({"record_format": dl.FORMAT, "case": case, "arm": "RA-DET", "run_id": r,
                     "schedules_used": len(sched), "detected": first is not None, "first_detection_schedule": first,
                     "wall_seconds": None, "invalid_proposals": 0, "refused": False,
                     "fixed_version_violation": any(rec["verdict"] == "VIOLATED" for _, rec in revs["R2"]),
                     "tokens_in": 0, "tokens_out": 0, "est_usd": 0, "defective_schedules": sched,
                     "_order": len(rows)})
    return [dl.annotate(dl.validate(r)) for r in rows]


def test_oracle_record_lists_violated_assertions_with_their_flags():
    o = oracle("pgqueuer-8dfadf4")
    p = next(p for p in sorted((FIX / "captured" / "pgqueuer-8dfadf4").glob("*__R1__*.json"))
             if json.loads(p.read_text())["case_oracle_verdict"] == "VIOLATED")
    d = json.loads(p.read_text())
    assert o.evaluate(d["final_state"], d["raw_results"])["violated_assertions"] == [
        {"line": 258, "uses_deployspecter_code": True}]


def test_chancy_counts_through_line_164():
    rows = dev_runs("chancy-2895c4a")
    detecting = [r for r in rows if r["detected"]]
    assert detecting and all(r["detection_violated_without_deployspecter_code"] == [164] and
                             r["detection_violated_with_deployspecter_code"] == [] and
                             r["detected_without_deployspecter_code"] for r in detecting)
    assert not any(r["fixed_version_violation"] for r in rows)
    assert pe.cells(dl.detection_level_view(rows))[("chancy-2895c4a", "RA-DET")]["detected"] is True


def test_pgqueuer_does_not_count_line_258_only():
    rows = dev_runs("pgqueuer-8dfadf4")
    detecting = [r for r in rows if r["detected"]]
    assert len(detecting) == 3
    assert all(r["detection_violated_with_deployspecter_code"] == [258] and
               r["detection_violated_without_deployspecter_code"] == [] and
               not r["detected_without_deployspecter_code"] for r in detecting)
    assert not any(r["fixed_version_violation"] for r in rows)
    main = pe.cells(rows)[("pgqueuer-8dfadf4", "RA-DET")]
    level = pe.cells(dl.detection_level_view(rows))[("pgqueuer-8dfadf4", "RA-DET")]
    assert main["detected"] is True and main["schedules_to_detection"] == 2
    assert level["detected"] is False and level["censored"] is True
