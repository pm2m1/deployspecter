"""Gate A1 characterization, offline. Fake connections and a fake server log only; no database, container or command."""

from __future__ import annotations

import ast
import contextlib
import copy
import functools
import hashlib
import json
import math
import socket
import sys
from collections import Counter
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


V3_LOG = ROOT / "tests/research_tracks/fixtures/gate_a1_v3_observed.csv"


def observed_binding_details() -> list[str]:
    """The parameter details PostgreSQL 16.15 actually logged for op7b's 32 statements in the third real run, in order.
    The fake logs these, rather than the reconciler's own literal(), so the full simulated batch can't agree with itself
    by construction."""
    import csv
    import io
    raw = V3_LOG.read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode()), strict=True))
    tag = next(r[pr.APPLICATION] for r in rows if r[pr.APPLICATION].startswith("ds_parent_op7b"))
    session = next(r[pr.SESSION] for r in rows if r[pr.APPLICATION] == tag)
    return [r[pr.DETAIL] for r in rows if r[pr.SESSION] == session and pr.STATEMENT.match(r[pr.MESSAGE])]


class PsycopgPreparation:
    """psycopg 3's automatic preparation, as the second real run logged it (evidence 20261104T132842Z-1c432509): a
    connection's first ``threshold`` executions of a query are logged unprepared ("statement:" without parameters,
    "execute <unnamed>:" with them); from the next execution on, as "execute _pg3_<n>:", named in the order queries
    were first prepared. None disables preparation, which is Gate A1's policy. Keyed by query text only, so it doesn't
    model psycopg's per-parameter-type keys (see the real-log tests)."""

    def __init__(self, threshold):
        self.threshold, self.counts, self.names = threshold, Counter(), {}

    def verb(self, text, params):
        self.counts[text] += 1
        if self.threshold is not None and self.counts[text] > self.threshold:
            return f"execute {self.names.setdefault(text, f'_pg3_{len(self.names)}')}"
        return "execute <unnamed>" if params else "statement"


