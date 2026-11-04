"""Track A milestone 2, offline: parent-side export, independent re-read, pins, initial-state checks, binding and
acceptance gates. Every connection is a recording fake fed hand-written rows. No database is opened."""

from __future__ import annotations

import copy
import socket
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.live.trace import LiveTrace, RunEvidence  # noqa: E402
from deployspecter.research.track_a import acceptance as acc  # noqa: E402
from deployspecter.research.track_a import catalog_export as ce  # noqa: E402
from deployspecter.research.track_a import evidence_binding as eb  # noqa: E402
from deployspecter.research.track_a import pipeline as p  # noqa: E402
from deployspecter.research.track_a import scenario as sc  # noqa: E402
from live_app import reservations as app  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket, "socket", refuse)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0]


class FakeConnection:
    """Answers only the texts it was given; any other statement is an error, as a reviewer would want."""

    def __init__(self, answers):
        self.answers, self.log = answers, []

    def execute(self, text, params=None):
        self.log.append((text, params))
        if text in {"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", "COMMIT"}:
            return Cursor([])
        key = (text, params) if params else text
        if key not in self.answers:
            raise AssertionError(f"unexpected statement: {text[:80]}")
        return Cursor(self.answers[key])


NS, EVENTS, RES, RES_SEQ, REJ, REJ_SEQ = 2201, 17001, 17002, 17003, 17004, 17005
EV_PK, RES_PK, REJ_PK = 17010, 17011, 17012


def raw_export():
    rel = lambda oid, name, kind: (oid, name, "ds_run", kind, False, False, False, False)  # noqa: E731
    att = lambda r, n, name, typ, *, hasdef=False, coll=0: (r, n, name, typ, -1, True, hasdef, "", "", False, coll)  # noqa: E731
    con = lambda oid, name, kind, r, key, fr=0, fk=(), ind=0: (oid, name, kind, r, list(key), fr, list(fk),  # noqa: E731
                                                                False, False, True, ind)
    idx = lambda oid, r: (oid, r, [1], 1, True, True, True, True, True, False, False, False, False)  # noqa: E731
    return {
        "metadata": [("deployspecter_test", 160015, "ds_live_disposable", "origin", "2026-11-05 10:00:00+00",
                      "7400000000000000001")],
        "database": [("deployspecter_test", "UTF8", "c", "en_US.utf8", "en_US.utf8")],
        "relations": [rel(EVENTS, "events", "r"), rel(RES, "reservations", "r"),
                      rel(RES_SEQ, "reservations_id_seq", "S"),
                      rel(REJ, "rejections", "r"),
                      rel(REJ_SEQ, "rejections_id_seq", "S"),
                      rel(EV_PK, "events_pkey", "i"), rel(RES_PK, "reservations_pkey", "i"),
                      rel(REJ_PK, "rejections_pkey", "i")],
        "attributes": [att(EVENTS, 1, "id", 23), att(EVENTS, 2, "capacity", 23), att(EVENTS, 3, "reserved", 23),
                       att(RES, 1, "id", 23, hasdef=True), att(RES, 2, "event_id", 23),
                       att(RES, 3, "holder", 25, coll=100), att(RES, 4, "qty", 23),
                       att(REJ, 1, "id", 23, hasdef=True), att(REJ, 2, "event_id", 23),
                       att(REJ, 3, "holder", 25, coll=100), att(REJ, 4, "seats_available", 23)],
        "types": [(23, "int4", "pg_catalog", "b"), (25, "text", "pg_catalog", "b")],
        "collations": [(100, "default", "pg_catalog", "d", True)],
        "namespaces": [(11, "pg_catalog"), (NS, "ds_run")],
        "constraints": [con(31001, "events_pkey", "p", EVENTS, [1], ind=EV_PK),
                        con(31002, "reservations_pkey", "p", RES, [1], ind=RES_PK),
                        con(31003, "reservations_event_id_fkey", "f", RES, [2], EVENTS, [1], ind=EV_PK),
                        con(31004, "rejections_pkey", "p", REJ, [1], ind=REJ_PK)],
        "indexes": [idx(EV_PK, EVENTS), idx(RES_PK, RES), idx(REJ_PK, REJ)],
        "triggers": [(51001, RES, "RI_ConstraintTrigger_c_51001", "O", True, 5),
                     (51002, RES, "RI_ConstraintTrigger_c_51002", "O", True, 17),
                     (51003, EVENTS, "RI_ConstraintTrigger_a_51003", "O", True, 9),
                     (51004, EVENTS, "RI_ConstraintTrigger_a_51004", "O", True, 17)],
    }


