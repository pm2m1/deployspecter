import json

from deployspecter.benchmark import CaseScore, _agg, run_benchmark
from deployspecter.benchmark_report import write_report


def _report(generated_at, marker="x"):
    return {
        "split": "heldout", "generated_at": generated_at, "marker": marker, "summary": {}, "scores": [],
        "budget": {"schedules_per_case": 1, "seconds_per_case": 1}, "model": "m", "prompt_version": "v",
        "ai_repeats": 1, "notes": [],
    }


def test_later_report_does_not_replace_the_one_the_interface_shows(tmp_path):
    write_report(_report("2026-01-01T00:00:00+00:00", "first"), tmp_path)
    write_report(_report("2026-01-02T00:00:00+00:00", "second"), tmp_path)
    assert json.loads((tmp_path / "latest_heldout.json").read_text())["marker"] == "first"
    assert len(list(tmp_path.glob("heldout_*.json"))) == 2


def test_latest_moves_only_on_request(tmp_path):
    write_report(_report("2026-01-01T00:00:00+00:00", "first"), tmp_path)
    write_report(_report("2026-01-02T00:00:00+00:00", "second"), tmp_path, update_latest=True)
    assert json.loads((tmp_path / "latest_heldout.json").read_text())["marker"] == "second"


def test_corrected_case_with_no_valid_tests_is_not_a_clean_result():
    base = dict(split="heldout", system="ai", flagged=False, verified_by_execution=False, has_bug=False)
    ran = CaseScore(case_id="a/corrected", tests_proposed=3, tests_valid=3, schedules_executed=50, **base)
    rejected = CaseScore(case_id="b/corrected", tests_proposed=4, tests_valid=0, schedules_executed=0, **base)
    summary = _agg([ran, rejected])
    assert summary["corrected_runs"] == 2 and summary["corrected_runs_executed"] == 1


def test_benchmark_accepts_an_output_directory_outside_results(settings, tmp_path, monkeypatch):
    import deployspecter.database as database

    # The session's sandbox fixture holds the default key.
    monkeypatch.setattr(database, "_ADVISORY_LOCK_KEY", database._ADVISORY_LOCK_KEY + 3)
    report = run_benchmark(settings, "dev", ["static"], out_dir=tmp_path / "out")
    assert report["new_model_requests_sent"] == 0
    assert len(report["scores"]) == 4
