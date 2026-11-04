"""Gate A2.1, offline: the controls Gate A2 takes over from Gate A1. Fakes only; no database, container or command.

Simulated: the parent sessions and the server log come from Gate A1's offline fake, which logs what the driver actually
sent and the parameter details PostgreSQL 16.15 logged in the third real Gate A1 run. Passing here is driver logic on
mocked effects. It is not evidence that Gate A2 ran, and Gate A2 stays blocked (execution_manifest_v3.json).
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in ("tests", "research_tools", "tests/research_tracks"):
    sys.path.insert(0, str(ROOT / extra))

import gate_a1_characterize as a1  # noqa: E402
import gate_a2_run as drv  # noqa: E402
from psycopg.conninfo import conninfo_to_dict  # noqa: E402
from test_gate_a1 import FakeEffects as A1Fake  # noqa: E402
from test_track_a_milestone2 import raw_export  # noqa: E402
from test_track_a_milestone3 import row  # noqa: E402

from deployspecter.database import Sandbox  # noqa: E402
from deployspecter.live.trace import LiveTrace, RunEvidence  # noqa: E402
from deployspecter.research.track_a import evidence_binding as eb  # noqa: E402
from deployspecter.research.track_a import gate_a2_controls as c  # noqa: E402
from deployspecter.research.track_a import parent_reconciliation as pr  # noqa: E402
from deployspecter.research.track_a import pipeline as p  # noqa: E402

MANIFEST = ROOT / "docs/research/live_stepping/track_a/execution_manifest_v3.json"
A1_MANIFEST = ROOT / "docs/research/live_stepping/track_a/gate_a1_manifest.json"
DRIVER = ROOT / "research_tools/gate_a2_run.py"
CONTROLS = ROOT / "src/deployspecter/research/track_a/gate_a2_controls.py"
GOOD_URL = "postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def manifest():
    sha = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    return drv.load_manifest(MANIFEST, sha), sha


def keywords():
    return c.connection_keywords(conninfo_to_dict(GOOD_URL), manifest()[0])


# ------------------------------------------------------------------ a Gate A1 fake with Gate A2 breakages


def renumber(log, session):
    for n, r in enumerate((r for r in log if r[pr.SESSION] == session), 1):
        r[pr.LINE] = str(n)


class Fake(A1Fake):
    """Gate A1's fake parent sessions and server log, plus faults specific to Gate A2.1."""

    def __init__(self, breakage=None):
        super().__init__(breakage if breakage in {"cleanup_incomplete", "default_prepare_reader"} else None)
        self.a2, self.resets, self.event_reads, self.exports = breakage, 0, 0, 0

    @contextlib.contextmanager
    def parent_connection(self, tag, database):
        op = tag.split("_")[2]
        if op == "op0r":
            self.resets += 1
        fail_at = 3 if self.a2 == "partial_reset" and op == "op0r" else None
        with super().parent_connection(tag, database) as conn:
            original, sent = conn.execute, []

            def execute(text, params=None):
                sent.append(text)
                if fail_at is not None and len(sent) == fail_at:
                    raise psycopg.errors.LockNotAvailable("simulated: lock timeout")
                return original(text, params)

            conn.execute = execute
            yield conn
        mine = [r for r in self.log if r[pr.APPLICATION] == tag]
        session = mine[0][pr.SESSION]
        at = next(i for i, r in enumerate(self.log) if r is mine[-1])  # the disconnection
        if self.a2 == "admin_statement" and op == "op0r":
            self.log.insert(at, row(session, "statement: GRANT ALL ON SCHEMA ds_run TO PUBLIC", app=tag,
                                    pid=mine[0][pr.PID]))
        if self.a2 == "missing_reset_statement" and op == "op0r":
            self.log.remove(next(r for r in mine if r[pr.MESSAGE] == "statement: CREATE SCHEMA ds_run"))
        if self.a2 == "unapproved_parameter" and op == "op0r" and self.resets == 2:
            insert = next(r for r in mine if r[pr.MESSAGE].startswith('execute <unnamed>: INSERT INTO "events"'))
            insert[pr.DETAIL] = insert[pr.DETAIL].replace("$1 = '3'", "$1 = '300'")
        if self.a2 == "mismatched_tag" and op == "op2b":
            authorized = next(r for r in self.log if r[pr.SESSION] == session
                              and r[pr.MESSAGE].startswith("connection authorized:"))
            authorized[pr.MESSAGE] = authorized[pr.MESSAGE].replace(tag, tag.replace("op2b", "op2a"))
        if self.a2 == "prepared_parent" and op == "op2a":
            first = next(r for r in mine if r[pr.MESSAGE].startswith("statement: BEGIN"))
            first[pr.MESSAGE] = first[pr.MESSAGE].replace("statement:", "execute _pg3_0:", 1)
        if self.a2 == "extra_session" and op == "op2c":
            stranger = [list(r) for r in mine]
            for r in stranger:
                r[pr.SESSION], r[pr.PID] = "zz", "777"
            self.log[at:at] = stranger[1:-1]
        renumber(self.log, session)

    def answer(self, text, params):
        if text.startswith("SELECT current_database()::text, current_setting('server_version_num')"):
            self.exports += 1
            metadata = raw_export()["metadata"]
            if self.a2 == "other_instance" or (self.a2 == "instance_switch" and self.exports == 2):
                metadata = [(*metadata[0][:5], "7400000000000000099")]
            if self.a2 == "foreign_cluster":
                metadata = [(metadata[0][0], metadata[0][1], "another_cluster", *metadata[0][3:])]
            if self.a2 == "postgres_15":
                metadata = [(metadata[0][0], 150008, *metadata[0][2:])]
            if self.a2 == "no_instance_identity":
                metadata = [(*metadata[0][:5], "")]
            return metadata
        if self.a2 == "catalog_changed" and text.startswith("SELECT a.attrelid::bigint, a.attnum::int"):
            rows = super().answer(text, params)
            extra = (rows[0][0], 9, "added_later", *rows[0][3:])
            return sorted([*rows, extra], key=lambda r: (r[0], r[1]))
        if self.a2 == "cross_disagrees" and text.startswith("SELECT table_name::text, table_type::text"):
            return [*super().answer(text, params), ("ghost", "BASE TABLE")]
        if text.startswith("SELECT row_to_json") and '"events"' in text:
            self.event_reads += 1
            if self.a2 == "changed_initial_state" and self.event_reads == 2:
                return [(json.dumps({**r, "reserved": 1}),) for r in self.tables["events"]]
        return super().answer(text, params)


