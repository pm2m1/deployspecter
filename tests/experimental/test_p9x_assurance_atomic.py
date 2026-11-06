"""EXPERIMENTAL ds-p9x/1.1 assurance A3 (construction-session provenance against the server log) and A4 (failure
atomicity), on the disposable MVP PostgreSQL only (skipped unless it is running)."""

from __future__ import annotations

import copy
import os
import signal
from pathlib import Path

import psycopg
import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental.phase9b_ext import execute, pipeline_v1, provenance
from deployspecter.experimental.state_construction.typed import TV

SECRET = Path.home() / ".ds_mvp_pg_password"
PORT = 55441
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")

DDL = """
CREATE TABLE items (id int PRIMARY KEY, label text NOT NULL, w double precision, d jsonb, at timestamptz,
                    u uuid);
CREATE TABLE audit (item int NOT NULL, label text NOT NULL);
CREATE FUNCTION audit_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO audit (item, label) VALUES (NEW.id, NEW.label); RETURN NEW; END $$;
CREATE TRIGGER items_audit AFTER INSERT ON items FOR EACH ROW EXECUTE FUNCTION audit_item();
"""
ROWS = [("items", {"id": TV("int", i), "label": TV("str", f"it's {i}"), "w": TV("float", 0.1 * i),
                   "d": TV("jsonb", f'{{"n": {i}}}'),
                   "at": TV("timestamptz", f"2026-01-0{i}T01:02:03.000004+00:00"),
                   "u": TV("uuid", f"00000000-0000-0000-0000-00000000000{i}")}) for i in (1, 2, 3)]


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


@pytest.fixture(scope="module")
def sandbox(target):
    sb = pipeline_v1.open_sandbox(PORT, target.user, target.password)
    yield sb
    sb.close()
    assert pipeline_v1.teardown(PORT, target.user, target.password) == {"database_dropped": True,
                                                                        "role_dropped": True}


@pytest.fixture
def schema(target):
    s = "ds_p9x_atomic"
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
        c.execute(f"CREATE SCHEMA {s}")
        c.execute(f"SET search_path = {s}")
        c.execute(DDL)
    yield s
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")


def state(target, s):
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        items = c.execute(f"SELECT count(*) FROM {s}.items").fetchone()[0]
        audit = c.execute(f"SELECT count(*) FROM {s}.audit").fetchone()[0]
        sessions = c.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name = %s",
                             (provenance.APPLICATION,)).fetchone()[0]
        locks = c.execute("SELECT count(*) FROM pg_locks l JOIN pg_class r ON r.oid = l.relation "
                          "JOIN pg_namespace n ON n.oid = r.relnamespace WHERE n.nspname = %s", (s,)).fetchone()[0]
    return {"items": items, "audit": audit, "construct_sessions": sessions, "locks": locks}


EMPTY = {"items": 0, "audit": 0, "construct_sessions": 0, "locks": 0}


# ------------------------------------------------------------------------------------------------ A3

def test_the_construction_session_is_reconciled_with_the_server_log(target, sandbox, schema):
    o = pipeline_v1.construct_on_target(target, sandbox, schema, ROWS)
    assert o.status == "constructed", (o.status, o.detail)
    p = o.provenance
    assert p["ok"] and p["problems"] == [] and p["params_unchecked"] == 0
    assert p["by_kind"]["construction"] == 3 and p["by_kind"]["boundary"] == 2 and p["by_kind"]["admin"] > 5
    assert p["params_checked"] >= 18  # 3 rows x 6 typed values (timestamptz by instant), plus admin
    texts = [e["text"] for e in p["journal"]]
    assert texts[0] == "BEGIN" and texts[-1] == "COMMIT"
    assert [e["kind"] for e in p["journal"] if e["text"].startswith("INSERT")] == ["construction"] * 3


def test_a_journal_that_doesnt_match_the_log_is_refused(target, sandbox, schema):
    o = pipeline_v1.construct_on_target(target, sandbox, schema, ROWS)
    assert o.provenance["ok"]
    j = copy.deepcopy(o.provenance["journal"])
    next(e for e in j if e["kind"] == "construction")["params"][1] = "it's 99"
    assert provenance.server_rendering("it's 99") == "'it''s 99'"
    # the reconciler is re-run on a fresh window around a fresh construction with the tampered journal substituted
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        c.execute(f"DELETE FROM {schema}.audit")
        c.execute(f"DELETE FROM {schema}.items")
    captured = {}
    real = provenance.reconcile

    def tamper(data, **kw):
        captured["honest"] = real(data, **kw)
        kw["journal"] = [dict(e, params=list(e["params"])) for e in kw["journal"]]
        first = next(e for e in kw["journal"] if e["kind"] == "construction")
        first["params"][1] = "it's 99"
        dropped = [e for e in kw["journal"] if not e["text"].startswith("LOCK")]
        captured["tampered"] = real(data, **kw)
        captured["unjournaled"] = real(data, **{**kw, "journal": dropped})
        return captured["tampered"]
    provenance_reconcile = execute.provenance.reconcile
    execute.provenance.reconcile = tamper
    try:
        o2 = pipeline_v1.construct_on_target(target, sandbox, schema, ROWS)
    finally:
        execute.provenance.reconcile = provenance_reconcile
    assert captured["honest"]["ok"]
    assert any("parameter $2" in p for p in captured["tampered"]["problems"])
    assert any("doesn't record" in p or "logged" in p for p in captured["unjournaled"]["problems"])
    assert o2.status == "refused_provenance"


