"""Milestone: automatic discovery, then replay, with no failing schedule supplied."""

from deployspecter.explorer import Budget, explore
from deployspecter.minimizer import minimize
from deployspecter.oracle import evaluate
from deployspecter.replay import load_artifact, make_artifact, replay, save_artifact
from deployspecter.scheduler import preemptions


def _discover(load, hand_spec, runner, variant="buggy", **budget):
    case = load("backfill_overwrite", variant)
    spec = hand_spec(case)
    return case, spec, explore(runner, spec, case.setup_sql, case.requirements, Budget(**budget))


def test_explorer_finds_violation_without_being_given_a_schedule(load, hand_spec, runner):
    _, spec, result = _discover(load, hand_spec, runner)
    assert result.exhausted_space and result.executed == result.space_size == 84
    assert result.first_violation_index is not None
    # Not reachable serially: this finding really does need concurrency.
    assert result.violates_in_serial_order is False
    assert result.violating_total > 0
    assert result.non_serial_final_states == result.violating_total
    assert result.outcomes == {"completed": 84}
    assert all(preemptions(v.schedule, spec.step_counts()) >= 1 for v in result.violating)


def test_explorer_finds_no_violation_on_corrected_case_exhaustively(load, hand_spec, runner):
    _, _, result = _discover(load, hand_spec, runner, "corrected")
    assert result.exhausted_space and result.executed == 84
    assert result.violating_total == 0
    assert result.non_serial_final_states == 0


def test_budget_is_enforced(load, hand_spec, runner):
    _, _, result = _discover(load, hand_spec, runner, max_schedules=5)
    assert result.executed == 5 and not result.exhausted_space and result.stopped_by == "max_schedules"


def test_seed_is_recorded_and_reproduces_discovery_order(load, hand_spec, runner):
    _, _, first = _discover(load, hand_spec, runner, seed=11)
    _, _, second = _discover(load, hand_spec, runner, seed=11)
    assert first.seed == 11
    assert first.first_violation_index == second.first_violation_index


def test_saved_failure_replays_from_disk(load, hand_spec, runner, tmp_path):
    case, spec, result = _discover(load, hand_spec, runner, stop_on_first=True)
    artifact = make_artifact(
        case.case_id, case.setup_sql, case.requirements, spec, result.first_trace, result.first_verdict
    )
    path = save_artifact(artifact, tmp_path / "failure.json")

    report = replay(load_artifact(path), runner, attempts=5)
    assert report.reproduced, report.mismatches
    assert report.fingerprints_match == 5


def test_replay_detects_a_changed_system(load, hand_spec, runner):
    """A replay must fail when the failure no longer reproduces; otherwise it proves nothing."""
    case, spec, result = _discover(load, hand_spec, runner, stop_on_first=True)
    artifact = make_artifact(
        case.case_id, case.setup_sql, case.requirements, spec, result.first_trace, result.first_verdict
    )
    fixed = load("backfill_overwrite", "corrected")
    artifact.spec = hand_spec(fixed)
    report = replay(artifact, runner, attempts=1)
    assert not report.reproduced
    assert report.violations_match == 0


def test_minimizer_reduces_and_every_result_still_fails(load, hand_spec, runner):
    case, spec, result = _discover(load, hand_spec, runner)
    worst = max(result.violating, key=lambda v: v.preemptions)
    assert worst.preemptions > 1
    minimized = minimize(runner, spec, case.setup_sql, case.requirements, worst.schedule)

    assert minimized.after["preemptions"] < minimized.before["preemptions"]
    assert minimized.after["preemptions"] == 1
    # Re-verify independently of the minimizer's own bookkeeping.
    trace = runner.run(minimized.spec, case.setup_sql, minimized.schedule)
    assert evaluate(case.requirements, trace).classification == "true_violation"
    # Per-actor order preserved: each actor's issued step indices stay increasing.
    for actor in minimized.spec.actors:
        indices = [r.step_index for r in trace.records if r.actor == actor.name]
        assert indices == sorted(indices)


def test_minimizer_refuses_a_non_failing_schedule(load, hand_spec, runner):
    case = load("backfill_overwrite", "corrected")
    spec = hand_spec(case)
    schedule = ["user"] * 3 + ["backfill"] * 6
    try:
        minimize(runner, spec, case.setup_sql, case.requirements, schedule)
    except ValueError as exc:
        assert "nothing to minimize" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_minimizer_keeps_statements_the_source_always_runs(load, hand_spec, runner):
    """soft delete: the DELETE is irrelevant to the violation, but real code always issues it."""
    case = load("soft_delete_resurrection", "buggy")
    spec = hand_spec(case)
    result = explore(runner, spec, case.setup_sql, case.requirements, Budget())
    minimized = minimize(runner, spec, case.setup_sql, case.requirements, result.violating[0].schedule)
    kept = [s.statement_id for a in minimized.spec.actors for s in a.steps if s.kind == "sql"]
    assert "app_v2.py:delete_user#0" in kept and "app_v2.py:delete_user#1" in kept
