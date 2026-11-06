"""EXPERIMENTAL, offline: the paired detection analysis (research_tools/paired_eval.py), on synthetic data with known
answers, plus the conversion of the repository's existing development outputs."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools"))
import paired_eval as pe  # noqa: E402


def run(case, arm, i, *, det_at=None, used=1, budget=None, fp=False, refused=False, invalid=0, usd=0.0, secs=1.0,
        kind="defect", tin=0, tout=0):
    r = {"case": case, "arm": arm, "run_id": f"{arm}-{i}", "schedules_used": used, "detected": det_at is not None,
         "first_detection_schedule": det_at, "wall_seconds": secs, "invalid_proposals": invalid, "refused": refused,
         "fixed_version_violation": fp, "tokens_in": tin, "tokens_out": tout, "est_usd": usd, "seq": i,
         "case_kind": kind}
    if budget is not None:
        r["schedule_budget"] = budget
    return r


def write(tmp_path, rows):
    p = tmp_path / "runs.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def cell_rows(case, arm, first_hit, budget):
    """`budget` one-schedule runs; the first detection at run `first_hit` (1-based), or none."""
    return [run(case, arm, i, det_at=1 if first_hit is not None and i + 1 >= first_hit else None, budget=budget)
            for i in range(budget)]


# ------------------------------------------------------------------------------------------------ validation

def test_missing_required_field_and_extra_fields(tmp_path):
    r = run("c", "x", 0)
    del r["tokens_out"]
    with pytest.raises(pe.Refused, match="missing required"):
        pe.load([write(tmp_path, [r])])
    ok = run("c", "x", 0) | {"anything_else": [1, 2]}
    assert pe.load([write(tmp_path, [ok])])[0]["anything_else"] == [1, 2]


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r.update(detected=True), "disagree"),
    (lambda r: r.update(first_detection_schedule=3, detected=True), "outside"),
    (lambda r: r.update(schedules_used=True), "bool"),
    (lambda r: r.update(refused=True, detected=True, first_detection_schedule=1), "refused run"),
    (lambda r: r.update(case_kind="weird"), "unknown case_kind"),
    (lambda r: r.update(case_kind="no_known_defect", detected=True, first_detection_schedule=1), "no buggy"),
])
def test_inconsistent_rows_are_refused(tmp_path, mutate, msg):
    r = run("c", "x", 0)
    mutate(r)
    with pytest.raises(pe.Refused, match=msg):
        pe.load([write(tmp_path, [r])])


def test_duplicate_runs_are_refused(tmp_path):
    with pytest.raises(pe.Refused, match="duplicate"):
        pe.load([write(tmp_path, [run("c", "x", 0), run("c", "x", 0)])])


# ------------------------------------------------------------------------------------------------ per cell

def test_schedules_to_detection_accumulates_across_runs_and_censors_at_budget():
    rows = [run("c", "x", 0, used=3, budget=10), run("c", "x", 1, det_at=2, used=3, budget=10),
            run("c", "x", 2, used=4, budget=10)]
    for i, r in enumerate(rows):
        r["_order"] = i
    c = pe.summarize_cell(rows)
    assert c["detected"] and c["schedules_to_detection"] == 5 and not c["censored"]
    assert c["seconds_to_detection_upper_bound"] == 2.0 and c["runs"] == 3 and c["schedules_used"] == 10
    none = [run("c", "y", i, budget=4) | {"_order": i} for i in range(4)]
    n = pe.summarize_cell(none)
    assert not n["detected"] and n["censored"] and n["schedules_to_detection"] == 4


def test_truncation_counts_false_positives_after_a_detection_but_not_beyond_budget():
    rows = [run("c", "x", 0, det_at=1, budget=5), run("c", "x", 1, fp=True, budget=5),
            run("c", "x", 2, fp=True, budget=5)]
    for i, r in enumerate(rows):
        r["_order"] = i
    assert pe.summarize_cell(rows, budget=2)["fixed_version_false_positives"] == 1
    assert pe.summarize_cell(rows)["fixed_version_false_positives"] == 2


def test_budget_mismatch_is_refused_and_truncation_must_not_exceed():
    rows = cell_rows("c", "x", 1, 10) + cell_rows("c", "y", 1, 3)
    for i, r in enumerate(rows):
        r["_order"] = i
    table, _ = pe.paired_table(rows, "x", "y")
    with pytest.raises(pe.Refused, match="identical budgets"):
        pe.compare(table, "x", "y")
    t3, _ = pe.paired_table(rows, "x", "y", budget=3)
    assert pe.compare(t3, "x", "y")["paired_cases"] == 1
    with pytest.raises(pe.Refused, match="exceeds"):
        pe.paired_table(rows, "x", "y", budget=4)


def test_missing_time_makes_seconds_unknown():
    rows = [run("c", "x", 0, secs=None, budget=2), run("c", "x", 1, det_at=1, budget=2)]
    for i, r in enumerate(rows):
        r["_order"] = i
    assert pe.summarize_cell(rows)["seconds_to_detection_upper_bound"] is None


# ------------------------------------------------------------------------------------------------ statistics

def test_exact_sign_and_mcnemar_known_values():
    assert pe.binom_two_sided_sign(0, 0) == 1.0
    assert pe.mcnemar_exact(10, 0) == pytest.approx(2 / 1024)
    assert pe.mcnemar_exact(0, 10) == pytest.approx(2 / 1024)
    assert pe.mcnemar_exact(5, 5) == 1.0
    # n = 9, k = 1: 2 * (1 + 9) / 512
    assert pe.mcnemar_exact(1, 8) == pytest.approx(20 / 512)
    assert pe.binom_two_sided_sign(3, 6) == 1.0


def test_gehan_scores_with_censoring_and_ties():
    def c(t, cens):
        return {"schedules_to_detection": t, "censored": cens}
    assert pe.gehan_score(c(2, False), c(5, False)) == 1
    assert pe.gehan_score(c(5, False), c(2, False)) == -1
    assert pe.gehan_score(c(3, False), c(3, False)) == 0          # tie
    assert pe.gehan_score(c(10, True), c(10, True)) == 0          # both censored: indeterminate
    assert pe.gehan_score(c(10, False), c(10, True)) == 1         # detected at the last schedule beats none
    assert pe.gehan_score(c(4, True), c(7, False)) == 0           # censored earlier than the other's event


def test_kaplan_meier_median():
    assert pe.km_median([(1, False), (2, False), (3, False)]) == 2.0
    assert pe.km_median([(5, True), (5, True), (1, False)]) is None   # S never reaches 0.5
    assert pe.km_median([(1, False), (10, True), (10, True), (2, False)]) == 2.0


def test_insufficient_sample_even_with_a_perfect_split():
    rows = []
    for k in range(6):  # x always detects at schedule 1, y never: still fewer than 10 cases
        rows += cell_rows(f"case{k}", "x", 1, 5) + cell_rows(f"case{k}", "y", None, 5)
    for i, r in enumerate(rows):
        r["_order"] = i
    t, _ = pe.paired_table(rows, "x", "y")
    res = pe.compare(t, "x", "y")
    assert res["verdict"].startswith("INSUFFICIENT SAMPLE (6")
    assert res["mcnemar"]["p_exact_two_sided"] == pytest.approx(2 / 64)


def test_known_answer_with_enough_cases():
    rows = []
    for k in range(12):  # x detects in all 12; y in 2 of them; McNemar b=10, c=0
        rows += cell_rows(f"case{k}", "x", 1, 5) + cell_rows(f"case{k}", "y", 3 if k < 2 else None, 5)
    for i, r in enumerate(rows):
        r["_order"] = i
    t, _ = pe.paired_table(rows, "x", "y")
    res = pe.compare(t, "x", "y")
    assert res["mcnemar"]["b_only_x"] == 10 and res["mcnemar"]["c_only_y"] == 0
    assert res["mcnemar"]["p_exact_two_sided"] == pytest.approx(2 / 1024)
    assert res["verdict"] == "x better"
    assert res["gehan_sign"]["x_definitely_faster"] == 12
    assert res["totals"]["x"]["km_median_schedules_to_detection"] == 1.0
    assert res["totals"]["y"]["km_median_schedules_to_detection"] is None


def test_no_difference_and_gehan_used_when_detections_concordant():
    rows = []
    for k in range(10):  # both detect every case; x faster in 5, y faster in 5
        rows += cell_rows(f"c{k}", "x", 1 if k % 2 else 3, 5) + cell_rows(f"c{k}", "y", 3 if k % 2 else 1, 5)
    for i, r in enumerate(rows):
        r["_order"] = i
    t, _ = pe.paired_table(rows, "x", "y")
    res = pe.compare(t, "x", "y")
    assert res["mcnemar"]["p_exact_two_sided"] == 1.0 and res["gehan_sign"]["p_exact_two_sided"] == 1.0
    assert res["verdict"] == "NO DETECTED DIFFERENCE"


def test_no_known_defect_cases_count_false_positives_and_costs_but_not_detection():
    rows = (cell_rows("d1", "x", 1, 2) + cell_rows("d1", "y", None, 2)
            + [run("nd", "x", 0, kind="no_known_defect", fp=True, budget=2),
               run("nd", "x", 1, kind="no_known_defect", budget=2),
               run("nd", "y", 0, kind="no_known_defect", invalid=3, usd=0.02, tin=100, tout=900, budget=2),
               run("nd", "y", 1, kind="no_known_defect", refused=True, budget=2)])
    for i, r in enumerate(rows):
        r["_order"] = i
    t, _ = pe.paired_table(rows, "x", "y")
    res = pe.compare(t, "x", "y")
    assert res["paired_cases"] == 2 and res["paired_defect_cases"] == 1
    assert res["totals"]["x"]["fixed_version_false_positives"] == 1
    assert res["totals"]["y"]["invalid_proposals"] == 3 and res["totals"]["y"]["refused_runs"] == 1
    assert res["totals"]["y"]["est_usd"] == 0.02 and res["totals"]["y"]["tokens_out"] == 900
    assert res["warnings"]


def test_unpaired_cases_are_excluded_and_reported():
    rows = cell_rows("both", "x", 1, 2) + cell_rows("both", "y", 1, 2) + cell_rows("only_x", "x", 1, 2)
    for i, r in enumerate(rows):
        r["_order"] = i
    t, ex = pe.paired_table(rows, "x", "y")
    res = pe.compare(t, "x", "y", excluded=ex)
    assert res["unpaired_cases_excluded"] == ["only_x"] and res["paired_cases"] == 1


def test_cli_exit_codes(tmp_path):
    p = write(tmp_path, cell_rows("c", "x", 1, 3) + cell_rows("c", "y", None, 2))
    assert pe.main(["--runs", str(p), "--arms", "x", "y", "--out", str(tmp_path / "o")]) == 2  # budget mismatch
    assert pe.main(["--runs", str(p), "--arms", "x", "y", "--budget", "2", "--out", str(tmp_path / "o")]) == 0
    res = json.loads((tmp_path / "o" / "comparison.json").read_text())
    assert res["verdict"].startswith("INSUFFICIENT SAMPLE")


# ------------------------------------------------------------------------------------------------ existing data

def test_existing_development_outputs_convert_to_the_recorded_results():
    rows = pe.from_existing(ROOT)
    by = {}
    for r in rows:
        by.setdefault((r["case"], r["arm"]), []).append(r)
    hist = by[("chancy-2895c4a", "seeded_explore")]
    assert len(hist) == 10 and sum(r["detected"] for r in hist) == 9          # RESULTS.md: VIOLATED 9/10
    assert not any(r["fixed_version_violation"] for r in hist)               # the fix: HOLDS 10/10
    assert sum(r["detected"] for r in by[("chancy-2895c4a", "forced_schedule_fix_informed")]) == 3
    mut = by[("chancy-mutant-no_skip_locked_claim", "seeded_explore")]
    assert [r["run_id"] for r in mut if r["detected"]] == ["01_seeded1", "04_seeded4"]
    assert all(r["case_kind"] == "no_known_defect" for k, v in by.items() if k[0].startswith(("procrastinate",
               "pgqueuer", "chancy-0.25")) for r in v)
    assert not any(r["fixed_version_violation"] for r in rows)
    assert math.isclose(sum(r["est_usd"] for r in rows), 0.016684 + 0.009762 + 0.010854, abs_tol=1e-6)
