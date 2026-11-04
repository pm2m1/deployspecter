"""Gate A2.2, offline: the supervisor audit boundary. The real frozen Gate 2A supervisor and Sandbox run against a fake
server (gate2a_fakes); only the child process, the process-group kill and the ps scan are test fakes.

Simulated, not observed. Passing here shows the audit logic agrees with frozen Gate 2A's code under fakes. It is not
evidence that Gate A2 ran, that the template matches a real PostgreSQL log, or that any gap is closed.
"""

from __future__ import annotations

import contextlib
import copy
import csv
import hashlib
import io
import json
import re
import socket
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in ("tests", "research_tools", "tests/research_tracks"):
    sys.path.insert(0, str(ROOT / extra))

import gate2a_fakes as fk  # noqa: E402
import gate_a1_characterize as a1  # noqa: E402
import gate_a2_run as drv  # noqa: E402
from psycopg.conninfo import conninfo_to_dict  # noqa: E402

from deployspecter.database import Sandbox  # noqa: E402
from deployspecter.live.runner import ExplicitSchedule, LiveLimits  # noqa: E402
from deployspecter.live.serverlog import ServerLog  # noqa: E402
from deployspecter.live.supervisor import SupervisedRunner, SupervisorLimits  # noqa: E402
from deployspecter.research.track_a import characterization as ch  # noqa: E402
from deployspecter.research.track_a import evidence_binding as eb  # noqa: E402
from deployspecter.research.track_a import gate2a_audit as ga  # noqa: E402
from deployspecter.research.track_a import gate2a_prepare_model as pm  # noqa: E402
from deployspecter.research.track_a import gate_a2_controls as c  # noqa: E402
from deployspecter.research.track_a import parent_reconciliation as pr  # noqa: E402
from deployspecter.research.track_a import pipeline as p  # noqa: E402

MANIFEST = ROOT / "docs/research/live_stepping/track_a/execution_manifest_v5.json"
V2_LOG = ROOT / "tests/research_tracks/fixtures/gate_a1_v2_observed.csv"
URL = "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter"
INITIAL = {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "rejections": [], "reservations": []}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def manifest():
    return drv.load_manifest(MANIFEST, hashlib.sha256(MANIFEST.read_bytes()).hexdigest())


class Readers:
    """Gate A2's side on the fake server, as SegmentEffects does it: a reader that can disconnect and reconnect,
    Gate A1's frozen ServerLog through it, tagged parent sessions."""

    def __init__(self, server: fk.FakeServer, *faults: str) -> None:
        self.server, self.faults, self.conn, self.opened = server, set(faults), None, 0
        self.reader_open()
        query = lambda q, prm=None: self.conn.execute(q, prm).fetchone()  # noqa: E731
        self.logs = {"op": ServerLog(query), "batch": ServerLog(query), "whole": ServerLog(query)}

    def reader_open(self):
        self.opened += 1
        if "reader_refused" in self.faults and self.opened > 1:
            raise psycopg.OperationalError("simulated: the new reader couldn't connect")
        self.conn = self.server.connect(fk.ADMIN_USER, fk.TEST_DB, pr.LOG_READER_APP, threshold=None)

    def reader_close(self):
        if "reader_stays" not in self.faults:
            self.conn.close()

    def log_position(self, key="op"):
        self.logs[key].position()

    def log_mark(self, token):
        self.conn.execute(pr.marker_sql(token))

    def log_read(self, key="op"):
        return self.logs[key].read()

    def wait_gone(self, tag):
        return self.conn.execute(ch.GONE_SQL, (tag,)).fetchone()[0] == 0

    @contextlib.contextmanager
    def parent_connection(self, tag, database):
        conn = self.server.connect(fk.ADMIN_USER, database, tag, threshold=None)
        try:
            yield conn
        finally:
            conn.close()


def scenario():
    from live_app import reservations as app
    chain = p.run_offline_chain()
    return p.live_scenario(chain, variant="buggy", setup_sql=app.SETUP_SQL)