class FakeEffects:
    def __init__(self, breakage=None):
        self.breakage, self.log, self.n = breakage, [], 0
        self.reader_session, self.reader_line = "lr", 3  # its connection rows precede the batch
        self.reader_prep = PsycopgPreparation(5 if breakage == "default_prepare_reader" else a1.PREPARE_THRESHOLD)
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
        if self.breakage == "cleanup_incomplete" and tag.startswith("ds_parent_op9c"):
            logged.pop()
        prep = PsycopgPreparation(5 if self.breakage == "default_prepare_parent" else a1.PREPARE_THRESHOLD)
        details = None
        if tag.startswith("ds_parent_op7b") and len(logged) == len(ch.binding_statements()):
            details = observed_binding_details()
            if self.breakage == "negative_zero_as_python":
                details = [d.replace("$2 = '-0'", "$2 = '-0.0'") for d in details]
        rows = session_rows(tag, logged, session=f"p{self.n}", pid=str(2000 + self.n),
                            verbs=[prep.verb(t, p) for t, p in logged], details=details)
        if self.breakage == "no_auth_row":
            rows = [r for r in rows if not r[pr.MESSAGE].startswith("connection authenticated:")]
            for n, r in enumerate(rows, 1):
                r[pr.LINE] = str(n)
        self.log.extend(rows)
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

    def reader(self, message, detail=""):
        """One log-reader session, as the real one logs: one session and process, consecutive session lines."""
        verb, text = message.split(": ", 1)
        if verb in ("statement", "execute <unnamed>"):
            message = f"{self.reader_prep.verb(text, detail)}: {text}"
        self.reader_line += 1
        r = row(self.reader_session, message, app=ch.LOG_READER_APP, pid="91", detail=detail)
        r[pr.LINE] = str(self.reader_line)
        self.log.append(r)

    def log_position(self, key="op"):
        if self.breakage == "intruder_between" and any("op7b" in r[pr.APPLICATION] for r in self.log):
            self.log.append(row("zz", "statement: DELETE FROM ds_run.events", app="psql"))
            self.breakage = None
        if self.breakage == "reader_switches" and self.n:
            self.reader_session = "lr2"
        self.reader("statement: SELECT pg_current_logfile('csvlog')")
        self.reader("execute <unnamed>: SELECT size FROM pg_stat_file($1, true)", "parameters: $1 = 'log/ds.csv'")
        if self.breakage == "log_reader_writes":
            self.reader("statement: DELETE FROM ds_run.events")
        if self.breakage == "reader_extra_between" and self.n == 3:
            self.reader("statement: SELECT pg_ls_dir('log')")

    def log_mark(self, token):
        if self.breakage == "second_reader" and token.startswith("ds_parent_mark_batch_end"):
            self.log.append(row("lr9", "statement: SELECT pg_current_logfile('csvlog')", app=ch.LOG_READER_APP))
        self.reader(f"statement: {pr.marker_sql(token)}")

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
        polls = 2 if self.breakage == "backend_lingers" else 1
        if self.breakage == "many_polls":
            polls = a1.reader_poll_limit(package()[0]) + 1
        for _ in range(polls):
            self.reader(f"execute <unnamed>: {pr.READER_POLL}", f"parameters: $1 = {pr.literal(tag)}")
        if self.breakage == "reader_reads_in_window" and tag.startswith("ds_parent_op2a"):
            self.reader("execute <unnamed>: SELECT pg_read_binary_file($1, $2, $3, true)",
                        "parameters: $1 = 'log/ds.csv', $2 = '0', $3 = '10'")
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
        reader = pr.ReaderSession("lr", "91", "deployspecter", "deployspecter_test", start_line=4, end_line=6)

        def reader_row(line, message):
            r = row("lr", message, app=ch.LOG_READER_APP, pid="91")
            r[pr.LINE] = str(line)
            return r

        rows = [*session_rows("ds_parent_op0p_0123456789ab", [("SELECT 1", ())]),
                reader_row(5, "execute <unnamed>: SELECT pg_read_binary_file($1, $2, $3, true)"),
                row("bg", "checkpoint complete: wrote 3 buffers", user="", db="", backend="checkpointer")]
        audit = functools.partial(ch.audit_batch, reconciled_tags={"ds_parent_op0p_0123456789ab"},
                                  user="deployspecter", databases={"deployspecter_test"}, reader=reader)
        ok = audit(rows)
        assert ok.ok and set(ok.sessions.values()) == {"reconciled:ds_parent_op0p_0123456789ab", "log_reader",
                                                       "background"}
        bad = audit([*rows, row("x", "statement: SELECT 1", app="psql")])
        assert not bad.ok and "unattributed" in bad.problems[0]
        second = audit([*rows, row("lr9", "statement: SELECT pg_current_logfile('csvlog')", app=ch.LOG_READER_APP)])
        assert not second.ok and "second session" in second.problems[0]
        gap = audit([r for r in rows if r[pr.SESSION] != "lr"] + [reader_row(7, "statement: SELECT 'x'")])
        assert not gap.ok and "session lines" in gap.problems[0]
        other_pid = [*rows[:-2], reader_row(5, "statement: SELECT pg_current_logfile('csvlog')"), rows[-1]]
        other_pid[-2][pr.PID] = "92"
        assert not audit(other_pid).ok


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
        # -0.0 logs as '-0' (observed in the third real run): float8 is sent in binary and logged with float8out.
        assert [pr.literal(v) for v in (math.nan, math.inf, -math.inf, -0.0, 0.1, True, None, "a'b")] == [
            "'NaN'", "'Infinity'", "'-Infinity'", "'-0'", "'0.1'", "'t'", "NULL", "'a''b'"]

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
        start, ready, create, stop = ch.operator_commands(p)
        assert ready == ("docker exec deployspecter-live-pg pg_isready -h 127.0.0.1 -p 5432 -U deployspecter "
                         "-d deployspecter")
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
            info, prepare_threshold = Info(), None

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
        assert "prepare_threshold" in kwargs and kwargs["prepare_threshold"] is None
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
            info, prepare_threshold = Info(), None

            def close(self):
                closed.append(True)

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Conn())
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter")
        with pytest.raises(a1.DriverRefused, match="isn't to the manifest's fixed local target"):
            a1.RealEffects(p)
        assert closed == [True]