def raw_cross():
    col = lambda t, c, pos, *, typ="integer", dflt=False: (t, c, pos, typ, "NO", dflt, "NO", "NEVER")  # noqa: E731
    return {
        "tables": [("events", "BASE TABLE"), ("rejections", "BASE TABLE"), ("reservations", "BASE TABLE")],
        "columns": [col("events", "id", 1), col("events", "capacity", 2), col("events", "reserved", 3),
                    col("rejections", "id", 1, dflt=True), col("rejections", "event_id", 2),
                    col("rejections", "holder", 3, typ="text"), col("rejections", "seats_available", 4),
                    col("reservations", "id", 1, dflt=True), col("reservations", "event_id", 2),
                    col("reservations", "holder", 3, typ="text"), col("reservations", "qty", 4)],
        "keys": [("events", "events_pkey", "PRIMARY KEY", "id", 1),
                 ("rejections", "rejections_pkey", "PRIMARY KEY", "id", 1),
                 ("reservations", "reservations_event_id_fkey", "FOREIGN KEY", "event_id", 1),
                 ("reservations", "reservations_pkey", "PRIMARY KEY", "id", 1)],
        "references": [("reservations_event_id_fkey", "events", "id")],
        "user_triggers": [],
    }


def conn_for(queries, raw):
    return FakeConnection({text: raw[section] for section, text in queries if section in raw})


def exported(raw=None):
    return ce.export_snapshot(conn_for(ce.EXPORT_QUERIES, raw or raw_export()))


def crossed(snapshot, raw=None):
    return ce.cross_check_read(conn_for(ce.CROSS_CHECK_QUERIES, raw or raw_cross()), snapshot)


ATTEST = lambda d: ce.Attestation(reviewer="schema_reviewer", snapshot_digest=d, exported_on="2026-11-05")  # noqa: E731