class Batch:
    """One simulated Gate A2 batch: bootstrap, then Gate 2A runs, each followed by a reconciled op6f read."""

    def __init__(self, monkeypatch, tmp_path, *faults, behaviour="clean", reader_faults=(), harness_policy=True,
                 supervisor=None, change_manifest=None):
        self.server = fk.FakeServer(*faults)
        self.procs = fk.install(monkeypatch, self.server, behaviour)
        self.m = copy.deepcopy(manifest())
        if change_manifest:
            change_manifest(self.m)
        self.readers = Readers(self.server, *reader_faults)
        self.evidence = a1.a2.EvidenceDir.create(tmp_path)
        self.seg = drv.SegmentedRun(self.readers, self.m, self.evidence)
        self.harness_policy, self.scenario = harness_policy, scenario()
        self.supervisor = supervisor or {}
        self.runner = None

    def construct(self):
        kw = c.connection_keywords(conninfo_to_dict(URL), self.m)
        return Sandbox(c.harness_settings(kw))  # SegmentedRun.bootstrap applies B5 option 1 itself

    def bootstrap(self):
        self.seg.observe_roles("before the sandbox bootstrap")
        box = self.seg.bootstrap(self.construct)
        g = self.m["safety_ceilings"]["gate2a"]
        self.runner = SupervisedRunner(box, LiveLimits(**g["LiveLimits"]),
                                       SupervisorLimits(**{**g["SupervisorLimits"], **self.supervisor}))
        return box

    def gate2a(self, digest=None, schedule=("a", "b")):
        return self.seg.gate2a(lambda: self.runner.run(self.scenario, ExplicitSchedule(list(schedule))),
                               mode="stepped", scenario=self.scenario,
                               verified_initial_digest=digest or eb.canonical_digest(INITIAL))

    def op6f(self):
        tables = sorted(INITIAL)
        return self.seg.op("op6f", [(t, ()) for t in eb.final_read_statements(tables)],
                           lambda conn: eb.independent_final_read(conn, tables))

    def run_all(self, runs=2):
        self.bootstrap()
        traces = []
        for _ in range(runs):
            traces.append(self.gate2a())
            self.op6f()
        return traces, self.seg.finish()


def statuses(report, claim=None, status=None):
    return [c for c in report["claims"] if (claim is None or c["claim"] == claim)
            and (status is None or c["status"] == status)]


# ------------------------------------------------------------------ the clean batch


