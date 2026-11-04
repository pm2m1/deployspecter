"""The second real Gate A1 run's log (manifest version 2), offline. Real rows only; nothing here is a successful run.

The fixture is the second run's complete csvlog (evidence/gate_a1/20261104T132842Z-1c432509), verbatim. The run reached
the seeded op0r reset and stopped there because psycopg 3 prepared the log reader's sixth poll. These tests pin the
cause, show that the five completed operations and the seeded reset's own statements reconcile under the version 3
rules, and show that version 3 still refuses the prepared poll and every other deviation applied to these real rows.
Synthetic post-fix logs live in test_gate_a1.py, never here.
"""

from __future__ import annotations

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

import gate_a1_characterize as a1  # noqa: E402
from test_gate_a1 import PsycopgPreparation  # noqa: E402

from deployspecter.research.track_a import catalog_export as ce  # noqa: E402
from deployspecter.research.track_a import characterization as ch  # noqa: E402
from deployspecter.research.track_a import parent_reconciliation as pr  # noqa: E402
from deployspecter.research.track_a import pipeline as p  # noqa: E402
from live_app import reservations as app  # noqa: E402

FIXTURE = ROOT / "tests/research_tracks/fixtures/gate_a1_v2_observed.csv"
PROVENANCE = ROOT / "tests/research_tracks/fixtures/gate_a1_v2_observed.provenance.json"
FIXTURE_SHA256 = "fc76fd6736bd7d033de45c593142a1ad2162e98af5297cfb362d4ed8ae11d2e2"
USER, DB = "deployspecter", "deployspecter_test"
MARK = re.compile(r"^statement: SELECT 'ds_parent_mark_(start|end)_([0-9a-f]{12})'$")


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


def operations(rows: list[list[str]]) -> list[tuple[str, str, str]]:
    """(tag, start marker, end marker) for every operation window, in log order."""
    starts, out = {}, []
    for r in rows:
        m = MARK.match(r[pr.MESSAGE])
        if m and m.group(1) == "start":
            starts[m.group(2)] = True
        elif m:
            nonce = m.group(2)
            tag = next(x[pr.APPLICATION] for x in rows if x[pr.APPLICATION].startswith("ds_parent_op")
                       and x[pr.APPLICATION].endswith(nonce))
            out.append((tag, f"ds_parent_mark_start_{nonce}", f"ds_parent_mark_end_{nonce}"))
    return out


def expected(tag: str, nth_reset: int) -> tuple[pr.ExpectedStatement, ...]:
    """The approved statements, from the code, never from the log."""
    op = tag.split("_")[2]
    if op == "op0p":
        pairs = [(t, ()) for t in ch.PREFLIGHT]
    elif op == "op0r":
        pairs = ch.reset_statements(app.SETUP_SQL, [] if nth_reset == 0 else list(p.run_offline_chain().seed))
    elif op in ("op2a", "op2b"):
        pairs = [(t, ()) for _, t in ce.EXPORT_QUERIES]
    else:
        pairs = [(t, ()) for _, t in ce.CROSS_CHECK_QUERIES]
    return tuple(pr.ExpectedStatement(t, prm) for t, prm in pairs)


def reconcile_all(rows, *, unnamed_only=True):
    raw, out, resets = encode(rows), [], 0
    manifest = json.loads((ROOT / "docs/research/live_stepping/track_a/gate_a1_manifest.json").read_text())
    for tag, start, end in operations(rows):
        reader = pr.reader_session(*pr.marker_rows(raw, start, end), user=USER, database=DB)
        rec = pr.reconcile_parent_session(
            pr.window(raw, start, end), tag=tag, expected=expected(tag, resets), user=USER, database=DB,
            authentication="scram-sha-256", reader=reader, reader_poll_limit=a1.reader_poll_limit(manifest),
            unnamed_only=unnamed_only)
        resets += tag.startswith("ds_parent_op0r")
        out.append((tag, rec))
    return out


POLL_PROBLEM = "the log reader did something in the window other than this session's poll"