class TestExporter:
    def test_runs_exactly_the_fixed_statements_in_order(self):
        conn = conn_for(ce.EXPORT_QUERIES, raw_export())
        result = ce.export_snapshot(conn)
        assert [t for t, _ in conn.log] == [t for _, t in ce.EXPORT_QUERIES] == result.statements
        assert all(params is None for _, params in conn.log)
        assert conn.log[0][0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" and conn.log[-1][0] == "COMMIT"

    def test_query_sets_are_pinned_for_review(self):
        assert ce.EXPORT_QUERY_SET_SHA256 == ce.query_set_digest(ce.EXPORT_QUERIES)
        assert len({ce.EXPORT_QUERY_SET_SHA256, ce.CROSS_CHECK_QUERY_SET_SHA256}) == 2
        for _, text in (*ce.EXPORT_QUERIES, *ce.CROSS_CHECK_QUERIES):
            assert "%s" not in text and ";" not in text
            assert not any(w in text.upper().split() for w in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP",
                                                              "ALTER", "GRANT", "TRUNCATE", "SET"))

    def test_assembles_a_snapshot_that_matches_the_track_a_context(self):
        result = exported()
        assert result.identity["cluster_name"] == "ds_live_disposable" and result.snapshot["postgres_major"] == 16
        assert all(t["writes"] is None for t in result.snapshot["triggers"])
        registry = p.build_registry()
        context = sc.context_config(registry_digest=registry.digest, schema_digest=result.digest)
        pin = ce.pin_from_evidence(result, exported(), crossed(result.snapshot), ATTEST(result.digest))
        trust, verdict = ce.pin_context(context, registry=registry, snapshot=result.snapshot, snapshot_trust=pin,
                                        reviewer="schema_reviewer")
        assert verdict.outcome == "MATCH" and pin.source_label == "supervised_parent_export"

    @pytest.mark.parametrize("change, fragment", [
        (lambda r: r["triggers"].append((59999, RES, "odd", "O", False, 128)), "bits"),
        (lambda r: r["attributes"].append((EVENTS, 4, "x", 99999, -1, False, False, "", "", False, 0)),
         "unlisted type"),
        (lambda r: r["constraints"].append((31999, "k", "u", EVENTS, [2], 0, [], False, False, True, 99)),
         "unlisted index"),
        (lambda r: r.pop("indexes"), "not read"),
        (lambda r: r["database"].append(r["database"][0]), "exactly one row"),
        (lambda r: r["namespaces"].pop(), "isn't listed"),
        (lambda r: r["relations"].__setitem__(0, (EVENTS, "events", "ds_run", "q", False, False, False, False)),
         "no listed table"),
        (lambda r: r["types"].__setitem__(0, (23, "int4", "pg_catalog", "z")), "catsnap1"),
    ])
    def test_inconsistent_or_unknown_catalog_data_is_refused(self, change, fragment):
        raw = raw_export()
        change(raw)
        with pytest.raises(ce.ExportError, match=fragment):
            ce.assemble(raw)

    def test_a_user_trigger_leaves_its_writes_unknown_and_blocks_match(self):
        raw = raw_export()
        raw["triggers"].append((52000, EVENTS, "audit_events", "O", False, 5))
        result = exported(raw)
        registry = p.build_registry()
        context = sc.context_config(registry_digest=registry.digest, schema_digest=result.digest)
        cross = raw_cross()
        cross["user_triggers"] = [("audit_events", "events", "INSERT", "AFTER")]
        pin = ce.pin_from_evidence(result, exported(raw), crossed(result.snapshot, cross), ATTEST(result.digest))
        with pytest.raises(ce.ExportError, match="context verification gave"):
            ce.pin_context(context, registry=registry, snapshot=result.snapshot, snapshot_trust=pin,
                           reviewer="schema_reviewer")


class TestCrossCheck:
    def test_agreeing_views_agree(self):
        assert crossed(exported().snapshot).agrees

    @pytest.mark.parametrize("change, fragment", [
        (lambda r: r["columns"].__setitem__(1, ("events", "capacity", 2, "integer", "YES", False, "NO", "NEVER")),
         "nullability"),
        (lambda r: r["columns"].__setitem__(1, ("events", "capacity", 2, "money", "NO", False, "NO", "NEVER")),
         "no reviewed mapping"),
        (lambda r: r["tables"].pop(), "tables differ"),
        (lambda r: r["references"].clear(), "foreign-key targets"),
        (lambda r: r["user_triggers"].append(("t", "events", "INSERT", "AFTER")), "user triggers"),
    ])
    def test_any_disagreement_is_reported(self, change, fragment):
        raw = raw_cross()
        change(raw)
        result = crossed(exported().snapshot, raw)
        assert not result.agrees and any(fragment in x for x in result.problems)


