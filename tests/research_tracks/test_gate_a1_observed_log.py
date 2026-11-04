"""Gate A1 reconciliation against the real PostgreSQL 16 log of the first run, offline.

The fixture holds verbatim csvlog rows from the first Gate A1 run (evidence/gate_a1/20261104T113256Z-d0b21a60), which
stopped closed at op0p. These tests show that the observed op0p sequence now reconciles, and that each kind of
unexpected activity, applied to those same real rows, still refuses. Only op0p ran, so nothing here says anything about
the catalog, state or binding operations.
"""

from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "research_tools"))

import gate_a1_characterize as a1  # noqa: E402

from deployspecter.research.track_a import characterization as ch  # noqa: E402
from deployspecter.research.track_a import parent_reconciliation as pr  # noqa: E402

FIXTURE = ROOT / "tests/research_tracks/fixtures/gate_a1_op0p_observed.csv"
PROVENANCE = ROOT / "tests/research_tracks/fixtures/gate_a1_op0p_observed.provenance.json"
FIXTURE_SHA256 = "d8e48ebbd5bab843f75473ceba3dc7fd58b02512753657b5d68813ef7293c6c1"
START, END = "ds_parent_mark_start_220d1918c9e9", "ds_parent_mark_end_220d1918c9e9"
TAG = "ds_parent_op0p_220d1918c9e9"
USER, DB = "deployspecter", "deployspecter_test"
READER_SESSION, OP_SESSION = "6aeb17e8.5b", "6aeb17e8.5c"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def observed() -> list[list[str]]:
    return list(csv.reader(io.StringIO(FIXTURE.read_text()), strict=True))