class TestTheFixture:
    def test_is_the_pinned_verbatim_log(self):
        assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
        provenance = json.loads(PROVENANCE.read_text())
        assert provenance["fixture_sha256"] == FIXTURE_SHA256 and provenance["rows"] == len(observed()) == 160
        source = ROOT / provenance["source"]
        if source.exists():  # the run's evidence is kept locally and isn't committed
            assert source.read_bytes() == FIXTURE.read_bytes()


class TestTheObservedCause:
    def test_six_operations_ran_and_only_the_sixth_failed_only_on_the_prepared_poll(self):
        results = reconcile_all(observed())
        assert [t.split("_")[2] for t, _ in results] == ["op0p", "op0r", "op2a", "op2b", "op2c", "op0r"]
        assert all(rec.ok for _, rec in results[:5]), [rec.problems for _, rec in results[:5]]
        assert results[5][1].problems == [POLL_PROBLEM]  # its own seven statements, seed parameters included, matched

    def test_the_v2_rules_saw_the_same_single_cause(self):
        results = reconcile_all(observed(), unnamed_only=False)
        assert all(rec.ok for _, rec in results[:5]) and results[5][1].problems == [POLL_PROBLEM]

    def test_the_sixth_poll_is_the_approved_poll_under_a_prepared_name(self):
        rows = observed()
        polls = [r for r in rows if r[pr.MESSAGE].endswith(pr.READER_POLL)]
        assert [r[pr.MESSAGE].split(":")[0] for r in polls] == ["execute <unnamed>"] * 5 + ["execute _pg3_2"]
        tags = [t for t, _s, _e in operations(rows)]
        assert [r[pr.DETAIL] for r in polls] == [f"parameters: $1 = {pr.literal(t)}" for t in tags]
        assert {r[pr.SESSION] for r in polls} == {next(r[pr.SESSION] for r in rows if r[pr.MESSAGE].startswith(
            "statement: SELECT 'ds_parent_mark_batch_start_"))}

    def test_every_prepared_execution_is_the_readers_and_one_of_three_approved_texts(self):
        rows = observed()
        named = [r for r in rows if r[pr.MESSAGE].startswith("execute ") and
                 not r[pr.MESSAGE].startswith("execute <unnamed>")]
        assert len(named) == 17 and {r[pr.APPLICATION] for r in named} == {pr.LOG_READER_APP}
        assert {r[pr.MESSAGE].split(": ", 1)[1] for r in named} == {
            "SELECT pg_current_logfile('csvlog')", "SELECT size FROM pg_stat_file($1, true)", pr.READER_POLL}

    def test_no_other_session_is_hidden_behind_the_explanation(self):
        rows = observed()
        start = next(i for i, r in enumerate(rows) if "ds_parent_mark_batch_start_" in r[pr.MESSAGE])
        sessions = {r[pr.SESSION]: {x[pr.APPLICATION] for x in rows[start:] if x[pr.SESSION] == r[pr.SESSION]} - {""}
                    for r in rows[start:]}
        assert sorted(next(iter(a)) for a in sessions.values()) == sorted(
            [pr.LOG_READER_APP] + [t for t, _s, _e in operations(rows)])

    def test_the_preparation_model_reproduces_the_real_readers_log(self):
        """The fake's preparation model, checked against the real reader: threshold 5, names in order of first
        preparation. pg_read_binary_file is left out: it ran six times and was never prepared, consistent with psycopg
        keying prepared queries by parameter types as well as text; the model doesn't cover that."""
        rows = observed()
        reader = next(r[pr.SESSION] for r in rows if r[pr.APPLICATION] == pr.LOG_READER_APP)
        model, checked = PsycopgPreparation(5), 0
        for r in rows:
            if r[pr.SESSION] != reader or not pr.STATEMENT.match(r[pr.MESSAGE]):
                continue
            text = r[pr.MESSAGE].split(": ", 1)[1]
            if text.startswith("SELECT pg_read_binary_file"):
                assert r[pr.MESSAGE].startswith("execute <unnamed>: ")
                continue
            assert f"{model.verb(text, r[pr.DETAIL])}: {text}" == r[pr.MESSAGE]
            checked += 1
        assert checked == 45


