import math

from hypothesis import given, settings
from hypothesis import strategies as st

from deployspecter.models import Actor, ColumnValue, ExecutableSpec, SeedRow, Step
from deployspecter.scheduler import enumerate_schedules, preemptions, schedule_space_size

counts_strategy = st.dictionaries(
    st.sampled_from(["a", "b", "c"]), st.integers(min_value=1, max_value=3), min_size=1
)


@settings(deadline=None)
@given(counts_strategy)
def test_enumeration_is_complete_unique_and_order_preserving(counts):
    schedules = [tuple(s) for s in enumerate_schedules(counts)]
    assert len(schedules) == len(set(schedules)) == schedule_space_size(counts)
    for schedule in schedules:
        assert {a: schedule.count(a) for a in counts} == counts


@settings(deadline=None)
@given(counts_strategy)
def test_enumeration_orders_by_preemptions_with_serial_first(counts):
    schedules = list(enumerate_schedules(counts))
    bounds = [preemptions(s, counts) for s in schedules]
    assert bounds == sorted(bounds)
    assert bounds.count(0) == math.factorial(len(counts))


def test_seeded_order_is_reproducible_and_complete():
    counts = {"a": 3, "b": 3}
    first = list(enumerate_schedules(counts, seed=7))
    assert first == list(enumerate_schedules(counts, seed=7))
    assert first != list(enumerate_schedules(counts))
    assert sorted(map(tuple, first)) == sorted(map(tuple, enumerate_schedules(counts)))


SETUP = "CREATE TABLE t (id int primary key, v int);"
SEED = [
    SeedRow(table="t", values=[ColumnValue(column="id", value=1), ColumnValue(column="v", value=0)]),
    SeedRow(table="t", values=[ColumnValue(column="id", value=2), ColumnValue(column="v", value=0)]),
]


def _sql(text):
    return Step(kind="sql", sql=text, statement_id=text)


def _txn(*statements):
    return [Step(kind="begin"), *map(_sql, statements), Step(kind="commit")]


def _spec(a_steps, b_steps):
    return ExecutableSpec(
        name="t", generator="test", seed=SEED,
        actors=[Actor(name="a", function="f:a", steps=a_steps), Actor(name="b", function="f:b", steps=b_steps)],
    )


def test_real_deadlock_is_detected_and_classified_as_conflict(runner):
    spec = _spec(
        _txn("UPDATE t SET v = 1 WHERE id = 1", "UPDATE t SET v = 1 WHERE id = 2"),
        _txn("UPDATE t SET v = 2 WHERE id = 2", "UPDATE t SET v = 2 WHERE id = 1"),
    )
    trace = runner.run(spec, SETUP, ["a", "a", "b", "b", "a", "b", "a", "b"])
    assert trace.outcome == "transaction_conflict"
    errors = [r for r in trace.records if r.status == "error"]
    assert [e.sqlstate for e in errors] == ["40P01"]
    # The survivor's transaction committed; the victim's rolled back entirely.
    survivor = "a" if errors[0].actor == "b" else "b"
    expected = 1 if survivor == "a" else 2
    assert {r["v"] for r in trace.final_state["t"]} == {expected}


def test_ordinary_sql_error_rolls_back_and_skips_rest_of_actor(runner):
    spec = _spec(
        _txn("UPDATE t SET v = 9 WHERE id = 1", "UPDATE t SET v = 1 / 0 WHERE id = 2"),
        _txn("UPDATE t SET v = 5 WHERE id = 2"),
    )
    trace = runner.run(spec, SETUP, ["a"] * 4 + ["b"] * 3)
    assert trace.outcome == "execution_error"
    assert trace.records[3].status == "skipped_after_abort"
    assert trace.final_state["t"] == [{"id": 1, "v": 0}, {"id": 2, "v": 5}]


def test_entries_for_a_blocked_actor_are_deferred(sandbox):
    from deployspecter.scheduler import ScheduleRunner

    spec = _spec(_txn("UPDATE t SET v = 1 WHERE id = 1"), _txn("UPDATE t SET v = 2 WHERE id = 1"))
    with ScheduleRunner(sandbox) as runner:
        trace = runner.run(spec, SETUP, ["a", "a", "b", "b", "b", "a"])
    assert trace.outcome == "completed"
    assert trace.final_state["t"][0]["v"] == 2
    assert trace.issue_order == ["a", "a", "b", "b", "a", "b"]


def test_statement_timeout_is_classified_as_timeout(runner):
    import dataclasses

    sandbox = runner.sandbox
    original = sandbox.settings
    sandbox.settings = dataclasses.replace(
        original, timeouts=dataclasses.replace(original.timeouts, statement_ms=100)
    )
    sandbox.close_actor("a")
    try:
        spec = _spec([_sql("SELECT pg_sleep(5)")], [_sql("SELECT 1")])
        trace = runner.run(spec, SETUP, ["a", "b"])
    finally:
        sandbox.settings = original
        sandbox.close_actor("a")
    assert trace.outcome == "timeout"
    assert trace.records[0].sqlstate == "57014"


def test_mismatched_schedule_is_refused(runner):
    spec = _spec(_txn("SELECT 1"), _txn("SELECT 2"))
    try:
        runner.run(spec, SETUP, ["a", "b"])
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_killed_session_is_reported_and_the_next_schedule_recovers(runner):
    spec = _spec(_txn("UPDATE t SET v = 1 WHERE id = 1"), _txn("UPDATE t SET v = 2 WHERE id = 2"))
    first = runner.run(spec, SETUP, ["a", "a", "a", "b", "b", "b"])
    runner.sandbox.admin.execute(
        "SELECT pg_terminate_backend(%s)", (runner.sandbox.actor_connection("a").info.backend_pid,)
    )
    second = runner.run(spec, SETUP, ["a", "a", "a", "b", "b", "b"])
    assert second.outcome == "execution_error"  # never mistaken for a clean run or for corruption
    third = runner.run(spec, SETUP, ["a", "a", "a", "b", "b", "b"])
    assert third.outcome == "completed" and third.final_state == first.final_state
