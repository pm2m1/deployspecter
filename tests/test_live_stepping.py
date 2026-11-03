"""Driver-level stepping of real Python functions on real PostgreSQL. Synthetic data, no network, no model requests."""

from __future__ import annotations

import threading
import time

import psycopg
import pytest
from psycopg import sql
from psycopg.adapt import Dumper
from psycopg.rows import dict_row

from deployspecter.live.driver import classify_sql
from deployspecter.live.explore import LiveBudget, explore_live
from deployspecter.live.runner import (
    ExplicitSchedule,
    LiveActor,
    LiveLimits,
    LiveRunner,
    LiveScenario,
    open_session,
    run_uninstrumented_serial,
)
from deployspecter.models import ColumnValue, SeedRow
from live_app import properties as props
from live_app import reservations as app

A5 = ["a"] * 5
B5 = ["b"] * 5
# Both actors read before either writes, then a finishes before b.
BOTH_READ_FIRST = ["a", "b", "a", "b", "a", "a", "a", "b", "b", "b"]


def row(table: str, **values) -> SeedRow:
    return SeedRow(table=table, values=[ColumnValue(column=k, value=v) for k, v in values.items()])


def seats(capacity=3, reserved=0, reservations=()) -> list[SeedRow]:
    rows = [row("events", id=1, capacity=capacity, reserved=reserved)]
    rows += [row("reservations", event_id=1, holder=h, qty=q) for h, q in reservations]
    return rows


def two_reservations(lock_row=False, capacity=3, reserved=0, qty=2) -> LiveScenario:
    return LiveScenario("two_reservations", app.SETUP_SQL, seats(capacity, reserved), [
        LiveActor("a", app.reserve_seats, dict(event_id=1, holder="alice", qty=qty, lock_row=lock_row)),
        LiveActor("b", app.reserve_seats, dict(event_id=1, holder="bob", qty=qty, lock_row=lock_row)),
    ])


def reserve_and_release(lock_row=False) -> LiveScenario:
    return LiveScenario("reserve_and_release", app.SETUP_SQL, seats(5, 2, [("carol", 2)]), [
        LiveActor("a", app.reserve_seats, dict(event_id=1, holder="alice", qty=2, lock_row=lock_row)),
        LiveActor("b", app.release_seats, dict(holder="carol", lock_row=lock_row)),
    ])


COUNTERS = "CREATE TABLE counters (id integer PRIMARY KEY, n integer NOT NULL);"
COUNTER_SEED = [row("counters", id=1, n=0), row("counters", id=2, n=0)]


def scenario(*actors, setup=COUNTERS, seed=None) -> LiveScenario:
    return LiveScenario("adhoc", setup, COUNTER_SEED if seed is None else seed, list(actors))


def ops(trace, actor, kind=None):
    return [r for r in trace.records if r.actor == actor and (kind is None or r.kind == kind)]


def counters(trace) -> dict[int, int]:
    return {r["id"]: r["n"] for r in trace.final_state["counters"]}


@pytest.fixture
def runner(sandbox):
    return LiveRunner(sandbox)


def violations_for(trace, serial_states, seeded=frozenset(), holders=None):
    holders = holders or {"a": "alice", "b": "bob"}
    return (
        props.check_invariants(trace.final_state)
        + props.check_results(trace.final_state, set(seeded), trace.results(), holders)
        + props.check_serializable(trace.final_state, serial_states)
    )


def serial_states(sandbox, scen) -> list:
    names = [a.name for a in scen.actors]
    return [
        props.canonical(run_uninstrumented_serial(sandbox, scen, order)["final_state"])
        for order in (names, names[::-1])
    ]


# ---------------------------------------------------------------------- interleavings of the application


