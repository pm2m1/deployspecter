"""Gate A1 characterization, offline. Fake connections and a fake server log only; no database, container or command."""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import json
import math
import socket
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in ("tests", "research_tools", "tests/research_tracks"):
    sys.path.insert(0, str(ROOT / extra))

import gate_a1_characterize as a1  # noqa: E402
from test_track_a_milestone2 import raw_cross, raw_export  # noqa: E402
from test_track_a_milestone3 import csv_bytes, row, session_rows  # noqa: E402

from deployspecter.research.track_a import catalog_export as ce  # noqa: E402
from deployspecter.research.track_a import characterization as ch  # noqa: E402
from deployspecter.research.track_a import parent_reconciliation as pr  # noqa: E402

PACKAGE = ROOT / "docs/research/live_stepping/track_a/gate_a1_manifest.json"
NEVER_FLUSH_READ_BOUND = 100  # a 0.2 s wait polls about 4 times at 0.05 s; 100 reads is about 5 s
DRIVER = ROOT / "research_tools/gate_a1_characterize.py"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def jsonb_like(value):
    """How psycopg's default loader is expected to return to_jsonb output. Characterization tests this for real."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, float) and value == 0 and math.copysign(1, value) < 0:
        return 0
    return value


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0]


class FakeEffects:
    def __init__(self, breakage=None):
        self.breakage, self.log, self.n = breakage, [], 0
        self.tables = {}
        self.bindings = {}

    @contextlib.contextmanager
    def parent_connection(self, tag, database):
        self.n += 1
        executed, effects = [], self

        class Conn:
            def execute(self, text, params=None):
                executed.append((text, tuple(params or ())))
                return Cursor(effects.answer(text, tuple(params or ())))

        yield Conn()
        logged = [(pr.ExpectedStatement(t).server_text(), p) for t, p in executed]
        if self.breakage == "extra_in_reset" and tag.startswith("ds_parent_op0r"):
            logged.append(("SELECT 1", ()))
        self.log.extend(session_rows(tag, logged, session=f"p{self.n}", pid=str(2000 + self.n)))
        if self.breakage == "intruder" and tag.startswith("ds_parent_op7b"):
            self.log.append(row("zz", "statement: DELETE FROM ds_run.events", app="psql"))

    def answer(self, text, params):
        if text == ch.PREFLIGHT[0]:
            settings = dict(ch.EXPECTED_SETTINGS)
            if self.breakage == "preflight":
                settings["log_statement"] = "none"
            return sorted(settings.items())
        if text == ch.PREFLIGHT[1]:
            return [(0,)]
        if text == ch.PREFLIGHT[2]:
            return [("deployspecter_test", "deployspecter", True, 160015)]
        if text.startswith("DROP SCHEMA IF EXISTS ds_run"):
            self.tables = {}
        if text.lstrip().startswith("CREATE TABLE events") or "CREATE TABLE events" in text:
            self.tables = {"events": [], "reservations": [], "rejections": []}
        if text.startswith('INSERT INTO "events"'):
            self.tables["events"].append({"capacity": params[0], "id": params[1], "reserved": params[2]})
        for section, query in ce.EXPORT_QUERIES:
            if text == query:
                return raw_export().get(section, [])
        for section, query in ce.CROSS_CHECK_QUERIES:
            if text == query:
                return raw_cross().get(section, [])
        if text.startswith("INSERT INTO ds_char.bindings"):
            self.bindings[params[0]] = params[1]
        if text.startswith("SELECT id, i4"):
            out = []
            for i, (column, _value) in enumerate(ch.CASES, 1):
                cells = [None] * 6
                stored = self.bindings[i]
                if self.breakage == "binding" and column == "b":
                    stored = int(stored)
                cells[list(ch.COLUMNS).index(column)] = stored
                out.append((i, *cells))
            return out
        if text.startswith("SELECT to_jsonb(r)"):
            docs = []
            for i, (column, _value) in enumerate(ch.CASES, 1):
                value = jsonb_like(self.bindings[i])
                if self.breakage == "jsonb_int" and column == "i4":
                    value = float(value)
                docs.append(({"id": i, column: value},))
            return docs
        if text.startswith("SELECT row_to_json"):
            return [(json.dumps(r),) for r in self.tables[text.split('"')[1]]]
        if text.startswith("SELECT md5"):
            return [("d",)]
        if " WHERE " in text and text.startswith("SELECT count(*)"):
            return [(1,)]
        if text.startswith("SELECT count(*) FROM ds_run"):
            return [(len(self.tables[text.split('"')[1]]),)]
        return []

    def log_position(self, key="op"):
        if self.breakage == "intruder_between" and any("op7b" in r[pr.APPLICATION] for r in self.log):
            self.log.append(row("zz", "statement: DELETE FROM ds_run.events", app="psql"))
            self.breakage = None
        self.log.append(row("lr", "execute <unnamed>: SELECT size FROM pg_stat_file($1, true)", app=ch.LOG_READER_APP,
                            detail="parameters: $1 = 'log/ds.csv'"))
        if self.breakage == "log_reader_writes":
            self.log.append(row("lr", "statement: DELETE FROM ds_run.events", app=ch.LOG_READER_APP))

    def log_mark(self, token):
        self.log.append(row("lr", f"statement: {pr.marker_sql(token)}", app=ch.LOG_READER_APP))

    def log_read(self, key="op"):
        self.reads = getattr(self, "reads", 0) + 1
        if self.breakage == "slow_flush" and self.reads % 3:
            return csv_bytes(self.log[:-1])  # the end marker isn't flushed yet
        if self.breakage == "never_flushes":
            # The test's own bound, independent of the driver's clock: an unbounded wait fails here, not hangs.
            assert self.reads <= NEVER_FLUSH_READ_BOUND, "the driver kept waiting for an end marker that never comes"
            return csv_bytes(self.log[:-1])
        return csv_bytes(self.log)

    def wait_gone(self, tag):
        return self.breakage != "backend_lingers"


def package():
    sha = hashlib.sha256(PACKAGE.read_bytes()).hexdigest()
    return a1.load_package(PACKAGE, sha), sha


def run(tmp_path, breakage=None, change=None):
    p, _ = package()
    if change:
        p = copy.deepcopy(p)
        change(p)
    return a1.characterize(FakeEffects(breakage), p, a1.a2.EvidenceDir.create(tmp_path))


class TestPure:
    def test_preflight_judge(self):
        good = [sorted(ch.EXPECTED_SETTINGS.items()), [(0,)], [("deployspecter_test", "deployspecter", True, 160015)]]
        assert ch.judge_preflight(good, database="deployspecter_test", user="deployspecter") == []
        bad = copy.deepcopy(good)
        bad[2] = [("deployspecter_test", "deployspecter", True, 150004)]
        assert ch.judge_preflight(bad, database="deployspecter_test", user="deployspecter") == ["not PostgreSQL 16"]

    def test_bindings_judge_separates_relied_on_from_recorded(self):
        plain = []
        docs = []
        for i, (column, value) in enumerate(ch.CASES, 1):
            cells = [None] * 6
            cells[list(ch.COLUMNS).index(column)] = value
            plain.append((i, *cells))
            docs.append({"id": i, column: jsonb_like(value)})
        result = ch.judge_bindings(plain, docs)
        assert result.assumptions_hold and len(result.jsonb_findings) == 4  # -0.0, NaN, +inf, -inf
        docs[0]["i4"] = float(docs[0]["i4"])
        assert ch.judge_bindings(plain, docs).d1_refuted

    def test_reset_statements_are_fixed_and_named_safely(self):
        stmts = ch.reset_statements("CREATE TABLE x (id int)", [])
        assert [t for t, _ in stmts][:4] == ["DROP SCHEMA IF EXISTS ds_run CASCADE", "CREATE SCHEMA ds_run",
                                             "SET search_path = ds_run", "BEGIN"]

        class Row:
            table, values = "events; DROP", []

        with pytest.raises(ValueError):
            ch.reset_statements("x", [Row()])

    def test_batch_audit(self):
        rows = [*session_rows("ds_parent_op0p_0123456789ab", [("SELECT 1", ())]),
                row("lr", "execute <unnamed>: SELECT pg_read_binary_file($1, $2, $3, true)", app=ch.LOG_READER_APP),
                row("bg", "checkpoint complete: wrote 3 buffers", user="", db="", backend="checkpointer")]
        ok = ch.audit_batch(rows, reconciled_tags={"ds_parent_op0p_0123456789ab"}, user="deployspecter",
                            databases={"deployspecter_test"})
        assert ok.ok and set(ok.sessions.values()) == {"reconciled:ds_parent_op0p_0123456789ab", "log_reader",
                                                       "background"}
        bad = ch.audit_batch([*rows, row("x", "statement: SELECT 1", app="psql")], reconciled_tags={
            "ds_parent_op0p_0123456789ab"}, user="deployspecter", databases={"deployspecter_test"})
        assert not bad.ok and "unattributed" in bad.problems[0]


class TestDriver:
    def test_guards(self):
        tree = ast.parse(DRIVER.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & {"subprocess", "os", "shlex", "pty", "multiprocessing"}
        assert not any("supervisor" in n or "database" in n for n in names)
        assert {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr.startswith("_")
                and not n.attr.startswith("__")} == set()

    def test_driver_logic_only_a_clean_mocked_batch_is_characterized(self, tmp_path):
        """Driver logic on mocked effects. Not evidence that any database was characterized."""
        summary = run(tmp_path)
        assert summary["outcome"] == "CHARACTERIZED", summary
        assert summary["findings"]["every_session_attributed"]

    def test_a_refuted_driver_assumption_is_a_finding_not_a_pass(self, tmp_path):
        summary = run(tmp_path, "jsonb_int")
        assert summary["outcome"] == "CHARACTERIZED_WITH_FINDINGS" and not summary["findings"]["bindings_hold"]
        assert run(tmp_path / "b", "binding")["findings"]["bindings_hold"] is False

    def test_an_intruder_inside_an_operation_window_stops_that_operation(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="op7b didn't reconcile.*unattributed"):
            run(tmp_path, "intruder")

    def test_an_intruder_between_operations_is_caught_by_the_batch_audit(self, tmp_path):
        summary = run(tmp_path, "intruder_between")
        assert summary["outcome"] == "CHARACTERIZED_WITH_FINDINGS"
        assert summary["findings"]["every_session_attributed"] is False

    @pytest.mark.parametrize("breakage, fragment", [("preflight", "log_statement"),
                                                    ("extra_in_reset", "op0r didn't reconcile")])
    def test_deliberate_breakage_stops(self, tmp_path, breakage, fragment):
        with pytest.raises(a1.DriverRefused, match=fragment):
            run(tmp_path, breakage)

    def test_log_reader_writing_is_a_finding(self, tmp_path):
        assert run(tmp_path, "log_reader_writes")["findings"]["every_session_attributed"] is False

    def test_batch_ceiling(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="safety ceiling"):
            run(tmp_path, change=lambda p: p["safety_ceilings"].update(batch_wall_clock_s=0))

    def test_approval_and_manifest_are_required(self, tmp_path):
        _, sha = package()
        with pytest.raises(a1.DriverRefused, match="digest"):
            a1.load_package(PACKAGE, "0" * 64)
        with pytest.raises(a1.DriverRefused, match="no approval"):
            a1.main(["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha],
                    effects_factory=lambda p: FakeEffects())


class TestPackage:
    def test_package_matches_the_code(self):
        p, _ = package()
        assert p["status"] == "WAITING_FOR_SEPARATE_EXECUTION_APPROVAL" and p["scope"] == "characterization_only"
        a1.a2.verify_sources(p)
        assert p["statement_counts"]["op2a_op2b_export"] == len(ce.EXPORT_QUERIES)
        assert p["cluster"]["image"].startswith("postgres:16@sha256:1beaa308")


def test_statement_set_hashes_match_the_code():
    from deployspecter.research.track_a import evidence_binding as eb
    from deployspecter.research.track_a import pipeline as p
    from live_app import reservations as app
    m, _ = package()
    chain = p.run_offline_chain()
    expected = p.expected_initial_state(chain)
    types = {tuple(k.split(".")): v for k, v in m["state_checks"]["column_types"].items()}
    sets = {"op0p_preflight": [(t, ()) for t in ch.PREFLIGHT],
            "op0r_reset_empty": ch.reset_statements(app.SETUP_SQL, []),
            "op0r_reset_seeded": ch.reset_statements(app.SETUP_SQL, list(chain.seed)),
            "op2a_op2b_export": [(t, ()) for _, t in ce.EXPORT_QUERIES],
            "op2c_cross_check": [(t, ()) for _, t in ce.CROSS_CHECK_QUERIES],
            "op6i_typed_check": list(eb.independent_statements(expected, types)),
            "op6f_typed_read": [(t, ()) for t in eb.final_read_statements(sorted(expected))],
            "op7b_bindings": ch.binding_statements(), "op9c_cleanup": ch.cleanup_statements()}
    digest = lambda x: hashlib.sha256(json.dumps(x, default=repr).encode()).hexdigest()  # noqa: E731
    assert m["statement_sets_sha256"] == {k: digest(v) for k, v in sets.items()}


class TestFinalReview:
    def test_float_parameters_are_formatted_as_psycopg_sends_them(self):
        assert [pr.literal(v) for v in (math.nan, math.inf, -math.inf, -0.0, 0.1, True, None, "a'b")] == [
            "'NaN'", "'Infinity'", "'-Infinity'", "'-0.0'", "'0.1'", "'t'", "NULL", "'a''b'"]

    def test_a_late_log_flush_is_waited_for_boundedly(self, tmp_path):
        assert run(tmp_path, "slow_flush")["outcome"] == "CHARACTERIZED"
        effects = FakeEffects("never_flushes")
        p, _ = package()
        p = copy.deepcopy(p)
        p["safety_ceilings"]["log_wait_s"] = 0.2
        with pytest.raises(pr.EndMarkerMissing):
            a1.characterize(effects, p, a1.a2.EvidenceDir.create(tmp_path / "x"))
        assert 1 < effects.reads < NEVER_FLUSH_READ_BOUND

    def test_a_backend_that_doesnt_end_stops_the_batch(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="didn't end"):
            run(tmp_path, "backend_lingers")

    def test_operator_commands_come_from_the_manifest_and_bind_locally(self):
        p, _ = package()
        start, create, stop = ch.operator_commands(p)
        assert p["cluster"]["image"] in start and "-p 127.0.0.1:55433:5432" in start and "0.0.0.0" not in start
        assert '"$DS_PG_PASSWORD"' in start and "--memory 1g" in start and "--pids-limit 256" in start
        assert all(f"-c {k}={v}" in start for k, v in p["cluster"]["server_settings"].items())
        assert create.endswith("-c 'CREATE DATABASE deployspecter_test'")
        assert stop == "docker stop deployspecter-live-pg"

    @pytest.mark.parametrize("raised, kind", [(KeyboardInterrupt, "interrupted_stop"),
                                              (ConnectionError, "unknown_effect_stop")])
    def test_interrupted_or_failed_starts_leave_a_record(self, tmp_path, monkeypatch, raised, kind):
        p, sha = package()
        monkeypatch.setitem(p, "artifacts_root", str(tmp_path.relative_to(ROOT)) if tmp_path.is_relative_to(ROOT)
                            else str(tmp_path))
        monkeypatch.setattr(a1, "load_package", lambda *_a: p)
        approval = tmp_path / "approval.json"
        approval.write_text(json.dumps({"manifest_sha256": sha, "phase": "gate_a1", "approver": "owner",
                                        "approved_on": "2026-11-05"}))

        def factory(_p):
            raise raised("stop")

        with pytest.raises(raised):
            a1.main(["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha, "--approval",
                     str(approval)], effects_factory=factory)
        failures = list(tmp_path.glob("*/failure.json"))
        assert len(failures) == 1 and json.loads(failures[0].read_text())["classification"] == kind

    def test_real_effects_refuse_a_model_key_or_a_foreign_target(self, monkeypatch):
        p, _ = package()

        class Info:
            host, port, dbname = "10.0.0.5", 5432, "deployspecter_test"

        class Conn:
            info = Info()

            def close(self):
                pass

        calls = []
        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(k) or Conn())
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@10.0.0.5:5432/deployspecter")
        monkeypatch.setenv("MODEL_API_KEY", "x")
        with pytest.raises(a1.DriverRefused, match="MODEL_API_KEY"):
            a1.RealEffects(p)
        monkeypatch.delenv("MODEL_API_KEY")
        with pytest.raises(a1.DriverRefused, match="fixed local target"):
            a1.RealEffects(p)
        assert calls == []  # refused before any connection was opened

    @pytest.mark.parametrize("dsn, fragment", [
        ("postgresql://deployspecter:s3cret@10.0.0.5:55433/deployspecter", "differs on ['host']"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55432/deployspecter", "differs on ['port']"),
        ("postgresql://other:s3cret@127.0.0.1:55433/deployspecter", "differs on ['user']"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter?hostaddr=10.0.0.5", "keys"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter?service=x", "keys"),
        ("postgresql://deployspecter@127.0.0.1:55433/deployspecter", "no password"),
        ("postgresql://deployspecter:s3cret%zz@127.0.0.1:55433/deployspecter", "couldn't be parsed"),
    ])
    def test_the_target_is_fixed_by_the_manifest_before_connecting(self, monkeypatch, dsn, fragment):
        p, _ = package()
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.setenv("DATABASE_URL", dsn)
        with pytest.raises(a1.DriverRefused) as raised:
            a1.RealEffects(p)  # the autouse fixture fails the test if any connection is attempted
        assert fragment in str(raised.value) and "s3cret" not in str(raised.value)

    def test_the_fixed_target_overrides_libpq_redirection(self, monkeypatch):
        p, _ = package()

        class Info:
            host, port, dbname, user = "127.0.0.1", 55433, "deployspecter_test", "deployspecter"

        class Conn:
            info = Info()

        calls = []
        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append((a, k)) or Conn())
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.setenv("PGHOSTADDR", "10.0.0.5")
        monkeypatch.setenv("DATABASE_URL", "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter")
        effects = a1.RealEffects(p)
        (args, kwargs), = calls
        assert args == () and {k: kwargs[k] for k in ("host", "hostaddr", "port", "dbname", "user")} == {
            "host": "127.0.0.1", "hostaddr": "127.0.0.1", "port": "55433", "dbname": "deployspecter_test",
            "user": "deployspecter"}
        assert "s3cret" not in repr(effects.settings)

    @pytest.mark.parametrize("field, value", [("host", "10.0.0.5"), ("port", 55432), ("dbname", "deployspecter"),
                                              ("user", "postgres")])
    def test_a_connection_that_lands_elsewhere_is_closed_and_refused(self, monkeypatch, field, value):
        p, _ = package()
        closed = []

        class Info:
            host, port, dbname, user = "127.0.0.1", 55433, "deployspecter_test", "deployspecter"

        setattr(Info, field, value)

        class Conn:
            info = Info()

            def close(self):
                closed.append(True)

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Conn())
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter")
        with pytest.raises(a1.DriverRefused, match="isn't to the manifest's fixed local target"):
            a1.RealEffects(p)
        assert closed == [True]
