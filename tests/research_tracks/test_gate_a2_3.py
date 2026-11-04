"""Gate A2.3, offline: B5 option 1 shown from the real code, B2 option A's role-catalog contract, and the one-run
supervisor characterization package, run with the real frozen Sandbox and SupervisedRunner against a fake server.

Simulated, not observed. Passing here is not evidence that the characterization ran or that any blocker is closed.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import socket
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in ("tests", "research_tools", "tests/research_tracks"):
    sys.path.insert(0, str(ROOT / extra))

import gate2a_fakes as fk  # noqa: E402
import gate_a1_characterize as a1  # noqa: E402
import gate_a2_run as drv  # noqa: E402
import gate_a2_supervisor_characterize as gc  # noqa: E402
from psycopg.conninfo import conninfo_to_dict  # noqa: E402
from test_gate_a2_2 import Readers  # noqa: E402

from deployspecter.database import Sandbox  # noqa: E402
from deployspecter.live.runner import LiveLimits  # noqa: E402
from deployspecter.live.supervisor import SupervisedRunner, SupervisorLimits  # noqa: E402
from deployspecter.research.track_a import gate2a_audit as ga  # noqa: E402
from deployspecter.research.track_a import gate2a_prepare_model as pm  # noqa: E402
from deployspecter.research.track_a import gate2a_role_catalog as rc  # noqa: E402
from deployspecter.research.track_a import gate_a2_controls as c  # noqa: E402

PACKAGE = ROOT / "docs/research/live_stepping/track_a/gate_a2_supervisor_characterization_manifest.json"
URL = "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def package():
    sha = hashlib.sha256(PACKAGE.read_bytes()).hexdigest()
    return gc.load_package(PACKAGE, sha), sha


class Fake:
    """What RealCharacterization provides, on the fake server: segment effects, the Sandbox, the frozen runner."""

    def __init__(self, monkeypatch, *faults, behaviour="clean", reader_faults=(), supervisor=None, construct=None):
        self.server = fk.FakeServer(*faults)
        self.procs = fk.install(monkeypatch, self.server, behaviour)
        self.m, _ = package()
        self.effects = Readers(self.server, *reader_faults)
        self.supervisor, self._construct = supervisor or {}, construct
        self.box = None

    def construct(self):
        if self._construct is not None:
            return self._construct(self)
        self.box = Sandbox(c.harness_settings(c.connection_keywords(conninfo_to_dict(URL), self.m)))
        return self.box

    def runner(self, box):
        g = self.m["safety_ceilings"]["gate2a"]
        return SupervisedRunner(box, LiveLimits(**g["LiveLimits"]),
                                SupervisorLimits(**{**g["SupervisorLimits"], **self.supervisor}))


def characterize(monkeypatch, tmp_path, *faults, **kw):
    fake = Fake(monkeypatch, *faults, **kw)
    evidence = a1.a2.EvidenceDir.create(tmp_path)
    return fake, evidence, lambda: gc.characterize(fake.effects, fake.m, evidence, construct=fake.construct,
                                                   runner_factory=fake.runner)


def coverage(evidence):
    path = evidence.path / "gate2a_coverage.json"
    assert path.exists(), "a stopped or finished batch must leave its coverage record"
    return json.loads(path.read_text())


def claims(report, name=None):
    return [(x["scope"], x["claim"], x["status"], x["basis"]) for x in report["claims"]
            if name is None or x["claim"] == name]


# ------------------------------------------------------------------ B5 option 1, from the real implementation


class TestB5FromTheRealCode:
    def test_psycopgs_property_setter_only_assigns_and_sends_nothing(self):
        prop = psycopg.Connection.prepare_threshold
        assert isinstance(prop, property) and prop.fset is not None and "None" in prop.__doc__
        body = ast.parse(textwrap.dedent(inspect.getsource(prop.fset))).body[0].body
        assert len(body) == 1 and isinstance(body[0], ast.Assign)
        assert ast.unparse(body[0]) == "self._prepared.prepare_threshold = value"
        assert not [n for n in ast.walk(body[0]) if isinstance(n, ast.Call)]

    def test_sandbox_init_sends_three_distinct_statements_once_each_and_none_on_monitor(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(Sandbox.__init__)))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "execute"]
        targets = [ast.unparse(n.func.value) for n in calls]
        texts = [ast.unparse(n.args[0]) for n in calls]
        assert targets == ["self.admin"] * 3 and len(set(texts)) == 3
        assigned = [ast.unparse(t) for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets]
        assert assigned.count("self.admin") == 1 and assigned.count("self.monitor") == 1

    def test_nothing_in_gate2a_reassigns_the_harness_sessions(self):
        for path in ("src/deployspecter/live/supervisor.py", "src/deployspecter/database.py"):
            tree = ast.parse((ROOT / path).read_text())
            assigned = [ast.unparse(t) for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AugAssign))
                        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])]
            assert not [t for t in assigned if t.endswith((".admin", ".monitor")) and t not in (
                "self.admin", "self.monitor")], path
        source = (ROOT / "src/deployspecter/live/supervisor.py").read_text()
        assert "self.box.admin.execute" in source and "= self.box.admin\n" not in source

    def test_supervised_runner_init_sends_nothing(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(SupervisedRunner.__init__)))
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in (
            "execute", "_admin", "_one", "_all", "_bounded")]

    def test_psycopg_with_preparation_off_never_names_or_deallocates(self):
        """psycopg 3.3.6's real PrepareManager: the three init statements once at the default, then None."""
        session = pm.PreparedSession(5)
        init = [pm.Op("SET lock_timeout = 30000", command="SET"), pm.Op("SELECT pg_advisory_lock(%s)", (7420001,)),
                pm.Op("SET lock_timeout = 0", command="SET")]
        assert {v for op in init for v, _ in session.execute(op)} <= {"statement", "execute <unnamed>"}
        session.manager.prepare_threshold = None
        later = [pm.Op("SELECT current_setting(%s, true)", ("x",))] * 20 + [pm.Op("DROP SCHEMA x", command="DROP")]
        out = [entry for op in later for entry in session.execute(op)]
        assert {v for v, _ in out} == {"execute <unnamed>", "statement"} and len(out) == 21

    def test_the_production_path_sets_only_the_harness_sessions_and_sends_nothing(self, monkeypatch, tmp_path):
        fake, evidence, _run = characterize(monkeypatch, tmp_path)
        seg = drv.SegmentedRun(fake.effects, fake.m, evidence)
        seg.observe_roles("before")
        sets = []
        original = fk.FakeConn.prepare_threshold.fset

        def counting(conn, value):
            before = len(fake.server.data)
            original(conn, value)
            sets.append((conn.session.app, conn.session.pid, value, len(fake.server.data) - before))
        monkeypatch.setattr(fk.FakeConn, "prepare_threshold", property(fk.FakeConn.prepare_threshold.fget, counting))
        box = seg.bootstrap(fake.construct)
        assert [(app, value, logged) for app, _pid, value, logged in sets] == [
            (c.HARNESS_APP, None, 0), (c.HARNESS_APP, None, 0)]  # two sessions, nothing logged by the change
        assert {pid for _a, pid, _v, _l in sets} == {box.admin.session.pid, box.monitor.session.pid}
        assert box.admin.prepare_threshold is None and box.monitor.prepare_threshold is None
        assert fake.effects.conn.prepare_threshold is None  # the reader was opened unprepared, not changed
        bootstrap = [x for x in seg.coverage.claims if x.claim == "Sandbox construction statements"]
        assert bootstrap[0].status == ga.VERIFIED

    @pytest.mark.parametrize("build, fragment", [
        (lambda f: SimpleNamespace(admin=object(), monitor=object()), "no supported prepare_threshold property"),
        (lambda f: (lambda b: SimpleNamespace(admin=b.admin, monitor=b.admin))(
            Sandbox(c.harness_settings(c.connection_keywords(conninfo_to_dict(URL), f.m)))),
         "admin and monitor sessions are the same backend"),
    ])
    def test_a_harness_without_the_supported_property_or_identity_is_refused(self, monkeypatch, tmp_path, build,
                                                                             fragment):
        fake, evidence, _run = characterize(monkeypatch, tmp_path, construct=build)
        seg = drv.SegmentedRun(fake.effects, fake.m, evidence)
        seg.observe_roles("before")
        with pytest.raises(drv.DriverRefused, match=fragment):
            seg.bootstrap(fake.construct)
        assert seg.calls[-1]["outcome"] == "failed" and coverage(evidence)["stopped"] == "the sandbox bootstrap"

    def test_a_failed_sandbox_connection_is_refused_and_its_gap_still_read(self, monkeypatch, tmp_path):
        def fail(_f):
            raise psycopg.OperationalError("simulated: connection refused")
        fake, evidence, _run = characterize(monkeypatch, tmp_path, construct=fail)
        seg = drv.SegmentedRun(fake.effects, fake.m, evidence)
        seg.observe_roles("before")
        with pytest.raises(drv.DriverRefused, match="the sandbox bootstrap raised OperationalError"):
            seg.bootstrap(fake.construct)
        assert fake.effects.opened == 2  # the reader reopened once, to read the gap; nothing was retried

    def test_the_reader_pid_is_never_a_harness_session(self):
        conn = lambda pid: SimpleNamespace(info=SimpleNamespace(  # noqa: E731
            host="127.0.0.1", hostaddr="127.0.0.1", port=55433, dbname="deployspecter_test", user="deployspecter",
            backend_pid=pid))

        class Conn:
            def __init__(self, pid):
                self.info, self.value = conn(pid).info, 5

            @property
            def prepare_threshold(self):
                return self.value

            @prepare_threshold.setter
            def prepare_threshold(self, value):
                self.value = value

        want = {"host": "127.0.0.1", "hostaddr": "127.0.0.1", "port": "55433", "dbname": "deployspecter_test",
                "user": "deployspecter"}
        box = SimpleNamespace(admin=Conn(10), monitor=Conn(11))
        assert c.apply_harness_policy(box, want, reader_pid=12) == []
        assert any("reader's backend" in x for x in c.apply_harness_policy(box, want, reader_pid=11))