def test_another_session_during_the_window_is_unexplained(target, sandbox, schema):
    def intruder():
        with psycopg.connect(**target.conninfo(), autocommit=True, application_name="ds_p9x_intruder") as c:
            c.execute("SELECT 1")
    o = pipeline_v1.construct_on_target(target, sandbox, schema, ROWS)
    assert o.status == "constructed"
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        c.execute(f"DELETE FROM {schema}.audit")
        c.execute(f"DELETE FROM {schema}.items")
    w = execute.write_and_verify(target.conninfo(), schema, ROWS, [0, 1, 2], o.report["final_snapshot"],
                                 o.fingerprint_sha256, hooks={"before_verify": intruder})
    assert not w["provenance"]["ok"] and any("unexplained session" in p for p in w["provenance"]["problems"])


# ------------------------------------------------------------------------------------------------ A4

class Injected(RuntimeError):
    pass


def _raise(*_a):
    raise Injected("injected")


def _terminate_self_then_raise(target):
    def hook(k):
        if k == 1:
            with psycopg.connect(**target.conninfo(), autocommit=True) as c:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = %s",
                          (provenance.APPLICATION,))
            raise Injected("injected after the construction backend was terminated: ROLLBACK can't be sent")
    return hook


def _sigint(*_a):
    os.kill(os.getpid(), signal.SIGINT)


@pytest.mark.parametrize("stage,make,exc", [
    ("before_writes", lambda t: _raise, Injected),
    ("on_insert", lambda t: (lambda k: _raise() if k == 1 else None), Injected),
    ("before_verify", lambda t: _raise, Injected),
    ("after_verify", lambda t: _raise, Injected),
    ("on_insert", _terminate_self_then_raise, (Injected, execute.ExecutionRefused, psycopg.OperationalError)),
    ("before_verify", lambda t: _sigint, KeyboardInterrupt)],
    ids=["before_writes", "during_writes", "before_verification", "after_verification_before_commit",
         "rollback_impossible_backend_terminated", "sigint_interruption"])
def test_failed_construction_leaves_no_partial_state(target, sandbox, schema, stage, make, exc):
    o = pipeline_v1.construct_on_target(target, sandbox, schema, ROWS)
    assert o.status == "constructed"
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        c.execute(f"DELETE FROM {schema}.audit")
        c.execute(f"DELETE FROM {schema}.items")
    old = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        with pytest.raises(exc) as info:
            execute.write_and_verify(target.conninfo(), schema, ROWS, [0, 1, 2], o.report["final_snapshot"],
                                     o.fingerprint_sha256, hooks={stage: make(target)})
    finally:
        signal.signal(signal.SIGINT, old)
    assert state(target, schema) == EMPTY
    prov = getattr(info.value, "provenance", None) or getattr(info.value.__context__, "provenance", None)
    assert prov is not None  # the attempt's own server-log record is kept with the failure


def _csvlog(rows):
    """Synthetic csvlog rows: (pid, message, detail, application), 26 columns each."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for pid, msg, det, app in rows:
        cells = [""] * 26
        cells[0], cells[1], cells[2], cells[3] = "2026-01-01 00:00:00.000 UTC", "u", "d", str(pid)
        cells[11], cells[12], cells[13], cells[14], cells[22], cells[23] = "LOG", "00000", msg, det, app, \
            "client backend"
        w.writerow(cells)
    return buf.getvalue().encode()


def test_a_timestamptz_parameter_is_reconciled_as_the_same_instant_not_loosely():
    assert provenance._instant_us("2026-01-01T05:30:00+05:30") == provenance._instant_us("2026-01-01T00:00:00+00:00")
    j = [{"seq": 0, "kind": "construction", "text": "SELECT %s::timestamptz", "params": ["2026-01-01T00:00:00+00:00"],
          "casts": ["timestamptz"], "outcome": "ok"}]

    def log(value):
        app = provenance.APPLICATION
        return _csvlog([(2, "statement: SELECT 'S'", "", "a"),
                        (1, f"connection authorized: user=u database=d application_name={app}", "", app),
                        (1, "execute <unnamed>: SELECT $1::timestamptz", f"parameters: $1 = '{value}'", app),
                        (1, "disconnection: session time: 0:00:00.001", "", app),
                        (2, "statement: SELECT 'E'", "", "a")])
    kw = {"start": "S", "end": "E", "journal": j, "construct_pid": 1, "audit_pid": 2, "user": "u", "database": "d"}
    same = provenance.reconcile(log("2026-01-01 00:00:00+00"), **kw)
    other_zone = provenance.reconcile(log("2026-01-01 05:30:00+05:30"), **kw)
    later = provenance.reconcile(log("2026-01-01 00:00:00.000001+00"), **kw)
    as_text = provenance.reconcile(log("2026-01-01 00:00:00+00"), **{**kw, "journal": [dict(j[0], casts=[None])]})
    assert same["ok"], same["problems"]
    assert other_zone["ok"], other_zone["problems"]
    assert not later["ok"] and any("instant" in p for p in later["problems"])
    assert not as_text["ok"]  # without the cast, the text must be exact