class TestTheCleanBatch:
    def test_every_observable_claim_verifies_and_the_gaps_are_disclosed(self, monkeypatch, tmp_path):
        traces, report = Batch(monkeypatch, tmp_path).run_all()
        assert all(t.scoreable for t in traces), [t.evidence.flags for t in traces]
        bad = [c for c in report["claims"] if c["status"] in (ga.REFUTED, ga.INDETERMINATE)]
        assert bad == []
        assert report["classification"] == "OBSERVABLE_SQL_VERIFIED_WITH_DISCLOSED_GAPS"
        assert report["counts"][ga.UNOBSERVABLE] >= 2 * 3 + 2 and report["counts"][ga.NOT_TESTED] == 3
        assert [c["status"] for c in statuses(report, "every session's log lines are consecutive")] == [ga.VERIFIED]

    def test_the_frozen_supervisor_matches_the_declared_template_phase_by_phase(self, monkeypatch, tmp_path):
        traces, report = Batch(monkeypatch, tmp_path).run_all()
        phases = ["preflight", "hardening", "outside-session sweep", "reset", "initial snapshot", "window opened",
                  "role creation", "role configuration", "supervision", "teardown", "final snapshot",
                  "role removal", "window closed", "nothing else on the admin session"]
        for i, t in enumerate(traces):
            got = [(c["claim"], c["status"]) for c in report["claims"] if c["scope"] == f"run {t.evidence.run_id}"
                   and c["claim"] in phases]
            want = [(name, ga.UNOBSERVABLE if name == "role creation" else ga.VERIFIED) for name in phases
                    if name != "hardening" or i == 0]  # hardening happens once per runner
            assert got == want

    def test_template_texts_are_frozen_gate2as_own(self):
        from deployspecter.live import supervisor as sv
        assert sv._ACTIVITY_SQL.replace("%s", "$1") == ga.ACTIVITY_SQL
        assert tuple(sv.REQUIRED_SETTINGS) == ga.PREFLIGHT_SETTINGS
        source = (ROOT / "src/deployspecter/database.py").read_text()
        assert ga.RUNNER_ROLE_SQL.replace("deployspecter_runner", "{RUNNER_ROLE}") in source
        m = manifest()
        assert m["gate2a_audit"]["template_sha256"] == hashlib.sha256(
            json.dumps(ga.template_texts()).encode()).hexdigest()

    def test_each_run_is_bound_to_its_run_id_and_state_digest(self, monkeypatch, tmp_path):
        traces, report = Batch(monkeypatch, tmp_path).run_all()
        for t in traces:
            scope = f"run {t.evidence.run_id}"
            claims = {c["claim"]: c["status"] for c in report["claims"] if c["scope"] == scope}
            assert claims["initial snapshot equals the verified initial state"] == ga.VERIFIED
            assert claims["initial state derived from the logged reset"] == ga.VERIFIED
            assert claims["initial snapshot values independently observed"] == ga.UNOBSERVABLE
        evidence = json.loads(next(tmp_path.glob("*/gate2a_coverage.json")).read_text())
        assert evidence["classification"] == report["classification"]

    def test_no_gate_a2_session_exists_while_gate2a_runs(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        seen = []
        b.server.hooks.append((ga.LOG_OFF_SQL, lambda conn: seen.append(
            sorted(s.app for s in b.server.sessions.values()))))
        b.run_all(runs=1)
        assert seen and not any(app == pr.LOG_READER_APP or app.startswith("ds_parent_") for app in seen[0])


class TestClassification:
    def test_an_unobservable_step_is_never_all_sql_audited(self):
        cov = ga.Coverage()
        cov.add("run x", "a", ga.VERIFIED, "")
        assert cov.classification == "ALL_SQL_VERIFIED"
        cov.add("run x", "role creation", ga.UNOBSERVABLE, "")
        assert cov.classification == "OBSERVABLE_SQL_VERIFIED_WITH_DISCLOSED_GAPS" and not cov.fails_closed
        cov.add("run x", "b", ga.INDETERMINATE, "")
        assert cov.classification == "INDETERMINATE" and cov.fails_closed
        cov.add("run x", "c", ga.REFUTED, "")
        assert cov.classification == "REFUTED"
        with pytest.raises(ValueError):
            cov.add("run x", "d", "PROBABLY_FINE", "")


# ------------------------------------------------------------------ fault injection through the real supervisor


def refused(batch_fn, fragment):
    with pytest.raises(drv.DriverRefused) as raised:
        batch_fn()
    assert re.search(fragment, str(raised.value)), str(raised.value)
    return str(raised.value)


class TestFaultsThroughTheFrozenSupervisor:
    def test_a_lost_reset_statement_is_refuted(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()
        b.server.suppress.add("CREATE SCHEMA ds_run")
        refused(b.gate2a, "reset: expected 'reset statement 2 of 10'")

    def test_a_partially_completed_reset_stops_the_batch(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path, "seed_fails")
        b.bootstrap()
        refused(b.gate2a, "raised SetupError; the batch stops, nothing is retried")
        claims = {(c.claim, c.status) for c in b.seg.coverage.claims}
        assert ("the Gate 2A call returned", ga.INDETERMINATE) in claims
        assert ("admin rows of a call without a run id", ga.INDETERMINATE) in claims
        assert len(b.procs) == 0  # no child was ever started

    def test_an_unexpected_admin_statement_is_refuted(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()
        b.server.inject["GRANT USAGE ON SCHEMA ds_run TO deployspecter_runner"] = ["GRANT ALL ON SCHEMA ds_run "
                                                                                   "TO PUBLIC"]
        refused(b.gate2a, "reset: expected 'reset statement 9 of 10', logged 'GRANT ALL ON SCHEMA")

    def test_an_extra_session_during_the_run_is_refuted(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()
        b.server.hooks.append(("GRANT CONNECT", lambda conn: b.server.connect("deployspecter", fk.TEST_DB, "psql")))
        refused(b.gate2a, "didn't verify")
        bad = {c.claim for c in b.seg.coverage.claims if c.status == ga.REFUTED}
        assert {"every session in the gap attributed", "teardown"} <= bad  # unattributed, and ended by teardown

    def test_an_extra_session_inside_a_segment_is_refuted_by_gate_a1s_audit(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()
        b.gate2a()
        b.server.connect("deployspecter", fk.TEST_DB, "psql").execute("DELETE FROM ds_run.events")
        b.op6f()
        refused(b.seg.finish, "batch audit failed")

    def test_a_reader_still_connected_at_the_supervisors_sweep_is_refuted(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()
        b.readers.faults.add("reader_stays")
        refused(b.gate2a, "present at the supervisor's sweep and was terminated")
        assert any(c.claim == "the previous reader closed before Gate 2A began" and c.status == ga.REFUTED
                   and "terminated" in c.basis for c in b.seg.coverage.claims)

    def test_a_reader_that_cant_reconnect_stops_the_batch_without_a_retry(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path, reader_faults=("reader_refused",))
        refused(b.bootstrap, "next reader couldn't connect")
        assert b.readers.opened == 2

    def test_an_incomplete_log_flush_is_refused_after_a_bounded_wait(self, monkeypatch, tmp_path):
        def short_wait(m):
            m["safety_ceilings"]["log_wait_s"] = 0.2
        b = Batch(monkeypatch, tmp_path, change_manifest=short_wait)
        b.server.faults.add("incomplete_flush")
        refused(b.bootstrap, "incomplete flush")

    def test_a_harness_session_that_keeps_preparing_is_still_refuted_by_the_matcher(self, monkeypatch, tmp_path):
        """A connection whose property reports None but still prepares passes the policy check; the matcher, which
        allowlists no prepared name, is the backstop."""
        b = Batch(monkeypatch, tmp_path, "prepare_ignored")
        b.bootstrap()
        refused(b.gate2a, r"prepared-statement name: execute _pg3_0 \(B5\)")

    def test_a_wrong_verified_digest_is_refuted(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()
        other = {**INITIAL, "events": [{"id": 1, "capacity": 3, "reserved": 1}]}
        refused(lambda: b.gate2a(digest=eb.canonical_digest(other)),
                "initial snapshot equals the verified initial state")

    def test_a_worker_timeout_is_escalated_audited_and_stops_the_batch(self, monkeypatch, tmp_path):
        fast = {"run_deadline_s": 0.05, "escalation_grace_s": 0.02, "kill_wait_s": 0.05, "handshake_s": 2.0}
        b = Batch(monkeypatch, tmp_path, behaviour="timeout", supervisor=fast)
        b.bootstrap()
        refused(b.gate2a, "child statements reconciled: the run is flagged")
        claims = {c.claim: c.status for c in b.seg.coverage.claims if c.scope.startswith("run ")}
        assert claims["supervision"] == ga.VERIFIED  # the cancel and terminate steps match the trace's escalation
        assert claims["child sessions attributed"] == ga.VERIFIED  # terminated children's FATAL rows explained
        assert claims["teardown"] == ga.VERIFIED
        gap = {c.claim: c.status for c in b.seg.coverage.claims if c.scope == "gap 2"}
        assert gap["the previous reader closed before Gate 2A began"] == ga.VERIFIED

    def test_incomplete_teardown_stops_the_batch(self, monkeypatch, tmp_path):
        fast = {"run_deadline_s": 0.05, "escalation_grace_s": 0.02, "kill_wait_s": 0.05, "quiescence_s": 0.05}
        b = Batch(monkeypatch, tmp_path, behaviour="linger", supervisor=fast)
        b.bootstrap()
        refused(b.gate2a, "unmanaged activity")
        assert b.runner.poisoned in {"sessions survived termination", "the database isn't quiescent"}  # no rerun

    def test_a_role_that_isnt_dropped_is_refuted(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path, "role_drop_fails")
        b.bootstrap()
        refused(b.gate2a, "role removal: ERROR row on the admin session")

    def test_an_unregistered_child_session_is_refuted(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path, "unregistered_child")
        b.bootstrap()
        refused(b.gate2a, "isn't one of this run's sessions")
        bad = {c.claim for c in b.seg.coverage.claims if c.status == ga.REFUTED}
        assert "teardown" in bad and "child statements reconciled" in bad

    def test_logging_that_isnt_restored_after_role_creation_stops_the_batch(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path, "reset_fails")
        b.bootstrap()
        refused(b.gate2a, "raised InternalError")

    def test_an_interrupt_during_role_setup_is_kept_an_interrupt_after_the_evidence_is_read(self, monkeypatch,
                                                                                            tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()

        def interrupt(_conn):
            raise KeyboardInterrupt
        b.server.hooks.append(("GRANT CONNECT", interrupt))
        with pytest.raises(KeyboardInterrupt):
            b.gate2a()
        assert b.readers.opened == 3 and b.seg.current.name == "segment_002"  # reopened only to read the gap

    def test_a_wrong_reviewed_initial_row_is_refuted_even_when_the_reset_matches(self, monkeypatch, tmp_path):
        def change(m):
            m["initial_rows"]["events"][0]["capacity"] = 4
        b = Batch(monkeypatch, tmp_path, change_manifest=change)
        b.bootstrap()
        refused(b.gate2a, "aren't the manifest's reviewed initial rows")
        claims = {c.claim: c.status for c in b.seg.coverage.claims if c.scope.startswith("run ")}
        assert claims["reset"] == ga.VERIFIED

    def test_hardening_of_another_database_set_is_refuted(self, monkeypatch, tmp_path):
        def change(m):
            m["gate2a_audit"]["connectable_databases"].remove("template1")
        b = Batch(monkeypatch, tmp_path, change_manifest=change)
        b.bootstrap()
        refused(b.gate2a, "hardening: revoked on")

    @pytest.mark.parametrize("after, extra, fragment", [
        (ga.DB_EXISTS_SQL, "SELECT 2", "ensure_test_database's statements"),
        (ga.ADVISORY_LOCK_SQL, "SELECT pg_sleep(0)", "harness admin session's statements"),
    ])
    def test_an_extra_bootstrap_statement_is_refuted(self, monkeypatch, tmp_path, after, extra, fragment):
        b = Batch(monkeypatch, tmp_path)
        b.server.inject[after] = [extra]
        refused(b.bootstrap, fragment)

    def test_a_missing_test_database_is_refuted_at_bootstrap(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path, "missing_test_database")
        refused(b.bootstrap, "CREATE DATABASE ran")


class TestTheBlockedBoundary:
    def test_plain_serial_calls_are_refused_as_unbindable(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        b.bootstrap()
        refused(lambda: b.seg.gate2a(lambda: None, mode="plain", scenario=b.scenario, verified_initial_digest=None),
                "can't be bound to a supervisor-reported run id")

    def test_a_gate2a_call_before_an_audited_bootstrap_is_refused(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        refused(lambda: b.seg.gate2a(lambda: None, mode="stepped", scenario=b.scenario,
                                     verified_initial_digest=None), "wasn't constructed in an audited gap")

    def test_the_command_line_still_cant_run_gate2a(self):
        m = manifest()
        assert drv.GATE2A_SECTION_IMPLEMENTED is False and m["status"] == "OFFLINE_ONLY_EXECUTION_BLOCKED"
        assert [b["id"] for b in m["execution_blockers"]] == [f"B{i}" for i in range(1, 9)]
        assert all(b["status"].startswith("OPEN") for b in m["execution_blockers"])
        with pytest.raises(drv.DriverRefused, match="isn't executable"):
            drv.require_executable(m, "run")
        lifted = {**m, "execution_blockers": [], "status": "WAITING_FOR_EXECUTION_APPROVAL"}
        with pytest.raises(drv.DriverRefused, match="whole-batch audit"):
            drv.require_executable(lifted, "run")

    def test_the_current_driver_refuses_the_older_manifests(self):
        for old in ("v3", "v4"):
            path = ROOT / f"docs/research/live_stepping/track_a/execution_manifest_{old}.json"
            with pytest.raises(drv.DriverRefused, match="version 5"):
                drv.load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_the_manifest_needs_the_audit_inputs(self, tmp_path):
        data = json.loads(MANIFEST.read_text())
        data["gate2a_audit"].pop("log_path")
        path = tmp_path / "m.json"
        path.write_text(json.dumps(data))
        with pytest.raises(drv.DriverRefused, match="Gate 2A audit's databases and log path"):
            drv.load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_the_current_manifest_pins_its_sources(self):
        m = manifest()
        a1.a2.verify_sources(m)
        assert {"src/deployspecter/research/track_a/gate2a_audit.py",
                "src/deployspecter/research/track_a/gate2a_prepare_model.py",
                "research_tools/gate_a2_run.py"} <= set(m["source_sha256"])


# ------------------------------------------------------------------ precise log mutations of a real fake run


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    """A clean run's gap rows and the audit inputs, captured from the real supervisor under fakes."""
    captured = {}
    original = ga.audit_run_gap

    def capture(rows, **kw):
        captured.update(rows=list(rows), **kw)
        return original(rows, **kw)

    monkeypatch.setattr(ga, "audit_run_gap", capture)
    b = Batch(monkeypatch, tmp_path)
    b.bootstrap()
    b.gate2a()
    monkeypatch.setattr(ga, "audit_run_gap", original)
    return captured


def reaudit(captured, rows=None, **changes):
    kw = {k: v for k, v in captured.items() if k != "rows"}
    kw.update(changes)
    cov = ga.Coverage()
    kw["cov"] = cov
    ga.audit_run_gap(rows if rows is not None else captured["rows"], **kw)
    return cov


def admin_index(captured, predicate):
    pid = str(captured["harness"].admin_pid)
    return next(i for i, r in enumerate(captured["rows"]) if r[pr.PID] == pid and predicate(r.message))


def with_row(row, message=None, **cells):
    out = list(row.cells)
    if message is not None:
        out[pr.MESSAGE] = message
    for k, v in cells.items():
        out[getattr(pr, k.upper())] = v
    return ga.Row(out, row.start, row.end)


class TestLogMutations:
    def test_the_recorded_gap_reaudits_clean(self, recorded):
        assert not reaudit(recorded).fails_closed

    def assert_refuted(self, cov, claim, fragment=""):
        hits = [c for c in cov.claims if c.claim == claim and c.status == ga.REFUTED and fragment in c.basis]
        assert hits, [(c.claim, c.status, c.basis) for c in cov.claims if c.status != ga.VERIFIED]

    def test_another_run_id_is_refuted(self, recorded):
        ctx = recorded["call"].ctx
        other = ga.Call(**{**recorded["call"].__dict__, "ctx": ga.RunContext(**{**ctx.__dict__,
                                                                                "run_id": "0123456789ab"})})
        self.assert_refuted(reaudit(recorded, call=other), "window opened", "the start marker")

    def test_a_missing_reset_statement_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        rows.pop(admin_index(recorded, lambda m: m == "statement: COMMIT"))
        self.assert_refuted(reaudit(recorded, rows), "reset")

    def test_reordered_admin_statements_are_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m == "statement: SET lock_timeout = 2000")
        j = admin_index(recorded, lambda m: m == "statement: SET statement_timeout = 10000")
        rows[i], rows[j] = rows[j], rows[i]
        self.assert_refuted(reaudit(recorded, rows), "outside-session sweep")

    def test_a_changed_seed_parameter_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith('execute <unnamed>: INSERT INTO "events"'))
        rows[i] = with_row(rows[i], detail=rows[i][pr.DETAIL].replace("$1 = '3'", "$1 = '300'"))
        self.assert_refuted(reaudit(recorded, rows), "reset", "unapproved parameters")

    def test_an_extra_role_privilege_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: GRANT CONNECT"))
        role = recorded["call"].ctx.role
        rows.insert(i + 1, with_row(rows[i], f'statement: ALTER ROLE "{role}" SUPERUSER'))
        self.assert_refuted(reaudit(recorded, rows), "role configuration")

    def test_an_unexpected_role_creation_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: SET log_statement = 'none'"))
        rows.insert(i + 1, with_row(rows[i], 'statement: CREATE ROLE "intruder" SUPERUSER LOGIN'))
        self.assert_refuted(reaudit(recorded, rows), "role creation", "GRANT CONNECT")

    def test_logging_left_off_after_role_creation_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        rows.pop(admin_index(recorded, lambda m: m.startswith("statement: GRANT CONNECT")))
        self.assert_refuted(reaudit(recorded, rows), "role creation")

    def test_a_cancellation_the_trace_doesnt_record_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("execute <unnamed>: SELECT pid, usename FROM"))
        pid = recorded["call"].ctx.actor_pids[0]
        rows.insert(i + 1, with_row(rows[i], f"execute <unnamed>: {ga.CANCEL_SQL}", detail=f"parameters: $1 = '{pid}'"))
        self.assert_refuted(reaudit(recorded, rows), "supervision", "unexpected during supervision")

    def test_a_cancel_probe_on_a_run_that_didnt_halt_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: SELECT 'ds_mark_start_"))
        probe = with_row(rows[i], "connection received: host=172.17.0.1 port=1", session="6aeb0000.ffff",
                         pid="65535", user="", database="", application="", backend="not initialized")
        rows.insert(i + 2, probe)
        self.assert_refuted(reaudit(recorded, rows), "every session in the gap attributed")

    def test_a_teardown_termination_of_a_foreign_pid_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: ALTER ROLE") and m.endswith("NOLOGIN"))
        rows.insert(i + 2, with_row(rows[i], f"execute <unnamed>: {ga.TERMINATE_SQL}",
                                    detail="parameters: $1 = '424242'"))
        self.assert_refuted(reaudit(recorded, rows), "teardown", "isn't one of this run's sessions")

    def test_a_late_reader_disconnection_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        prev = recorded["prev_reader"]
        i = next(k for k, r in enumerate(rows) if r.session == prev and r.message.startswith("disconnection:"))
        moved = rows.pop(i)
        j = admin_index(recorded, lambda m: m.startswith("execute <unnamed>: SELECT current_setting")) + 1
        rows.insert(j, with_row(moved))
        rows = [ga.Row(r.cells, n, n + 1) for n, r in enumerate(rows)]  # offsets follow the new order
        self.assert_refuted(reaudit(recorded, rows), "the previous reader closed before Gate 2A began", "after")

    def test_a_missing_reader_disconnection_is_indeterminate(self, recorded):
        prev = recorded["prev_reader"]
        rows = [r for r in recorded["rows"] if not (r.session == prev and r.message.startswith("disconnection:"))]
        cov = reaudit(recorded, rows)
        assert any(c.claim == "the previous reader closed before Gate 2A began" and c.status == ga.INDETERMINATE
                   for c in cov.claims) and cov.fails_closed

    def test_a_reader_connecting_inside_the_run_is_refuted(self, recorded):
        nxt = recorded["next_reader"]
        mine = [r for r in recorded["rows"] if r.session == nxt]
        rows = [r for r in recorded["rows"] if r.session != nxt]
        i = admin_index(recorded, lambda m: m.startswith("statement: SET log_statement"))
        rows[i:i] = mine
        rows = [ga.Row(r.cells, n, n + 1) for n, r in enumerate(rows)]
        self.assert_refuted(reaudit(recorded, rows), "the next reader connected only after Gate 2A returned")

    def test_a_named_prepared_admin_statement_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("execute <unnamed>: SELECT current_setting"))
        rows[i] = with_row(rows[i], rows[i].message.replace("execute <unnamed>", "execute _pg3_0"))
        self.assert_refuted(reaudit(recorded, rows), "preflight", "(B5)")

    def test_an_error_row_on_the_admin_session_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: DROP SCHEMA"))
        rows.insert(i + 1, with_row(rows[i], "relation does not exist", severity="ERROR"))
        self.assert_refuted(reaudit(recorded, rows), "reset", "ERROR row")

    def test_a_monitor_statement_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: DROP SCHEMA"))
        rows.insert(i, with_row(rows[i], "statement: SELECT 1", pid=str(recorded["harness"].monitor_pid),
                                session="6aeb0000.mon"))
        self.assert_refuted(reaudit(recorded, rows), "Gate 2A's monitor session stayed idle")

    def test_a_child_session_outside_its_window_is_refuted(self, recorded):
        ctx = recorded["call"].ctx
        rows = list(recorded["rows"])
        child = next(i for i, r in enumerate(rows) if r[pr.APPLICATION] == f"ds:{ctx.run_id}:monitor")
        moved = rows.pop(child)
        rows.insert(admin_index(recorded, lambda m: m.startswith("statement: SET lock_timeout = 2000")), moved)
        rows = [ga.Row(r.cells, n, n + 1) for n, r in enumerate(rows)]
        cov = reaudit(recorded, rows)
        self.assert_refuted(cov, "child sessions attributed", "outside Gate 2A's window")
        self.assert_refuted(cov, "initial snapshot taken after the reset and before the run's first session")

    def test_an_unexplained_fatal_row_in_a_child_session_is_refuted(self, recorded):
        ctx = recorded["call"].ctx
        rows = list(recorded["rows"])
        i = next(i for i, r in enumerate(rows) if r[pr.APPLICATION] == f"ds:{ctx.run_id}:monitor"
                 and r.message.startswith("statement:"))
        rows.insert(i + 1, with_row(rows[i], "terminating connection due to administrator command", severity="FATAL"))
        self.assert_refuted(reaudit(recorded, rows), "child sessions attributed", "unexplained FATAL")

    def test_a_child_session_with_an_unregistered_pid_is_refuted(self, recorded):
        ctx = recorded["call"].ctx
        rows = list(recorded["rows"])
        i = next(i for i, r in enumerate(rows) if r[pr.APPLICATION] == f"ds:{ctx.run_id}:monitor"
                 and r.message.startswith("statement:"))
        rows.insert(i + 1, with_row(rows[i], "statement: SELECT 1", pid="9999", session="6aeb0000.270f"))
        self.assert_refuted(reaudit(recorded, rows), "child sessions attributed", "isn't one the supervisor registered")

    def test_log_reads_from_another_position_are_refuted(self, recorded):
        rows = list(recorded["rows"])
        pid = str(recorded["harness"].admin_pid)
        last = max(i for i, r in enumerate(rows) if r[pr.PID] == pid)
        cycle = [with_row(rows[k]) for k in range(last - 2, last + 1)]
        cycle[2] = with_row(cycle[2], detail=re.sub(r"\$2 = '\d+'", "$2 = '1'", cycle[2][pr.DETAIL]))
        rows[last + 1:last + 1] = cycle
        rows = [ga.Row(r.cells, n, n + 1) for n, r in enumerate(rows)]
        self.assert_refuted(reaudit(recorded, rows), "window closed", "one position")

    def test_an_activity_poll_that_hides_another_session_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m == f"execute <unnamed>: {ga.ACTIVITY_SQL}")
        rows[i] = with_row(rows[i], detail=rows[i][pr.DETAIL].replace("}'", ",424242}'"))
        self.assert_refuted(reaudit(recorded, rows), "outside-session sweep", "other than the supervisor's own")

    def test_a_statement_row_at_another_severity_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: DROP SCHEMA"))
        rows[i] = with_row(rows[i], severity="WARNING")
        self.assert_refuted(reaudit(recorded, rows), "reset", "WARNING row")

    def test_an_admin_statement_after_the_template_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        pid = str(recorded["harness"].admin_pid)
        last = max(i for i, r in enumerate(rows) if r[pr.PID] == pid)
        rows.insert(last + 1, with_row(rows[last], "statement: SELECT 1", detail=""))
        rows = [ga.Row(r.cells, n, n + 1) for n, r in enumerate(rows)]
        self.assert_refuted(reaudit(recorded, rows), "nothing else on the admin session")

    def test_an_escalation_the_log_doesnt_show_is_refuted(self, recorded):
        ctx = recorded["call"].ctx
        changed = ga.RunContext(**{**ctx.__dict__, "escalation": ("abort", "cancel")})
        escalated = ga.Call(**{**recorded["call"].__dict__, "ctx": changed})
        self.assert_refuted(reaudit(recorded, call=escalated), "supervision", "weren't logged")

    def test_a_missing_registration_check_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        rows.pop(admin_index(recorded, lambda m: m.startswith(f"execute <unnamed>: {ga.REGISTRATION_SQL}")))
        self.assert_refuted(reaudit(recorded, rows), "supervision", "no registration check")

    def test_a_truncated_log_is_refused(self):
        row = io.StringIO()
        csv.writer(row).writerow(["x"] * 26)
        data = row.getvalue().encode()
        assert len(ga.parse_log(data)) == 1
        with pytest.raises(ga.AuditError, match="incomplete flush"):
            ga.parse_log(data[:-5])
        with pytest.raises(ga.AuditError, match="columns"):
            ga.parse_log(b"a,b\n")

    def test_a_skipped_log_line_is_refuted(self, recorded):
        rows = list(recorded["rows"])
        i = admin_index(recorded, lambda m: m.startswith("statement: DROP SCHEMA"))
        rows.pop(i)
        cov = ga.Coverage()
        ga.line_completeness(rows, cov)
        assert cov.claims[0].status == ga.REFUTED


class TestSegmentBoundaries:
    def test_a_missing_segment_marker_is_refused(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        original = b.readers.log_read

        def drop_marker(key="op"):
            data = original(key)
            if key != "whole":
                return data
            end = b.seg.segments[-1]["end"].encode()
            return b"".join(line for line in data.splitlines(keepends=True) if end not in line)
        b.readers.log_read = drop_marker
        refused(b.bootstrap, "segment boundary isn't in the log exactly once")

    def test_segments_and_offsets_are_recorded_explicitly(self, monkeypatch, tmp_path):
        b = Batch(monkeypatch, tmp_path)
        _traces, report = b.run_all(runs=1)
        assert [s["start"].startswith("ds_parent_mark_batch_start_") for s in report["segments"]] == [True] * 3
        assert len({s["reader"] for s in report["segments"]}) == 3  # a separate reader session per segment
        assert b.readers.logs["whole"].offset == b.readers.logs["whole"].offset  # one batch offset, carried forward
        assert b.readers.logs["whole"].path == fk.LOG_PATH


# ------------------------------------------------------------------ B5: can psycopg's names be predicted?


class TestPreparedNamePrediction:
    def test_the_model_reproduces_the_real_v2_log_readers_statements(self):
        """Real evidence: PostgreSQL 16.15 and psycopg 3.3.6 at the default threshold, in the second Gate A1 run."""
        rows = list(csv.reader(io.StringIO(V2_LOG.read_text()), strict=True))
        reader = [r for r in rows if r[pr.APPLICATION] == pr.LOG_READER_APP and pr.STATEMENT.match(r[pr.MESSAGE])]
        ops, observed = [], []
        for r in reader:
            verb, text = r[pr.MESSAGE].split(": ", 1)
            values = [v[1:-1].replace("''", "'") for _, v in pr.PARAMETER.findall(r[pr.DETAIL])]
            params = tuple(int(v) if "pg_read_binary_file" in text and v.isdigit() else v for v in values) or None
            ops.append(pm.Op(re.sub(r"\$\d+", "%s", text), params))
            observed.append((verb, text))
        assert len(ops) == 51 and any(v.startswith("execute _pg3_") for v, _ in observed)
        assert pm.predict(ops) == observed

    def test_with_preparation_off_nothing_is_named(self):
        ops = [pm.Op("SELECT current_setting(%s, true)", ("x",))] * 12
        assert {v for v, _ in pm.predict(ops, threshold=None)} == {"execute <unnamed>"}

    def test_psycopg_clears_its_cache_on_drop_and_alter(self):
        """Documents psycopg 3.3.6's own logic, not validated against a real log: after a name exists, a DROP or
        ALTER sends DEALLOCATE ALL, and names keep counting up."""
        ops = [pm.Op("SELECT current_setting(%s, true)", ("x",))] * 6 + [pm.Op("DROP SCHEMA x", command="DROP SCHEMA")]
        ops += [pm.Op("SELECT current_setting(%s, true)", ("x",))] * 6
        out = pm.predict(ops)
        assert ("statement", "DEALLOCATE ALL") in out
        assert [v for v, _ in out if v.startswith("execute _pg3_")] == ["execute _pg3_0", "execute _pg3_1"]

    def test_integer_size_changes_the_key(self):
        small = [pm.Op("SELECT pg_terminate_backend(%s)", (150,))] * 5
        large = [pm.Op("SELECT pg_terminate_backend(%s)", (70000,))]
        assert pm.predict(small + large)[-1][0] == "execute <unnamed>"  # int2 then int4: two keys

    def test_the_model_is_pinned_to_the_installed_psycopg(self):
        assert psycopg.__version__ == pm.PSYCOPG_VERSION