def encode(rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode()


def limit() -> int:
    manifest = json.loads((ROOT / "docs/research/live_stepping/track_a/gate_a1_manifest.json").read_text())
    return a1.reader_poll_limit(manifest)


def reconcile(rows: list[list[str]], *, poll_limit: int | None = None, authentication: str | None = "scram-sha-256",
              with_reader: bool = True) -> pr.ParentReconciliation:
    raw = encode(rows)
    reader = pr.reader_session(*pr.marker_rows(raw, START, END), user=USER, database=DB) if with_reader else None
    return pr.reconcile_parent_session(
        pr.window(raw, START, END), tag=TAG, expected=tuple(pr.ExpectedStatement(t) for t in ch.PREFLIGHT), user=USER,
        database=DB, authentication=authentication, reader=reader,
        reader_poll_limit=limit() if poll_limit is None else poll_limit)


def index(rows, session, line):
    return next(i for i, r in enumerate(rows) if r[pr.SESSION] == session and r[pr.LINE] == str(line))


class TestTheFixture:
    def test_is_the_pinned_verbatim_extract(self):
        assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
        provenance = json.loads(PROVENANCE.read_text())
        assert provenance["fixture_sha256"] == FIXTURE_SHA256 and provenance["rows"] == len(observed()) == 22
        source = ROOT / provenance["source"]
        if source.exists():  # the run's evidence is kept locally and isn't committed
            raw = source.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == provenance["source_sha256"]
            assert b"".join(raw.splitlines(keepends=True)[39:61]) == FIXTURE.read_bytes()

    def test_has_every_record_the_first_run_showed(self):
        messages = [r[pr.MESSAGE] for r in observed() if r[pr.SESSION] == OP_SESSION]
        assert [m.split(":")[0] for m in messages] == [
            "connection received", "connection authenticated", "connection authorized", "statement", "statement",
            "statement", "disconnection"]
        poll = observed()[index(observed(), READER_SESSION, 10)]
        assert poll[pr.MESSAGE] == f"execute <unnamed>: {pr.READER_POLL}"
        assert poll[pr.DETAIL] == f"parameters: $1 = '{TAG}'"


class TestTheObservedSequence:
    def test_the_real_op0p_window_now_reconciles(self):
        out = reconcile(observed())
        assert out.ok, out.problems
        assert out.session_id == OP_SESSION and out.statements_seen == 3

    def test_the_reader_is_identified_from_both_real_markers(self):
        reader = pr.reader_session(*pr.marker_rows(FIXTURE.read_bytes(), START, END), user=USER, database=DB)
        assert (reader.session, reader.pid, reader.start_line, reader.end_line) == (READER_SESSION, "91", 9, 11)

    def test_without_the_established_reader_the_poll_is_still_unattributed(self):
        out = reconcile(observed(), with_reader=False)
        assert not out.ok and any("unattributed" in p for p in out.problems)


def change(session_id, line_no, **cells):
    def apply(rows):
        r = rows[index(rows, session_id, line_no)]
        for name, value in cells.items():
            r[getattr(pr, name.upper())] = value
    return apply


def duplicate_authentication(rows):
    i = index(rows, OP_SESSION, 2)
    rows.insert(i + 1, list(rows[i]))


def swap_authentication_and_authorization(rows):
    i, j = index(rows, OP_SESSION, 2), index(rows, OP_SESSION, 3)
    rows[i], rows[j] = rows[j], rows[i]
    rows[i][pr.LINE], rows[j][pr.LINE] = "2", "3"


def authorization_after_a_statement(rows):
    authorized = rows.pop(index(rows, OP_SESSION, 3))
    rows.insert(index(rows, OP_SESSION, 4) + 1, authorized)
    for n, i in enumerate((i for i, r in enumerate(rows) if r[pr.SESSION] == OP_SESSION), 1):
        rows[i][pr.LINE] = str(n)


def drop(session, line):
    return lambda rows: rows.pop(index(rows, session, line))


def poll_before_the_work(rows):
    poll = rows.pop(index(rows, READER_SESSION, 10))
    rows.insert(index(rows, OP_SESSION, 6), poll)


def second_reader(rows):
    poll = list(rows[index(rows, READER_SESSION, 10)])
    poll[pr.SESSION], poll[pr.PID], poll[pr.LINE] = "6aeb17e8.99", "99", "1"
    rows.insert(index(rows, READER_SESSION, 10), poll)


def unrelated_session(rows):
    stranger = list(rows[index(rows, OP_SESSION, 4)])
    stranger[pr.SESSION], stranger[pr.PID], stranger[pr.APPLICATION] = "6aeb17e8.77", "77", "psql"
    stranger[pr.MESSAGE], stranger[pr.LINE] = "statement: DELETE FROM ds_run.events", "4"
    rows.insert(index(rows, OP_SESSION, 6), stranger)


AUTH = 'connection authenticated: identity="{}" method={} (/var/lib/postgresql/data/pg_hba.conf:128)'


@pytest.mark.parametrize("breakage, fragment", [
    (duplicate_authentication, "authenticated more than once"),
    (change(OP_SESSION, 2, message=AUTH.format("intruder", "scram-sha-256")), "another identity"),
    (change(OP_SESSION, 2, message=AUTH.format(USER, "trust")), "another identity or with another method"),
    (change(OP_SESSION, 2, message=AUTH.format(USER, "scram-sha-256") + " extra"), "malformed or out of order"),
    (swap_authentication_and_authorization, "out of order"),
    (authorization_after_a_statement, "the authorization record is malformed or out of order"),
    (change(OP_SESSION, 3, message=f"connection authorized: user=intruder database={DB} application_name={TAG}"),
     "connection identity differs"),
    (change(OP_SESSION, 3, message=f"connection authorized: user={USER} database=prod application_name={TAG}"),
     "connection identity differs"),
    (change(OP_SESSION, 3, message=f"connection authorized: user={USER} database={DB} application_name=other"),
     "connection identity differs"),
    (change(OP_SESSION, 3, message=f"connection authorized: user={USER} database={DB} application_name={TAG} "
                                   "SSL enabled"), "malformed or out of order"),
    (change(OP_SESSION, 5, message="connection reset: unknown state"), "unexplained"),
    (drop(OP_SESSION, 1), "connection isn't in the window"),
    (drop(OP_SESSION, 2), "authentication isn't in the window"),
    (drop(OP_SESSION, 5), "log lines aren't complete"),
    (change(OP_SESSION, 7, message="disconnection: session time: 0:00:00.007 user=deployspecter "
                                   "database=deployspecter_test host=172.17.0.1 port=1"), "doesn't match"),
    (change(OP_SESSION, 5, user="postgres"), "another user or database"),
    (change(READER_SESSION, 10, message="execute <unnamed>: SELECT pg_read_binary_file($1, $2, $3, true)"),
     "other than this session's poll"),
    (change(READER_SESSION, 10, detail="parameters: $1 = 'ds_parent_op7b_220d1918c9e9'"),
     "other than this session's poll"),
    (change(READER_SESSION, 10, pid="12"), "other than this session's poll"),
    (change(READER_SESSION, 10, user="postgres"), "other than this session's poll"),
    (change(READER_SESSION, 10, line="12"), "incomplete or out of sequence"),
    (drop(READER_SESSION, 10), "0 log-reader polls"),
    (poll_before_the_work, "polled before the session's work"),
    (second_reader, "unattributed"),
    (unrelated_session, "unattributed"),
])
def test_unexpected_activity_on_the_real_rows_still_refuses(breakage, fragment):
    rows = observed()
    breakage(rows)
    out = reconcile(rows)
    assert not out.ok and any(fragment in p for p in out.problems), out.problems


def test_more_polls_than_the_bound_refuse():
    out = reconcile(observed(), poll_limit=0)
    assert not out.ok and any("allowed 1 to 0" in p for p in out.problems)


def test_the_optional_authentication_mode_still_validates_a_present_record():
    assert reconcile(observed(), authentication=None).ok
    rows = observed()
    change(OP_SESSION, 2, message=AUTH.format(USER, "md5"))(rows)
    assert not reconcile(rows, authentication=None).ok
    assert not reconcile(observed(), authentication="md5").ok


@pytest.mark.parametrize("breakage, fragment", [
    (lambda rows: rows.pop(index(rows, READER_SESSION, 11)), "exactly once"),
    (change(READER_SESSION, 11, session="6aeb17e8.99"), "different sessions"),
    (change(READER_SESSION, 11, application="psql"), "wasn't written by the log reader"),
    (change(READER_SESSION, 9, user="postgres"), "wasn't written by the log reader"),
    (lambda rows: rows.insert(0, list(rows[index(rows, READER_SESSION, 9)])), "exactly once"),
    (change(READER_SESSION, 11, line="8"), "precedes the start marker"),
])
def test_markers_must_come_once_each_from_the_reader(breakage, fragment):
    rows = observed()
    breakage(rows)
    with pytest.raises(pr.ParentLogError, match=fragment):
        reconcile(rows)


def test_a_window_without_its_end_marker_is_not_trusted():
    rows = observed()
    rows.pop(index(rows, READER_SESSION, 11))
    with pytest.raises(pr.EndMarkerMissing):
        pr.window(encode(rows), START, END)