class TestRecoveryAfterTheFirstRun:
    """The first real run stopped at op0p (evidence/gate_a1/20261104T113256Z-d0b21a60). These cover its two causes
    and the approval-copy discrepancy, on the fake log, which now writes what the real server wrote."""

    def test_a_clean_batch_with_real_connection_records_and_reader_polls_is_characterized(self, tmp_path):
        effects = FakeEffects()
        p, _ = package()
        summary = a1.characterize(effects, p, a1.a2.EvidenceDir.create(tmp_path))
        assert summary["outcome"] == "CHARACTERIZED"
        assert any(r[pr.MESSAGE].startswith("connection authenticated:") for r in effects.log)
        assert sum(r[pr.MESSAGE] == f"execute <unnamed>: {pr.READER_POLL}" for r in effects.log) == effects.n

    def test_the_reader_may_not_change_session_during_the_batch(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="different log-reader session"):
            run(tmp_path, "reader_switches")

    def test_the_reader_may_only_poll_inside_a_window(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="op2a didn't reconcile.*other than this session's poll"):
            run(tmp_path, "reader_reads_in_window")

    def test_a_second_reader_session_is_a_batch_finding(self, tmp_path):
        summary = run(tmp_path, "second_reader")
        assert summary["outcome"] == "CHARACTERIZED_WITH_FINDINGS"
        assert summary["findings"]["every_session_attributed"] is False

    def test_the_driver_applies_the_poll_bound(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="op0p didn't reconcile.*log-reader polls"):
            run(tmp_path, "many_polls")

    def test_the_driver_requires_the_manifests_authentication_record(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="op0p didn't reconcile.*authentication isn't in the window"):
            run(tmp_path, "no_auth_row")

    def test_the_poll_bound_follows_the_manifest(self):
        p, _ = package()
        assert a1.reader_poll_limit(p) == int(p["safety_ceilings"]["backend_exit_wait_s"] / a1.POLL_INTERVAL_S) + 2

    def _main(self, tmp_path, monkeypatch, approval_bytes, tamper=False):
        p, sha = package()
        monkeypatch.setitem(p, "artifacts_root", str(tmp_path))
        monkeypatch.setattr(a1, "load_package", lambda *_a: p)
        approval = tmp_path / "signed" / "approval.json"
        approval.parent.mkdir()
        approval.write_bytes(approval_bytes.replace(b"SHA", sha.encode()))
        (tmp_path / "signed" / "approval.json.sig").write_bytes(b"-----BEGIN SSH SIGNATURE-----\n")
        if tamper:
            original = a1.a2.load_approval

            def load_then_change(*a, **k):
                out = original(*a, **k)
                approval.write_bytes(approval.read_bytes().replace(b"owner", b"other"))
                return out
            monkeypatch.setattr(a1.a2, "load_approval", load_then_change)
        argv = ["characterize", "--manifest", str(PACKAGE), "--manifest-sha256", sha, "--approval", str(approval)]
        return a1.main(argv, effects_factory=lambda _p: FakeEffects()), approval

    def test_the_approval_is_kept_byte_for_byte_with_its_provenance(self, tmp_path, monkeypatch):
        signed = (b'{\n  "manifest_sha256": "SHA",\n  "phase": "gate_a1",\n  "approver": "owner",\n'
                  b'  "approved_on": "2026-11-05"\n}\n')
        code, approval = self._main(tmp_path, monkeypatch, signed)
        (run_dir,) = [d for d in tmp_path.iterdir() if d.name != "signed"]
        assert code == 0 and (run_dir / "approval.json").read_bytes() == approval.read_bytes()
        provenance = json.loads((run_dir / "approval_provenance.json").read_text())
        assert provenance["sha256"] == hashlib.sha256(approval.read_bytes()).hexdigest()
        assert provenance["copied_byte_for_byte"] is True and provenance["signature_checked_by_driver"] is False
        assert provenance["signature_file"]["sha256"] == hashlib.sha256(
            (approval.parent / "approval.json.sig").read_bytes()).hexdigest()

    def test_an_approval_that_changes_while_being_read_is_refused(self, tmp_path, monkeypatch):
        signed = b'{"manifest_sha256": "SHA", "phase": "gate_a1", "approver": "owner", "approved_on": "2026-11-05"}'
        with pytest.raises(a1.DriverRefused, match="changed while it was being read"):
            self._main(tmp_path, monkeypatch, signed, tamper=True)
        assert [d.name for d in tmp_path.iterdir()] == ["signed"]  # refused before any evidence or connection