def attest(digest):
    return {"reviewer": "schema_reviewer", "snapshot_digest": digest, "exported_on": "2026-11-05"}


def export(tmp_path, breakage=None):
    m, _ = manifest()
    effects, evidence = Fake(breakage), a1.a2.EvidenceDir.create(tmp_path)
    return drv.export_batch(effects, m, evidence), effects, evidence


def construct(tmp_path, breakage=None, export_breakage=None):
    m, _ = manifest()
    result, _, evidence = export(tmp_path, export_breakage)
    effects = Fake(breakage)
    return drv.construction_batch(effects, m, evidence, attest(result["digest"])), effects, evidence


# ------------------------------------------------------------------ positive cases


class TestTheManifestAndTarget:
    def test_the_manifest_fixes_one_synthetic_target_and_forbids_the_development_database(self):
        m, _ = manifest()
        t = c.target(m)
        assert (t["host"], t["hostaddr"], t["port"], t["database"], t["initial_database"], t["admin_user"]) == (
            "127.0.0.1", "127.0.0.1", "55433", "deployspecter_test", "deployspecter", "deployspecter")
        assert t["container_name"] == "deployspecter-live-pg" and t["cluster_name"] == "ds_live_disposable"
        assert m["forbidden_targets"] == [{"container": "deployspecter-pg", "port": "55432",
                                           "reason": m["forbidden_targets"][0]["reason"]}]

    def test_the_manifest_is_blocked_and_says_why(self):
        m, _ = manifest()
        assert m["status"] == "OFFLINE_ONLY_EXECUTION_BLOCKED"
        assert [b["id"] for b in m["execution_blockers"]] == [f"B{i}" for i in range(1, 9)]
        assert drv.GATE2A_SECTION_IMPLEMENTED is False

    def test_the_correct_dsn_gives_both_databases_from_the_manifest(self):
        kw = keywords()
        assert kw["admin"] == {"host": "127.0.0.1", "hostaddr": "127.0.0.1", "port": "55433", "user": "deployspecter",
                               "password": "s3cret", "dbname": "deployspecter"}
        assert kw["test"] == {**kw["admin"], "dbname": "deployspecter_test"}

    def test_gate2a_settings_name_the_address_and_carry_no_model_key(self):
        settings = c.harness_settings(keywords())
        for dsn, db in ((settings.admin_dsn, "deployspecter"), (settings.test_dsn, "deployspecter_test")):
            assert conninfo_to_dict(dsn) == {"host": "127.0.0.1", "hostaddr": "127.0.0.1", "port": "55433",
                                             "dbname": db, "user": "deployspecter", "password": "s3cret",
                                             "application_name": c.HARNESS_APP, "connect_timeout": "5"}
        parts = urlsplit(settings.test_dsn)  # what Gate 2A's supervisor hands the child
        assert (parts.hostname, parts.port) == ("127.0.0.1", 55433)
        assert settings.model_api_key is None and "s3cret" not in repr(settings)

    def test_a_password_with_url_characters_survives_the_harness_url(self):
        kw = c.connection_keywords(conninfo_to_dict(
            "postgresql://deployspecter:a%2Fb%40c%3Fd@127.0.0.1:55433/deployspecter"), manifest()[0])
        assert conninfo_to_dict(c.harness_url(kw["test"]))["password"] == "a/b@c?d"

    def test_clean_preconditions_pass(self):
        m, _ = manifest()
        kw = drv.preconditions(m, environ={"PATH": "/usr/bin", "HOME": "/h"}, dotenv={"DATABASE_URL": GOOD_URL},
                               modules=["deployspecter.live.supervisor"])
        assert kw["test"]["dbname"] == "deployspecter_test"