class TestPins:
    @pytest.mark.parametrize("mutate, fragment", [
        ("second_differs", "two exports differ"),
        ("cross_disagrees", "re-read disagrees"),
        ("wrong_cluster", "disposable cluster"),
        ("wrong_attestation", "attestation"),
        ("replication_role", "session_replication_role"),
    ])
    def test_a_pin_needs_every_piece_of_evidence(self, mutate, fragment):
        raw = raw_export()
        first = exported(raw)
        second = exported(raw)
        cross = crossed(first.snapshot)
        attest = ATTEST(first.digest)
        if mutate == "second_differs":
            other = copy.deepcopy(raw)
            other["attributes"][2] = (EVENTS, 3, "reserved", 23, -1, False, False, "", "", False, 0)
            second = exported(other)
        elif mutate == "cross_disagrees":
            cross = ce.CrossCheck(False, ["x"], cross.statements)
        elif mutate == "wrong_cluster":
            first.identity["cluster_name"] = "production"
        elif mutate == "wrong_attestation":
            attest = ATTEST("0" * 64)
        elif mutate == "replication_role":
            first.identity["session_replication_role"] = "replica"
        with pytest.raises(ce.ExportError, match=fragment):
            ce.pin_from_evidence(first, second, cross, attest)

    def test_a_dry_run_snapshot_can_never_back_a_context_pin(self):
        chain = p.run_offline_chain()
        fake = ce.cv.TrustedSnapshot(exporter="dry_run_fixture", source_label="hand_written_track_a_fixture",
                                     exported_on="2026-11-05", pinned_digest=chain.digests["snapshot"])
        registry = p.build_registry()
        context = sc.context_config(registry_digest=registry.digest, schema_digest=chain.digests["snapshot"])
        with pytest.raises(ce.ExportError, match="only a supervised parent export"):
            ce.pin_context(context, registry=registry, snapshot=sc.catalog_snapshot_fixture(), snapshot_trust=fake,
                           reviewer="schema_reviewer")


TYPES = {("events", "id"): "integer", ("events", "capacity"): "integer", ("events", "reserved"): "integer"}


def independent(counts=(1, 0, 0), match=1, digest="d"):
    expected = p.expected_initial_state(p.run_offline_chain())
    answers = {}
    for text, params in eb.independent_statements(expected, TYPES):
        if text.startswith("SELECT md5"):
            answers[text] = [(digest,)]
        elif " WHERE " in text:
            answers[(text, params)] = [(match,)]
        elif text.startswith("SELECT count"):
            table = text.split('"')[1]
            answers[text] = [({"events": counts[0], "reservations": counts[1], "rejections": counts[2]}[table],)]
    conn = FakeConnection(answers)
    return eb.independent_state_check(conn, expected, TYPES), expected, conn


class TestInitialState:
    def test_statements_are_typed_and_parameterised(self):
        check, expected, conn = independent()
        where = [t for t, prm in conn.log if prm]
        assert where == ['SELECT count(*) FROM ds_run."events" WHERE "capacity" IS NOT DISTINCT FROM %s::int4 AND '
                         '"id" IS NOT DISTINCT FROM %s::int4 AND "reserved" IS NOT DISTINCT FROM %s::int4']
        assert eb.judge_independent(check, expected) == []

    @pytest.mark.parametrize("kwargs, fragment", [({"counts": (2, 0, 0)}, "expected exactly 1"),
                                                  ({"counts": (1, 1, 0)}, "expected exactly 0"),
                                                  ({"match": 0}, "matched 0 rows")])
    def test_independent_disagreement_fails(self, kwargs, fragment):
        check, expected, _ = independent(**kwargs)
        assert any(fragment in x for x in eb.judge_independent(check, expected))

    def test_runs_must_share_one_exact_initial_state(self):
        expected = p.expected_initial_state(p.run_offline_chain())
        good = {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [], "rejections": []}
        check = independent()[0]
        ok = eb.verify_initial_states(expected, [good, copy.deepcopy(good)], [check, check])
        assert ok["problems"] == [] and ok["verified_initial_digest"] == eb.canonical_digest(good)
        drift = copy.deepcopy(good)
        drift["rejections"] = [{"id": 1, "event_id": 1, "holder": "x", "seats_available": 0}]
        bad = eb.verify_initial_states(expected, [good, drift], [check])
        assert bad["verified_initial_digest"] is None and len(bad["problems"]) >= 2
        assert "no independent check" in eb.verify_initial_states(expected, [good], [])["problems"]

    def test_time_typed_columns_are_found(self):
        snap = sc.catalog_snapshot_fixture()
        assert eb.check_no_time_types(snap) == []
        snap["types"].append({"oid": 1184, "typname": "timestamptz", "nspname": "pg_catalog", "typtype": "b"})
        snap["attributes"].append({**snap["attributes"][0], "attnum": 9, "attname": "at", "atttypid": 1184})
        assert eb.check_no_time_types(snap) == [f"{sc.EVENTS}.at"]


