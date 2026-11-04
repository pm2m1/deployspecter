"""The third real Gate A1 run's log (manifest version 3), offline. Real rows only; nothing here is a successful run.

The fixture is the third run's complete csvlog (evidence/gate_a1/20261104T140952Z-58f6e18e), verbatim: 299 CSV records
on 353 lines. Automatic preparation was off. The run reconciled op0p to op6f and stopped at op7b, because float8
negative zero was logged as '-0' and the version 3 reconciler expected '-0.0'. The binding read-back was never judged,
so nothing here says the binding cases passed. Synthetic logs live in test_gate_a1.py.
"""

from __future__ import annotations

import csv
import decimal
import functools
import hashlib
import io
import json
import math
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

from deployspecter.research.track_a import catalog_export as ce  # noqa: E402
from deployspecter.research.track_a import characterization as ch  # noqa: E402
from deployspecter.research.track_a import evidence_binding as eb  # noqa: E402
from deployspecter.research.track_a import parent_reconciliation as pr  # noqa: E402
from deployspecter.research.track_a import pipeline as p  # noqa: E402
from live_app import reservations as app  # noqa: E402

FIXTURE = ROOT / "tests/research_tracks/fixtures/gate_a1_v3_observed.csv"
PROVENANCE = ROOT / "tests/research_tracks/fixtures/gate_a1_v3_observed.provenance.json"
FIXTURE_SHA256 = "c48ea596101cf972a91987bf7f2090057b9e635d7f846bf1f39d40c798218023"
MANIFEST = ROOT / "docs/research/live_stepping/track_a/gate_a1_manifest.json"
USER, DB = "deployspecter", "deployspecter_test"
END = re.compile(r"^statement: SELECT 'ds_parent_mark_end_([0-9a-f]{12})'$")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def observed() -> list[list[str]]:
    return list(csv.reader(io.StringIO(FIXTURE.read_text()), strict=True))