class TestStatementSets:
    def test_every_parent_statement_set_is_gate_a1_v4s_exactly(self):
        m, _ = manifest()
        a1m = json.loads(A1_MANIFEST.read_text())
        shared = [k for k in m["statement_sets_sha256"] if k != "gate2a_reset_model"]
        assert m["shared_with_gate_a1_v4"] == sorted(shared)
        assert {k: m["statement_sets_sha256"][k] for k in shared} == {k: a1m["statement_sets_sha256"][k]
                                                                      for k in shared}

    def test_the_manifest_digests_match_the_code(self):
        from live_app import reservations as app
        m, _ = manifest()
        types = {tuple(k.split(".")): v for k, v in m["state_checks"]["column_types"].items()}
        sets = c.statement_sets(app.SETUP_SQL, p.run_offline_chain(), types)
        assert m["statement_sets_sha256"] == {k: c.set_digest(v) for k, v in sets.items()}
        assert m["statement_counts"] == {k: len(v) for k, v in sets.items()}

    def test_the_gate2a_reset_model_is_what_sandbox_reset_sends(self):
        """Runs Gate 2A's own Sandbox.reset against a recording admin session. psycopg's transaction() on an
        autocommit connection sends BEGIN and COMMIT; the recorder models that."""
        from live_app import reservations as app
        seed = list(p.run_offline_chain().seed)

        class Admin:
            def __init__(self):
                self.sent = []

            def execute(self, query, params=None):
                text = query if isinstance(query, str) else query.as_string(None)
                self.sent.append((text, tuple(params or ())))

            @contextlib.contextmanager
            def transaction(self):
                self.sent.append(("BEGIN", ()))
                yield
                self.sent.append(("COMMIT", ()))

        box = object.__new__(Sandbox)
        box.admin, box._actors = Admin(), {}
        box.reset(app.SETUP_SQL, seed)
        assert box.admin.sent == c.gate2a_reset_statements(app.SETUP_SQL, seed)
        assert box.admin.sent[:-3] == a1._modules()[1].reset_statements(app.SETUP_SQL, seed)


class TestReconciledBatches:
    def test_the_export_batch_reconciles_every_session_and_the_whole_batch(self, tmp_path):
        result, _, evidence = export(tmp_path)
        records = evidence.read("parent_reconciliation_export.json")
        assert [(r["op"], r["statements"]) for r in records] == [
            ("op0p", 3), ("op0r", 6), ("op2a", 12), ("op2b", 12), ("op2c", 7), ("op9c", 2)]
        assert all(r["ok"] for r in records)
        audit = evidence.read("batch_audit_export.json")
        assert audit["ok"] and sorted(set(audit["sessions"].values()))[0] == "log_reader"
        assert result["identity"]["system_identifier"] == "7400000000000000001"

    def test_the_construction_batch_verifies_the_bridged_state_twice(self, tmp_path):
        built, _, evidence = construct(tmp_path)
        records = evidence.read("parent_reconciliation_construction.json")
        assert [r["op"] for r in records] == ["op0p", "op0r", "op2a", "op0r", "op6i", "op6f", "op0r", "op6i", "op6f",
                                              "op9c"]
        assert all(r["ok"] for r in records) and evidence.read("batch_audit_construction.json")["ok"]
        record = evidence.read("construction.json")
        assert record["problems"] == [] and record["repeats"] == 2 and len(set(record["read_digests"])) == 1
        assert built.prepared == manifest()[0]["scenario_digests"]

    def test_the_verified_digest_is_the_one_gate2a_snapshot_gives_for_the_same_state(self, tmp_path):
        built, effects, _ = construct(tmp_path)
        gate2a_snapshot = {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [], "rejections": []}
        assert built.verified_initial_digest == eb.canonical_digest(gate2a_snapshot)


