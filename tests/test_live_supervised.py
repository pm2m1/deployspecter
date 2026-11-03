"""Gate 2A: a supervised run fails closed when execution escapes control. Disposable PostgreSQL only, no model requests.

These tests need the logging cluster described in ``GATE2A_REPORT.md``. Anywhere else they skip, loudly, and the
fail-closed test on an unprepared cluster still runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from urllib.parse import urlsplit

import psycopg
import pytest

from deployspecter.database import Sandbox
from deployspecter.live.explore import LiveBudget, explore_live
from deployspecter.live.runner import ExplicitSchedule, LiveActor, LiveLimits, LiveRunner, LiveScenario
from deployspecter.live.serverlog import LogUnverified, normal_sql, parse_window, reconcile
from deployspecter.live.supervisor import (
    REQUIRED_SETTINGS,
    InfrastructureFailure,
    SupervisedRunner,
    SupervisorHooks,
    SupervisorLimits,
)
from deployspecter.live.trace import LiveTrace, OpRecord
from deployspecter.models import ColumnValue, SeedRow
from live_app import adversarial as adv
from live_app import compat
from live_app import properties as props
from live_app import reservations as app
from live_app.proxy import FaultProxy

ADV = "live_app.adversarial:"
RES = "live_app.reservations:"
COMPAT = "live_app.compat:"
BOTH_READ_FIRST = ["a", "b", "a", "b", "a", "a", "a", "b", "b", "b"]
FAST = SupervisorLimits(escalation_grace_s=0.3, kill_wait_s=3.0)


@pytest.fixture(scope="module")
def prepared(sandbox):
    problems = SupervisedRunner(sandbox).preflight_problems()
    if problems:
        pytest.skip(f"needs the disposable logging cluster: {problems}")
    return sandbox


@pytest.fixture
def supervised(prepared):
    runner = SupervisedRunner(prepared)
    yield runner
    runner.close()


def actor(name, function, prefix=ADV, **options):
    kwargs = options.pop("kwargs", {})
    return LiveActor(name, f"{prefix}{function}", kwargs, **options)


def scen(*actors, setup=adv.SETUP, seed=None):
    return LiveScenario("supervised", setup, seed or [], list(actors))


def one(function, **options):
    return scen(actor("a", function, **options))


def kinds(trace):
    return {f.kind for f in trace.evidence.flags}


def values(trace, table="c"):
    return {r["id"]: r["v"] for r in trace.final_state[table]}


def assert_trusted(trace):
    assert trace.evidence.flags == []
    assert trace.scoreable and trace.evidence.quiescent and trace.evidence.role_dropped
    assert trace.evidence.reconciliation["unknown_sessions"] == []
    assert trace.evidence.reconciliation["statements_logged"] == trace.evidence.reconciliation["statements_matched"]


def assert_never_scored(trace):
    assert not trace.scoreable and trace.inconclusive


def run_roles(box):
    return box.admin.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ds_run_%'").fetchone()[0]


def seats(capacity=3, reserved=0):
    return [SeedRow(table="events", values=[
        ColumnValue(column="id", value=1), ColumnValue(column="capacity", value=capacity),
        ColumnValue(column="reserved", value=reserved),
    ])]


def two_reservations(lock_row=False, capacity=3, reserved=0):
    return LiveScenario("two_reservations", app.SETUP_SQL, seats(capacity, reserved), [
        actor("a", "reserve_seats", prefix=RES, kwargs=dict(event_id=1, holder="alice", qty=2, lock_row=lock_row)),
        actor("b", "reserve_seats", prefix=RES, kwargs=dict(event_id=1, holder="bob", qty=2, lock_row=lock_row)),
    ])


def reserve_and_release(lock_row=False):
    seed = [*seats(5, 2), SeedRow(table="reservations", values=[
        ColumnValue(column="event_id", value=1), ColumnValue(column="holder", value="carol"),
        ColumnValue(column="qty", value=2),
    ])]
    return LiveScenario("reserve_and_release", app.SETUP_SQL, seed, [
        actor("a", "reserve_seats", prefix=RES, kwargs=dict(event_id=1, holder="alice", qty=2, lock_row=lock_row)),
        actor("b", "release_seats", prefix=RES, kwargs=dict(holder="carol", lock_row=lock_row)),
    ])


# ------------------------------------------------------------------ 1. restricted login from connection creation


class TestRestrictedLogin:
    def test_clean_run_is_trusted(self, supervised):
        trace = supervised.run(one("clean"), ExplicitSchedule([]))
        assert trace.outcome == "completed" and trace.results()["a"] == "ok"
        assert_trusted(trace)

    def test_sessions_log_in_directly_with_verified_privileges(self, supervised):
        trace = supervised.run(one("clean"), ExplicitSchedule([]))
        role = f"ds_run_{trace.evidence.run_id}"
        for session in ("a", "monitor"):
            check = trace.evidence.privilege_checks[session]
            assert check["current_user"] == check["session_user"] == role
            assert not any(check[k] for k in (
                "superuser", "createdb", "createrole", "replication", "bypassrls", "schema_create",
                "database_create", "database_temp", "read_server_files", "write_server_files",
                "execute_server_program", "signal_backend",
            ))
            assert check["search_path"] == "ds_run" and check["database"].endswith("_test")
            assert check["statement_timeout"] == 5000 and check["lock_timeout"] == 3000
            assert check["idle_in_transaction_timeout"] == 15000 and check["deadlock_timeout"] == 50

    def test_child_holds_no_credentials(self, supervised):
        got = supervised.run(one("inspect_process"), ExplicitSchedule([])).results()["a"]
        assert got["env"] == [] and got["cwd_has_env_file"] is False
        assert got["user"] == got["session_user"] and got["user"].startswith("ds_run_")
        assert all(a.isdigit() for a in got["args"]), "nothing but the pipe number on the command line"
        assert got["open_sockets"] == 2, "its own actor session and monitor, and nothing inherited"
        assert got["connection_users"] == [got["user"]]

    def test_child_is_confined_away_from_private_paths(self, supervised, prepared, tmp_path):
        import sys

        if sys.platform != "darwin":
            pytest.skip("confinement uses macOS sandbox-exec")
        import shutil
        import tempfile

        repo = os.path.abspath(".")
        private = tempfile.mkdtemp(prefix=".ds-confinement-check-", dir=os.path.expanduser("~"))
        try:
            secret = os.path.join(private, "secret.txt")
            with open(secret, "w") as handle:
                handle.write("not for the child")
            targets = {
                "env_file": os.path.join(repo, ".env"),
                "repo_root_file": os.path.join(repo, "pyproject.toml"),
                "private_file": secret,
                "private_dir": private,
                "home_write": os.path.join(private, "written_by_child"),
                "tmp_write": str(tmp_path / "written_by_child"),
            }
            trace = supervised.run(one("probe_files", kwargs={"targets": targets}), ExplicitSchedule([]))
            assert not os.path.exists(targets["home_write"])
        finally:
            shutil.rmtree(private, ignore_errors=True)
        got = trace.results()["a"]
        assert trace.evidence.confinement.startswith("macos-sandbox")
        for name in ("env_file", "repo_root_file", "private_file", "private_dir", "home_write"):
            assert got[name] == "PermissionError", (name, got[name])
        assert got["tmp_write"] == "ok" and got["outbound_network"] == "PermissionError"
        assert_trusted(trace)

    def test_child_import_path_holds_no_private_directory(self, supervised):
        import sys

        trace = supervised.run(one("report_sys_path"), ExplicitSchedule([]))
        home = os.path.expanduser("~")
        repo = os.path.abspath(".")
        allowed = tuple(os.path.realpath(p) for p in (*(os.path.join(repo, d) for d in ("src", "tests", ".venv")),
                                                       sys.base_prefix))
        for entry in map(os.path.realpath, trace.results()["a"]):
            assert entry != os.path.realpath(repo), "the repository root, which holds .env, isn't importable"
            assert not entry.startswith(os.path.realpath(home)) or entry.startswith(allowed), entry

    def test_private_directory_on_the_parents_path_stays_off_the_childs(self, supervised, monkeypatch, tmp_path):
        import sys
        import tempfile

        private = tempfile.mkdtemp(prefix=".ds-path-check-", dir=os.path.expanduser("~"))
        try:
            monkeypatch.setattr(sys, "path", [private, os.path.abspath("."), *sys.path])
            child_path = supervised._child_sys_path(supervised._code_paths(one("clean")))
        finally:
            os.rmdir(private)
        assert os.path.realpath(private) not in child_path
        assert os.path.realpath(".") not in child_path, "the repository root holds .env"

    def test_role_verifier_does_not_reach_the_log(self, supervised, prepared):
        trace = supervised.run(one("clean"), ExplicitSchedule([]))
        path = prepared.admin.execute("SELECT pg_current_logfile('csvlog')").fetchone()[0]
        size = prepared.admin.execute("SELECT size FROM pg_stat_file(%s)", (path,)).fetchone()[0]
        start = max(0, size - 2_000_000)
        data = bytes(prepared.admin.execute("SELECT pg_read_binary_file(%s, %s, %s)", (path, start, size - start))
                     .fetchone()[0])
        assert trace.evidence.run_id.encode() in data
        assert b"SCRAM-SHA-256$" not in data
        assert b"PASSWORD" not in data.upper().replace(b"PASSWORD AUTHENTICATION", b"")

    def test_missing_privilege_check_is_not_accepted(self, supervised):
        from deployspecter.live.supervisor import RunContext

        flags = []
        ctx = RunContext("x", "ds_run_x", actor_pids={"a": 1, "b": 2})
        checks = {"a": supervised._expected_checks(ctx), "monitor": supervised._expected_checks(ctx)}
        supervised._verify_checks(ctx, checks, lambda k, d: flags.append((k, d)))
        assert flags and "['b']" in flags[0][1]

    def test_registered_session_must_belong_to_the_run_role(self, supervised, prepared):
        from deployspecter.live.supervisor import RunContext

        flags = []
        ctx = RunContext("x", "ds_run_x", actor_pids={"a": prepared.admin.info.backend_pid})
        supervised._verify_registration(ctx, lambda k, d: flags.append(k))
        assert flags == ["unmanaged_activity"]

    def test_handled_timeout_cannot_be_scored(self, prepared):
        limits = LiveLimits.from_settings(prepared.settings, statement_ms=100)
        trace = SupervisedRunner(prepared, limits).run(one("catches_statement_timeout"), ExplicitSchedule([]))
        assert trace.results()["a"] == "caught"
        assert trace.outcome == "statement_timeout"
        assert_never_scored(trace)

    def test_parameter_coverage_is_reported(self, supervised):
        trace = supervised.run(one("many_types"), ExplicitSchedule([]))
        summary = trace.evidence.reconciliation
        assert summary["parameters_compared"] == 1, "only the null is comparable among these five"
        assert summary["parameters_not_compared"] == 4
        batch = supervised.run(one("batch", kwargs={"rows": [[5, 1], [6, 2]]}, autocommit=True), ExplicitSchedule([]))
        assert batch.evidence.reconciliation["statements_parameters_unchecked"] == 2

    def test_temporary_tables_stay_denied_even_if_the_database_grant_returns(self, prepared):
        dbname = prepared.admin.info.dbname
        prepared.admin.execute(f"GRANT TEMPORARY ON DATABASE {dbname} TO PUBLIC")
        trace = SupervisedRunner(prepared).run(one("clean"), ExplicitSchedule([]))
        assert trace.evidence.privilege_checks["a"]["database_temp"] is False
        assert_trusted(trace)

    def test_trace_holds_no_credentials(self, supervised, prepared):
        admin = urlsplit(prepared.settings.test_dsn).password
        trace = supervised.run(one("own_connection_read", kwargs={"dsn": prepared.settings.test_dsn}),
                               ExplicitSchedule([]))
        assert admin not in trace.model_dump_json()

    def test_child_plan_has_the_run_login_and_not_the_admin_one(self, supervised, prepared):
        from deployspecter.live.supervisor import RunContext

        admin = urlsplit(prepared.settings.test_dsn)
        plan = supervised._plan(RunContext("x", "ds_run_x"), "pw", one("clean"), mode="stepped", schedule=[],
                                strict=False)
        assert plan["login"]["user"] == "ds_run_x" and plan["login"]["password"] == "pw"
        assert admin.password not in json.dumps(plan)

    def test_raw_driver_calls_gain_no_privilege(self, supervised):
        trace = supervised.run(one("raw_privilege_probe"), ExplicitSchedule([]))
        got = trace.results()["a"]
        for denied in ("SET ROLE deployspecter", "SET SESSION AUTHORIZATION deployspecter", "DROP TABLE ds_run.c",
                       "CREATE TABLE ds_run.x (i int)", "CREATE TEMP TABLE y (i int)", "CREATE ROLE z SUPERUSER",
                       "SELECT pg_read_file('postgresql.conf')", "COPY (SELECT 1) TO PROGRAM 'cat'"):
            assert got[denied] == "42501", denied
        assert got["RESET ROLE"] == "ok", "allowed, and it must change nothing"
        assert got["who"][0] == got["who"][1] and got["who"][2] == "f"
        assert "c" in trace.final_state and "x" not in trace.final_state
        assert "unmanaged_activity" in kinds(trace), "and those raw calls still can't be scored"
        assert_never_scored(trace)

    def test_a_wider_privilege_is_detected(self, prepared):
        def widen(runner, ctx):
            runner.box.admin.execute(f"GRANT CREATE ON SCHEMA ds_run TO deployspecter_runner")  # noqa: F541

        trace = SupervisedRunner(prepared, hooks=SupervisorHooks(after_reset=widen)).run(
            one("clean"), ExplicitSchedule([]),
        )
        assert "infrastructure_failure" in kinds(trace)
        assert any("schema_create" in f.detail for f in trace.evidence.flags)
        assert_never_scored(trace)

    def test_connection_limit_refuses_one_more_session_and_it_is_logged(self, supervised):
        trace = supervised.run(one("stolen_password_connection"), ExplicitSchedule([]))
        assert trace.results()["a"] == "refused"
        assert "unmanaged_activity" in kinds(trace), "the attempt itself is in the log"
        assert trace.evidence.reconciliation["unknown_sessions"]
        assert_never_scored(trace)

    def test_role_is_dropped_after_success_and_failure(self, supervised, prepared):
        for function in ("clean", "sql_error", "uses_stream", "raw_pgconn"):
            trace = supervised.run(one(function), ExplicitSchedule([]))
            assert trace.evidence.role_dropped, function
            assert run_roles(prepared) == 0, function

    def test_role_left_by_a_crash_is_swept_before_anything_runs(self, supervised, prepared):
        prepared.admin.execute("CREATE ROLE ds_run_crashed LOGIN PASSWORD 'x' IN ROLE deployspecter_runner")
        outside = psycopg.connect(prepared.settings.test_dsn)  # a session the crash also left behind
        trace = supervised.run(one("clean"), ExplicitSchedule([]))
        outside.close()
        assert_trusted(trace)
        assert run_roles(prepared) == 0

    def test_function_object_is_refused(self, supervised):
        with pytest.raises(ValueError, match="module:function"):
            supervised.run(scen(LiveActor("a", adv.clean)), ExplicitSchedule([]))

    def test_entrypoint_that_will_not_import_is_not_an_application_result(self, supervised):
        trace = supervised.run(scen(LiveActor("a", "no_such_module:nope")), ExplicitSchedule([]))
        assert trace.outcome == "infrastructure_failure"
        assert_never_scored(trace)

    def test_application_output_cannot_reach_the_results_pipe(self, supervised):
        assert_trusted(supervised.run(one("prints_to_stdout"), ExplicitSchedule([])))

    def test_engine_only_runner_is_never_scoreable(self, prepared):
        trace = LiveRunner(prepared).run(scen(LiveActor("a", adv.clean)), ExplicitSchedule([]))
        assert trace.outcome == "completed" and not trace.scoreable


class TestUnpreparedCluster:
    def test_no_application_code_runs_without_the_required_logging(self, sandbox):
        required = {**REQUIRED_SETTINGS, "log_statement": "this value is never set"}
        roles = run_roles(sandbox)
        runner = SupervisedRunner(sandbox, required_settings=required)
        trace = runner.run(scen(actor("a", "clean")), ExplicitSchedule([]))
        assert trace.outcome == "log_unverified" and not trace.records and not trace.actors
        assert run_roles(sandbox) == roles
        assert_never_scored(trace)


# ------------------------------------------------------------------ 2. unmanaged sessions and statements


class TestBypassesAreFlagged:
    @pytest.mark.parametrize("function,needs_dsn", [
        ("own_connection_read", True), ("own_connection_write", True), ("async_connection", True),
        ("thread_connection", True), ("subprocess_connection", True),
        ("raw_pgconn", False), ("raw_pgconn_write", False), ("parent_class_wait", False),
        ("repeated_identical_statement", False), ("raw_same_text_different_values", False),
        ("raw_prepared_transaction", False), ("uses_monitor_connection", False),
    ])
    def test_never_scored(self, supervised, prepared, function, needs_dsn):
        kwargs = {"dsn": prepared.settings.test_dsn} if needs_dsn else {}
        trace = supervised.run(one(function, kwargs=kwargs), ExplicitSchedule([]))
        assert trace.results().get("a") == "ok", "python returned normally, which must not be enough"
        assert "unmanaged_activity" in kinds(trace)
        assert trace.outcome == "unmanaged_activity"
        assert_never_scored(trace)
        assert trace.evidence.quiescent and trace.evidence.role_dropped

    def test_unmanaged_write_is_visible_in_state_and_not_scored(self, supervised, prepared):
        trace = supervised.run(one("own_connection_write", kwargs={"dsn": prepared.settings.test_dsn}),
                               ExplicitSchedule([]))
        assert values(trace)[1] == 777 and not trace.scoreable

    def test_raw_write_on_a_registered_session_is_not_scored(self, supervised):
        trace = supervised.run(one("raw_pgconn_write"), ExplicitSchedule([]))
        assert not trace.scoreable
        assert any("UPDATE c SET v = 888" in s for s in trace.evidence.reconciliation["unexpected_statements"])

    def test_prepared_transaction_cannot_commit_later(self, supervised, prepared):
        trace = supervised.run(one("raw_prepared_transaction"), ExplicitSchedule([]))
        assert any("prepared" in f.detail for f in trace.evidence.flags)
        assert values(trace)[1] == 0
        assert prepared.admin.execute("SELECT count(*) FROM pg_prepared_xacts").fetchone()[0] == 0

    def test_live_activity_poll_sees_an_unregistered_session(self, supervised, prepared):
        trace = supervised.run(one("holds_own_connection", kwargs={"dsn": prepared.settings.test_dsn}),
                               ExplicitSchedule([]))
        assert any("unregistered session" in f.detail for f in trace.evidence.flags)

    def test_session_counter_is_supporting_evidence(self, supervised):
        flags = []
        current = supervised._one("SELECT sessions FROM pg_stat_database WHERE datname = current_database()")[0]
        evidence = LiveTrace(scenario="x", requested_schedule=[]).evidence
        supervised._check_session_counter(current, {}, ["a"], evidence, lambda k, d: flags.append(k), None)
        assert flags == [] and evidence.session_counter_delta == 0
        supervised._check_session_counter(current - 5, {}, ["a"], evidence, lambda k, d: flags.append(k), None)
        assert flags == ["unmanaged_activity"]

    def test_forged_result_on_the_pipe_is_rejected(self, supervised):
        trace = supervised.run(one("write_fake_result"), ExplicitSchedule([]))
        assert "unmanaged_activity" in kinds(trace)
        assert_never_scored(trace)


def _rows_for(events):
    rows = []
    for pid, user, message, detail in events:
        rows.append(["t", user, "db", str(pid), "", "", "", "", "", "", "", "LOG", "00000", message, detail, "", "", "",
                     "", "", "", "", "", "client backend", "", ""])
    return rows


def _session(pid):
    return [(pid, "r", "connection authorized: user=r", "")], [(pid, "r", "disconnection: session time", "")]


def _trace(*ops):
    records = [
        OpRecord(seq=i, actor=a, actor_step=i, kind=k, sql=s, backend_pid=p, status="ok", params=v,
                 txn_before="IDLE", txn_after="IDLE")
        for i, (a, p, k, s, v) in enumerate(ops)
    ]
    return LiveTrace(scenario="x", requested_schedule=[], records=records)


class TestReconcilerUnit:
    PIDS = {1: "a", 2: "b"}

    def _run(self, trace, statements, pids=None):
        pids = pids or self.PIDS
        start, end = [], []
        for pid in pids:
            s, e = _session(pid)
            start += s
            end += e
        return reconcile(_rows_for(start + statements + end), trace, pids, None, set())

    def test_clean(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None), ("b", 2, "sql", "SELECT 2", None))
        report = self._run(trace, [(1, "r", "statement: SELECT 1", ""), (2, "r", "statement: SELECT 2", "")])
        assert report.clean and report.statements_matched == 2

    def test_cross_session_order_must_match_admission_order(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None), ("b", 2, "sql", "SELECT 2", None))
        report = self._run(trace, [(2, "r", "statement: SELECT 2", ""), (1, "r", "statement: SELECT 1", "")])
        assert report.order_violations and not report.clean

    def test_claimed_statement_missing_from_the_log(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None), ("a", 1, "sql", "SELECT 2", None))
        report = self._run(trace, [(1, "r", "statement: SELECT 1", "")])
        assert report.missing_statements and not report.clean

    def test_session_end_must_be_logged(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None))
        rows = _rows_for([(1, "r", "connection authorized: user=r", ""), (1, "r", "statement: SELECT 1", "")])
        report = reconcile(rows, trace, {1: "a"}, None, set())
        assert report.missing_session_events and not report.clean

    def test_session_seen_twice_is_flagged(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None))
        s, e = _session(1)
        report = reconcile(_rows_for(s + s + [(1, "r", "statement: SELECT 1", "")] + e), trace, {1: "a"}, None, set())
        assert report.missing_session_events

    def test_parameter_mismatch_on_identical_text(self):
        trace = _trace(("a", 1, "sql", "SELECT %s::int", [5]))
        wrong = self._run(trace, [(1, "r", "execute <unnamed>: SELECT $1::int", "Parameters: $1 = '999'")], {1: "a"})
        right = self._run(trace, [(1, "r", "execute <unnamed>: SELECT $1::int", "Parameters: $1 = '5'")], {1: "a"})
        assert wrong.unexpected_statements and right.clean

    def test_unknown_session_with_no_statements_is_flagged(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None))
        report = self._run(trace, [(1, "r", "statement: SELECT 1", ""),
                                   (9, "", "connection received: host=x", "")], {1: "a"})
        assert report.unknown_sessions

    def test_cancel_connection_is_accepted_only_on_a_stopped_run(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None))
        s, e = _session(1)
        rows = _rows_for(s + [(1, "r", "statement: SELECT 1", ""), (9, "", "connection received: host=x", "")] + e)
        assert reconcile(rows, trace, {1: "a"}, None, set(), halted=False).unknown_sessions
        assert not reconcile(rows, trace, {1: "a"}, None, set(), halted=True).unknown_sessions
        failed = _rows_for(s + [(1, "r", "statement: SELECT 1", ""), (9, "", "connection received: host=x", ""),
                                (9, "", "password authentication failed", "")] + e)
        assert reconcile(failed, trace, {1: "a"}, None, set(), halted=True).unknown_sessions

    def test_monitor_may_only_run_its_own_statements(self):
        trace = _trace(("a", 1, "sql", "SELECT 1", None))
        report = reconcile(
            _rows_for([(3, "r", "connection authorized: user=r", ""), (3, "r", "statement: UPDATE c SET v = 1", ""),
                       (3, "r", "disconnection: x", "")]),
            trace, {3: "monitor"}, 3, set(),
        )
        assert report.unexpected_statements

    def test_normal_sql_treats_placeholders_alike(self):
        assert normal_sql("SELECT %s,\n  %(a)s") == normal_sql("SELECT $1, $2") == "SELECT ?, ?"
        assert normal_sql('SAVEPOINT "x"') == "SAVEPOINT x"


class TestLogParsing:
    def _row(self, message):
        return ",".join(["t", "", "", "1", "", "", "", "", "", "", "", "LOG", "", f'"{message}"'] + [""] * 12) + "\n"

    def test_window(self):
        data = (self._row("mark_start") + self._row("hello") + self._row("mark_end") + self._row("after")).encode()
        rows = parse_window(data, "mark_start", "mark_end")
        assert [r[13] for r in rows] == ["hello"]

    def test_not_finished_yet(self):
        assert parse_window((self._row("mark_start") + self._row("x")).encode(), "mark_start", "mark_end") is None

    def test_start_marker_missing(self):
        with pytest.raises(LogUnverified, match="start marker"):
            parse_window((self._row("x") + self._row("mark_end")).encode(), "mark_start", "mark_end")

    def test_wrong_column_count_inside_the_window(self):
        data = self._row("mark_start") + "a,b,c\n" + self._row("mark_end")
        with pytest.raises(LogUnverified, match="unparsable"):
            parse_window(data.encode(), "mark_start", "mark_end")

    def test_multi_line_statement_keeps_its_field(self):
        data = self._row("mark_start") + self._row("statement: SELECT\n1") + self._row("mark_end")
        assert parse_window(data.encode(), "mark_start", "mark_end")[0][13] == "statement: SELECT\n1"


def _program(box, command):
    box.admin.execute(f"COPY (SELECT 1) TO PROGRAM '{command}; cat > /dev/null'")


def _log_path(box):
    return box.admin.execute("SELECT pg_current_logfile('csvlog')").fetchone()[0]


class TestLogFailuresFailClosed:
    def _run_with(self, prepared, fault, function="clean"):
        def before(runner, ctx):
            fault(runner.box)
            time.sleep(0.05)

        limits = SupervisorLimits(log_wait_s=0.5)
        return SupervisedRunner(prepared, supervisor=limits, hooks=SupervisorHooks(before_reconcile=before)).run(
            one(function), ExplicitSchedule([]),
        )

    def _assert_recovers(self, supervised):
        assert_trusted(supervised.run(one("clean"), ExplicitSchedule([])))

    def test_deleted_log(self, prepared, supervised):
        trace = self._run_with(prepared, lambda box: _program(box, f"rm -f {_log_path(box)}"))
        assert trace.outcome == "log_unverified"
        assert_never_scored(trace)
        prepared.admin.execute("SELECT pg_rotate_logfile()")
        time.sleep(0.2)
        self._assert_recovers(supervised)

    def test_truncated_log(self, prepared, supervised):
        trace = self._run_with(prepared, lambda box: _program(box, f"truncate -s 0 {_log_path(box)}"))
        assert trace.outcome == "log_unverified"
        assert_never_scored(trace)
        self._assert_recovers(supervised)

    def test_unparsable_line_inside_the_window(self, prepared, supervised):
        trace = self._run_with(prepared, lambda box: _program(box, f"echo a,b,c >> {_log_path(box)}"))
        assert trace.outcome == "log_unverified"
        assert_never_scored(trace)
        self._assert_recovers(supervised)

    def test_unterminated_quote_inside_the_window(self, prepared, supervised):
        trace = self._run_with(prepared, lambda box: _program(box, f"printf %s \\\"oops >> {_log_path(box)}"))
        assert trace.outcome == "log_unverified"
        assert_never_scored(trace)

    def test_logging_switched_off_during_the_run(self, prepared, supervised):
        # A per-database setting overrides the server's command line for sessions started afterwards.
        def off(runner, ctx):
            runner.box.admin.execute("ALTER DATABASE deployspecter_test SET log_statement = 'none'")

        def on(runner, ctx):
            runner.box.admin.execute("ALTER DATABASE deployspecter_test RESET log_statement")

        try:
            trace = SupervisedRunner(prepared, hooks=SupervisorHooks(after_reset=off, before_reconcile=on)).run(
                one("clean"), ExplicitSchedule([]),
            )
        finally:  # also when the run never reaches reconciliation, or every later run is refused
            prepared.admin.execute("ALTER DATABASE deployspecter_test RESET log_statement")
        assert "unmanaged_activity" in kinds(trace)
        assert trace.evidence.reconciliation["missing_statements"]
        assert_never_scored(trace)
        self._assert_recovers(supervised)

    def test_logging_off_before_the_run_refuses_to_start(self, prepared):
        prepared.admin.execute("ALTER DATABASE deployspecter_test SET log_statement = 'none'")
        try:
            trace = SupervisedRunner(prepared).run(one("clean"), ExplicitSchedule([]))
            assert trace.outcome == "log_unverified" and not trace.records
        finally:
            prepared.admin.execute("ALTER DATABASE deployspecter_test RESET log_statement")


# ------------------------------------------------------------------ 4. external lock holders


class _Outside:
    """A session outside the run, opened after reset, closed by the test."""

    def __init__(self, box, *statements):
        self.conn = psycopg.connect(box.settings.test_dsn, autocommit=False)
        self.conn.execute("SET search_path = ds_run")
        for statement in statements:
            self.conn.execute(statement)

    def close(self):
        self.conn.close()


class TestExternalLockHolders:
    @pytest.mark.parametrize("statements,function", [
        (["UPDATE c SET v = 9 WHERE id = 1"], "bump"),
        (["LOCK TABLE c IN ACCESS EXCLUSIVE MODE"], "table_reader"),
        (["SELECT pg_advisory_lock(424250)"], "takes_advisory"),
    ], ids=["row", "table", "advisory"])
    def test_held_before_the_run(self, prepared, statements, function):
        holders = []
        hooks = SupervisorHooks(after_reset=lambda r, c: holders.append(_Outside(r.box, *statements)))
        trace = SupervisedRunner(prepared, hooks=hooks).run(one(function), ExplicitSchedule([]))
        assert "external_interference" in kinds(trace)
        assert trace.external_blockers == [holders[0].conn.info.backend_pid]
        assert_never_scored(trace)

    def test_prepared_transaction_holding_a_lock(self, prepared):
        def hold(runner, ctx):
            admin = runner.box.admin
            admin.execute("BEGIN")
            admin.execute("UPDATE c SET v = 9 WHERE id = 1")
            admin.execute("PREPARE TRANSACTION 'outside'")

        trace = SupervisedRunner(prepared, hooks=SupervisorHooks(after_reset=hold)).run(
            one("bump"), ExplicitSchedule([]),
        )
        assert "external_interference" in kinds(trace) and trace.external_blockers == [0]
        assert_never_scored(trace)
        assert prepared.admin.execute("SELECT count(*) FROM pg_prepared_xacts").fetchone()[0] == 0

    def test_lock_taken_during_the_run(self, prepared):
        holders = []

        def tick(runner, ctx):
            if ctx.actor_pids and not holders:
                holders.append(_Outside(runner.box, "UPDATE c SET v = 9 WHERE id = 1"))

        trace = SupervisedRunner(prepared, hooks=SupervisorHooks(on_tick=tick)).run(
            one("pause_then_bump"), ExplicitSchedule([]),
        )
        assert "external_interference" in kinds(trace) or "unmanaged_activity" in kinds(trace)
        assert holders and trace.outcome in {"unmanaged_activity", "external_interference"}
        assert_never_scored(trace)

    def test_autovacuum_must_be_off(self):
        assert REQUIRED_SETTINGS["autovacuum"] == "off"


# ------------------------------------------------------------------ 5. surviving threads and processes


def _limits(prepared, **overrides):
    return LiveLimits.from_settings(prepared.settings, **overrides)


class TestProcessEscalation:
    @pytest.mark.parametrize("skip,expected_steps,outcome", [
        (frozenset({"abort"}), ["abort", "cancel"], "statement_timeout"),
        (frozenset({"abort", "cancel"}), ["abort", "cancel", "terminate"], "connection_lost"),
        (frozenset({"abort", "cancel", "terminate"}), ["abort", "cancel", "terminate", "kill"], "harness_killed"),
    ], ids=["cancel_works", "cancel_lost", "terminate_lost"])
    def test_each_step_when_the_earlier_ones_are_lost(self, prepared, skip, expected_steps, outcome):
        limits = _limits(prepared, statement_ms=60000, lock_ms=60000, run_s=60)
        supervisor = SupervisorLimits(escalation_grace_s=0.4, run_deadline_s=0.3)
        started = time.monotonic()
        trace = SupervisedRunner(prepared, limits, supervisor, SupervisorHooks(skip_steps=skip)).run(
            one("sleeping_statement"), ExplicitSchedule([]),
        )
        assert time.monotonic() - started < 15
        assert [e["step"] for e in trace.evidence.escalation][:len(expected_steps)] == expected_steps
        assert trace.outcome == outcome
        assert trace.evidence.quiescent and trace.evidence.role_dropped
        assert not trace.scoreable

    def test_a_cancel_really_lost_on_the_server_ends_in_termination(self, prepared):
        limits = _limits(prepared, statement_ms=60000, lock_ms=60000, run_s=60)
        supervisor = SupervisorLimits(escalation_grace_s=0.4, run_deadline_s=0.3)
        trace = SupervisedRunner(prepared, limits, supervisor, SupervisorHooks(skip_steps=frozenset({"abort"}))).run(
            one("uncancellable_statement"), ExplicitSchedule([]),
        )
        steps = [e["step"] for e in trace.evidence.escalation]
        assert steps[:3] == ["abort", "cancel", "terminate"], "the cancel was sent, and did nothing"
        assert trace.outcome == "connection_lost" and trace.evidence.quiescent
        assert_never_scored(trace)

    def test_child_abort_request_stops_the_run(self, prepared):
        limits = _limits(prepared, statement_ms=60000, lock_ms=60000, run_s=60, abort_grace_s=2.0)
        trace = SupervisedRunner(prepared, limits, SupervisorLimits(run_deadline_s=0.3)).run(
            one("sleeping_statement"), ExplicitSchedule([]),
        )
        assert trace.evidence.escalation[0]["step"] == "abort"
        assert trace.outcome == "run_timeout" and any("supervisor" in d for d in trace.diagnostics)
        assert_never_scored(trace)

    @pytest.mark.parametrize("function,overrides", [
        ("infinite_python_loop", {"python_step_s": 0.2, "abort_grace_s": 0.2}),
        ("python_sleep_forever", {"python_step_s": 0.2, "abort_grace_s": 0.2}),
    ])
    def test_python_that_never_reaches_a_gate(self, prepared, function, overrides):
        started = time.monotonic()
        runner = SupervisedRunner(prepared, _limits(prepared, **overrides), FAST)
        trace = runner.run(one(function), ExplicitSchedule([]))
        assert time.monotonic() - started < 10
        assert trace.outcome == "harness_killed" and trace.leaked_threads == ["a"]
        assert trace.evidence.quiescent and trace.evidence.role_dropped
        assert_never_scored(trace)

    def test_c_call_holding_the_gil_can_only_be_killed(self, prepared):
        limits = _limits(prepared, run_s=0.3, abort_grace_s=0.2, python_step_s=0.2)
        started = time.monotonic()
        trace = SupervisedRunner(prepared, limits, FAST).run(one("gil_holding_c_call"), ExplicitSchedule([]))
        assert time.monotonic() - started < 15
        assert [e["step"] for e in trace.evidence.escalation] == ["abort", "cancel", "terminate", "kill"]
        assert trace.outcome == "harness_killed" and trace.evidence.child_exit == -9
        assert trace.evidence.quiescent and trace.evidence.role_dropped

    def test_next_run_is_clean_after_a_kill(self, prepared, supervised):
        limits = _limits(prepared, run_s=0.3, abort_grace_s=0.2, python_step_s=0.2)
        SupervisedRunner(prepared, limits, FAST).run(one("gil_holding_c_call"), ExplicitSchedule([]))
        trace = supervised.run(one("bump"), ExplicitSchedule([]))
        assert_trusted(trace)
        assert values(trace) == {1: 1, 2: 0}


class TestAbandonedExecution:
    def _state(self, prepared):
        return dict(prepared.admin.execute("SELECT id, v FROM ds_run.c ORDER BY id").fetchall())

    def test_thread_left_behind_dies_with_the_child(self, supervised, prepared):
        trace = supervised.run(one("abandoned_thread_commits_later", kwargs={"delay": 0.3}), ExplicitSchedule([]))
        assert trace.results()["a"] == "ok" and trace.evidence.child_exit is not None
        time.sleep(0.6)
        assert self._state(prepared) == {1: 0, 2: 0}

    @staticmethod
    def _alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        # A killed child stays a zombie until something reaps it, and it can't act.
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith("Z")

    def test_surviving_detached_process_is_found_and_killed(self, supervised, tmp_path):
        pid_file = tmp_path / "pid"
        trace = supervised.run(one("detached_process_left_running", kwargs={"pid_file": str(pid_file)}),
                               ExplicitSchedule([]))
        assert any("outlived the run" in f.detail for f in trace.evidence.flags)
        assert_never_scored(trace)
        pid = int(pid_file.read_text())
        for _ in range(100):
            if not self._alive(pid):
                break
            time.sleep(0.02)
        assert not self._alive(pid)

    def test_child_left_in_the_process_group_is_killed(self, supervised, tmp_path):
        pid_file = tmp_path / "pid"
        supervised.run(one("child_in_the_process_group_left_running", kwargs={"pid_file": str(pid_file)}),
                       ExplicitSchedule([]))
        pid = int(pid_file.read_text())
        for _ in range(100):
            if not self._alive(pid):
                break
            time.sleep(0.02)
        assert not self._alive(pid)

    def test_detached_process_cannot_log_in_later(self, supervised, prepared, tmp_path):
        out = tmp_path / "result"
        trace = supervised.run(
            one("detached_process_commits_later", kwargs={"delay": 0.5, "result_file": str(out)}),
            ExplicitSchedule([]),
        )
        assert trace.evidence.role_dropped
        for _ in range(100):
            if out.exists():
                break
            time.sleep(0.05)
        assert out.exists(), "the detached process finished its attempt"
        assert "committed" not in out.read_text() and "OperationalError" in out.read_text()
        assert self._state(prepared) == {1: 0, 2: 0}

    def test_detached_process_with_an_open_session_cannot_commit_later(self, prepared, tmp_path):
        out, pid_file = tmp_path / "result", tmp_path / "pid"
        runner = SupervisedRunner(prepared, hooks=SupervisorHooks(extra_slots=1))
        trace = runner.run(
            one("detached_process_holds_session", kwargs={
                "delay": 0.8, "result_file": str(out), "pid_file": str(pid_file),
            }),
            ExplicitSchedule([]),
        )
        assert "unmanaged_activity" in kinds(trace)
        for _ in range(200):
            if out.exists():
                break
            time.sleep(0.05)
        assert "committed" not in out.read_text()
        assert self._state(prepared) == {1: 0, 2: 0}
        assert_never_scored(trace)

    def test_later_connection_attempt_is_caught_by_the_next_run_if_it_lands_there(self, supervised, tmp_path):
        out = tmp_path / "result"
        supervised.run(one("detached_process_commits_later", kwargs={"delay": 0.15, "result_file": str(out)}),
                       ExplicitSchedule([]))
        trace = supervised.run(one("pause_then_bump", kwargs={"pause": 0.5}), ExplicitSchedule([]))
        assert not trace.scoreable or out.exists(), "either flagged, or it finished before this run's window"


# ------------------------------------------------------------------ 6. lost cancels and disconnections


def _terminate_when(condition):
    done = threading.Event()

    def tick(runner, ctx):
        if done.is_set() or not ctx.actor_pids:
            return
        rows = runner.box.admin.execute(
            "SELECT pid, query, state, wait_event_type FROM pg_stat_activity WHERE pid = ANY(%s)",
            (list(ctx.actor_pids.values()),),
        ).fetchall()
        for pid, query, state, wait in rows:
            if condition(pid, query, state, wait, ctx):
                runner.box.admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
                done.set()

    return tick


class TestDisconnections:
    def test_in_flight_statement(self, prepared):
        tick = _terminate_when(lambda p, q, s, w, c: "pg_sleep" in q and s == "active")
        trace = SupervisedRunner(prepared, hooks=SupervisorHooks(on_tick=tick)).run(
            one("write_then_wait"), ExplicitSchedule([]),
        )
        assert trace.outcome == "connection_lost" and values(trace)[1] == 0
        assert_never_scored(trace)

    def test_application_catching_the_loss_cannot_score_the_run(self, prepared):
        tick = _terminate_when(lambda p, q, s, w, c: "pg_sleep" in q and s == "active")
        trace = SupervisedRunner(prepared, hooks=SupervisorHooks(on_tick=tick)).run(
            one("swallows_connection_loss"), ExplicitSchedule([]),
        )
        assert trace.results()["a"] == "carried on as if nothing happened"
        assert trace.outcome == "connection_lost"
        assert_never_scored(trace)

    def test_blocked_on_a_lock(self, prepared):
        tick = _terminate_when(lambda p, q, s, w, c: w == "Lock")
        scenario = scen(actor("a", "hold_row", kwargs={"seconds": 0.6}), actor("b", "bump"))
        hooks = SupervisorHooks(on_tick=tick)
        trace = SupervisedRunner(prepared, hooks=hooks).run(
            scenario, ExplicitSchedule(["a", "a", "b", "b", "a", "a"]),
        )
        assert trace.outcome == "connection_lost"
        assert_never_scored(trace)

    def test_idle_in_transaction_at_its_gate(self, prepared):
        tick = _terminate_when(lambda p, q, s, w, c: s == "idle in transaction" and p == c.actor_pids["a"])
        scenario = scen(actor("a", "set_pair", kwargs={"first": 1, "second": 2, "value": 5}),
                        actor("b", "pause_then_bump", kwargs={"pause": 0.5}))
        trace = SupervisedRunner(prepared, hooks=SupervisorHooks(on_tick=tick)).run(
            scenario, ExplicitSchedule(["a", "a", "b", "a", "a"]),
        )
        assert trace.outcome == "connection_lost"
        assert values(trace) == {1: 1, 2: 0}, "a's writes are gone, and b's bump of row 1 committed"
        assert_never_scored(trace)

    def test_during_commit_the_effect_is_unknown(self, prepared):
        tick = _terminate_when(lambda p, q, s, w, c: q.strip().upper().startswith("COMMIT") and s == "active")
        trace = SupervisedRunner(prepared, hooks=SupervisorHooks(on_tick=tick)).run(
            scen(actor("a", "slow_commit"), setup=adv.COMMIT_DELAY_SETUP), ExplicitSchedule([]),
        )
        assert trace.outcome == "effect_unknown"
        commit = next(r for r in trace.records if r.kind == "commit")
        write = next(r for r in trace.records if r.kind == "sql")
        assert commit.effect == write.effect == "effect_unknown"
        assert_never_scored(trace)


class TestLostAcknowledgment:
    def _run(self, prepared, function, token, **options):
        parts = urlsplit(prepared.settings.test_dsn)
        proxy = FaultProxy(parts.hostname, parts.port)
        proxy.drop_client_after = token
        try:
            hooks = SupervisorHooks(connect_via=("127.0.0.1", proxy.port))
            trace = SupervisedRunner(prepared, hooks=hooks).run(one(function, **options), ExplicitSchedule([]))
        finally:
            proxy.close()
        assert proxy.dropped.is_set()
        return trace

    def test_commit_that_landed_is_not_called_rolled_back(self, prepared):
        trace = self._run(prepared, "slow_commit", b"COMMIT")
        assert values(trace)[1] == 7, "the server did commit"
        assert trace.outcome == "effect_unknown"
        commit = next(r for r in trace.records if r.kind == "commit")
        assert commit.status == "error" and commit.effect == "effect_unknown"
        assert_never_scored(trace)

    def test_autocommit_write_that_landed_is_not_called_failed(self, prepared):
        trace = self._run(prepared, "autocommit_write", b"UPDATE c", autocommit=True)
        assert values(trace)[1] == 11
        assert trace.outcome == "effect_unknown" and trace.records[0].effect == "effect_unknown"
        assert_never_scored(trace)

    def test_write_lost_inside_a_transaction_is_not_committed(self, prepared):
        trace = self._run(prepared, "slow_commit", b"UPDATE c")
        assert values(trace)[1] == 0
        assert trace.outcome == "connection_lost"
        assert next(r for r in trace.records if r.kind == "sql").effect != "committed"
        assert_never_scored(trace)


# ------------------------------------------------------------------ 7. bounded reset and teardown


def _proxied_sandbox(box, proxy):
    parts = urlsplit(box.settings.test_dsn)
    netloc = f"{parts.username}:{parts.password}@127.0.0.1:{proxy.port}"
    dsn = parts._replace(netloc=netloc).geturl()
    fake = object.__new__(Sandbox)
    fake.settings = box.settings
    fake.admin = psycopg.connect(dsn, autocommit=True)
    fake.monitor = psycopg.connect(dsn, autocommit=True)
    # The real fixture's sessions are this test process's own, so the fake supervisor must not end them.
    fake._actors = {"real_admin": box.admin, "real_monitor": box.monitor}
    return fake


class TestBoundedResetAndTeardown:
    def test_leftover_session_holding_a_table_lock_is_ended_before_reset(self, prepared, supervised):
        supervised.run(one("clean"), ExplicitSchedule([]))
        outside = _Outside(prepared, "LOCK TABLE c IN ACCESS EXCLUSIVE MODE")
        trace = supervised.run(one("clean"), ExplicitSchedule([]))
        assert_trusted(trace)
        with pytest.raises(psycopg.OperationalError):
            outside.conn.execute("SELECT 1")
        outside.close()

    def test_lock_that_outlasts_the_limit_stops_the_batch(self, prepared):
        runner = SupervisedRunner(prepared)
        runner.run(one("clean"), ExplicitSchedule([]))
        holder = prepared.monitor  # a harness session, so it isn't ended, and frozen reset doesn't roll it back
        holder.execute("BEGIN")
        holder.execute("LOCK TABLE ds_run.c IN ACCESS EXCLUSIVE MODE")
        limited = SupervisedRunner(prepared, supervisor=SupervisorLimits(reset_lock_ms=150))
        try:
            started = time.monotonic()
            trace = limited.run(one("clean"), ExplicitSchedule([]))
            assert time.monotonic() - started < 5
            assert trace.outcome == "infrastructure_failure" and not trace.records
            assert_never_scored(trace)
            assert limited.poisoned
            with pytest.raises(RuntimeError, match="can't be trusted"):
                limited.run(one("clean"), ExplicitSchedule([]))
        finally:
            holder.execute("ROLLBACK")
        assert prepared.admin.execute("SHOW lock_timeout").fetchone()[0] == "0", "admin limits were restored"
        assert prepared.admin.execute("SHOW statement_timeout").fetchone()[0] == "0"

    def test_unresponsive_server_is_bounded(self, prepared):
        parts = urlsplit(prepared.settings.test_dsn)
        proxy = FaultProxy(parts.hostname, parts.port)
        fake = _proxied_sandbox(prepared, proxy)
        runner = SupervisedRunner(fake, supervisor=SupervisorLimits(db_call_s=0.5))
        proxy.blackhole = True
        started = time.monotonic()
        try:
            trace = runner.run(one("clean"), ExplicitSchedule([]))
            assert time.monotonic() - started < 5
            assert trace.outcome == "infrastructure_failure" and runner.poisoned
            assert_never_scored(trace)
        finally:
            proxy.close()
            runner.close()

    def test_child_is_killed_when_the_database_stops_responding_mid_run(self, prepared):
        parts = urlsplit(prepared.settings.test_dsn)
        proxy = FaultProxy(parts.hostname, parts.port)
        fake = _proxied_sandbox(prepared, proxy)
        seen = {}

        def tick(runner, ctx):
            if ctx.actor_pids and "proc" not in seen:
                seen["proc"] = ctx.proc
                proxy.blackhole = True

        limits = LiveLimits.from_settings(prepared.settings, statement_ms=60000, lock_ms=60000, run_s=60)
        supervisor = SupervisorLimits(db_call_s=0.5, escalation_grace_s=0.2, kill_wait_s=2.0)
        runner = SupervisedRunner(fake, limits, supervisor, SupervisorHooks(connect_via=(parts.hostname, parts.port),
                                                                            on_tick=tick))
        try:
            trace = runner.run(one("python_sleep_forever"), ExplicitSchedule([]))
            assert trace.outcome == "infrastructure_failure" and runner.poisoned
            assert_never_scored(trace)
            assert seen["proc"].poll() is not None, "the child must not outlive the supervisor's view of the database"
        finally:
            proxy.close()
            runner.close()
        prepared.admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename LIKE 'ds_run_%'")
        for name in prepared.admin.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'ds_run_%'").fetchall():
            prepared.admin.execute(f'DROP OWNED BY "{name[0]}"')
            prepared.admin.execute(f'DROP ROLE "{name[0]}"')

    def test_sessions_that_will_not_end_poison_the_runner(self, prepared, monkeypatch):
        runner = SupervisedRunner(prepared)
        monkeypatch.setattr(runner, "_end_outside_sessions", lambda seconds: [(1234,)])
        trace = runner.run(one("clean"), ExplicitSchedule([]))
        assert trace.outcome == "infrastructure_failure" and runner.poisoned
        with pytest.raises(RuntimeError, match="can't be trusted"):
            runner.run(one("clean"), ExplicitSchedule([]))

    def test_bounded_call_raises_rather_than_hangs(self, prepared):
        runner = SupervisedRunner(prepared)
        with pytest.raises(InfrastructureFailure):
            runner._bounded(lambda: time.sleep(1), seconds=0.05)
        assert runner.poisoned

    def test_role_that_cannot_be_dropped_is_flagged(self, prepared, monkeypatch):
        runner = SupervisedRunner(prepared)
        original = runner._admin

        def refuse_drop(query, params=None):
            if "DROP ROLE" in repr(query):
                raise psycopg.errors.DependentObjectsStillExist("still in use")
            return original(query, params)

        monkeypatch.setattr(runner, "_admin", refuse_drop)
        trace = runner.run(one("clean"), ExplicitSchedule([]))
        assert not trace.evidence.role_dropped and not trace.scoreable
        assert "infrastructure_failure" in kinds(trace)
        for name in prepared.admin.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'ds_run_%'").fetchall():
            prepared.admin.execute(f'DROP OWNED BY "{name[0]}"')
            prepared.admin.execute(f'DROP ROLE "{name[0]}"')

    def test_advisory_lock_contention_is_a_clear_stop(self, prepared):
        from deployspecter.database import SandboxBusy

        with pytest.raises(SandboxBusy):
            Sandbox(prepared.settings, lock_wait_ms=50)


# ------------------------------------------------------------------ 8. opaque executemany


PIPELINE_MODE = "autocommit_in_pipeline_block"
ALL_CELLS = [(m, c) for m in compat.MODES for c in compat.CASES]
CLIENT_ERRORS = {"unadaptable_parameter_in_middle", "wrong_arity_in_middle", "generator_raises_after_first"}
COMPARED = ("rowcount", "results", "statusmessage", "exception", "commit_exception", "txn_after_call")


def _cell_scenario(mode, case):
    return LiveScenario(f"{mode}:{case}", compat.SETUP, [], [
        actor("a", "cell", prefix=COMPAT, kwargs={"mode": mode, "case": case},
              autocommit=mode != "not_autocommit_commit_anyway"),
    ])


def _final_ids(state):
    return sorted(r["id"] for r in state["t"])


class TestOpaqueBatchCompatibility:
    def test_there_are_44_cells(self):
        assert len(ALL_CELLS) == 44

    @pytest.mark.parametrize("mode,case", ALL_CELLS)
    def test_cell_against_plain_psycopg(self, supervised, mode, case):
        scenario = _cell_scenario(mode, case)
        plain = supervised.plain_serial(scenario, ["a"])
        assert plain["flags"] == [] and plain["errors"] == {}
        trace = supervised.run(scenario, ExplicitSchedule([]))
        if mode == PIPELINE_MODE:
            assert trace.outcome == "unsupported" and trace.refusals, "an explicit pipeline block stays refused"
            assert_never_scored(trace)
            return
        got, want = trace.results()["a"], plain["results"]["a"]
        assert {k: got.get(k) for k in COMPARED} == {k: want.get(k) for k in COMPARED}
        assert _final_ids(trace.final_state) == _final_ids(plain["final_state"])
        assert trace.evidence.reconciliation["unexpected_statements"] == []
        assert trace.evidence.reconciliation["missing_statements"] == []
        batch = next(r for r in trace.records if r.kind == "opaque_batch")
        assert batch.params is None, "a batch's parameters aren't individually recorded"
        if mode == "autocommit_bare" and case in CLIENT_ERRORS:
            assert batch.effect == "effect_unknown" and trace.outcome == "effect_unknown"
            assert_never_scored(trace)


class TestOpaqueBatchSemantics:
    def _run(self, supervised, rows, transaction=False, **options):
        return supervised.run(
            one("batch", kwargs={"rows": rows, "transaction": transaction}, **options), ExplicitSchedule([]),
        )

    def test_success_is_one_step_with_a_count(self, supervised):
        trace = self._run(supervised, [[5, 1], [6, 2]], autocommit=True)
        assert_trusted(trace)
        batch = next(r for r in trace.records if r.kind == "opaque_batch")
        assert batch.batch_count == 2 and batch.effect == "autocommitted"
        assert sum(r.kind == "opaque_batch" for r in trace.records) == trace.opaque_steps == 1
        assert not trace.statement_level_coverage
        assert values(trace) == {1: 5, 2: 6} and trace.results()["a"] == 2

    def test_explicit_transaction_commits_through_the_commit_step(self, supervised):
        trace = self._run(supervised, [[5, 1], [6, 2]], transaction=True, autocommit=True)
        assert_trusted(trace)
        assert next(r for r in trace.records if r.kind == "opaque_batch").effect == "committed"

    def test_server_error_under_autocommit_commits_nothing_and_is_recorded_so(self, supervised):
        scenario = _cell_scenario("autocommit_bare", "conflict_in_middle")
        trace = supervised.run(scenario, ExplicitSchedule([]))
        batch = next(r for r in trace.records if r.kind == "opaque_batch")
        assert batch.status == "error" and batch.effect == "none" and batch.sqlstate == "23505"
        assert _final_ids(trace.final_state) == [2]
        assert_trusted(trace)

    def test_client_error_under_autocommit_is_effect_unknown(self, supervised):
        trace = supervised.run(_cell_scenario("autocommit_bare", "generator_raises_after_first"), ExplicitSchedule([]))
        assert trace.outcome == "effect_unknown" and _final_ids(trace.final_state) == [2, 10], "row 1 really committed"
        assert_never_scored(trace)

    def test_iterator_stays_lazy(self, supervised):
        trace = supervised.run(_cell_scenario("autocommit_in_transaction", "generator_raises_after_first"),
                               ExplicitSchedule([]))
        assert trace.results()["a"]["exception"] == ["ValueError", None]
        assert next(r for r in trace.records if r.kind == "opaque_batch").batch_count == 1

    def test_blocked_batch_lets_the_lock_holder_finish(self, supervised):
        setup = "CREATE TABLE r (id integer PRIMARY KEY, v integer); INSERT INTO r VALUES (1, 0), (2, 0);"
        scenario = LiveScenario("blocked", setup, [], [
            actor("a", "batch_two_rows", prefix=COMPAT, autocommit=True), actor("b", "row_two_holder", prefix=COMPAT),
        ])
        trace = supervised.run(scenario, ExplicitSchedule(["b", "b", "a", "b", "b"]))
        batch = next(r for r in trace.records if r.kind == "opaque_batch")
        assert batch.blocked_by == ["b"] and trace.results()["a"] == 2
        assert_trusted(trace)

    @pytest.mark.parametrize("function", ["batch_commit_text", "batch_multi_statement"])
    def test_refused_forms(self, supervised, function):
        trace = supervised.run(scen(actor("a", function, prefix=COMPAT), setup=compat.SETUP), ExplicitSchedule([]))
        assert trace.outcome == "unsupported" and trace.refusals
        assert_never_scored(trace)

    def test_exploration_does_not_claim_coverage_inside_a_batch(self, supervised):
        setup = """CREATE TABLE r (id integer PRIMARY KEY, v integer); CREATE TABLE log (id serial PRIMARY KEY);
                   INSERT INTO r VALUES (1, NULL), (2, NULL);"""
        scenario = LiveScenario("torn", setup, [], [
            actor("a", "batch_in_transaction", prefix=COMPAT),
            actor("b", "insert_log", prefix=COMPAT, autocommit=True),
        ])
        result = explore_live(supervised, scenario, LiveBudget(max_runs=30))
        assert result.exhausted and result.opaque_runs == result.runs
        assert result.complete_schedule_coverage is False
        states = {tuple(r["v"] for r in t.final_state["r"]) for t in result.traces}
        assert (0, 1) not in states, "the torn state is real on plain psycopg, and an opaque batch can't reach it"
        assert all(t.scoreable for t in result.traces)

    def test_the_unreachable_torn_state_is_real_on_plain_psycopg(self, prepared):
        setup = """CREATE TABLE r (id integer PRIMARY KEY, v integer); CREATE TABLE log (id serial PRIMARY KEY);
                   INSERT INTO r VALUES (1, NULL), (2, NULL);"""
        prepared.reset(setup, [])
        dsn = prepared.settings.test_dsn
        worker = psycopg.connect(dsn, options="-c search_path=ds_run")
        other = psycopg.connect(dsn, autocommit=True, options="-c search_path=ds_run")
        query = "UPDATE r SET v = (SELECT count(*) FROM log) WHERE id = %s AND (SELECT pg_sleep(%s)) IS NOT NULL"

        def work():
            with worker.transaction():
                worker.cursor().executemany(query, [(1, 0.4), (2, 0.0)])

        thread = threading.Thread(target=work)
        thread.start()
        time.sleep(0.1)
        other.execute("INSERT INTO log DEFAULT VALUES")
        thread.join(5)
        worker.close()
        other.close()
        assert [r["v"] for r in prepared.snapshot()["r"]] == [0, 1]


# ------------------------------------------------------------------ the application behaviour itself, supervised


def _canon_states(runner, scenario):
    names = [a.name for a in scenario.actors]
    return [props.canonical(runner.plain_serial(scenario, o)["final_state"]) for o in (names, names[::-1])]


class TestSupervisedApplication:
    @pytest.mark.parametrize("build", [
        two_reservations, lambda: two_reservations(lock_row=True), lambda: two_reservations(capacity=9),
        reserve_and_release, lambda: reserve_and_release(lock_row=True),
    ])
    @pytest.mark.parametrize("order", [["a", "b"], ["b", "a"]])
    def test_serial_instrumented_equals_serial_plain(self, supervised, build, order):
        scenario = build()
        plain = supervised.plain_serial(scenario, order)
        assert plain["flags"] == [] and plain["quiescent"]
        traced = supervised.run(scenario, ExplicitSchedule([order[0]] * 16 + [order[1]] * 16))
        assert_trusted(traced)
        assert traced.results() == plain["results"]
        assert traced.final_state == plain["final_state"]

    def test_writes_use_the_value_read_in_this_run(self, supervised):
        low = supervised.run(two_reservations(capacity=9, reserved=0), ExplicitSchedule(BOTH_READ_FIRST))
        high = supervised.run(two_reservations(capacity=9, reserved=5), ExplicitSchedule(BOTH_READ_FIRST))
        assert [r for r in low.records if "UPDATE" in r.sql][0].params == [2, 1]
        assert [r for r in high.records if "UPDATE" in r.sql][0].params == [7, 1]
        assert_trusted(low)
        assert_trusted(high)

    def test_real_defect_is_found_and_trusted(self, supervised):
        scenario = two_reservations()
        known = _canon_states(supervised, scenario)
        trace = supervised.run(scenario, ExplicitSchedule(BOTH_READ_FIRST))
        assert_trusted(trace)
        assert props.check_invariants(trace.final_state) and props.check_serializable(trace.final_state, known)

    @pytest.mark.parametrize("build,violating", [
        (two_reservations, True), (lambda: two_reservations(lock_row=True), False),
    ])
    def test_whole_tree_is_trusted_with_server_evidence(self, supervised, build, violating):
        scenario = build()
        result = explore_live(
            supervised, scenario, LiveBudget(max_runs=120, max_seconds=120),
            check=lambda t: props.check_invariants(t.final_state),
        )
        assert result.exhausted and result.divergences == 0
        assert all(t.scoreable for t in result.traces), [t.evidence.flags for t in result.traces if not t.scoreable][:2]
        assert any(result.verdicts) is violating
        assert result.complete_schedule_coverage

    def test_replay_matches_across_processes(self, supervised):
        scenario = two_reservations(lock_row=True)
        first = supervised.run(scenario, ExplicitSchedule(BOTH_READ_FIRST))
        for _ in range(5):
            again = supervised.run(scenario, ExplicitSchedule(first.chosen_schedule, strict=True))
            assert again.fingerprint() == first.fingerprint()

    @pytest.mark.parametrize("function,kwargs,auto", [
        ("savepoints", {}, False), ("text_transaction", {}, True), ("multi_line_and_quotes", {}, False),
        ("named_params", {}, False), ("many_types", {}, False), ("reads_with_factories", {}, False),
        ("forgets_commit", {}, False),
    ])
    def test_ordinary_driver_paths_reconcile_cleanly(self, supervised, function, kwargs, auto):
        assert_trusted(supervised.run(one(function, kwargs=kwargs, autocommit=auto), ExplicitSchedule([])))

    @pytest.mark.parametrize("function,outcome", [("sql_error", "execution_error"), ("app_error", "python_exception")])
    def test_application_errors_are_scoreable_results(self, supervised, function, outcome):
        trace = supervised.run(one(function), ExplicitSchedule([]))
        assert trace.outcome == outcome
        assert_trusted(trace)

    def test_deadlock_and_real_retry_are_trusted(self, supervised):
        scenario = scen(actor("a", "retry_pair", kwargs={"first": 1, "second": 2, "value": 10}),
                        actor("b", "retry_pair", kwargs={"first": 2, "second": 1, "value": 20}))
        trace = supervised.run(scenario, ExplicitSchedule(["a", "b", "a", "b"]))
        assert trace.outcome == "completed" and sorted(trace.results().values()) == [1, 2]
        assert [r.sqlstate for r in trace.records if r.status == "error"] == ["40P01"]
        assert_trusted(trace)

    def test_clean_runs_never_flag(self, supervised):
        for _ in range(25):
            assert_trusted(supervised.run(two_reservations(lock_row=True), ExplicitSchedule(BOTH_READ_FIRST)))