def encode(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode()


@functools.lru_cache(maxsize=1)
def chain_and_state():
    chain = p.run_offline_chain()
    return chain, p.expected_initial_state(chain)


def expected(op: str, resets: int):
    """The approved statements, from the code, never from the log."""
    chain, state = chain_and_state()
    column_types = json.loads(MANIFEST.read_text())["state_checks"]["column_types"]
    types = {tuple(k.split(".")): v for k, v in column_types.items()}
    pairs = {"op0p": lambda: [(t, ()) for t in ch.PREFLIGHT],
             "op0r": lambda: ch.reset_statements(app.SETUP_SQL, [] if resets == 0 else list(chain.seed)),
             "op2a": lambda: [(t, ()) for _, t in ce.EXPORT_QUERIES],
             "op2b": lambda: [(t, ()) for _, t in ce.EXPORT_QUERIES],
             "op2c": lambda: [(t, ()) for _, t in ce.CROSS_CHECK_QUERIES],
             "op6i": lambda: list(eb.independent_statements(state, types)),
             "op6f": lambda: [(t, ()) for t in eb.final_read_statements(sorted(state))],
             "op7b": ch.binding_statements}[op]()
    return tuple(pr.ExpectedStatement(t, prm) for t, prm in pairs)


def reconcile_all(rows):
    raw, out, resets = encode(rows), [], 0
    limit = a1.reader_poll_limit(json.loads(MANIFEST.read_text()))
    for r in rows:
        m = END.match(r[pr.MESSAGE])
        if not m:
            continue
        start, end = f"ds_parent_mark_start_{m.group(1)}", f"ds_parent_mark_end_{m.group(1)}"
        window = pr.window(raw, start, end)
        tag = next(x[pr.APPLICATION] for x in window if x[pr.APPLICATION].startswith("ds_parent_op"))
        op = tag.split("_")[2]
        reader = pr.reader_session(*pr.marker_rows(raw, start, end), user=USER, database=DB)
        out.append((op, pr.reconcile_parent_session(
            window, tag=tag, expected=expected(op, resets), user=USER, database=DB, authentication="scram-sha-256",
            reader=reader, reader_poll_limit=limit, unnamed_only=True)))
        resets += op == "op0r"
    return out


def op7b_rows(rows):
    session = next(r[pr.SESSION] for r in rows if r[pr.APPLICATION].startswith("ds_parent_op7b"))
    return [i for i, r in enumerate(rows) if r[pr.SESSION] == session and pr.STATEMENT.match(r[pr.MESSAGE])]


def v3_literal(value):
    """The version 3 rule, kept only to show the original failure reproduces."""
    if isinstance(value, float) and not (math.isnan(value) or math.isinf(value)):
        return "'" + str(value) + "'"
    return pr.literal(value)


class TestTheFixture:
    def test_is_the_pinned_verbatim_log(self):
        assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
        provenance = json.loads(PROVENANCE.read_text())
        assert provenance["fixture_sha256"] == FIXTURE_SHA256
        assert provenance["records"] == len(observed()) == 299 and provenance["lines"] == 353
        source = ROOT / provenance["source"]
        if source.exists():  # the run's evidence is kept locally and isn't committed
            assert source.read_bytes() == FIXTURE.read_bytes()


class TestTheObservedRun:
    def test_the_version_3_failure_reproduces_on_statement_15_only(self):
        rows = observed()
        details = [rows[i][pr.DETAIL] for i in op7b_rows(rows)]
        assert len(details) == 32
        mismatched = []
        for n, ((_text, params), detail) in enumerate(zip(ch.binding_statements(), details, strict=True)):
            logged = dict((int(k), v) for k, v in pr.PARAMETER.findall(detail))
            if logged != {k + 1: v3_literal(v) for k, v in enumerate(params)}:
                mismatched.append((n, params, detail))
        assert mismatched == [(15, (14, -0.0), "parameters: $1 = '14', $2 = '-0'")]

    def test_every_window_including_all_32_binding_statements_now_reconciles(self):
        results = reconcile_all(observed())
        assert [op for op, _ in results] == ["op0p", "op0r", "op2a", "op2b", "op2c", "op0r", "op6i", "op6f",
                                             "op0r", "op6i", "op6f", "op7b"]
        assert all(rec.ok for _, rec in results), [(op, rec.problems) for op, rec in results if not rec.ok]
        assert results[-1][1].statements_seen == 32

    def test_the_readers_activity_passes_the_batch_audit_up_to_the_stop(self):
        """The batch never reached its end marker, so this audits the run's real rows from the batch start to op7b's end
        marker. It is the real log under the version 4 audit, not a completed batch."""
        rows = observed()
        start = next(i for i, r in enumerate(rows) if "ds_parent_mark_batch_start_" in r[pr.MESSAGE])
        last = max(i for i, r in enumerate(rows) if END.match(r[pr.MESSAGE]))
        tags = {r[pr.APPLICATION] for r in rows if r[pr.APPLICATION].startswith("ds_parent_op")}
        reader = pr.ReaderSession(rows[start][pr.SESSION], rows[start][pr.PID], USER, DB, int(rows[start][pr.LINE]),
                                  int(rows[last][pr.LINE]) + 1)
        audit = ch.audit_batch(rows[start + 1:last + 1], reconciled_tags=tags, user=USER, databases={DB},
                               reader=reader)
        assert audit.ok, audit.problems
        assert sorted(set(audit.sessions.values())) == ["log_reader"] + sorted(f"reconciled:{t}" for t in tags)

    def test_nothing_was_prepared_and_no_other_session_appeared(self):
        rows = observed()
        assert not [r for r in rows if r[pr.MESSAGE].startswith("execute ") and
                    not r[pr.MESSAGE].startswith("execute <unnamed>")]
        start = next(i for i, r in enumerate(rows) if "ds_parent_mark_batch_start_" in r[pr.MESSAGE])
        apps = {r[pr.SESSION]: r[pr.APPLICATION] for r in rows[start:] if r[pr.APPLICATION]}
        assert set(apps.values()) - {pr.LOG_READER_APP} == {
            r[pr.APPLICATION] for r in rows if r[pr.APPLICATION].startswith("ds_parent_op")}
        assert len(apps) == 13 and {r[pr.SEVERITY] for r in rows[start:]} == {"LOG"}


def at15(rows):
    return op7b_rows(rows)[15]


def set_detail(text):
    def apply(rows):
        rows[at15(rows)][pr.DETAIL] = text
    return apply


def set_message(n, text):
    def apply(rows):
        rows[op7b_rows(rows)[n]][pr.MESSAGE] = text
    return apply


def swap_inserts(rows):
    i, j = op7b_rows(rows)[14], op7b_rows(rows)[15]
    for column in (pr.MESSAGE, pr.DETAIL):
        rows[i][column], rows[j][column] = rows[j][column], rows[i][column]


def foreign_session(rows):
    rows[at15(rows)][pr.SESSION] = "6aeb3cb0.99"


def unknown_session(rows):
    stranger = list(rows[at15(rows)])
    stranger[pr.SESSION], stranger[pr.PID], stranger[pr.APPLICATION] = "6aeb3cb0.98", "998", "psql"
    rows.insert(at15(rows) + 1, stranger)


def extra_statement(rows):
    i = at15(rows)
    rows.insert(i + 1, list(rows[i]))


INSERT = "execute <unnamed>: INSERT INTO ds_char.bindings (id, f8) VALUES ($1, $2)"


@pytest.mark.parametrize("breakage, fragment", [
    (set_detail("parameters: $1 = '14', $2 = '0'"), "unapproved parameters"),  # sign lost
    (set_detail("parameters: $1 = '14', $2 = '-0.0'"), "unapproved parameters"),  # not both variants
    (set_detail("parameters: $1 = '14'"), "unapproved parameters"),  # a parameter missing
    (set_detail("parameters: $1 = '14', $2 = '-0', $3 = '1'"), "unapproved parameters"),  # one extra
    (set_detail("parameters: $1 = '-0', $2 = '14'"), "unapproved parameters"),  # order
    (set_message(15, INSERT.replace("f8", "t")), "differs from the approved text"),
    (set_message(15, INSERT.replace("execute <unnamed>", "execute _pg3_0")), "prepared-statement name"),
    (swap_inserts, "unapproved parameters"),
    (foreign_session, "sessions carry the tag"),
    (unknown_session, "unattributed"),
    (extra_statement, "statements logged"),
])
def test_deviations_on_the_real_binding_rows_still_refuse(breakage, fragment):
    rows = observed()
    breakage(rows)
    problems = dict(reconcile_all(rows))["op7b"].problems
    assert any(fragment in x for x in problems), problems


class TestTheFloat8LogModel:
    def test_negative_zero_is_minus_zero_and_stays_distinct_from_positive_zero(self):
        assert pr.literal(-0.0) == "'-0'"
        with pytest.raises(pr.ParentLogError, match="positive zero"):
            pr.literal(0.0)

    def test_a_string_is_never_read_as_a_float(self):
        assert pr.literal("-0.0") == "'-0.0'" and pr.literal("-0") == "'-0'" and pr.literal("NaN") == "'NaN'"

    @pytest.mark.parametrize("value", [2.0, -1.0, 1e15 + 0.5, 9007199254740992.0])
    def test_float_forms_without_an_established_log_text_are_refused(self, value):
        with pytest.raises(pr.ParentLogError, match="no established"):
            pr.literal(value)

    def test_other_parameter_types_are_refused(self):
        with pytest.raises(pr.ParentLogError, match="Decimal"):
            pr.literal(decimal.Decimal("1.5"))

    def test_every_binding_case_has_an_established_log_form(self):
        assert all(pr.literal(v) for _t, params in ch.binding_statements() for v in params)

    def test_the_model_matches_psycopgs_installed_adapters(self):
        """The model assumes psycopg sends ints, booleans and floats in binary and strings as text. Checked against the
        installed psycopg, offline; a different psycopg would fail here, not in a live run."""
        from psycopg.adapt import PyFormat, Transformer
        tx = Transformer()
        formats = {type(v).__name__: tx.get_dumper(v, PyFormat.AUTO).format.name for v in (1, 2**40, True, -0.0, "x")}
        assert formats == {"int": "BINARY", "bool": "BINARY", "float": "BINARY", "str": "TEXT"}
        assert tx.get_dumper(-0.0, PyFormat.AUTO).oid == 701  # float8


def test_an_expected_parameter_without_an_established_form_is_a_reconciliation_problem():
    rows = observed()
    raw = encode(rows)
    tag = next(r[pr.APPLICATION] for r in rows if r[pr.APPLICATION].startswith("ds_parent_op7b"))
    nonce = tag.rsplit("_", 1)[1]
    start, end = f"ds_parent_mark_start_{nonce}", f"ds_parent_mark_end_{nonce}"
    statements = list(expected("op7b", 1))
    statements[15] = pr.ExpectedStatement(statements[15].text, (14, 0.0))
    out = pr.reconcile_parent_session(
        pr.window(raw, start, end), tag=tag, expected=tuple(statements), user=USER, database=DB,
        authentication="scram-sha-256", reader=pr.reader_session(*pr.marker_rows(raw, start, end), user=USER,
                                                                 database=DB),
        reader_poll_limit=a1.reader_poll_limit(json.loads(MANIFEST.read_text())), unnamed_only=True)
    assert not out.ok and out.problems == ["statement 15 has a parameter without an established log form: "
                                           "positive zero has no established float8 log form"]