class TestPerAttemptStateRecords:
    def trace(self, run_id="r1", initial=None, final=None):
        state = {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [], "rejections": []}
        return LiveTrace(scenario="track_a", requested_schedule=[], initial_state=initial or copy.deepcopy(state),
                         final_state=final or copy.deepcopy(state), evidence=RunEvidence(supervised=True,
                                                                                          run_id=run_id))

    def known(self, built, independent=None):
        state = {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [], "rejections": []}
        return {"variant": "buggy", "scenario_sha": built.prepared["buggy"],
                "verified_initial_digest": built.verified_initial_digest, "expected": built.expected,
                "independent_final": independent or state}

    def test_each_attempt_is_tied_to_its_run_and_the_verified_state(self, tmp_path):
        built, _, _ = construct(tmp_path)
        attempts = c.Attempts()
        first = attempts.record("explore-buggy", self.trace("r1"), **self.known(built))
        second = attempts.record("explore-buggy", self.trace("r2"), **self.known(built))
        assert first["problems"] == [] and second["problems"] == []
        assert (first["attempt"], second["attempt"]) == ("explore-buggy-0001", "explore-buggy-0002")
        assert first["initial_digest"] == built.verified_initial_digest and "not reconciled" in first["initial_source"]

    @pytest.mark.parametrize("change, fragment", [
        (lambda t: t.initial_state["events"][0].update(reserved=1), "didn't start from the verified initial state"),
        (lambda t: t.initial_state["reservations"].append({"id": 9, "event_id": 1, "holder": "x", "qty": 1}),
         "didn't start from the verified initial state"),
        (lambda t: t.final_state["events"][0].update(reserved=2), "reconciled final read disagrees"),
        (lambda t: setattr(t.evidence, "run_id", None), "no Gate 2A run id"),
    ])
    def test_a_changed_initial_state_or_unbound_attempt_is_a_problem(self, tmp_path, change, fragment):
        built, _, _ = construct(tmp_path)
        trace = self.trace()
        change(trace)
        record = c.Attempts().record("explore-buggy", trace, **self.known(built))
        assert any(fragment in x for x in record["problems"]), record["problems"]

    def test_an_attempt_matching_a_wrongly_verified_digest_still_fails_the_typed_check(self, tmp_path):
        """Defence in depth: even if the verified digest itself were of a wrong state, the attempt's initial state is
        still compared with the bridge's typed rows."""
        built, _, _ = construct(tmp_path)
        trace = self.trace()
        trace.initial_state["events"][0]["reserved"] = 1
        known = {**self.known(built), "verified_initial_digest": eb.canonical_digest(trace.initial_state)}
        record = c.Attempts().record("explore-buggy", trace, **known)
        assert any(x.startswith("initial state:") for x in record["problems"]), record["problems"]

    def test_a_run_id_seen_twice_is_a_problem(self, tmp_path):
        built, _, _ = construct(tmp_path)
        attempts = c.Attempts()
        attempts.record("explore-buggy", self.trace("r1"), **self.known(built))
        again = attempts.record("confirm", self.trace("r1"), **self.known(built))
        assert "recorded twice" in again["problems"][0]


# ------------------------------------------------------------------ negative cases


