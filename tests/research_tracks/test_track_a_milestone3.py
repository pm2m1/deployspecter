"""Track A milestone 3, offline: parent-session reconciliation and the bounded driver, all external effects mocked.

No database, container, subprocess or model is used. Fake connections answer only approved statements, and the fake
server log is written from what the driver actually executed, so the reconciliation is exercised for real.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import csv
import hashlib
import io
import json
import socket
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "research_tools"))
sys.path.insert(0, str(ROOT / "tests/research_tracks"))

import track_a_run as drv  # noqa: E402
from test_track_a_milestone2 import raw_cross, raw_export  # noqa: E402

from deployspecter.live.trace import LiveTrace, RunEvidence  # noqa: E402
from deployspecter.research.track_a import catalog_export as ce  # noqa: E402
from deployspecter.research.track_a import parent_reconciliation as pr  # noqa: E402

MANIFEST = ROOT / "docs/research/live_stepping/track_a/execution_manifest_v2.json"
DRIVER = ROOT / "research_tools/track_a_run.py"
USER, DB = "deployspecter", "deployspecter_test"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


# ------------------------------------------------------------------ a fake PostgreSQL 16 csvlog


def row(session, message, *, app="", user=USER, db=DB, pid="4242", severity="LOG", detail="",
        backend="client backend"):
    r = [""] * 26
    r[pr.USER], r[pr.DATABASE], r[pr.PID], r[pr.SESSION] = user, db, pid, session
    r[pr.SEVERITY], r[pr.MESSAGE], r[pr.DETAIL] = severity, message, detail
    r[pr.APPLICATION], r[pr.BACKEND] = app, backend
    return r


def csv_bytes(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode()


def session_rows(tag, statements, session="s1", pid="4242", verbs=None, details=None, **kw):
    """A session as PostgreSQL 16 logs it (observed in the first Gate A1 run): received (no user, database or name
    yet), authenticated and authorized (no application name yet), the statements, then the disconnection."""
    port = str(50000 + int(pid) % 10000)
    rows = [row(session, f"connection received: host=172.17.0.1 port={port}", pid=pid, user="", db="",
                backend="not initialized"),
            row(session, f'connection authenticated: identity="{USER}" method=scram-sha-256 '
                         f"(/var/lib/postgresql/data/pg_hba.conf:128)", pid=pid),
            row(session, f"connection authorized: user={USER} database={DB} application_name={tag}", pid=pid)]
    for n, (text, params) in enumerate(statements):
        detail = ", ".join(f"${i} = {pr.literal(v)}" for i, v in enumerate(params, 1))
        verb = verbs[n] if verbs else ("execute <unnamed>" if params else "statement")
        if details is not None:
            detail = details[n]
        elif params:
            detail = f"parameters: {detail}"
        rows.append(row(session, f"{verb}: {text}", app=tag, pid=pid, detail=detail if params else "", **kw))
    rows.append(row(session, f"disconnection: session time: 0:00:00.010 user={USER} database={DB} host=172.17.0.1 "
                             f"port={port}", app=tag, pid=pid))
    for n, r in enumerate(rows, 1):
        r[pr.LINE] = str(n)
    return rows


TAG, START, END = "ds_parent_op2a_0123456789ab", "ds_parent_mark_start_0123456789ab", "ds_parent_mark_end_0123456789ab"
EXPECTED = (pr.ExpectedStatement("SELECT 1"), pr.ExpectedStatement("SELECT %s", (3,)))


def logged(rows):
    marks = [row("m", f"statement: SELECT '{START}'", app="ds_parent_log_reader")]
    end = [row("m", f"statement: SELECT '{END}'", app="ds_parent_log_reader")]
    return pr.window(csv_bytes([row("x", "earlier noise"), *marks, *rows, *end]), START, END)


def good():
    return session_rows(TAG, [("SELECT 1", ()), ("SELECT $1", (3,))])


class TestParentReconciliation:
    def test_an_exact_session_reconciles(self):
        out = pr.reconcile_parent_session(logged(good()), tag=TAG, expected=EXPECTED, user=USER, database=DB)
        assert out.ok and out.statements_seen == 2 and out.session_id == "s1"

    def test_a_checkpoint_line_is_allowed(self):
        rows = good()
        rows.insert(2, row("bg", "checkpoint starting: time", user="", db="", backend="checkpointer"))
        assert pr.reconcile_parent_session(logged(rows), tag=TAG, expected=EXPECTED, user=USER, database=DB).ok

    @pytest.mark.parametrize("breakage, fragment", [
        (lambda r: r.pop(3), "statements logged"),
        (lambda r: r.insert(4, row("s1", "execute <unnamed>: SELECT 2", app=TAG)), "statements logged"),
        (lambda r: r.__setitem__(4, row("s1", "execute <unnamed>: SELECT $1", app=TAG, detail="parameters: $1 = '4'")),
         "unapproved parameters"),
        (lambda r: r.__setitem__(3, row("s1", "execute <unnamed>: DELETE FROM x", app=TAG)), "differs"),
        (lambda r: r.extend(session_rows(TAG, [], session="s2", pid="5")), "2 sessions"),
        (lambda r: r.append(row("s9", "statement: SELECT 1", app="other")), "unattributed"),
        (lambda r: r.__setitem__(2, row("s1", f"connection authorized: user=intruder database={DB} "
                                              f"application_name={TAG}", user="intruder")), "identity"),
        (lambda r: r.__setitem__(2, row("s1", f"connection authorized: user={USER} database=prod "
                                              f"application_name={TAG}", db="prod")), "identity"),
        (lambda r: r.insert(4, row("s1", "relation does not exist", app=TAG, severity="ERROR")), "severities"),
        (lambda r: r[4].__setitem__(pr.USER, "postgres"), "another user or database"),
        (lambda r: r[3].__setitem__(pr.DATABASE, "prod"), "another user or database"),
        (lambda r: r.pop(), "didn't disconnect"),
        (lambda r: r.pop(0), "connection isn't in the window"),
        (lambda r: r.__setitem__(4, row("s1", "execute <unnamed>: SELECT $1", app=TAG, pid="9",
                                        detail="parameters: $1 = '3'")), "process id"),
        (lambda r: r.insert(3, row("s1", "duration: 1 ms", app=TAG)), "unexplained"),
    ])
    def test_every_deliberate_breakage_fails_closed(self, breakage, fragment):
        rows = good()
        breakage(rows)
        out = pr.reconcile_parent_session(logged(rows), tag=TAG, expected=EXPECTED, user=USER, database=DB)
        assert not out.ok and any(fragment in p for p in out.problems), out.problems

    def test_a_missing_end_marker_or_bad_log_fails_closed(self):
        with pytest.raises(pr.ParentLogError, match="end marker"):
            pr.window(csv_bytes([row("m", f"statement: SELECT '{START}'"), *good()]), START, END)
        with pytest.raises(pr.ParentLogError):
            pr.window(csv_bytes([row("x", "no markers")]), START, END)
        with pytest.raises(pr.ParentLogError):
            pr.window(b"", "not_a_marker", END)

    def test_only_parent_tags_and_markers_are_accepted(self):
        assert not pr.reconcile_parent_session([], tag="anything", expected=(), user=USER, database=DB).ok
        with pytest.raises(pr.ParentLogError):
            pr.marker_sql("x'; DROP TABLE y; --")


# ------------------------------------------------------------------ fake effects for the whole driver


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0]


def lost_state():
    return {"events": [{"id": 1, "capacity": 3, "reserved": 2}], "rejections": [],
            "reservations": [{"id": 1, "event_id": 1, "holder": "alice", "qty": 2},
                             {"id": 2, "event_id": 1, "holder": "bob", "qty": 2}]}


def serial_state(first="alice", second="bob"):
    return {"events": [{"id": 1, "capacity": 3, "reserved": 2}],
            "reservations": [{"id": 1, "event_id": 1, "holder": first, "qty": 2}],
            "rejections": [{"id": 1, "event_id": 1, "holder": second, "seats_available": 1}]}


INITIAL = {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [], "rejections": []}


class FakeEffects:
    """Every external effect, simulated. ``breakage`` names one deliberate fault to inject."""

    def __init__(self, breakage=None):
        self.breakage, self.state, self.log, self.sessions, self.runs = breakage, copy.deepcopy(INITIAL), [], 0, 0
        self.raw, self.cross = raw_export(), raw_cross()

    def preflight(self):
        return ["cluster_name must be 'ds_live_disposable'"] if self.breakage == "preflight" else []

    def reset(self, setup_sql, seed):
        self.state = copy.deepcopy(INITIAL) if seed else {"events": [], "reservations": [], "rejections": []}

    def snapshot(self):
        return copy.deepcopy(self.state)

    @contextlib.contextmanager
    def parent_connection(self, tag):
        self.sessions += 1
        session, executed = f"p{self.sessions}", []
        effects = self

        class Conn:
            def execute(self, text, params=None):
                executed.append((text, tuple(params or ())))
                return Cursor(effects.answer(text, params))

        yield Conn()
        statements = [(pr.ExpectedStatement(t).server_text(), p) for t, p in executed]
        if (self.breakage == "extra_parent_statement" and tag.startswith("ds_parent_op2a")) or \
                (self.breakage == "extra_op6f_statement" and tag.startswith("ds_parent_op6f")):
            statements.append(("SELECT pg_sleep(0)", ()))
        self.log.extend(session_rows(tag, statements, session=session, pid=str(1000 + self.sessions)))

    def answer(self, text, params):
        for section, query in ce.EXPORT_QUERIES:
            if text == query:
                return self.raw.get(section, [])
        for section, query in ce.CROSS_CHECK_QUERIES:
            if text == query:
                return self.cross.get(section, [])
        if text.startswith("SELECT row_to_json"):
            table = text.split('"')[1]
            rows = copy.deepcopy(self.state[table])
            if self.breakage == "independent_disagrees" and table == "events":
                rows[0]["reserved"] += 1
            return [(json.dumps(r),) for r in rows]
        if text.startswith("SELECT md5"):
            return [("digest",)]
        if " WHERE " in text:
            return [(1,)]
        if text.startswith("SELECT count(*)"):
            return [(len(self.state[text.split('"')[1]]),)]
        return []

    def log_position(self):
        if self.breakage == "log_lost":
            raise pr.ParentLogError("the server isn't writing a CSV log")

    def log_mark(self, token):
        self.log.append(row("m", f"statement: {pr.marker_sql(token)}", app="ds_parent_log_reader"))

    def log_read(self):
        return csv_bytes(self.log)

    def trace(self, final, schedule):
        self.runs += 1
        self.state = copy.deepcopy(final)
        flags = [{"kind": "unmanaged_activity", "detail": "x"}] if self.breakage == "flagged_run" else []
        return LiveTrace(scenario="track_a", requested_schedule=schedule, chosen_schedule=schedule,
                         initial_state=copy.deepcopy(INITIAL), final_state=copy.deepcopy(final),
                         evidence=RunEvidence(supervised=True, run_id=f"r{self.runs}", quiescent=True,
                                              role_dropped=True, child_exit=0, flags=flags))

    def plain_serial(self, scenario, order):
        final = serial_state() if order == ["a", "b"] else serial_state("bob", "alice")
        return {"final_state": final, "flags": [], "results": {}}

    def explore(self, scenario, budget, check):
        lock = scenario.actors[0].kwargs["lock_row"]
        finals = [serial_state(), serial_state("bob", "alice")]
        if not lock or self.breakage == "fixed_violates":
            finals.insert(1, lost_state())
        traces = []
        for i, final in enumerate(finals):
            trace = self.trace(final, ["a", "b"] if i % 2 == 0 else ["a", "b", "a", "b"])
            check(trace)
            traces.append(trace)

        class Exploration:
            pass

        result = Exploration()
        result.traces, result.exhausted, result.complete_schedule_coverage = traces, True, True
        return result

    def run(self, scenario, schedule):
        return self.trace(lost_state(), list(schedule.requested))


def manifest():
    sha = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    return drv.load_manifest(MANIFEST, sha), sha


def attest(digest):
    return {"reviewer": "schema_reviewer", "snapshot_digest": digest, "exported_on": "2026-11-05"}


def full_run(tmp_path, breakage=None):
    m, _ = manifest()
    effects = FakeEffects(breakage)
    evidence = drv.EvidenceDir.create(tmp_path)
    digest = drv.export_phase(effects, m, evidence)
    return drv.run_phase(effects, m, evidence, attest(digest)), evidence, effects


class TestDriverGuards:
    def test_importing_the_driver_does_nothing_and_it_runs_no_commands(self):
        tree = ast.parse(DRIVER.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & {"subprocess", "os", "shlex", "pty", "multiprocessing"}
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not any("psycopg" in ast.dump(n) or "supervisor" in ast.dump(n) for n in top)

    def test_no_private_supervisor_capability_is_used(self):
        private = {n.attr for n in ast.walk(ast.parse(DRIVER.read_text())) if isinstance(n, ast.Attribute)
                   and n.attr.startswith("_") and not n.attr.startswith("__")}
        assert private == set()

    def test_manifest_digest_version_and_variants_are_enforced(self, tmp_path):
        m, sha = manifest()
        with pytest.raises(drv.DriverRefused, match="digest"):
            drv.load_manifest(MANIFEST, "0" * 64)
        for change, fragment in ((lambda d: d.update(version=1), "version 2"),
                                 (lambda d: d["variants"]["buggy"].update(lock_row=True), "variants"),
                                 (lambda d: d["application"].update(name="other"), "reservations"),
                                 (lambda d: d.update(context="heldout_evaluation"), "synthetic")):
            data = json.loads(MANIFEST.read_text())
            change(data)
            path = tmp_path / "m.json"
            path.write_text(json.dumps(data))
            with pytest.raises(drv.DriverRefused, match=fragment):
                drv.load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_approvals_are_bound_to_manifest_and_phase(self, tmp_path):
        _, sha = manifest()
        with pytest.raises(drv.DriverRefused, match="no approval"):
            drv.load_approval(None, manifest_sha256=sha, phase="export")
        path = tmp_path / "a.json"
        path.write_text(json.dumps({"manifest_sha256": sha, "phase": "export", "approver": "owner",
                                    "approved_on": "2026-11-05"}))
        assert drv.load_approval(path, manifest_sha256=sha, phase="export")["approver"] == "owner"
        with pytest.raises(drv.DriverRefused, match="another manifest or phase"):
            drv.load_approval(path, manifest_sha256=sha, phase="run")

    def test_sources_must_match_the_reviewed_digests(self):
        m, _ = manifest()
        drv.verify_sources(m)
        broken = copy.deepcopy(m)
        broken["source_sha256"]["tests/live_app/properties.py"] = "0" * 64
        with pytest.raises(drv.DriverRefused, match="changed since"):
            drv.verify_sources(broken)

    def test_evidence_is_never_overwritten(self, tmp_path):
        evidence = drv.EvidenceDir.create(tmp_path)
        evidence.write("x.json", {})
        with pytest.raises(drv.DriverRefused, match="never overwritten"):
            evidence.write("x.json", {})

    def test_plan_touches_nothing(self, capsys):
        _, sha = manifest()
        assert drv.main(["plan", "--manifest", str(MANIFEST), "--manifest-sha256", sha]) == 0
        assert "OP-6f" in capsys.readouterr().out

    def test_export_and_run_need_approvals(self):
        _, sha = manifest()
        with pytest.raises(drv.DriverRefused, match="no approval"):
            drv.main(["export", "--manifest", str(MANIFEST), "--manifest-sha256", sha],
                     effects_factory=lambda m: FakeEffects())


class TestDriverWithMockedEffects:
    def test_driver_logic_only_a_clean_mocked_run_passes_every_gate(self, tmp_path):
        """Driver logic on mocked effects. Not evidence that any database run happened."""
        from deployspecter.research.track_a import acceptance as acc
        bundle, _, effects = full_run(tmp_path)
        verdict, judgements = acc.judge(bundle)
        assert verdict == "PASS", [j for j in judgements if j.status != "pass"]
        assert bundle["replay"]["result"] == "reproduced" and bundle["replay"]["attempts"] == 3
        assert all(r["ok"] for r in bundle["parent_reconciliation"])
        assert {r["op"] for r in bundle["parent_reconciliation"]} == {"op6i", "op6f", "op2d"}

    @pytest.mark.parametrize("breakage, fragment", [
        ("preflight", "preflight"),
        ("extra_parent_statement", "didn't reconcile"),
        ("log_lost", "CSV log"),
        ("independent_disagrees", "disagrees"),
    ])
    def test_deliberate_breakage_stops_the_driver(self, tmp_path, breakage, fragment):
        with pytest.raises((drv.DriverRefused, pr.ParentLogError), match=fragment):
            full_run(tmp_path, breakage)

    def test_a_run_phase_session_that_doesnt_reconcile_stops_at_once(self, tmp_path):
        effects = FakeEffects("extra_op6f_statement")
        m, _ = manifest()
        evidence = drv.EvidenceDir.create(tmp_path)
        digest = drv.export_phase(effects, m, evidence)
        with pytest.raises(drv.DriverRefused, match="parent session op6f didn't reconcile"):
            drv.run_phase(effects, m, evidence, attest(digest))
        assert effects.runs == 1  # stopped inside the first run's check, before any further run

    def test_the_batch_ceiling_stops_before_any_run(self, tmp_path):
        m, _ = manifest()
        m = copy.deepcopy(m)
        m["safety_ceilings"]["batch_wall_clock_s"] = 0
        effects = FakeEffects()
        evidence = drv.EvidenceDir.create(tmp_path)
        digest = drv.export_phase(effects, m, evidence)
        with pytest.raises(drv.DriverRefused, match="safety ceiling"):
            drv.run_phase(effects, m, evidence, attest(digest))
        assert effects.runs == 0

    def test_a_fixed_version_violation_fails_the_negative_control(self, tmp_path):
        from deployspecter.research.track_a import acceptance as acc
        bundle, _, _ = full_run(tmp_path, "fixed_violates")
        status = {j.gate: j.status for j in acc.judge(bundle)[1]}
        assert status["G6_fixed_negative_control"] == "fail"

    def test_a_flagged_run_is_an_unknown_effect_and_fails(self, tmp_path):
        from deployspecter.research.track_a import acceptance as acc
        bundle, _, _ = full_run(tmp_path, "flagged_run")
        assert bundle["unknown_effects"] and acc.judge(bundle)[0] == "FAIL"

    def test_a_wrong_attestation_stops_before_any_run(self, tmp_path):
        m, _ = manifest()
        effects = FakeEffects()
        evidence = drv.EvidenceDir.create(tmp_path)
        drv.export_phase(effects, m, evidence)
        with pytest.raises(ce.ExportError, match="attestation"):
            drv.run_phase(effects, m, evidence, attest("0" * 64))
        assert effects.runs == 0

    def test_a_run_directory_is_used_once(self, tmp_path):
        bundle, evidence, effects = full_run(tmp_path)
        evidence.write("bundle.json", bundle)
        with pytest.raises(drv.DriverRefused, match="already holds a run"):
            drv.run_phase(effects, manifest()[0], evidence, attest(evidence.read("export_1.json")["digest"]))


class TestManifestV2:
    def test_v2_matches_the_code_it_names(self):
        m, _ = manifest()
        assert m["status"] == "WAITING_FOR_EXECUTION_APPROVAL" and m["supersedes"]["version"] == 1
        drv.verify_sources(m)
        assert m["operations"]["OP-2"]["export_query_set_sha256"] == ce.EXPORT_QUERY_SET_SHA256
        assert m["cluster"]["image"].startswith("postgres:16@sha256:1beaa308")
        assert set(m["operations"]) == {"OP-1", "OP-2", "OP-2b", "OP-2d", "OP-3", "OP-3a", "OP-4", "OP-5", "OP-6i",
                                        "OP-6f", "OP-7"}


class TestDetectionClaims:
    def test_a_passing_mocked_bundle_is_only_automated_characterization(self, tmp_path):
        from deployspecter.research.track_a import acceptance as acc
        bundle, _, _ = full_run(tmp_path)
        assert acc.detection_status(bundle) == "automated_characterization_only"
        manual = {"oracle_author_review": {"reviewer": "oracle_author", "confirmed": True},
                  "uninstrumented_reproduction": {"attempts": 3, "reproduced": True}}
        assert acc.detection_status({**bundle, "manual_confirmation": manual}) == \
            "detection_confirmed_under_amendment_002"
        manual["uninstrumented_reproduction"]["attempts"] = 126
        assert acc.detection_status({**bundle, "manual_confirmation": manual}) == "automated_characterization_only"
        for review, plain in (({"reviewer": "oracle_author", "confirmed": False}, {"attempts": 3, "reproduced": True}),
                              ({"confirmed": True}, {"attempts": 3, "reproduced": True}),
                              ({"reviewer": "oracle_author", "confirmed": True}, {"attempts": 3, "reproduced": False})):
            claim = {"oracle_author_review": review, "uninstrumented_reproduction": plain}
            assert acc.detection_status({**bundle, "manual_confirmation": claim}) == "automated_characterization_only"

    def test_contended_wakeups_record_the_contenders(self):
        from deployspecter.live.trace import Decision
        from deployspecter.research.track_a import replay_policy as rp
        t = LiveTrace(scenario="s", requested_schedule=[], decisions=[
            Decision(index=0, enabled=[], blocked={"a": ["c"], "b": ["c"]}, chosen="c", source="schedule"),
            Decision(index=1, enabled=["b"], blocked={"a": ["b"]}, chosen="b", source="schedule")])
        assert rp.server_choice_signature(t)[3] == ((0, ("b",), ("a", "b")),)