# ------------------------------------------------------------------ B2 option A


def observation(label="o", **changes):
    roles = {"deployspecter": {**rc.RUNNER_ATTRIBUTES, "rolsuper": True, "rolcanlogin": True},
             "deployspecter_runner": dict(rc.RUNNER_ATTRIBUTES)}
    acls = dict.fromkeys(fk.DATABASES, "{deployspecter=CTc/deployspecter}")
    base = {"roles": roles, "memberships": (), "role_settings": (), "database_acls": acls}
    base.update(changes)
    return rc.Observation(label, **base)


class TestB2RoleCatalogContract:
    def conditions(self, hardened=True):
        return rc.Conditions("deployspecter", list(fk.DATABASES), hardened)

    def judged(self, before, after, hardened=True):
        cov = ga.Coverage()
        rc.judge(before, after, self.conditions(hardened), "run x", cov)
        return {x.claim: (x.status, x.basis) for x in cov.claims}

    def test_equal_clean_observations_verify_only_the_stated_conditions(self):
        out = self.judged(observation("before"), observation("after"))
        assert out["role catalog meets the before/after conditions"][0] == ga.VERIFIED
        assert out["the run role's attributes and privileges inside the unlogged interval"][0] == ga.UNOBSERVABLE
        assert out["the role-creation SQL itself"][0] == ga.UNOBSERVABLE

    @pytest.mark.parametrize("after, fragment", [
        (observation(roles={**observation().roles, "ds_run_0123456789ab": dict(rc.RUNNER_ATTRIBUTES)}), "role ds_run_"),
        (observation(memberships=(("deployspecter_runner", "ds_run_0123456789ab", False),)), "membership"),
        (observation(role_settings=(("", "ds_run_0123456789ab", "{search_path=ds_run}"),)), "setting for"),
        (observation(database_acls={**observation().database_acls, "deployspecter_test":
                                    "{deployspecter=CTc/deployspecter,ds_run_0123456789ab=c/deployspecter}"}),
         "privilege on deployspecter_test"),
        (observation(roles={**observation().roles, "intruder": dict(rc.RUNNER_ATTRIBUTES)}), "roles or role attri"),
        (observation(roles={**observation().roles, "deployspecter_runner": {**rc.RUNNER_ATTRIBUTES,
                                                                            "rolcanlogin": True}}), "DO block"),
        (observation(roles={**observation().roles, "deployspecter": dict(rc.RUNNER_ATTRIBUTES)}), "superuser"),
        (observation(database_acls={**observation().database_acls, "postgres": ""}), "PUBLIC can still connect"),
        (observation(database_acls={**observation().database_acls, "postgres": "{=Tc/deployspecter}"}),
         "PUBLIC can still connect"),
    ])
    def test_any_residue_or_change_is_refuted(self, after, fragment):
        status, basis = self.judged(observation("before"), after)["role catalog meets the before/after conditions"]
        assert status == ga.REFUTED and fragment in basis, basis

    @pytest.mark.parametrize("before, after, fragment", [
        # each detected by one check only
        (observation(memberships=(("deployspecter_runner", "ds_run_0123456789ab", False),)),
         observation(memberships=(("deployspecter_runner", "ds_run_0123456789ab", False),)),
         "membership of ds_run_0123456789ab in deployspecter_runner"),
        (observation(), observation(memberships=(("deployspecter_runner", "intruder", False),)),
         "role memberships changed"),
        (observation(), observation(role_settings=(("", "deployspecter", "{log_statement=none}"),)),
         "role settings changed"),
    ])
    def test_a_lasting_membership_or_a_new_setting_is_refuted(self, before, after, fragment):
        status, basis = self.judged(before, after)["role catalog meets the before/after conditions"]
        assert status == ga.REFUTED and fragment in basis, basis

    def test_a_bootstrap_that_leaves_the_runner_role_wrong_is_refuted(self):
        before = observation(roles={"deployspecter": observation().roles["deployspecter"]})
        wrong = observation(roles={**observation().roles, "deployspecter_runner": {**rc.RUNNER_ATTRIBUTES,
                                                                                   "rolcanlogin": True}})
        cov = ga.Coverage()
        rc.judge_bootstrap(before, wrong, self.conditions(False), "gap 1", cov)
        assert cov.claims[0].status == ga.REFUTED and "DO block declares" in cov.claims[0].basis

    def test_a_missing_observation_is_indeterminate(self):
        for before, after in ((None, observation()), (observation(), None)):
            assert self.judged(before, after)["role catalog meets the before/after conditions"][0] == \
                ga.INDETERMINATE

    def test_without_hardening_database_privileges_must_not_change(self):
        before = observation(database_acls=dict.fromkeys(fk.DATABASES, ""))
        out = self.judged(before, observation(), hardened=False)
        assert out["role catalog meets the before/after conditions"][0] == ga.REFUTED

    def test_a_clean_bootstrap_only_adds_the_runner_role_as_declared(self):
        before = observation(roles={"deployspecter": observation().roles["deployspecter"]})
        cov = ga.Coverage()
        rc.judge_bootstrap(before, observation(), self.conditions(False), "gap 1", cov)
        assert cov.claims[0].status == ga.VERIFIED
        cov = ga.Coverage()
        rc.judge_bootstrap(before, observation(roles={**observation().roles, "x": dict(rc.RUNNER_ATTRIBUTES)}),
                           self.conditions(False), "gap 1", cov)
        assert cov.claims[0].status == ga.REFUTED

    def test_an_interval_with_missing_admin_lines_is_refuted(self):
        def row(line, message):
            cells = [""] * 26
            cells[ga.pr.LINE], cells[ga.pr.PID], cells[ga.pr.MESSAGE] = str(line), "205", message
            return ga.Row(cells, line, line + 1)
        ctx = SimpleNamespace(role="ds_run_0123456789ab")
        for gap, status in ((1, ga.VERIFIED), (3, ga.REFUTED)):
            run = SimpleNamespace(ctx=ctx, scope="run x", marks={
                "log_off": row(10, "statement: SET log_statement = 'none'"),
                "log_on": row(10 + gap, 'statement: GRANT CONNECT ON DATABASE "d" TO "ds_run_0123456789ab"')})
            cov = ga.Coverage()
            rc.interval_contract(run, cov)
            assert cov.claims[0].status == status
        cov = ga.Coverage()
        rc.interval_contract(SimpleNamespace(ctx=ctx, scope="run x", marks={}), cov)
        assert cov.claims[0].status == ga.INDETERMINATE

    def test_a_malformed_catalog_answer_refuses(self):
        class Conn:
            def execute(self, text):
                return SimpleNamespace(fetchall=lambda: [("only-a-name",)])
        with pytest.raises(ga.AuditError, match="malformed"):
            rc.read(Conn(), "x")