class TestTargetAndCredentialRefusals:
    @pytest.mark.parametrize("dsn, fragment", [
        ("postgresql://deployspecter:s3cret@127.0.0.1:55432/deployspecter", "forbidden target"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:5432/deployspecter", "differs on ['port']"),
        ("postgresql://deployspecter:s3cret@10.0.0.5:55433/deployspecter", "differs on ['host']"),
        ("postgresql://deployspecter:s3cret@localhost:55433/deployspecter", "differs on ['host']"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55433/postgres", "differs on ['dbname']"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter_test", "differs on ['dbname']"),
        ("postgresql://postgres:s3cret@127.0.0.1:55433/deployspecter", "differs on ['user']"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter?hostaddr=10.0.0.5", "keys"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter?options=-c%20log_statement%3Dnone", "keys"),
        ("postgresql://deployspecter:s3cret@127.0.0.1:55433/deployspecter?service=other", "keys"),
        ("postgresql://deployspecter@127.0.0.1:55433/deployspecter", "no password"),
        ("postgresql://deployspecter:s3cret%zz@127.0.0.1:55433/deployspecter", "couldn't be parsed"),
    ])
    def test_a_wrong_database_port_user_or_key_is_refused_before_connecting(self, dsn, fragment):
        m, _ = manifest()
        with pytest.raises(drv.DriverRefused) as raised:
            drv.preconditions(m, environ={}, dotenv={"DATABASE_URL": dsn}, modules=[])
        assert fragment in str(raised.value) and "s3cret" not in str(raised.value)

    @pytest.mark.parametrize("environ, fragment", [
        ({"PGHOSTADDR": "10.0.0.5"}, "libpq environment variables"),
        ({"PGOPTIONS": "-c log_statement=none"}, "libpq environment variables"),
        ({"PGSERVICE": "prod"}, "libpq environment variables"),
        ({"PGPASSFILE": "/x"}, "libpq environment variables"),
        ({"PGPASSWORD": "x"}, "libpq environment variables"),
        ({"DATABASE_URL": GOOD_URL}, "only reviewed source"),
        ({"MODEL_API_KEY": "sk-1"}, "model credentials"),
        ({"MODEL_API_BASE_URL": "https://api.example"}, "model credentials"),
        ({"OPENAI_API_KEY": "sk-1"}, "model credentials"),
        ({"ANTHROPIC_API_KEY": "sk-1"}, "model credentials"),
        ({"some_api_key": "x"}, "model credentials"),
    ])
    def test_an_unexpected_environment_is_refused(self, environ, fragment):
        m, _ = manifest()
        with pytest.raises(drv.DriverRefused) as raised:
            drv.preconditions(m, environ=environ, dotenv={"DATABASE_URL": GOOD_URL}, modules=[])
        assert fragment in str(raised.value)
        assert not any(v in str(raised.value) for v in environ.values() if v != GOOD_URL)

    @pytest.mark.parametrize("dotenv, fragment", [
        ({"DATABASE_URL": GOOD_URL, "MODEL_API_KEY": "sk-1"}, "model credentials"),
        ({"DATABASE_URL": GOOD_URL, "PGHOSTADDR": "10.0.0.5"}, "nothing else"),
        ({}, "nothing else"),
        ({"DATABASE_URL": ""}, "empty"),
    ])
    def test_a_second_secret_source_or_model_key_in_dotenv_is_refused(self, dotenv, fragment):
        m, _ = manifest()
        with pytest.raises(drv.DriverRefused, match=fragment) as raised:
            drv.preconditions(m, environ={}, dotenv=dotenv, modules=[])
        assert "sk-1" not in str(raised.value)

    @pytest.mark.parametrize("module", ["deployspecter.meta_client", "deployspecter.guided", "openai.types",
                                        "anthropic", "httpx"])
    def test_a_model_related_import_is_refused(self, module):
        m, _ = manifest()
        with pytest.raises(drv.DriverRefused, match="model-related modules"):
            drv.preconditions(m, environ={}, dotenv={"DATABASE_URL": GOOD_URL}, modules=["json", module])

    def test_the_drivers_import_closure_includes_no_model_module(self):
        """In a fresh interpreter, so other test files' imports don't count: everything the export and construction
        batches import, then the same check the driver applies."""
        import subprocess
        code = ("import sys; sys.path[:0] = ['src', 'tests', 'research_tools']; import gate_a2_run as d; "
                "d.a1._modules(); d._controls(); import live_app.reservations, live_app.properties; "
                "import dotenv, psycopg.conninfo, deployspecter.config, deployspecter.live.serverlog; "
                "print(d._controls().module_problems(list(sys.modules)))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={"PATH": "/usr/bin:/bin"},
                             capture_output=True, text=True, timeout=60, check=True).stdout
        assert out.strip() == "[]"

    @pytest.mark.parametrize("change, fragment", [
        (lambda m: m["target"].update(port="55432"), "forbidden"),
        (lambda m: m["target"].update(container_name="deployspecter-pg"), "forbidden"),
        (lambda m: m["target"].update(host="localhost"), "loopback"),
        (lambda m: m["target"].update(database="deployspecter"), "test database"),
        (lambda m: m["target"].pop("hostaddr"), "completely"),
        (lambda m: m.update(forbidden_targets=[]), "forbidden targets"),
        (lambda m: m["container"].update(port="55434"), "don't name its target"),
        (lambda m: m["cluster"].update(admin_user="postgres"), "don't name its target"),
        (lambda m: m["client_connection_policy"].update(psycopg_prepare_threshold=5), "connection policy"),
        (lambda m: m["client_connection_policy"].pop("psycopg_prepare_threshold"), "connection policy"),
        (lambda m: m["safety_ceilings"]["session_options"].update(extra_float_digits=3), "extra_float_digits"),
        (lambda m: m.update(version=2), "version 3"),
        (lambda m: m.update(context="heldout_evaluation"), "synthetic"),
        (lambda m: m["variants"]["fixed"].update(lock_row=False), "variants"),
        (lambda m: m.pop("execution_blockers"), "execution blockers"),
    ])
    def test_a_manifest_that_loosens_a_control_is_refused(self, tmp_path, change, fragment):
        data = json.loads(MANIFEST.read_text())
        change(data)
        path = tmp_path / "m.json"
        path.write_text(json.dumps(data))
        with pytest.raises(drv.DriverRefused, match=fragment):
            drv.load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_real_effects_refuse_before_any_connection(self, monkeypatch):
        import dotenv
        m, _ = manifest()
        real_module_problems = c.module_problems
        # Other test files may have imported model modules into this process; that check gets its own case below.
        monkeypatch.setattr(c, "module_problems", lambda _modules: [])
        cases = [({"MODEL_API_KEY": "sk-1"}, {"DATABASE_URL": GOOD_URL}, "model credentials"),
                 ({"PGHOSTADDR": "10.0.0.5"}, {"DATABASE_URL": GOOD_URL}, "libpq"),
                 ({}, {"DATABASE_URL": GOOD_URL.replace("55433", "55432")}, "forbidden target"),
                 ({}, {"DATABASE_URL": GOOD_URL}, "model-related modules")]
        for environ, values, fragment in cases:
            if fragment == "model-related modules":
                monkeypatch.setattr(c, "module_problems", real_module_problems)
                monkeypatch.setitem(sys.modules, "deployspecter.meta_client", sys.modules[__name__])
            monkeypatch.setattr(dotenv, "dotenv_values", lambda *_a, values=values, **_k: dict(values))
            for name in [n for n in list(__import__("os").environ) if c.MODEL_ENV.match(n.upper())
                         or c.LIBPQ_ENV.match(n.upper()) or n == "DATABASE_URL"]:
                monkeypatch.delenv(name)
            for name, value in environ.items():
                monkeypatch.setenv(name, value)
            with pytest.raises(drv.DriverRefused, match=fragment):
                drv.RealEffects(m)  # the autouse fixture fails the test if anything connects
            for name in environ:
                monkeypatch.delenv(name)


class TestPreparationPolicyRefusals:
    class Info:
        host, hostaddr, port, dbname, user = "127.0.0.1", "127.0.0.1", 55433, "deployspecter_test", "deployspecter"

    def conn(self, **info):
        holder = type("Info", (), {**{k: getattr(self.Info, k) for k in ("host", "hostaddr", "port", "dbname",
                                                                          "user")}, **info})()
        return type("Conn", (), {"prepare_threshold": 5, "info": holder})()

    def test_the_harness_policy_turns_preparation_off_and_checks_the_target(self):
        box = type("Box", (), {})()
        box.admin, box.monitor = self.conn(), self.conn()
        assert c.apply_harness_policy(box, keywords()["test"]) == []
        assert box.admin.prepare_threshold is None and box.monitor.prepare_threshold is None

    def test_a_harness_session_that_keeps_preparing_or_landed_elsewhere_is_a_problem(self):
        class Sticky:
            info = TestPreparationPolicyRefusals.Info()

            @property
            def prepare_threshold(self):
                return 5

            @prepare_threshold.setter
            def prepare_threshold(self, _value):
                pass

        box = type("Box", (), {})()
        box.admin, box.monitor = Sticky(), self.conn(port=55432)
        problems = c.apply_harness_policy(box, keywords()["test"])
        assert any("admin session would still prepare" in x for x in problems)
        assert any("monitor session isn't connected" in x for x in problems)

    def test_a_parent_statement_logged_under_a_prepared_name_is_refused(self, tmp_path):
        with pytest.raises(drv.DriverRefused, match="prepared-statement name"):
            export(tmp_path, "prepared_parent")

    def test_a_log_reader_with_default_preparation_is_refused(self, tmp_path):
        """psycopg's default threshold (5) renames the reader's repeated poll, as the second real Gate A1 run showed."""
        with pytest.raises(drv.DriverRefused, match="didn't reconcile") as raised:
            export(tmp_path, "default_prepare_reader")
        assert "other than this session's poll" in str(raised.value)

    def test_gate2a_child_sessions_already_disable_preparation_in_source(self):
        tree = ast.parse((ROOT / "src/deployspecter/live/worker.py").read_text())
        connects = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "connect"]
        assert connects and all(any(k.arg == "prepare_threshold" and isinstance(k.value, ast.Constant)
                                    and k.value.value is None for k in n.keywords) for n in connects)