def at(rows, tag_prefix, statement_index):
    """The row of the nth statement of the first session whose tag starts with tag_prefix."""
    session = next(r[pr.SESSION] for r in rows if r[pr.APPLICATION].startswith(tag_prefix))
    mine = [i for i, r in enumerate(rows) if r[pr.SESSION] == session and pr.STATEMENT.match(r[pr.MESSAGE])]
    return mine[statement_index]


def edit(tag_prefix, statement_index, **cells):
    def apply(rows):
        r = rows[at(rows, tag_prefix, statement_index)]
        for name, value in cells.items():
            r[getattr(pr, name.upper())] = value
    return apply


def swap(tag_prefix, i, j):
    def apply(rows):
        a, b = at(rows, tag_prefix, i), at(rows, tag_prefix, j)
        rows[a][pr.MESSAGE], rows[b][pr.MESSAGE] = rows[b][pr.MESSAGE], rows[a][pr.MESSAGE]
    return apply


def reader_poll(n, **cells):
    def apply(rows):
        polls = [i for i, r in enumerate(rows) if r[pr.MESSAGE].endswith(pr.READER_POLL)]
        for name, value in cells.items():
            rows[polls[n]][getattr(pr, name.upper())] = value
    return apply


def extra_statement(rows):
    i = at(rows, "ds_parent_op0p", 2)
    extra = list(rows[i])
    extra[pr.MESSAGE] = "statement: SELECT 1"
    rows.insert(i + 1, extra)


@pytest.mark.parametrize("breakage, op, fragment", [
    (edit("ds_parent_op0p", 0, message="statement: SELECT name FROM pg_settings"), 0, "differs from the approved text"),
    (swap("ds_parent_op2a", 0, 1), 2, "differs from the approved text"),
    (extra_statement, 0, "statements logged"),
    (edit("ds_parent_op0p", 1, message="execute _pg3_0: " + ch.PREFLIGHT[1]), 0, "prepared-statement name"),
    (reader_poll(0, message="execute _pg3_9: " + pr.READER_POLL), 0, POLL_PROBLEM),
    (reader_poll(1, detail="parameters: $1 = 'ds_parent_op7b_000000000000'"), 1, POLL_PROBLEM),
    (reader_poll(2, message="execute <unnamed>: SELECT count(*) FROM pg_stat_activity"), 2, POLL_PROBLEM),
    (reader_poll(3, session="6aeb330a.99"), 3, "unattributed"),
])
def test_deviations_on_the_real_rows_still_refuse(breakage, op, fragment):
    rows = observed()
    breakage(rows)
    problems = reconcile_all(rows)[op][1].problems
    assert any(fragment in x for x in problems), problems


def test_a_wrong_seed_parameter_in_the_real_reset_refuses():
    rows = observed()
    sessions = [r[pr.SESSION] for r in rows if r[pr.APPLICATION].startswith("ds_parent_op0r")]
    insert = next(i for i, r in enumerate(rows) if r[pr.SESSION] == sessions[-1] and "INSERT" in r[pr.MESSAGE])
    rows[insert][pr.DETAIL] = rows[insert][pr.DETAIL].replace("'3'", "'4'", 1)
    assert "'4'" in rows[insert][pr.DETAIL]
    assert any("unapproved parameters" in x for x in reconcile_all(rows)[5][1].problems)


def test_the_version_3_batch_audit_would_also_refuse_the_readers_prepared_statements():
    """Outside operation windows the second run's reader logged _pg3_0 and _pg3_1. Version 2's batch audit accepted
    those forms; version 3's refuses them. (The batch never reached its end marker; this audits its first part.)"""
    rows = observed()
    start = next(i for i, r in enumerate(rows) if "ds_parent_mark_batch_start_" in r[pr.MESSAGE])
    last_end = max(i for i, r in enumerate(rows) if MARK.match(r[pr.MESSAGE]))
    part = rows[start + 1:last_end + 1]
    reader = pr.ReaderSession(rows[start][pr.SESSION], rows[start][pr.PID], USER, DB, int(rows[start][pr.LINE]),
                              int(rows[last_end][pr.LINE]) + 1)
    audit = ch.audit_batch(part, reconciled_tags={t for t, _s, _e in operations(rows)}, user=USER, databases={DB},
                           reader=reader)
    assert not audit.ok and "prepared statement execute _pg3_0" in audit.problems[0]