# ------------------------------------------------------------------ the characterization, end to end under fakes


class TestTheCharacterization:
    def test_a_clean_run_is_characterized_with_disclosed_gaps(self, monkeypatch, tmp_path):
        fake, evidence, run = characterize(monkeypatch, tmp_path)
        summary = run()
        assert summary["outcome"] == "CHARACTERIZED_WITH_DISCLOSED_GAPS" and summary["never_all_sql_verified"]
        report = coverage(evidence)
        assert report["classification"] == "OBSERVABLE_SQL_VERIFIED_WITH_DISCLOSED_GAPS"
        assert not [x for x in claims(report) if x[2] in (ga.REFUTED, ga.INDETERMINATE)]
        statuses = {(x[1], x[2]) for x in claims(report)}
        assert ("role catalog meets the before/after conditions", ga.VERIFIED) in statuses
        assert ("role catalog around the sandbox bootstrap", ga.VERIFIED) in statuses
        assert ("unlogged interval bounded by the log", ga.VERIFIED) in statuses
        assert ("final state independently read after the run", ga.VERIFIED) in statuses
        assert ("role creation", ga.UNOBSERVABLE) in statuses and ("role-creation SQL itself", ga.VERIFIED) not in \
            statuses
        assert [x["outcome"] for x in report["calls"]] == ["completed", "completed"]
        run_id = json.loads((evidence.path / "trace_summary.json").read_text())["run_id"]
        assert set(report["role_catalog"]) == {"before the sandbox bootstrap", "after the sandbox bootstrap",
                                               f"after run {run_id}"}
        (contract,) = report["b2_interval"]
        assert contract["role"] == f"ds_run_{run_id}" and "SET log_statement = 'none'" in contract["starts_after"]
        assert contract["ends_before"].endswith(f'GRANT CONNECT ON DATABASE "deployspecter_test" TO "ds_run_{run_id}"')
        assert len(report["segments"]) == 3

    def test_the_evidence_holds_no_secret(self, monkeypatch, tmp_path):
        _fake, evidence, run = characterize(monkeypatch, tmp_path)
        run()
        names = {f.name for f in evidence.path.iterdir()}
        assert {"preflight.json", "construction.json", "gate2a_coverage.json", "trace_summary.json",
                "summary.json"} <= names
        assert not [f for f in evidence.path.iterdir() if b"s3cret" in f.read_bytes() or b"SCRAM" in f.read_bytes()]

    @pytest.mark.parametrize("faults, fragment", [
        (("residual_role",), "role ds_run_"),
        (("residual_privilege",), "privilege on deployspecter_test"),
    ])
    def test_a_residual_role_or_privilege_is_refuted(self, monkeypatch, tmp_path, faults, fragment):
        _fake, evidence, run = characterize(monkeypatch, tmp_path, *faults)
        with pytest.raises(drv.DriverRefused):
            run()
        bad = [x for x in claims(coverage(evidence), "role catalog meets the before/after conditions")
               if x[2] == ga.REFUTED]
        assert bad and fragment in bad[0][3]

    def test_a_runner_role_with_other_attributes_is_refuted_at_bootstrap(self, monkeypatch, tmp_path):
        _fake, evidence, run = characterize(monkeypatch, tmp_path, "runner_role_wrong")
        with pytest.raises(drv.DriverRefused, match="DO block declares"):
            run()

    def test_an_interrupt_before_teardown_leaves_a_login_role_that_the_after_observation_shows(self, monkeypatch,
                                                                                            tmp_path):
        fake, evidence, run = characterize(monkeypatch, tmp_path)

        def interrupt(_conn):
            raise KeyboardInterrupt
        fake.server.hooks.append(("GRANT CONNECT", interrupt))
        with pytest.raises(KeyboardInterrupt):
            run()
        report = coverage(evidence)
        assert report["calls"][-1]["outcome"] == "interrupted_unknown_effects"
        left = [r for r, a in report["role_catalog"]["after a stopped a Gate 2A call"]["roles"].items()
                if r.startswith("ds_run_")]
        assert len(left) == 1 and report["role_catalog"]["after a stopped a Gate 2A call"]["roles"][left[0]][
            "rolcanlogin"] is True
        bad = [x for x in claims(report, "role catalog meets the before/after conditions") if x[2] == ga.REFUTED]
        assert bad and "role ds_run_" in bad[0][3]

    @pytest.mark.parametrize("faults, behaviour, supervisor, outcome", [
        (("create_role_fails",), "clean", None, "failed"),
        (("reset_fails",), "clean", None, "failed"),
        (("seed_fails",), "clean", None, "failed"),
        ((), "worker_fails", None, "flagged"),
        ((), "timeout", {"run_deadline_s": 0.05, "escalation_grace_s": 0.02, "kill_wait_s": 0.05,
                         "handshake_s": 2.0}, "flagged"),
        ((), "linger", {"run_deadline_s": 0.05, "escalation_grace_s": 0.02, "kill_wait_s": 0.05,
                        "quiescence_s": 0.05}, "flagged"),
    ])
    def test_failed_or_flagged_calls_stop_with_a_record(self, monkeypatch, tmp_path, faults, behaviour, supervisor,
                                                        outcome):
        _fake, evidence, run = characterize(monkeypatch, tmp_path, *faults, behaviour=behaviour,
                                            supervisor=supervisor)
        with pytest.raises(drv.DriverRefused):
            run()
        report = coverage(evidence)
        assert report["calls"][-1]["outcome"] == outcome and report["stopped"] == "a Gate 2A call"
        assert "after a stopped a Gate 2A call" in report["role_catalog"]
        assert not (evidence.path / "summary.json").exists()

    def test_a_reader_present_at_the_sweep_stops_the_run(self, monkeypatch, tmp_path):
        fake, evidence, run = characterize(monkeypatch, tmp_path)
        original = fake.effects.reader_close
        fake.effects.reader_close = lambda: (fake.effects.faults.add("reader_stays") if fake.effects.opened >= 2
                                             else None) or original()
        with pytest.raises(drv.DriverRefused, match="present at the supervisor's sweep"):
            run()

    def test_a_final_read_that_disagrees_is_refuted(self, monkeypatch, tmp_path):
        fake, evidence, run = characterize(monkeypatch, tmp_path)

        def drift(_conn):
            fake.server.tables["events"][0]["reserved"] = 99  # after the run, unseen by Gate 2A's final snapshot
        fake.server.hooks.append(("SELECT 'ds_mark_end_", drift))
        summary = run()
        assert summary["outcome"] == "NOT_CHARACTERIZED"
        assert [x[2] for x in claims(coverage(evidence), "final state independently read after the run")] == [
            ga.REFUTED]

    def test_a_missing_final_snapshot_in_the_log_stops_the_run(self, monkeypatch, tmp_path):
        fake, evidence, run = characterize(monkeypatch, tmp_path)
        seen = []

        def lose_the_final_snapshot(_conn):
            seen.append(True)  # the prepared-transaction query runs at the reset sweep and again in teardown
            if len(seen) == 2:
                fake.server.unlogged_prefixes.append("SELECT table_name FROM information_schema.tables")
        fake.server.hooks.append(("SELECT gid FROM pg_prepared_xacts", lose_the_final_snapshot))
        with pytest.raises(drv.DriverRefused, match="final snapshot"):
            run()
        assert [x[2] for x in claims(coverage(evidence), "final snapshot")] == [ga.REFUTED]

    def test_unexpected_bootstrap_activity_stops_the_run(self, monkeypatch, tmp_path):
        fake, evidence, run = characterize(monkeypatch, tmp_path)
        fake.server.inject[ga.ADVISORY_LOCK_SQL] = ["SELECT pg_sleep(0)"]
        with pytest.raises(drv.DriverRefused, match="sandbox bootstrap didn't verify"):
            run()

    def test_a_missing_batch_offset_is_refused(self, monkeypatch, tmp_path):
        from deployspecter.live.serverlog import LogUnverified
        fake, evidence, run = characterize(monkeypatch, tmp_path)

        def no_position(key="op"):
            raise LogUnverified("the server isn't writing a CSV log")
        fake.effects.log_position = no_position
        with pytest.raises(drv.DriverRefused, match="no position to start from"):
            run()

    def test_bootstrap_and_calls_need_a_role_observation_before_them(self, monkeypatch, tmp_path):
        fake, evidence, _run = characterize(monkeypatch, tmp_path)
        seg = drv.SegmentedRun(fake.effects, fake.m, evidence)
        with pytest.raises(drv.DriverRefused, match="before the sandbox bootstrap"):
            seg.bootstrap(fake.construct)
        seg.observe_roles("before")
        seg.bootstrap(fake.construct)
        seg.last_roles = None
        with pytest.raises(drv.DriverRefused, match="before the Gate 2A call"):
            seg.gate2a(lambda: None, mode="stepped", scenario=None, verified_initial_digest=None)

    @pytest.mark.parametrize("fault, fragment", [("preflight_bad", "preflight"),
                                                 ("parent_seed_differs", "construction check failed")])
    def test_a_bad_preflight_or_construction_stops_before_gate2a(self, monkeypatch, tmp_path, fault, fragment):
        fake, _evidence, run = characterize(monkeypatch, tmp_path, fault)
        with pytest.raises(drv.DriverRefused, match=fragment):
            run()
        assert fake.procs == []  # no supervised child was ever started

    def test_a_scenario_other_than_the_reviewed_one_is_refused(self, monkeypatch, tmp_path):
        fake, _evidence, run = characterize(monkeypatch, tmp_path)
        fake.m = {**fake.m, "scenario": {**fake.m["scenario"], "digest": "0" * 64}}
        with pytest.raises(drv.DriverRefused, match="scenario differs"):
            gc.characterize(fake.effects, fake.m, _evidence, construct=fake.construct, runner_factory=fake.runner)

    def test_plain_serial_stays_refused(self, monkeypatch, tmp_path):
        m, _ = package()
        assert m["plain_serial"] == "refused" and "plain_serial" in " ".join(m["excludes"])