class TestReconciliationRefusals:
    @pytest.mark.parametrize("breakage, fragment", [
        ("admin_statement", "parent session op0r didn't reconcile"),
        ("missing_reset_statement", "parent session op0r didn't reconcile"),
        ("mismatched_tag", "parent session op2b didn't reconcile"),
        ("extra_session", "parent session op2c didn't reconcile"),
        ("cleanup_incomplete", "parent session op9c didn't reconcile"),
    ])
    def test_unexpected_or_missing_activity_stops_the_export_batch(self, tmp_path, breakage, fragment):
        with pytest.raises(drv.DriverRefused, match=fragment):
            export(tmp_path, breakage)

    def test_an_unapproved_reset_parameter_stops_construction(self, tmp_path):
        with pytest.raises(drv.DriverRefused, match="parent session op0r didn't reconcile") as raised:
            construct(tmp_path, "unapproved_parameter")
        assert "unapproved parameters" in str(raised.value)

    def test_a_changed_initial_state_between_repeats_stops_construction(self, tmp_path):
        with pytest.raises(drv.DriverRefused, match="construction verification failed"):
            construct(tmp_path, "changed_initial_state")

    @pytest.mark.parametrize("breakage, fragment", [
        ("instance_switch", "different server instances"),
        ("cross_disagrees", "re-read disagrees"),
    ])
    def test_an_export_batch_that_cant_be_attested_is_refused(self, tmp_path, breakage, fragment):
        with pytest.raises(drv.DriverRefused, match=fragment):
            export(tmp_path, breakage)

    def test_columns_whose_two_reads_differ_are_refused(self):
        snapshot = {"types": [{"oid": 23, "typname": "int4"}, {"oid": 1700, "typname": "numeric"},
                              {"oid": 701, "typname": "float8"}],
                    "attributes": [{"attrelid": 1, "attname": "id", "atttypid": 23, "attisdropped": False},
                                   {"attrelid": 1, "attname": "price", "atttypid": 1700, "attisdropped": False},
                                   {"attrelid": 1, "attname": "gone", "atttypid": 701, "attisdropped": True}]}
        assert c.snapshot_type_problems(snapshot) == ["columns whose two reads aren't comparable: ['1.price']"]
        snapshot["attributes"].pop(1)
        assert c.snapshot_type_problems(snapshot) == []

    @pytest.mark.parametrize("breakage", ["foreign_cluster", "postgres_15", "no_instance_identity"])
    def test_an_export_from_the_wrong_server_isnt_offered_for_attestation(self, tmp_path, breakage):
        with pytest.raises(drv.DriverRefused, match="disposable cluster|PostgreSQL 16|instance identity"):
            export(tmp_path, breakage)

    def test_a_structurally_changed_catalog_stops_construction(self, tmp_path):
        with pytest.raises(drv.DriverRefused, match="differs structurally from the attested export"):
            construct(tmp_path, "catalog_changed")

    def test_another_server_instance_stops_construction(self, tmp_path):
        with pytest.raises(drv.DriverRefused, match="server instance the reviewer attested"):
            construct(tmp_path, "other_instance")

    def test_a_partial_reset_stops_without_repair_or_retry(self, tmp_path):
        m, _ = manifest()
        effects, evidence = Fake("partial_reset"), a1.a2.EvidenceDir.create(tmp_path)
        with pytest.raises(drv.DriverRefused, match="stopped at statement 3 of 6") as raised:
            drv.export_batch(effects, m, evidence)
        assert "LockNotAvailable" in str(raised.value)
        assert effects.resets == 1 and effects.n == 2  # op0p, then the one failed reset; nothing after it
        assert not any("op2a" in r[pr.APPLICATION] for r in effects.log)

    def test_an_unattributed_session_between_operations_fails_the_batch_audit(self, tmp_path, monkeypatch):
        original = Fake.log_position

        def intrude(self, key="op"):
            if key == "op" and self.n == 2:
                self.log.append(row("zz", "statement: DELETE FROM ds_run.events", app="psql"))
            return original(self, key)

        monkeypatch.setattr(Fake, "log_position", intrude)
        with pytest.raises(drv.DriverRefused, match="export batch audit failed"):
            export(tmp_path)