class TestPreparationPolicy:
    """The second real run (evidence/gate_a1/20261104T132842Z-1c432509) stopped because psycopg prepared the reader's
    sixth poll. Gate A1 v3 disables automatic preparation on its own connections. These logs are synthetic: the
    fake applies the preparation model that the real-log tests check against the second run's log."""

    def test_under_the_policy_nothing_is_logged_prepared(self, tmp_path):
        effects = FakeEffects()
        p, _ = package()
        assert a1.characterize(effects, p, a1.a2.EvidenceDir.create(tmp_path))["outcome"] == "CHARACTERIZED"
        assert not any(r[pr.MESSAGE].startswith("execute _pg3_") for r in effects.log)
        inserts = [r for r in effects.log if r[pr.MESSAGE].startswith("execute <unnamed>: INSERT INTO ds_char")]
        assert len(inserts) == len(ch.CASES)  # the repeated binding INSERTs stay unprepared

    def test_a_default_prepared_reader_reproduces_the_second_run(self, tmp_path):
        effects = FakeEffects("default_prepare_reader")
        p, _ = package()
        with pytest.raises(a1.DriverRefused, match="op0r didn't reconcile.*other than this session's poll"):
            a1.characterize(effects, p, a1.a2.EvidenceDir.create(tmp_path))
        assert effects.n == 6  # stopped at the sixth operation, the seeded reset, as the real run did

    def test_a_default_prepared_parent_session_is_refused(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="didn't reconcile.*prepared-statement name"):
            run(tmp_path, "default_prepare_parent")

    def test_the_manifest_must_declare_the_policy(self, tmp_path):
        for policy in (None, {}, {"psycopg_prepare_threshold": 5}, {"psycopg_prepare_threshold": 0}):
            data = json.loads(PACKAGE.read_text())
            data.pop("client_connection_policy")
            if policy is not None:
                data["client_connection_policy"] = policy
            path = tmp_path / "m.json"
            path.write_text(json.dumps(data))
            with pytest.raises(a1.DriverRefused, match="connection policy"):
                a1.load_package(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def _effects(self, monkeypatch, threshold):
        class Info:
            host, port, dbname, user = "127.0.0.1", 55433, "deployspecter_test", "deployspecter"

        calls, closed = [], []

        class Conn:
            info, prepare_threshold = Info(), threshold

            def close(self):
                closed.append(True)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                closed.append(True)

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(k) or Conn())
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter")
        return calls, closed

    def test_every_gate_a1_connection_disables_preparation(self, monkeypatch):
        calls, _closed = self._effects(monkeypatch, None)
        p, _ = package()
        effects = a1.RealEffects(p)
        with effects.parent_connection("ds_parent_op0p_0123456789ab", "deployspecter_test"):
            pass
        assert len(calls) == 2 and all("prepare_threshold" in k and k["prepare_threshold"] is None for k in calls)

    def test_a_connection_that_would_still_prepare_is_refused(self, monkeypatch):
        _calls, closed = self._effects(monkeypatch, 5)
        p, _ = package()
        with pytest.raises(a1.DriverRefused, match="automatic statement preparation"):
            a1.RealEffects(p)
        assert closed == [True]

    def test_a_parent_connection_that_would_still_prepare_is_refused(self, monkeypatch):
        calls, _closed = self._effects(monkeypatch, None)
        p, _ = package()
        effects = a1.RealEffects(p)
        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: type(
            "Prepared", (), {"prepare_threshold": 5, "__enter__": lambda s: s, "__exit__": lambda s, *e: None})())
        effects.psycopg = psycopg
        with pytest.raises(a1.DriverRefused, match="automatic statement preparation"):
            with effects.parent_connection("ds_parent_op0p_0123456789ab", "deployspecter_test"):
                pass

    def test_the_batch_audit_refuses_a_prepared_reader_statement(self):
        reader = pr.ReaderSession("lr", "91", "deployspecter", "deployspecter_test", start_line=4, end_line=6)
        r = row("lr", "execute _pg3_0: SELECT pg_current_logfile('csvlog')", app=ch.LOG_READER_APP, pid="91")
        r[pr.LINE] = "5"
        out = ch.audit_batch([r], reconciled_tags=set(), user="deployspecter", databases={"deployspecter_test"},
                             reader=reader)
        assert not out.ok and "prepared statement execute _pg3_0" in out.problems[0]