class TestInterleavedApplication:
    def test_serial_order_takes_the_rejection_branch(self, runner):
        trace = runner.run(two_reservations(), ExplicitSchedule(A5 + B5))
        assert trace.outcome == "completed"
        assert trace.results()["a"] == {"status": "reserved", "available": 1}
        assert trace.results()["b"] == {"status": "rejected", "available": 1}
        assert [r.kind for r in ops(trace, "a")] == ["begin", "sql", "sql", "sql", "commit"]
        assert [r.kind for r in ops(trace, "b")] == ["begin", "sql", "sql", "commit"]
        assert "rejections" in ops(trace, "b")[2].sql

    def test_overlapping_reads_oversell_and_lose_an_update(self, runner, sandbox):
        scen = two_reservations()
        trace = runner.run(scen, ExplicitSchedule(BOTH_READ_FIRST))
        assert trace.outcome == "completed"
        assert trace.results()["a"]["status"] == trace.results()["b"]["status"] == "reserved"
        assert trace.final_state["events"][0]["reserved"] == 2
        assert sorted(r["holder"] for r in trace.final_state["reservations"]) == ["alice", "bob"]
        found = violations_for(trace, serial_states(sandbox, scen))
        assert any(v.startswith("R1") for v in found)
        assert any(v.startswith("R2") for v in found)
        assert any(v.startswith("R4") for v in found)

    def test_writes_use_the_value_read_in_this_run(self, runner):
        """Same schedule, different seed: the written parameter changes, so nothing was replayed."""
        low = runner.run(two_reservations(capacity=9, reserved=0), ExplicitSchedule(BOTH_READ_FIRST))
        high = runner.run(two_reservations(capacity=9, reserved=5), ExplicitSchedule(BOTH_READ_FIRST))
        assert ops(low, "a")[1].rows == [[9, 0]] and ops(low, "a")[2].params == [2, 1]
        assert ops(high, "a")[1].rows == [[9, 5]] and ops(high, "a")[2].params == [7, 1]

    def test_interleaving_changes_what_the_function_computes(self, runner):
        serial = runner.run(two_reservations(capacity=9), ExplicitSchedule(A5 + B5))
        overlapped = runner.run(two_reservations(capacity=9), ExplicitSchedule(BOTH_READ_FIRST))
        assert ops(serial, "b")[2].params == [4, 1]  # read a's committed 2, then added 2
        assert ops(overlapped, "b")[2].params == [2, 1]  # read 0 before a committed
        assert serial.results()["b"]["available"] == 5 and overlapped.results()["b"]["available"] == 7

    def test_row_lock_variant_makes_b_wait_then_read_fresh_data(self, runner, sandbox):
        scen = two_reservations(lock_row=True)
        trace = runner.run(scen, ExplicitSchedule(BOTH_READ_FIRST))
        assert trace.outcome == "completed"
        read = ops(trace, "b")[1]
        commit = ops(trace, "a", "commit")[0]
        assert read.blocked_by == ["a"]
        assert read.seq < commit.seq and read.completed_decision == commit.admitted_decision
        assert read.rows == [[3, 2]]
        assert trace.results()["b"] == {"status": "rejected", "available": 1}
        assert violations_for(trace, serial_states(sandbox, scen)) == []

    def test_reserve_and_release_also_loses_an_update(self, runner, sandbox):
        scen = reserve_and_release()
        order = ["b", "a", "b", "a", "b", "b", "b", "a", "a", "a", "a", "b"]
        trace = runner.run(scen, ExplicitSchedule(order))
        assert trace.results()["a"]["status"] == "reserved" and trace.results()["b"]["status"] == "released"
        found = violations_for(trace, serial_states(sandbox, scen), seeded={"carol"},
                               holders={"a": "alice", "b": "carol"})
        assert any(v.startswith("R1") for v in found)

    @pytest.mark.parametrize("build,seeded,holders,violating", [
        (two_reservations, set(), {"a": "alice", "b": "bob"}, True),
        (lambda: two_reservations(lock_row=True), set(), {"a": "alice", "b": "bob"}, False),
        (reserve_and_release, {"carol"}, {"a": "alice", "b": "carol"}, True),
        (lambda: reserve_and_release(lock_row=True), {"carol"}, {"a": "alice", "b": "carol"}, False),
    ])
    def test_whole_tree_of_each_scenario(self, runner, sandbox, build, seeded, holders, violating):
        scen = build()
        known = serial_states(sandbox, scen)
        result = explore_live(
            runner, scen, LiveBudget(max_runs=400),
            check=lambda t: violations_for(t, known, seeded=seeded, holders=holders),
        )
        assert result.exhausted and result.divergences == 0
        assert set(result.outcomes) == {"completed"}
        assert result.runs > 2
        # Serial orders first, and the requirements hold on each of them.
        assert result.verdicts[0] == [] and result.verdicts[1] == []
        assert any(result.verdicts) is violating


# ---------------------------------------------------------------------- locks


def update_one(conn, first, second=None):
    with conn.transaction():
        conn.execute("UPDATE counters SET n = n + 1 WHERE id = %s", (first,))
        if second is not None:
            conn.execute("UPDATE counters SET n = n + 1 WHERE id = %s", (second,))