# ------------------------------------------------------------------ the command line and the blocked boundary


class TestTheBlockedDriver:
    def test_plan_lists_the_blockers_and_touches_nothing(self, capsys):
        _, sha = manifest()
        assert drv.main(["plan", "--manifest", str(MANIFEST), "--manifest-sha256", sha]) == 0
        out = capsys.readouterr().out
        assert "B1:" in out and "B8:" in out and "BLOCKED" in out

    @pytest.mark.parametrize("command", ["export", "run"])
    def test_export_and_run_are_refused_before_any_approval_evidence_or_connection(self, tmp_path, command):
        _, sha = manifest()
        made = []
        with pytest.raises(drv.DriverRefused, match="isn't executable"):
            drv.main([command, "--manifest", str(MANIFEST), "--manifest-sha256", sha,
                      "--approval", str(tmp_path / "none.json")], effects_factory=lambda m: made.append(m))
        assert made == [] and list(tmp_path.iterdir()) == []

    def test_lifting_the_manifests_blockers_alone_cant_make_run_execute(self):
        m, _ = manifest()
        lifted = {**m, "execution_blockers": [], "status": "WAITING_FOR_EXECUTION_APPROVAL"}
        drv.require_executable(lifted, "export")
        with pytest.raises(drv.DriverRefused, match="whole-batch audit"):
            drv.require_executable(lifted, "run")

    def test_with_blockers_lifted_the_export_keeps_the_approval_and_writes_new_evidence(self, tmp_path, monkeypatch):
        m, sha = manifest()
        lifted = {**m, "execution_blockers": [], "status": "WAITING_FOR_EXECUTION_APPROVAL",
                  "artifacts_root": str(tmp_path / "evidence")}
        monkeypatch.setattr(drv, "load_manifest", lambda *_a: lifted)
        approval = tmp_path / "approval.json"
        approval.write_bytes(json.dumps({"manifest_sha256": sha, "phase": "gate_a2_export", "approver": "owner",
                                         "approved_on": "2026-11-05"}).encode())
        code = drv.main(["export", "--manifest", str(MANIFEST), "--manifest-sha256", sha, "--approval",
                         str(approval)], effects_factory=lambda _m: Fake())
        (run_dir,) = (tmp_path / "evidence").iterdir()
        assert code == 0 and (run_dir / "approval.json").read_bytes() == approval.read_bytes()
        assert {f.name for f in run_dir.iterdir()} >= {"approval_provenance.json", "export_1.json",
                                                       "batch_audit_export.json"}

    def lifted_main(self, tmp_path, monkeypatch, *, change=None, tamper=False):
        m, sha = manifest()
        lifted = copy.deepcopy({**m, "execution_blockers": [], "status": "WAITING_FOR_EXECUTION_APPROVAL",
                                "artifacts_root": str(tmp_path / "evidence")})
        if change:
            change(lifted)
        monkeypatch.setattr(drv, "load_manifest", lambda *_a: lifted)
        approval = tmp_path / "approval.json"
        approval.write_text(json.dumps({"manifest_sha256": sha, "phase": "gate_a2_export", "approver": "owner",
                                        "approved_on": "2026-11-05"}))
        if tamper:
            original = drv.v2.load_approval

            def load_then_change(*a, **k):
                out = original(*a, **k)
                approval.write_text(approval.read_text().replace("owner", "other"))
                return out
            monkeypatch.setattr(drv.v2, "load_approval", load_then_change)
        made = []
        argv = ["export", "--manifest", str(MANIFEST), "--manifest-sha256", sha, "--approval", str(approval)]
        return lambda: drv.main(argv, effects_factory=lambda _m: made.append(Fake()) or made[-1]), made

    def test_an_approval_that_changes_while_being_read_is_refused(self, tmp_path, monkeypatch):
        run, made = self.lifted_main(tmp_path, monkeypatch, tamper=True)
        with pytest.raises(drv.DriverRefused, match="changed while it was being read"):
            run()
        assert made == [] and not (tmp_path / "evidence").exists()

    def test_a_changed_source_file_is_refused_before_any_evidence_or_connection(self, tmp_path, monkeypatch):
        def change(m):
            m["source_sha256"]["src/deployspecter/live/supervisor.py"] = "0" * 64
        run, made = self.lifted_main(tmp_path, monkeypatch, change=change)
        with pytest.raises(drv.DriverRefused, match="changed since the manifest was reviewed"):
            run()
        assert made == [] and not (tmp_path / "evidence").exists()

    def test_the_manifest_digest_must_be_the_one_given(self):
        with pytest.raises(drv.DriverRefused, match="digest isn't the one given"):
            drv.load_manifest(MANIFEST, "0" * 64)

    def test_a_failed_export_leaves_a_failure_record_and_isnt_retried(self, tmp_path, monkeypatch):
        m, sha = manifest()
        lifted = {**m, "execution_blockers": [], "status": "WAITING_FOR_EXECUTION_APPROVAL",
                  "artifacts_root": str(tmp_path / "evidence")}
        monkeypatch.setattr(drv, "load_manifest", lambda *_a: lifted)
        approval = tmp_path / "approval.json"
        approval.write_text(json.dumps({"manifest_sha256": sha, "phase": "gate_a2_export", "approver": "owner",
                                        "approved_on": "2026-11-05"}))
        made = []
        with pytest.raises(drv.DriverRefused):
            drv.main(["export", "--manifest", str(MANIFEST), "--manifest-sha256", sha, "--approval", str(approval)],
                     effects_factory=lambda _m: made.append(Fake("partial_reset")) or made[-1])
        (run_dir,) = (tmp_path / "evidence").iterdir()
        failure = json.loads((run_dir / "failure.json").read_text())
        assert failure["classification"] == "refused_fail_closed" and failure["retried"] is False
        assert len(made) == 1