# ------------------------------------------------------------------ the package and its guard


class TestThePackage:
    def test_the_manifest_pins_its_sources_and_statement_sets(self):
        from deployspecter.research.track_a import pipeline as p
        from live_app import reservations as app
        m, _ = package()
        a1.a2.verify_sources(m)
        frozen = {"src/deployspecter/live/supervisor.py", "src/deployspecter/live/worker.py",
                  "src/deployspecter/live/serverlog.py", "src/deployspecter/database.py"}
        assert frozen <= set(m["source_sha256"])
        assert m["statement_sets_sha256"]["op0p_role_catalog"] == c.set_digest(rc.statements())
        types = {tuple(k.split(".")): v for k, v in m["state_checks"]["column_types"].items()}
        sets = c.statement_sets(app.SETUP_SQL, p.run_offline_chain(), types)
        assert all(m["statement_sets_sha256"][k] == c.set_digest(v) for k, v in sets.items()
                   if k in m["statement_sets_sha256"])
        assert m["gate2a_audit"]["template_sha256"] == hashlib.sha256(
            json.dumps(ga.template_texts()).encode()).hexdigest()
        assert m["status"] == "WAITING_FOR_SEPARATE_EXECUTION_APPROVAL" and m["b2_policy"]["option"] == "A"

    @pytest.mark.parametrize("change, fragment", [
        (lambda m: m.update(scope="buggy_fixed_demonstration"), "supervisor only"),
        (lambda m: m["client_connection_policy"].update(gate2a_harness_admin_and_monitor="default"), "B5 option 1"),
        (lambda m: m["b2_policy"].update(limitations=[]), "B2 option A"),
        (lambda m: m.update(plain_serial="allowed"), "plain_serial"),
        (lambda m: m["target"].update(port="55432"), "forbidden"),
        (lambda m: m.update(version=2), "version 1"),
        (lambda m: m["safety_ceilings"]["session_options"].update(extra_float_digits=3), "extra_float_digits"),
        (lambda m: m["container"].update(port="55434"), "don't name its target"),
        (lambda m: m["gate2a_audit"].pop("log_path"), "Gate 2A audit's inputs"),
    ])
    def test_a_manifest_that_loosens_the_package_is_refused(self, tmp_path, change, fragment):
        data = json.loads(PACKAGE.read_text())
        change(data)
        path = tmp_path / "m.json"
        path.write_text(json.dumps(data))
        with pytest.raises(drv.DriverRefused, match=fragment):
            gc.load_package(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_plan_prints_steps_stops_and_operator_commands(self, capsys):
        _, sha = package()
        assert gc.main(["plan", "--manifest", str(PACKAGE), "--manifest-sha256", sha]) == 0
        out = capsys.readouterr().out
        assert "gap 2: exactly one SupervisedRunner.run" in out and "docker run -d --rm --name deployspecter-live-pg" \
            in out and "stop conditions:" in out and "s3cret" not in out

    def test_characterize_needs_the_signed_phase_approval(self, tmp_path):
        _, sha = package()
        made = []
        with pytest.raises(drv.DriverRefused, match="no approval"):
            gc.main(["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha], factory=made.append)
        approval = tmp_path / "a.json"
        approval.write_text(json.dumps({"manifest_sha256": sha, "phase": "gate_a1", "approver": "o",
                                        "approved_on": "2026-11-06"}))
        with pytest.raises(drv.DriverRefused, match="another manifest or phase"):
            gc.main(["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha, "--approval",
                     str(approval)], factory=made.append)
        assert made == []

    def test_with_an_approval_main_runs_once_and_keeps_it_byte_for_byte(self, monkeypatch, tmp_path):
        m, sha = package()
        moved = {**m, "artifacts_root": str(tmp_path / "evidence")}
        monkeypatch.setattr(gc, "load_package", lambda *_a: moved)
        approval = tmp_path / "approval.json"
        approval.write_bytes(json.dumps({"manifest_sha256": sha, "phase": gc.PHASE, "approver": "owner",
                                         "approved_on": "2026-11-06"}).encode())
        made = []

        def factory(manifest):
            made.append(Fake(monkeypatch))
            return made[-1]
        assert gc.main(["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha, "--approval",
                        str(approval)], factory=factory) == 0
        (run_dir,) = (tmp_path / "evidence").iterdir()
        assert len(made) == 1 and (run_dir / "approval.json").read_bytes() == approval.read_bytes()
        assert json.loads((run_dir / "summary.json").read_text())["retried"] is False

    def test_the_manifest_digest_must_be_the_one_given(self):
        with pytest.raises(drv.DriverRefused, match="digest isn't the one given"):
            gc.load_package(PACKAGE, "0" * 64)

    def guarded(self, monkeypatch, tmp_path, change=None, tamper=False):
        m, sha = package()
        moved = {**m, "artifacts_root": str(tmp_path / "evidence"), "source_sha256": dict(m["source_sha256"])}
        if change:
            change(moved)
        monkeypatch.setattr(gc, "load_package", lambda *_a: moved)
        approval = tmp_path / "approval.json"
        approval.write_text(json.dumps({"manifest_sha256": sha, "phase": gc.PHASE, "approver": "owner",
                                        "approved_on": "2026-11-06"}))
        if tamper:
            original = gc.v2.load_approval

            def load_then_change(*a, **k):
                out = original(*a, **k)
                approval.write_text(approval.read_text().replace("owner", "other"))
                return out
            monkeypatch.setattr(gc.v2, "load_approval", load_then_change)
        made = []
        argv = ["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha, "--approval", str(approval)]
        return (lambda: gc.main(argv, factory=lambda _m: made.append(Fake(monkeypatch)) or made[-1])), made

    @pytest.mark.parametrize("change, tamper, fragment", [
        (lambda m: m.update(status="EXECUTED"), False, "isn't waiting for approval"),
        (None, True, "changed while it was being read"),
        (lambda m: m["source_sha256"].update({"src/deployspecter/live/supervisor.py": "0" * 64}), False,
         "changed since the manifest was reviewed"),
    ])
    def test_the_guard_refuses_before_any_evidence_or_connection(self, monkeypatch, tmp_path, change, tamper,
                                                                 fragment):
        run, made = self.guarded(monkeypatch, tmp_path, change, tamper)
        with pytest.raises(drv.DriverRefused, match=fragment):
            run()
        assert made == [] and not (tmp_path / "evidence").exists()

    def test_a_failed_run_leaves_a_failure_record(self, monkeypatch, tmp_path):
        m, sha = package()
        monkeypatch.setattr(gc, "load_package", lambda *_a: {**m, "artifacts_root": str(tmp_path / "evidence")})
        approval = tmp_path / "approval.json"
        approval.write_text(json.dumps({"manifest_sha256": sha, "phase": gc.PHASE, "approver": "owner",
                                        "approved_on": "2026-11-06"}))
        with pytest.raises(drv.DriverRefused):
            gc.main(["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha, "--approval",
                     str(approval)], factory=lambda _m: Fake(monkeypatch, "seed_fails"))
        (run_dir,) = (tmp_path / "evidence").iterdir()
        failure = json.loads((run_dir / "failure.json").read_text())
        assert failure["classification"] == "refused_fail_closed" and failure["retried"] is False

    def test_the_gate_a2_demonstration_stays_blocked(self):
        v5 = ROOT / "docs/research/live_stepping/track_a/execution_manifest_v5.json"
        m = drv.load_manifest(v5, hashlib.sha256(v5.read_bytes()).hexdigest())
        assert drv.GATE2A_SECTION_IMPLEMENTED is False
        assert all(b["status"].startswith("OPEN") for b in m["execution_blockers"])
        with pytest.raises(drv.DriverRefused, match="isn't executable"):
            drv.require_executable(m, "run")

    def test_the_driver_runs_no_command_and_imports_no_model(self):
        tree = ast.parse((ROOT / "research_tools/gate_a2_supervisor_characterize.py").read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & {"subprocess", "shlex", "pty", "multiprocessing", "deployspecter.meta_client"}
        private = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr.startswith("_")
                   and not n.attr.startswith("__")}
        assert private <= {"_modules"}