class TestLocks:
    def test_a_lock_wait_is_not_a_deadlock_and_the_holder_keeps_going(self, runner):
        scen = scenario(LiveActor("a", update_one, {"first": 1}), LiveActor("b", update_one, {"first": 1}))
        trace = runner.run(scen, ExplicitSchedule(["a", "a", "b", "b", "a", "b"]))
        assert trace.outcome == "completed" and counters(trace)[1] == 2
        blocked = ops(trace, "b", "sql")[0]
        assert blocked.blocked_by == ["a"]
        waiting = [d for d in trace.decisions if d.blocked]
        assert waiting and waiting[0].blocked == {"b": ["a"]} and waiting[0].enabled == ["a"]
        # b's statement was admitted before a's commit and finished after it.
        commit = ops(trace, "a", "commit")[0]
        assert blocked.seq < commit.seq and blocked.completed_decision == commit.admitted_decision

    def test_commit_is_numbered_before_the_write_it_wakes(self, runner):
        def autocommit_write(conn):
            conn.execute("UPDATE counters SET n = 2 WHERE id = 1")

        scen = scenario(LiveActor("a", update_one, {"first": 1}), LiveActor("b", autocommit_write, autocommit=True))
        for _ in range(20):
            trace = runner.run(scen, ExplicitSchedule(["a", "a", "b", "a"]))
            commit, write = ops(trace, "a", "commit")[0], ops(trace, "b", "sql")[0]
            assert write.blocked_by == ["a"] and write.effect == "autocommitted"
            assert write.completed_decision == commit.admitted_decision
            assert (commit.commit_seq, write.commit_seq) == (1, 2)
            assert ops(trace, "a", "sql")[0].commit_seq == 1

    def test_transactions_do_overlap(self, runner):
        def holds(conn):
            with conn.transaction():
                return conn.execute("SELECT pg_backend_pid(), txid_current()").fetchone()[1]

        trace = runner.run(
            scenario(LiveActor("a", holds), LiveActor("b", holds)),
            ExplicitSchedule(["a", "b", "a", "b", "a", "b"]),
        )
        a, b = ops(trace, "a"), ops(trace, "b")
        assert a[0].seq < b[0].seq < a[2].seq  # b began while a's transaction was open
        assert trace.results()["a"] != trace.results()["b"]
        assert trace.connections["a"] != trace.connections["b"]
        assert ops(trace, "a", "sql")[0].rows[0][0] == trace.connections["a"]
        assert ops(trace, "b", "sql")[0].rows[0][0] == trace.connections["b"]

    def test_three_actor_lock_chain_resolves(self, runner):
        scen = scenario(*(LiveActor(n, update_one, {"first": 1}) for n in "abc"))
        trace = runner.run(scen, ExplicitSchedule(["a", "a", "b", "b", "c", "c", "a", "b", "c"]))
        assert trace.outcome == "completed" and counters(trace)[1] == 3
        assert ops(trace, "b", "sql")[0].blocked_by == ["a"]
        assert ops(trace, "c", "sql")[0].blocked_by

    def test_real_deadlock_is_left_to_postgresql(self, runner):
        scen = scenario(LiveActor("a", update_one, {"first": 1, "second": 2}),
                        LiveActor("b", update_one, {"first": 2, "second": 1}))
        trace = runner.run(scen, ExplicitSchedule(["a", "b", "a", "b", "a", "b"]))
        assert trace.outcome == "deadlock" and not trace.inconclusive
        victims = [r for r in trace.records if r.sqlstate == "40P01"]
        assert len(victims) == 1 and victims[0].error_category == "deadlock"
        survivor = "a" if victims[0].actor == "b" else "b"
        assert counters(trace) == {1: 1, 2: 1}, "only the survivor's two updates count"
        assert ops(trace, survivor, "commit")[0].effect == "committed"
        assert ops(trace, victims[0].actor, "rollback")[0].effect == "rolled_back"
        assert all(r.effect == "rolled_back" for r in ops(trace, victims[0].actor, "sql") if r.status == "ok")

    def test_application_retry_after_deadlock_runs_for_real(self, runner):
        def with_retry(conn, first, second):
            for attempt in range(3):
                try:
                    update_one(conn, first, second)
                    return {"attempts": attempt + 1}
                except psycopg.errors.DeadlockDetected:
                    continue
            return {"attempts": None}

        scen = scenario(LiveActor("a", with_retry, {"first": 1, "second": 2}),
                        LiveActor("b", with_retry, {"first": 2, "second": 1}))
        trace = runner.run(scen, ExplicitSchedule(["a", "b", "a", "b"]))
        assert trace.outcome == "completed"
        assert sorted(v["attempts"] for v in trace.results().values()) == [1, 2]
        assert counters(trace) == {1: 2, 2: 2}
        assert [r.error_category for r in trace.records if r.status == "error"] == ["deadlock"]

    def test_lock_timeout_is_classified(self, sandbox):
        def holder(conn):
            with conn.transaction():
                conn.execute("UPDATE counters SET n = 1 WHERE id = 1")
                conn.execute("SELECT pg_sleep(0.4)")

        runner = LiveRunner(sandbox, LiveLimits.from_settings(sandbox.settings, lock_ms=100))
        scen = scenario(LiveActor("a", holder), LiveActor("b", update_one, {"first": 1}))
        trace = runner.run(scen, ExplicitSchedule(["a", "a", "b", "b", "a", "a"]))
        assert trace.outcome == "lock_timeout" and trace.inconclusive
        assert ops(trace, "b", "sql")[0].sqlstate == "55P03"
        assert counters(trace)[1] == 1

    def test_lock_held_by_a_session_outside_the_run(self, sandbox):
        runner = LiveRunner(sandbox, LiveLimits.from_settings(sandbox.settings, lock_ms=150))
        outside: list[psycopg.Connection] = []

        def takes_lock_after_reset(conn):
            other = open_session(sandbox.settings, "outside", runner.limits, connection_class=psycopg.Connection)
            outside.append(other)
            other.execute("UPDATE counters SET n = 9 WHERE id = 1")
            update_one(conn, 1)

        try:
            trace = runner.run(scenario(LiveActor("a", takes_lock_after_reset)), ExplicitSchedule([]))
        finally:
            for conn in outside:
                conn.close()
        assert trace.outcome == "external_interference" and trace.inconclusive
        assert trace.external_blockers and ops(trace, "a", "sql")[0].status in {"aborted", "error"}
        assert not trace.scoreable

    def test_statement_timeout_is_classified(self, sandbox):
        def slow(conn):
            conn.execute("SELECT pg_sleep(5)")

        runner = LiveRunner(sandbox, LiveLimits.from_settings(sandbox.settings, statement_ms=100))
        trace = runner.run(scenario(LiveActor("a", slow)), ExplicitSchedule([]))
        assert trace.outcome == "statement_timeout" and trace.records[0].sqlstate == "57014"
        assert trace.records[-1].kind == "implicit_rollback"

    def test_handled_lock_timeout_still_cannot_be_scored(self, sandbox):
        def holder(conn):
            with conn.transaction():
                conn.execute("UPDATE counters SET n = 1 WHERE id = 1")
                conn.execute("SELECT pg_sleep(0.4)")

        def catches_timeout(conn):
            try:
                update_one(conn, 1)
            except psycopg.errors.LockNotAvailable:
                return "gave up politely"

        runner = LiveRunner(sandbox, LiveLimits.from_settings(sandbox.settings, lock_ms=100))
        scen = scenario(LiveActor("a", holder), LiveActor("b", catches_timeout))
        trace = runner.run(scen, ExplicitSchedule(["a", "a", "b", "b", "a", "a"]))
        assert trace.results()["b"] == "gave up politely"
        assert trace.outcome == "lock_timeout" and trace.inconclusive

    def test_serialization_failure_is_classified(self, runner):
        def read_then_write(conn):
            with conn.transaction():
                n = conn.execute("SELECT n FROM counters WHERE id = 1").fetchone()[0]
                conn.execute("UPDATE counters SET n = %s WHERE id = 1", (n + 1,))

        level = psycopg.IsolationLevel.REPEATABLE_READ
        scen = scenario(LiveActor("a", read_then_write, isolation=level),
                        LiveActor("b", read_then_write, isolation=level))
        trace = runner.run(scen, ExplicitSchedule(["a", "b", "a", "b", "a", "b", "a", "b"]))
        assert trace.outcome == "serialization_failure" and not trace.inconclusive
        assert [r.sqlstate for r in trace.records if r.status == "error"] == ["40001"]
        assert counters(trace)[1] == 1

    def test_idle_in_transaction_kill_is_inconclusive(self, sandbox):
        def idles(conn):
            with conn.transaction():
                conn.execute("UPDATE counters SET n = 7 WHERE id = 1")
                conn.execute("UPDATE counters SET n = 8 WHERE id = 1")

        def sleeper(conn):
            conn.execute("SELECT pg_sleep(0.4)")

        runner = LiveRunner(sandbox, LiveLimits.from_settings(sandbox.settings, idle_in_transaction_ms=100))
        trace = runner.run(scenario(LiveActor("a", idles), LiveActor("b", sleeper)),
                           ExplicitSchedule(["a", "a", "b", "a", "a", "b"]))
        assert trace.outcome in {"idle_in_transaction_timeout", "connection_lost"} and trace.inconclusive
        assert counters(trace)[1] == 0
        assert trace.sessions_left_open == 0