class TestTheTrustBoundary:
    def test_the_driver_reuses_gate_a1_and_runs_no_command(self):
        tree = ast.parse(DRIVER.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert "gate_a1_characterize" in names
        assert not names & {"subprocess", "shlex", "pty", "multiprocessing", "deployspecter.live.supervisor",
                            "deployspecter.database"}
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not any("psycopg" in ast.dump(n) or "os" == getattr(n, "names", [None])[0].name for n in top)
        defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        assert not defined & {"Batch", "audit_batch", "reconcile_parent_session", "connection_target"}

    def test_no_private_attribute_is_used_by_the_driver_or_the_controls(self):
        for path in (DRIVER, CONTROLS):
            private = {n.attr for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Attribute)
                       and n.attr.startswith("_") and not n.attr.startswith("__")}
            # Gate A1's own module helpers, reused unchanged; nothing private of Gate 2A, the sandbox or psycopg.
            assert private <= {"_run_ids", "_modules", "_execute"}, (path.name, private)

    def test_gate_a1s_pinned_sources_still_match_its_v4_manifest(self):
        a1.a2.verify_sources(json.loads(A1_MANIFEST.read_text()))

    def test_the_v3_manifest_pins_the_sources_it_depends_on(self):
        m, _ = manifest()
        a1.a2.verify_sources(m)
        assert {"research_tools/gate_a2_run.py", "src/deployspecter/research/track_a/gate_a2_controls.py",
                "research_tools/gate_a1_characterize.py", "src/deployspecter/live/supervisor.py"} <= set(
            m["source_sha256"])

    def test_the_supervisor_ends_a_client_session_it_doesnt_own_such_as_a_log_reader(self):
        """Blocker B3, shown on Gate 2A's own code: a Gate A2 log reader (pid 77) isn't one of the supervisor's
        sessions, so _bounded_reset's sweep terminates it. Uses Gate 2A's private method, here only, to show it."""
        from deployspecter.live.runner import LiveLimits
        from deployspecter.live.supervisor import SupervisedRunner, SupervisorLimits

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return self.rows[0] if self.rows else None

        class Conn:
            def __init__(self, pid):
                self.info, self.sent, self.reader_alive = type("I", (), {"backend_pid": pid})(), [], True

            def execute(self, query, params=None):
                self.sent.append((query, params))
                if "FROM pg_stat_activity" in str(query):
                    alive = self.reader_alive
                    return Cursor([(77, "deployspecter", "deployspecter_test", "ds_parent_log_reader",
                                    "client backend", "")] if alive else [])
                if "pg_terminate_backend" in str(query):
                    self.reader_alive = False
                return Cursor([(True,)])

        box = object.__new__(Sandbox)
        box.admin, box.monitor, box._actors = Conn(1), Conn(2), {}
        m, _ = manifest()
        runner = SupervisedRunner(box, LiveLimits(**m["safety_ceilings"]["gate2a"]["LiveLimits"]),
                                  SupervisorLimits(**m["safety_ceilings"]["gate2a"]["SupervisorLimits"]))
        try:
            assert runner._harness_pids() == {1, 2}
            assert runner._end_outside_sessions(1.0) == []
        finally:
            runner.close()
        assert ("SELECT pg_terminate_backend(%s)", (77,)) in box.admin.sent