class TestNegativeZeroAndFinalOperations:
    """Version 4: float8 parameters are logged with float8's text output, as the third real run showed. These run the
    whole logical batch on the fake, whose op7b parameter text comes from that run's real log, through the final
    operations that no real run has reached yet. Passing here is not a live observation of them."""

    def test_the_full_simulated_batch_reaches_its_final_operations(self, tmp_path):
        effects = FakeEffects()
        p, _ = package()
        evidence = a1.a2.EvidenceDir.create(tmp_path)
        summary = a1.characterize(effects, p, evidence)
        assert summary["outcome"] == "CHARACTERIZED"
        names = {f.name for f in evidence.path.iterdir()}
        assert {"preflight.json", "catalog.json", "states.json", "bindings.json", "parent_reconciliation.json",
                "batch_audit.json", "summary.json"} <= names
        records = json.loads((evidence.path / "parent_reconciliation.json").read_text())
        assert [r["op"] for r in records][-2:] == ["op7b", "op9c"] and all(r["ok"] for r in records)
        assert json.loads((evidence.path / "batch_audit.json").read_text())["ok"] is True
        assert any("$2 = '-0'" in r[pr.DETAIL] for r in effects.log)  # the real form reached the reconciler

    def test_the_version_3_expectation_for_negative_zero_is_refused(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="op7b didn't reconcile.*statement 15 has unapproved parameters"):
            run(tmp_path, "negative_zero_as_python")

    def test_a_missing_cleanup_statement_is_refused(self, tmp_path):
        with pytest.raises(a1.DriverRefused, match="op9c didn't reconcile.*1 statements logged, 2 approved"):
            run(tmp_path, "cleanup_incomplete")

    def test_an_extra_reader_statement_between_operations_is_a_batch_finding(self, tmp_path):
        summary = run(tmp_path, "reader_extra_between")
        assert summary["outcome"] == "CHARACTERIZED_WITH_FINDINGS"
        assert summary["findings"]["every_session_attributed"] is False

    def test_a_binding_case_without_an_established_log_form_stops_before_any_sql(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ch, "CASES", (*ch.CASES, ("f8", 0.0)))
        effects = FakeEffects()
        p, _ = package()
        with pytest.raises(a1.DriverRefused, match="binding case can't be reconciled.*positive zero"):
            a1.characterize(effects, p, a1.a2.EvidenceDir.create(tmp_path))
        assert effects.log == [] and effects.n == 0

    @pytest.mark.parametrize("value", [None, 0, 2, "2"])
    def test_the_manifest_must_pin_extra_float_digits(self, tmp_path, value):
        data = json.loads(PACKAGE.read_text())
        if value is None:
            data["safety_ceilings"]["session_options"].pop("extra_float_digits")
        else:
            data["safety_ceilings"]["session_options"]["extra_float_digits"] = value
        path = tmp_path / "m.json"
        path.write_text(json.dumps(data))
        with pytest.raises(a1.DriverRefused, match="extra_float_digits"):
            a1.load_package(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_every_connection_starts_with_extra_float_digits_1(self, monkeypatch):
        class Info:
            host, port, dbname, user = "127.0.0.1", 55433, "deployspecter_test", "deployspecter"

        class Conn:
            info, prepare_threshold = Info(), None

        calls = []
        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(k) or Conn())
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter")
        p, _ = package()
        a1.RealEffects(p)
        assert f" {calls[0]['options']} ".count(" -c extra_float_digits=1 ") == 1