# ---------------------------------------------------------------------- transactions


class TestTransactions:
    def _one(self, runner, function, **kwargs):
        return runner.run(scenario(LiveActor("a", function, **kwargs)), ExplicitSchedule([]))

    def test_commit_makes_writes_durable(self, runner):
        trace = self._one(runner, update_one, kwargs={"first": 1})
        assert ops(trace, "a", "sql")[0].effect == "committed" and counters(trace)[1] == 1
        assert ops(trace, "a", "commit")[0].commit_seq == 1

    def test_exception_rolls_back_through_a_scheduled_step(self, runner):
        def boom(conn):
            with conn.transaction():
                conn.execute("UPDATE counters SET n = 5 WHERE id = 1")
                raise ValueError("application error")

        trace = self._one(runner, boom)
        assert trace.outcome == "python_exception" and not trace.inconclusive
        assert trace.actors[0].exception_type == "ValueError"
        assert [r.kind for r in trace.records] == ["begin", "sql", "rollback"]
        assert trace.records[1].effect == "rolled_back" and counters(trace)[1] == 0

    def test_explicit_rollback_and_force_rollback(self, runner):
        def explicit(conn):
            with conn.transaction():
                conn.execute("UPDATE counters SET n = 5 WHERE id = 1")
                raise psycopg.Rollback()

        def forced(conn):
            with conn.transaction(force_rollback=True):
                conn.execute("UPDATE counters SET n = 5 WHERE id = 2")

        for function in (explicit, forced):
            trace = self._one(runner, function)
            assert trace.outcome == "completed"
            assert trace.records[-1].kind == "rollback" and trace.records[1].effect == "rolled_back"
            assert counters(trace) == {1: 0, 2: 0}

    def test_nested_transaction_rolls_back_only_the_savepoint(self, runner):
        def nested(conn):
            with conn.transaction():
                conn.execute("UPDATE counters SET n = 1 WHERE id = 1")
                try:
                    with conn.transaction():
                        conn.execute("UPDATE counters SET n = 2 WHERE id = 2")
                        conn.execute("UPDATE counters SET missing = 1")
                except psycopg.errors.UndefinedColumn:
                    pass
                conn.execute("UPDATE counters SET n = 3 WHERE id = 1")

        trace = self._one(runner, nested)
        assert trace.outcome == "completed" and counters(trace) == {1: 3, 2: 0}
        kinds = [r.kind for r in trace.records]
        assert kinds == ["begin", "sql", "savepoint", "sql", "sql", "rollback_to", "sql", "commit"]
        by_sql = [(r.params, r.effect) for r in trace.records if r.kind == "sql" and r.status == "ok"]
        assert [e for _, e in by_sql] == ["committed", "savepoint_rolled_back", "committed"]
        assert trace.records[5].sql.startswith("ROLLBACK TO + RELEASE _pg3_")

    def test_released_savepoint_commits_with_its_parent(self, runner):
        def nested(conn):
            with conn.transaction(), conn.transaction(savepoint_name="inner"):
                conn.execute("UPDATE counters SET n = 2 WHERE id = 2")

        trace = self._one(runner, nested)
        assert [r.kind for r in trace.records] == ["begin", "savepoint", "sql", "release", "commit"]
        assert trace.records[2].effect == "committed"
        assert trace.records[1].sql == "SAVEPOINT inner"

    def test_transaction_control_as_text_on_an_autocommit_connection(self, runner):
        def textual(conn):
            conn.execute("UPDATE counters SET n = 1 WHERE id = 1")
            conn.execute("BEGIN")
            conn.execute("UPDATE counters SET n = 2 WHERE id = 2")
            conn.execute("SAVEPOINT s1")
            conn.execute("UPDATE counters SET n = 99 WHERE id = 2")
            conn.execute("ROLLBACK TO SAVEPOINT s1")
            conn.execute("COMMIT")

        trace = self._one(runner, textual, autocommit=True)
        assert counters(trace) == {1: 1, 2: 2}
        effects = [r.effect for r in trace.records if r.kind == "sql"]
        assert effects == ["autocommitted", "committed", "savepoint_rolled_back"]

    def test_commit_of_a_failed_transaction_is_recorded_as_a_rollback(self, runner):
        def swallows(conn):
            conn.execute("UPDATE counters SET n = 1 WHERE id = 1")
            try:
                conn.execute("UPDATE counters SET missing = 1")
            except psycopg.errors.UndefinedColumn:
                pass
            conn.commit()
            return "thought it committed"

        trace = self._one(runner, swallows)
        assert trace.results()["a"] == "thought it committed"
        assert trace.records[0].effect == "rolled_back" and trace.records[-1].effect == "rolled_back"
        assert trace.records[-1].commit_seq is None and counters(trace)[1] == 0

    def test_transaction_left_open_is_rolled_back_in_a_scheduled_step(self, runner):
        def forgets(conn):
            conn.execute("UPDATE counters SET n = 1 WHERE id = 1")

        trace = self._one(runner, forgets)
        assert [r.kind for r in trace.records] == ["sql", "implicit_rollback"]
        assert trace.records[0].effect == "rolled_back" and counters(trace)[1] == 0

    def test_unhandled_sql_error_is_execution_error_and_cleans_up(self, runner):
        def bad(conn):
            conn.execute("SELECT * FROM no_such_table")

        trace = self._one(runner, bad)
        assert trace.outcome == "execution_error" and trace.records[0].sqlstate == "42P01"
        assert trace.sessions_left_open == 0 and not trace.leaked_threads

    def test_results_read_by_the_application_are_untouched_by_recording(self, runner):
        def reads(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT id, n FROM counters ORDER BY id")
                first = cur.fetchone()
                rest = cur.fetchall()
                cur.execute("SELECT id FROM counters ORDER BY id")
                looped = [r["id"] for r in cur]
            tuples = conn.execute("SELECT id FROM counters ORDER BY id").fetchall()
            conn.rollback()
            return {"first": first, "rest": rest, "looped": looped, "tuples": tuples}

        trace = self._one(runner, reads)
        assert trace.results()["a"] == {
            "first": {"id": 1, "n": 0}, "rest": [{"id": 2, "n": 0}], "looped": [1, 2], "tuples": [[1], [2]],
        }
        assert trace.records[0].columns == ["id", "n"] and trace.records[0].rows == [[1, 0], [2, 0]]


# ---------------------------------------------------------------------- determinism


class TestReproduction:
    @pytest.mark.parametrize("build,order", [
        (two_reservations, BOTH_READ_FIRST),
        (lambda: two_reservations(lock_row=True), BOTH_READ_FIRST),
        (reserve_and_release, ["b", "a", "b", "a", "b", "b", "b", "a", "a", "a", "a", "b"]),
    ])
    def test_replaying_the_decisions_reproduces_behaviour(self, runner, build, order):
        first = runner.run(build(), ExplicitSchedule(order))
        for _ in range(10):
            again = runner.run(build(), ExplicitSchedule(first.chosen_schedule, strict=True))
            assert again.fingerprint() == first.fingerprint()

    def test_every_explored_path_replays_identically(self, runner):
        result = explore_live(runner, two_reservations(lock_row=True), LiveBudget(max_runs=400))
        assert result.exhausted
        for trace in result.traces:
            assert runner.run(two_reservations(lock_row=True), ExplicitSchedule(trace.chosen_schedule, strict=True)
                              ).fingerprint() == trace.fingerprint()

    def test_different_interleavings_have_different_fingerprints(self, runner):
        a = runner.run(two_reservations(), ExplicitSchedule(A5 + B5))
        b = runner.run(two_reservations(), ExplicitSchedule(BOTH_READ_FIRST))
        assert a.fingerprint() != b.fingerprint()

    def test_strict_schedule_reports_divergence(self, runner):
        trace = runner.run(two_reservations(lock_row=True), ExplicitSchedule(["a", "a", "b", "b", "b"], strict=True))
        assert trace.outcome == "schedule_divergence" and trace.inconclusive

    def test_tolerant_schedule_defers_then_records_it(self, runner):
        trace = runner.run(two_reservations(lock_row=True), ExplicitSchedule(["a", "a", "b", "b", "b", "a"]))
        assert trace.outcome == "completed"
        assert trace.chosen_schedule != trace.requested_schedule
        assert "deferred" in {d.source for d in trace.decisions}

    def test_schedule_naming_an_unknown_actor_is_refused_before_running(self, runner):
        with pytest.raises(ValueError, match="unknown actors"):
            runner.run(two_reservations(), ExplicitSchedule(["nobody"]))


# ---------------------------------------------------------------------- refused paths


def uses_stream(conn):
    list(conn.cursor().stream("SELECT 1"))


def uses_copy(conn):
    with conn.cursor().copy("COPY counters TO STDOUT"):
        pass


def uses_pipeline(conn):
    with conn.pipeline():
        conn.execute("SELECT 1")


def uses_named_cursor(conn):
    conn.cursor(name="server_side").execute("SELECT 1")


def uses_multi_statement(conn):
    conn.execute("UPDATE counters SET n = 1 WHERE id = 1; UPDATE counters SET n = 1 WHERE id = 2")


def uses_direct_client_cursor(conn):
    psycopg.ClientCursor(conn).execute("SELECT 1")


def uses_direct_raw_cursor(conn):
    psycopg.RawCursor(conn).execute("SELECT 1")


def changes_autocommit(conn):
    conn.autocommit = True


def closes_connection(conn):
    conn.close()


def cancels(conn):
    conn.cancel_safe()


def uses_two_phase(conn):
    conn.tpc_begin("xid")


def waits_for_notifies(conn):
    list(conn.notifies(timeout=0))


def shares_with_a_thread(conn):
    errors: list[BaseException] = []

    def other():
        try:
            conn.execute("SELECT 1")
        except BaseException as exc:  # noqa: B036
            errors.append(exc)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    raise errors[0]


def swallows_the_refusal(conn):
    try:
        conn.cursor().stream("SELECT 1")
    except Exception:
        return "carried on"


def text(statement: str):
    def run(conn):
        conn.execute(statement)

    run.__name__ = f"text_{statement.split()[0].lower()}"
    return run


class TestRefused:
    @pytest.mark.parametrize("function", [
        uses_stream, uses_copy, uses_pipeline, uses_named_cursor, uses_multi_statement,
        uses_direct_client_cursor, uses_direct_raw_cursor, changes_autocommit, closes_connection, cancels,
        uses_two_phase, waits_for_notifies, shares_with_a_thread, swallows_the_refusal,
        text("COPY counters TO STDOUT"), text("LISTEN channel"), text("NOTIFY channel"),
        text("DECLARE c CURSOR FOR SELECT 1"), text("SET ROLE deployspecter"), text("RESET ROLE"),
        text("SET search_path = public"), text("SET LOCAL ROLE deployspecter"), text("DISCARD ALL"),
        text("PREPARE TRANSACTION 'x'"), text("COMMIT PREPARED 'x'"),
    ], ids=lambda f: f.__name__)
    def test_marked_unsupported_with_a_reason(self, runner, function):
        trace = runner.run(scenario(LiveActor("a", function)), ExplicitSchedule([]))
        assert trace.outcome == "unsupported" and trace.inconclusive
        assert trace.refusals and trace.refusals[0].reason
        assert trace.sessions_left_open == 0 and not trace.leaked_threads
        assert counters(trace) == {1: 0, 2: 0}

    def test_pipeline_is_refused_by_name_and_not_only_by_the_traffic_guard(self, runner):
        trace = runner.run(scenario(LiveActor("a", uses_pipeline)), ExplicitSchedule([]))
        assert [r.operation for r in trace.refusals] == ["pipeline"]

    def test_refusal_outranks_an_application_error_that_came_first(self, runner):
        def fails(conn):
            conn.execute("SELECT * FROM no_such_table")

        def refused_later(conn):
            conn.execute("SELECT 1")
            uses_stream(conn)

        trace = runner.run(scenario(LiveActor("a", fails), LiveActor("b", refused_later)),
                           ExplicitSchedule(["a", "a", "b", "b"]))
        assert trace.records[0].error_category == "execution_error"
        assert trace.actors[0].outcome == "raised" and trace.actors[1].outcome == "refused"
        assert trace.outcome == "unsupported" and trace.inconclusive

    @pytest.mark.parametrize("statement,kind,refused", [
        ("SELECT ';' FROM t", "sql", False),
        ("SELECT 1; -- trailing comment", "sql", False),
        ("SELECT $$a;b$$, $tag$c;d$tag$", "sql", False),
        ("SELECT E'it\\'s;', \"we;ird\" FROM t /* ; */", "sql", False),
        ("SELECT 1; SELECT 2", "sql", True),
        ("  begin isolation level serializable", "begin", False),
        ("START TRANSACTION", "begin", False),
        ("/* c */ commit;", "commit", False),
        ("END", "commit", False),
        ("ABORT", "rollback", False),
        ("ROLLBACK TO SAVEPOINT sp", "rollback_to", False),
        ("ROLLBACK TO sp", "rollback_to", False),
        ("RELEASE SAVEPOINT sp", "release", False),
        ("SET LOCAL lock_timeout = 10", "sql", False),
        ("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE", "sql", False),
        ("SET statement_timeout = 0", "sql", True),
        ("SET SESSION AUTHORIZATION deployspecter", "sql", True),
        ("SELECT prepared FROM t", "sql", False),
    ])
    def test_statement_classification(self, statement, kind, refused):
        got = classify_sql(statement)
        assert got.kind == kind and bool(got.refused) is refused


# ---------------------------------------------------------------------- isolation of the harness


class TestHarnessBoundaries:
    def test_sessions_keep_the_sandbox_boundaries(self, runner):
        def who(conn):
            row = conn.execute(
                """SELECT current_user, current_database(), current_setting('search_path'),
                          current_setting('statement_timeout'), current_setting('lock_timeout'),
                          current_setting('idle_in_transaction_session_timeout'),
                          current_setting('deadlock_timeout'),
                          (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"""
            ).fetchone()
            conn.rollback()
            return row

        got = runner.run(scenario(LiveActor("a", who)), ExplicitSchedule([])).results()["a"]
        assert got == ["deployspecter_runner", "deployspecter_test", "ds_run", "5s", "3s", "15s", "50ms", False]

    def test_actor_cannot_alter_the_schema(self, runner):
        def ddl(conn):
            conn.execute("DROP TABLE counters")

        trace = runner.run(scenario(LiveActor("a", ddl)), ExplicitSchedule([]))
        assert trace.outcome == "execution_error" and trace.records[0].sqlstate == "42501"
        assert "counters" in trace.final_state

    def test_monitoring_is_not_intercepted(self, runner, sandbox):
        assert type(sandbox.monitor) is psycopg.Connection and type(sandbox.admin) is psycopg.Connection
        trace = runner.run(two_reservations(lock_row=True), ExplicitSchedule(BOTH_READ_FIRST))
        assert ops(trace, "b")[1].blocked_by  # the monitor really was consulted
        assert not [r for r in trace.records if "pg_blocking_pids" in r.sql or "pg_stat_activity" in r.sql]
        assert {r.actor for r in trace.records} == {"a", "b"}

    def test_plain_connections_stay_plain(self, sandbox):
        conn = psycopg.connect(sandbox.settings.test_dsn, autocommit=True)
        try:
            assert type(conn.cursor()) is psycopg.Cursor
        finally:
            conn.close()

    def test_provenance_names_the_application_line_without_paths(self, runner):
        trace = runner.run(two_reservations(), ExplicitSchedule(A5 + B5))
        for record in trace.records:
            assert record.provenance["module"].endswith("reservations")
            assert record.provenance["function"] == "reserve_seats" and record.provenance["line"] > 0
        assert "/" not in repr([r.provenance for r in trace.records])

    def test_nested_operation_from_inside_a_driver_call_is_refused(self, runner):
        class Reenters:
            def __init__(self, conn):
                self.conn = conn

            def __str__(self):
                self.conn.execute("SELECT 1")
                return "1"

        class ReenteringDumper(Dumper):
            def dump(self, obj):
                return str(obj).encode()

        def registers(conn):
            conn.adapters.register_dumper(Reenters, ReenteringDumper)
            conn.execute("SELECT %s::text", (Reenters(conn),))

        trace = runner.run(scenario(LiveActor("a", registers)), ExplicitSchedule([]))
        assert trace.outcome == "unsupported"
        assert "inside another" in trace.refusals[0].reason

    def test_python_stall_stops_the_run(self, sandbox):
        release = threading.Event()

        def hangs(conn):
            release.wait(5)

        limits = LiveLimits.from_settings(sandbox.settings, python_step_s=0.1, abort_grace_s=0.1)
        runner = LiveRunner(sandbox, limits)
        trace = runner.run(scenario(LiveActor("a", update_one, {"first": 1}), LiveActor("b", hangs)),
                           ExplicitSchedule([]))
        assert trace.outcome == "python_stall" and trace.inconclusive
        assert trace.leaked_threads == ["b"] and runner.poisoned
        assert counters(trace)[1] == 0
        with pytest.raises(RuntimeError, match="cannot be trusted"):
            runner.run(two_reservations(), ExplicitSchedule([]))
        release.set()

    def test_application_lock_between_actors_stalls_rather_than_hangs(self, sandbox):
        shared = threading.Lock()
        entered = threading.Event()

        def first(conn):
            with shared:
                entered.set()
                update_one(conn, 1)  # stops at its gate holding a Python lock

        def second(conn):
            entered.wait(2)
            with shared:
                update_one(conn, 2)

        limits = LiveLimits.from_settings(sandbox.settings, python_step_s=0.2)
        runner = LiveRunner(sandbox, limits)
        trace = runner.run(scenario(LiveActor("a", first), LiveActor("b", second)), ExplicitSchedule([]))
        assert trace.outcome == "python_stall"
        assert not trace.leaked_threads, "aborting a's gate releases the Python lock, so b ends too"

    def test_whole_run_timeout_cancels_a_running_statement(self, sandbox):
        def slow(conn):
            conn.execute("SELECT pg_sleep(30)")

        limits = LiveLimits.from_settings(sandbox.settings, run_s=0.2, statement_ms=60000, lock_ms=60000)
        trace = LiveRunner(sandbox, limits).run(scenario(LiveActor("a", slow)), ExplicitSchedule([]))
        assert trace.outcome == "run_timeout" and trace.inconclusive
        assert not trace.leaked_threads and trace.sessions_left_open == 0

    def test_abort_during_a_transaction_never_commits(self, sandbox):
        def writes_then_sleeps(conn):
            try:
                with conn.transaction():
                    conn.execute("UPDATE counters SET n = 1 WHERE id = 1")
                    conn.execute("SELECT pg_sleep(30)")
            except Exception:
                return "swallowed"

        limits = LiveLimits.from_settings(sandbox.settings, run_s=0.2, statement_ms=60000, lock_ms=60000)
        trace = LiveRunner(sandbox, limits).run(scenario(LiveActor("a", writes_then_sleeps)), ExplicitSchedule([]))
        assert trace.outcome == "run_timeout"
        assert counters(trace)[1] == 0 and trace.sessions_left_open == 0

    def test_abort_swallowed_inside_a_transaction_block_still_never_commits(self, sandbox):
        def swallows_everything(conn):
            with conn.transaction():
                conn.execute("UPDATE counters SET n = 1 WHERE id = 1")
                time.sleep(0.4)  # the run's limit passes here
                try:
                    conn.execute("SELECT 1")
                except BaseException:  # noqa: B036 - deliberately hostile to the abort
                    pass
            return "believes it committed"

        limits = LiveLimits.from_settings(sandbox.settings, run_s=0.2)
        trace = LiveRunner(sandbox, limits).run(scenario(LiveActor("a", swallows_everything)), ExplicitSchedule([]))
        assert trace.outcome == "run_timeout" and trace.results()["a"] == "believes it committed"
        assert counters(trace)[1] == 0

    def test_next_run_starts_clean_after_every_failure_mode(self, runner):
        for function in (uses_stream, closes_connection, text("SELECT * FROM missing")):
            runner.run(scenario(LiveActor("a", function)), ExplicitSchedule([]))
            clean = runner.run(scenario(LiveActor("a", update_one, {"first": 1})), ExplicitSchedule([]))
            assert clean.outcome == "completed" and counters(clean) == {1: 1, 2: 0}

    def test_composed_sql_is_recorded_as_text(self, runner):
        def composed(conn):
            conn.execute(sql.SQL("SELECT n FROM {} WHERE id = %s").format(sql.Identifier("counters")), (1,))
            conn.rollback()

        trace = runner.run(scenario(LiveActor("a", composed)), ExplicitSchedule([]))
        assert trace.records[0].sql == 'SELECT n FROM "counters" WHERE id = %s'


# ---------------------------------------------------------------------- controls


class TestSerialControls:
    @pytest.mark.parametrize("build", [
        two_reservations, lambda: two_reservations(lock_row=True), lambda: two_reservations(capacity=9),
        reserve_and_release, lambda: reserve_and_release(lock_row=True),
    ])
    @pytest.mark.parametrize("order", [["a", "b"], ["b", "a"]])
    def test_instrumented_serial_equals_plain_psycopg(self, runner, sandbox, build, order):
        scen = build()
        plain = run_uninstrumented_serial(sandbox, scen, order)
        # Run the first actor to completion before the second's first operation.
        schedule = [order[0]] * 16 + [order[1]] * 16
        traced = runner.run(scen, ExplicitSchedule(schedule))
        assert traced.outcome == "completed"
        assert traced.results() == plain["results"]
        assert traced.final_state == plain["final_state"]
        first_of_second = next(r.seq for r in traced.records if r.actor == order[1])
        assert all(r.seq < first_of_second for r in traced.records if r.actor == order[0])

    def test_plain_psycopg_control_reproduces_the_interleaved_state(self, runner, sandbox):
        """The same interleaving written out by hand on uninstrumented connections, with no application code."""
        scen = two_reservations()
        traced = runner.run(scen, ExplicitSchedule(BOTH_READ_FIRST))
        limits = LiveLimits.from_settings(sandbox.settings)
        sandbox.reset(scen.setup_sql, scen.seed)
        a = open_session(sandbox.settings, "ctl_a", limits, connection_class=psycopg.Connection)
        b = open_session(sandbox.settings, "ctl_b", limits, connection_class=psycopg.Connection)
        try:
            read = "SELECT capacity, reserved FROM events WHERE id = 1"
            assert a.execute(read).fetchone() == (3, 0)
            assert b.execute(read).fetchone() == (3, 0)
            a.execute("UPDATE events SET reserved = 2 WHERE id = 1")
            a.execute("INSERT INTO reservations (event_id, holder, qty) VALUES (1, 'alice', 2)")
            a.commit()
            b.execute("UPDATE events SET reserved = 2 WHERE id = 1")
            b.execute("INSERT INTO reservations (event_id, holder, qty) VALUES (1, 'bob', 2)")
            b.commit()
        finally:
            a.close()
            b.close()
        assert sandbox.snapshot() == traced.final_state

    def test_committed_effects_in_the_trace_agree_with_the_database(self, runner):
        trace = runner.run(two_reservations(), ExplicitSchedule(BOTH_READ_FIRST))
        committed = [r for r in trace.records if r.kind == "sql" and r.effect == "committed" and "INSERT" in r.sql]
        assert sorted(r.params[1] for r in committed) == sorted(
            x["holder"] for x in trace.final_state["reservations"]
        )
        assert [r.commit_seq for r in trace.records if r.kind == "commit"] == [1, 2]