def trace(final, run_id="r1", initial=None, supervised=True):
    t = LiveTrace(scenario="track_a:buggy", requested_schedule=[], chosen_schedule=["a", "b"],
                  initial_state=initial or {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [],
                                            "rejections": []},
                  final_state=final, evidence=RunEvidence(supervised=supervised, run_id=run_id, quiescent=True,
                                                          role_dropped=True, child_exit=0))
    return t


LOST = {"events": [{"id": 1, "capacity": 3, "reserved": 2}], "rejections": [],
        "reservations": [{"id": 1, "event_id": 1, "holder": "alice", "qty": 2},
                         {"id": 2, "event_id": 1, "holder": "bob", "qty": 2}]}


class TestBinding:
    def oracle(self):
        from live_app import properties as props
        return props.check_invariants

    def test_records_and_binding(self):
        chain = p.run_offline_chain()
        buggy = p.live_scenario(chain, variant="buggy", setup_sql=app.SETUP_SQL)
        digest = eb.scenario_digest(buggy)
        record = eb.run_record(trace(LOST), variant="buggy", scenario_sha=digest, oracle=self.oracle())
        assert record["requirement_ids"] == ["R1", "R2"] and record["run_id"] == "r1"
        bound = eb.bind(chain=chain, prepared={"buggy": digest}, runs=[record],
                        verified_initial_digest=record["initial_digest"])
        assert len(bound) == 64

    def test_a_run_from_another_scenario_or_state_doesnt_bind(self):
        chain = p.run_offline_chain()
        buggy = eb.scenario_digest(p.live_scenario(chain, variant="buggy", setup_sql=app.SETUP_SQL))
        fixed = eb.scenario_digest(p.live_scenario(chain, variant="fixed", setup_sql=app.SETUP_SQL))
        assert buggy != fixed
        record = eb.run_record(trace(LOST), variant="buggy", scenario_sha=fixed, oracle=self.oracle())
        with pytest.raises(eb.BindingError, match="prepared"):
            eb.bind(chain=chain, prepared={"buggy": buggy}, runs=[record],
                    verified_initial_digest=record["initial_digest"])
        record = eb.run_record(trace(LOST), variant="buggy", scenario_sha=buggy,
                               oracle=self.oracle())
        with pytest.raises(eb.BindingError, match="unverified state"):
            eb.bind(chain=chain, prepared={"buggy": buggy}, runs=[record], verified_initial_digest="0" * 64)


