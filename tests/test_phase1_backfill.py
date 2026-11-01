"""Phase 1: controlled reproduction with a manually specified schedule.

This is a controlled reproduction, not automatic discovery; see test_explorer.py.
"""

from deployspecter.oracle import evaluate

# The classic stale-read order: backfill reads and commits its read transaction,
# the user update commits, and only then does the backfill's write transaction run.
MANUAL_STALE = ["backfill"] * 3 + ["user"] * 3 + ["backfill"] * 3


def _final(trace):
    return trace.final_state["profiles"][0]["display_name"]


def test_buggy_backfill_loses_committed_user_update(load, hand_spec, runner):
    case = load("backfill_overwrite", "buggy")
    trace = runner.run(hand_spec(case), case.setup_sql, MANUAL_STALE)

    assert trace.outcome == "completed"
    user_update = next(r for r in trace.records if r.actor == "user" and r.kind == "sql")
    assert user_update.rowcount == 1 and user_update.committed is True
    assert _final(trace) == "Alice"

    verdict = evaluate(case.requirements, trace)
    assert verdict.classification == "true_violation"
    assert verdict.violations[0].expected == "Alicia"
    assert verdict.violations[0].actual == "Alice"


def test_corrected_backfill_preserves_update_on_same_schedule(load, hand_spec, runner):
    case = load("backfill_overwrite", "corrected")
    trace = runner.run(hand_spec(case), case.setup_sql, MANUAL_STALE)

    assert trace.outcome == "completed"
    assert _final(trace) == "Alicia"
    backfill_write = [r for r in trace.records if r.actor == "backfill" and r.kind == "sql"][1]
    assert backfill_write.rowcount == 0  # the guard, not luck, kept the user's value
    assert evaluate(case.requirements, trace).classification == "no_violation"


def test_corrected_backfill_still_fills_untouched_rows(load, hand_spec, runner):
    """Guards against a 'fix' that passes by never writing anything."""
    case = load("backfill_overwrite", "corrected")
    spec = hand_spec(case)
    spec = spec.model_copy(update={"actors": [a for a in spec.actors if a.name == "backfill"]})
    trace = runner.run(spec, case.setup_sql, ["backfill"] * 6)
    assert _final(trace) == "Alice"


def test_lock_wait_is_observed_not_assumed(load, hand_spec, runner):
    """Backfill's UPDATE issued while the user's transaction holds the row lock."""
    case = load("backfill_overwrite", "corrected")
    # The third post-read backfill entry (COMMIT) is requested while its UPDATE is blocked.
    schedule = ["backfill"] * 3 + ["user", "user"] + ["backfill"] * 3 + ["user"]
    trace = runner.run(hand_spec(case), case.setup_sql, schedule)

    blocked = [r for r in trace.records if r.blocked_by]
    assert [(r.actor, r.blocked_by) for r in blocked] == [("backfill", ["user"])]
    assert _final(trace) == "Alicia"
    # READ COMMITTED re-checks the WHERE clause on the updated row after the lock wait.
    assert blocked[0].rowcount == 0
    # The COMMIT was deferred until the UPDATE returned; both orders are recorded.
    assert trace.requested_schedule[-2:] == ["backfill", "user"]
    assert trace.issue_order[-2:] == ["user", "backfill"]


def test_uncommitted_write_is_invisible_to_other_session(load, hand_spec, runner):
    case = load("backfill_overwrite", "buggy")
    schedule = ["user", "user"] + ["backfill"] * 3 + ["user"] + ["backfill"] * 3
    trace = runner.run(hand_spec(case), case.setup_sql, schedule)
    read = next(r for r in trace.records if r.actor == "backfill" and r.kind == "sql")
    assert read.rows == [{"id": 1, "legacy_name": "Alice"}]  # uncommitted Alicia not visible
    assert _final(trace) == "Alice"


def test_reset_restores_identical_state(load, hand_spec, runner):
    case = load("backfill_overwrite", "buggy")
    spec = hand_spec(case)
    first = runner.run(spec, case.setup_sql, MANUAL_STALE)
    second = runner.run(spec, case.setup_sql, MANUAL_STALE)
    assert first.initial_state == second.initial_state
    assert first.fingerprint() == second.fingerprint()