class TestGates:
    def statuses(self, bundle):
        return {j.gate: j.status for j in acc.judge(bundle)[1]}

    def test_without_execution_nothing_passes_except_validation(self):
        chain = p.run_offline_chain()
        verdict, judgements = acc.judge({"chain": {"stages": chain.stages, "rechecked": True}})
        status = {j.gate: j.status for j in judgements}
        assert verdict == "NOT_RUN" and status.pop("G0_validated_proposal") == "pass"
        assert set(status.values()) == {"not_run"}

    def test_dry_run_catalog_fails_g1(self):
        bundle = {"catalog_export": {"source_label": "hand_written_track_a_fixture", "verify_context": "MATCH"}}
        assert self.statuses(bundle)["G1_catalog_identity_and_completeness"] == "fail"

    def test_judge_logic_only_a_complete_bundle_passes(self):
        """Judge logic, on a synthetic bundle. Not evidence that any run happened."""
        chain = p.run_offline_chain()
        digests = {k: "a" * 64 for k in acc.REQUIRED_DIGESTS} | {"spec": chain.digests["spec"]}
        runs = [{"supervised": True, "scoreable": True, "flags": [], "opaque_steps": 0, "refusals": 0, "run_id": "r"}]
        bundle = {
            "chain": {"stages": chain.stages, "rechecked": True}, "digests": digests, "runs": runs,
            "catalog_export": {"source_label": "supervised_parent_export", "verify_context": "MATCH",
                               "exports_agree": True, "cross_check_agrees": True, "cluster_name": "ds_live_disposable",
                               "coverage_complete": True, "statements_reconciled": True, "time_typed_columns": []},
            "initial_states": {"problems": [], "verified_initial_digest": "b" * 64, "runs_checked": 1},
            "oracle": {"source_sha256": "c", "pinned_sha256": "c", "imports_tool_or_app": False,
                       "reads": "parent_final_state_and_independent_read"},
            "buggy_exploration": {"candidate_requirements": ["R1"], "candidate_run_id": "r"},
            "fixed_exploration": {"same_workload_digest": chain.digests["spec"], "target_violations": [],
                                  "exhausted": True, "complete_schedule_coverage": True, "inconclusive_runs": 0},
            "replay": {"result": "reproduced", "independent_oracle_each_attempt": True},
            "unknown_effects": [], "missing_evidence": [],
        }
        assert acc.judge(bundle)[0] == "PASS"
        for key in ("unknown_effects", "missing_evidence"):
            broken = dict(bundle)
            broken.pop(key)
            assert acc.judge(broken)[0] == "FAIL"
        broken = dict(bundle, fixed_exploration={**bundle["fixed_exploration"], "inconclusive_runs": 1})
        assert self.statuses(broken)["G6_fixed_negative_control"] == "fail"


class TestManifest:
    def test_manifest_matches_the_code_it_names(self):
        import hashlib
        import json
        m = json.loads((ROOT / "docs/research/live_stepping/track_a/execution_manifest_v1.json").read_text())
        sha = lambda rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()  # noqa: E731
        chain = p.run_offline_chain()
        assert m["status"] == "WAITING_FOR_EXECUTION_APPROVAL" and m["never_heldout_evidence"] is True
        assert m["chain_digests"] == chain.digests
        scenarios = {v: p.live_scenario(chain, variant=v, setup_sql=app.SETUP_SQL) for v in ("buggy", "fixed")}
        assert m["scenario_digests"] == {v: eb.scenario_digest(s) for v, s in scenarios.items()}
        assert m["schema"]["setup_sql_sha256"] == sc.SETUP_SQL_SHA256
        assert m["operations"]["OP-2"]["export_query_set_sha256"] == ce.EXPORT_QUERY_SET_SHA256
        assert m["operations"]["OP-2"]["cross_check_query_set_sha256"] == ce.CROSS_CHECK_QUERY_SET_SHA256
        # Version 1 is a superseded record: its module hashes are checked against the files as committed with it.
        import subprocess
        for op, path in (("OP-2", "src/deployspecter/research/track_a/catalog_export.py"),
                         ("OP-6", "src/deployspecter/research/track_a/evidence_binding.py")):
            committed = subprocess.run(["git", "show", f"fb0b4fd:{path}"], cwd=ROOT, capture_output=True,
                                       check=True).stdout
            assert m["operations"][op]["module_sha256"] == hashlib.sha256(committed).hexdigest()
        assert m["application"]["module_sha256"] == sha("tests/live_app/reservations.py")
        assert m["oracle"]["module_sha256"] == sha("tests/live_app/properties.py")
        assert m["variants"]["buggy"] == {"lock_row": False} and m["variants"]["fixed"] == {"lock_row": True}
        assert not any("*" in a for a in m["allowed_process_actions"])
