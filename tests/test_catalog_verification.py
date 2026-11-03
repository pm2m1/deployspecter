"""Offline tests of catalog-snapshot verification (step 5C.1). Hand-written synthetic snapshots only."""

from __future__ import annotations

import ast
import json
import random
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from deployspecter.live import catalog_verification as cv
from deployspecter.live import state_planning as sp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_argument_validation import copy, make_registry  # noqa: E402

PUBLIC, EVENTS, VENUES, AUDITS = 2200, 16001, 16002, 16003
INT4, INT8, TEXT, VARCHAR, FLOAT8, TIMESTAMPTZ, DOMAIN = 23, 20, 25, 1043, 701, 1184, 90001
DEFAULT_COLL, CI_COLL = 100, 90100


def attribute(rel, num, name, typ, *, notnull=False, hasdef=False, identity="", generated="", typmod=-1, coll=0):
    return {"attrelid": rel, "attnum": num, "attname": name, "atttypid": typ, "atttypmod": typmod,
            "attnotnull": notnull, "atthasdef": hasdef, "attidentity": identity, "attgenerated": generated,
            "attisdropped": False, "attcollation": coll}


def relation(oid, name, kind="r", **flags):
    return {"oid": oid, "relname": name, "nspname": "public", "relkind": kind, "relispartition": False,
            "relrowsecurity": False, "relhasrules": False, "relhassubclass": False, **flags}


def constraint(oid, name, kind, rel, key, **extra):
    return {"oid": oid, "conname": name, "contype": kind, "conrelid": rel, "conkey": key, "condeferrable": False,
            "condeferred": False, "convalidated": True, **extra}


def index(oid, rel, key, *, unique=True, primary=False, **flags):
    base = {"indexrelid": oid, "indrelid": rel, "indkey": key, "indnkeyatts": len(key), "indisunique": unique,
            "indisprimary": primary, "indisvalid": True, "indisready": True, "indimmediate": True,
            "indnullsnotdistinct": False, "has_expressions": False, "has_predicate": False,
            "nondefault_semantics": False}
    return {**base, **flags}


def trigger(oid, rel, name, *, events=("INSERT",), enabled="O", internal=False, writes=()):
    return {"oid": oid, "tgrelid": rel, "tgname": name, "tgenabled": enabled, "tgisinternal": internal,
            "timing": "AFTER", "level": "ROW", "events": list(events),
            "writes": None if writes is None else [{"nspname": "public", "relname": w} for w in writes]}


def snapshot():
    return {
        "export_format_version": 1, "postgres_major": 16, "schema_name": "public",
        "database": {"datname": "ticketing_test", "encoding": "UTF8", "datlocprovider": "c", "datcollate": "C.UTF-8",
                     "datctype": "C.UTF-8"},
        "coverage": {"relations": True, "attributes": True, "types": True, "collations": True, "constraints": True,
                     "indexes": True, "triggers": "database"},
        "namespaces": [{"oid": PUBLIC, "nspname": "public"}, {"oid": 11, "nspname": "pg_catalog"}],
        "types": [{"oid": o, "typname": n, "nspname": "pg_catalog", "typtype": "b"} for o, n in (
            (INT4, "int4"), (INT8, "int8"), (TEXT, "text"), (VARCHAR, "varchar"), (FLOAT8, "float8"),
            (TIMESTAMPTZ, "timestamptz"), (16, "bool"), (1700, "numeric"))]
                 + [{"oid": DOMAIN, "typname": "positive_int", "nspname": "public", "typtype": "d"}],
        "collations": [{"oid": DEFAULT_COLL, "collname": "default", "nspname": "pg_catalog", "collprovider": "d",
                        "collisdeterministic": True},
                       {"oid": CI_COLL, "collname": "case_insensitive", "nspname": "public", "collprovider": "i",
                        "collisdeterministic": False}],
        "relations": [relation(EVENTS, "events"), relation(VENUES, "venues")],
        "attributes": [
            attribute(EVENTS, 1, "id", INT4, notnull=True), attribute(EVENTS, 2, "capacity", INT4, notnull=True),
            attribute(EVENTS, 3, "title", TEXT, coll=DEFAULT_COLL),
            attribute(EVENTS, 4, "code", VARCHAR, typmod=12, coll=DEFAULT_COLL),
            attribute(EVENTS, 5, "price", FLOAT8), attribute(EVENTS, 6, "venue_id", INT4),
            attribute(EVENTS, 7, "created", TIMESTAMPTZ, hasdef=True),
            attribute(EVENTS, 8, "slug", TEXT, hasdef=True, generated="s", coll=DEFAULT_COLL),
            attribute(VENUES, 1, "id", INT4, notnull=True),
            attribute(VENUES, 2, "name", TEXT, notnull=True, coll=DEFAULT_COLL),
        ],
        "constraints": [
            constraint(30001, "events_pkey", "p", EVENTS, [1], conindid=40001),
            constraint(30002, "events_code_key", "u", EVENTS, [4], conindid=40002),
            constraint(30003, "events_venue_fk", "f", EVENTS, [6], confrelid=VENUES, confkey=[1]),
            constraint(30004, "venues_pkey", "p", VENUES, [1], conindid=40003),
        ],
        "indexes": [index(40001, EVENTS, [1], primary=True), index(40002, EVENTS, [4]),
                    index(40003, VENUES, [1], primary=True), index(40004, EVENTS, [2], unique=False)],
        "triggers": [trigger(50001, EVENTS, "RI_ConstraintTrigger_c_1", internal=True, writes=None)],
    }


def column(field, pg_type, *, nullable=True, **extra):
    if pg_type in {"text", "varchar"}:
        extra.setdefault("collation", "deterministic")
    return {"field": field, "column": field, "pg_type": pg_type, "nullable": nullable, **extra}


def context(schema_digest):
    return {
        "context_version": 1, "application": "ticketing", "registry_digest": make_registry().digest,
        "schema_digest": schema_digest, "encoding": "UTF8",
        "entities": [
            {"entity": "event", "table": "events", "primary_key": ["id"], "key_assignment": "ascending_int",
             "unique": [{"fields": ["code"], "nulls": "distinct"}],
             "foreign_keys": [{"field": "venue_id", "references_entity": "venue", "references_field": "id"}],
             "columns": [column("id", "integer", nullable=False), column("capacity", "integer", nullable=False),
                         column("title", "text"), column("code", "varchar", max_length=8),
                         column("price", "double precision"), column("venue_id", "integer"),
                         column("created", "opaque", has_default=True), column("slug", "text", generated=True)]},
            {"entity": "venue", "table": "venues", "primary_key": ["id"],
             "columns": [column("id", "integer", nullable=False), column("name", "text", nullable=False)]},
        ],
    }


def digest_of(snap):
    return cv.snapshot_digest(cv.CatalogSnapshot.model_validate(snap))


def pin_context(config):
    return sp.TrustedPlanning(reviewer="schema_reviewer",
                              pinned_digest=sp.context_digest(sp.SchemaContextConfig.model_validate(config)))


def pin_snapshot(snap):
    return cv.TrustedSnapshot(exporter="catalog_exporter", source_label="synthetic_fixture", exported_on="2026-11-03",
                              pinned_digest=digest_of(snap))


def run(snap=None, ctx=None, policy=None):
    """Mutate the snapshot, point the context at the mutated snapshot, mutate the context, pin both, verify."""
    s = snapshot()
    if snap:
        snap(s)
    c = context(digest_of(s))
    if ctx:
        ctx(c)
    return cv.verify_context(c, registry=make_registry(), context_trust=pin_context(c), snapshot=s,
                             snapshot_trust=pin_snapshot(s), policy=policy)


def found(result):
    return {(f.kind, f.code) for f in result.findings}


def attr(s, rel, name):
    return next(a for a in s["attributes"] if a["attrelid"] == rel and a["attname"] == name)


def entity(c, name):
    return next(e for e in c["entities"] if e["entity"] == name)


def col(c, ent, field):
    return next(x for x in entity(c, ent)["columns"] if x["field"] == field)


# ------------------------------------------------------------------ a matching pair


class TestMatch:
    def test_identical_context_and_snapshot(self):
        result = run()
        assert result.outcome == "MATCH" and result.findings == ()
        assert result.scope == "consistent with the supplied catalog snapshot only; not a check of any live database"
        identity = result.snapshot
        assert identity.digest == "catsnap1:" + digest_of(snapshot())
        assert (identity.postgres_major, identity.database, identity.schema_name, identity.encoding) == (
            16, "ticketing_test", "public", "UTF8")
        assert (identity.exporter, identity.source_label, identity.exported_on) == (
            "catalog_exporter", "synthetic_fixture", "2026-11-03")
        assert result.checked_entities == ("event", "venue") and cv.TRIGGER_ASSUMPTION in result.assumptions

    def test_internal_disabled_and_replica_triggers_dont_fire(self):
        result = run(lambda s: s["triggers"].extend([
            trigger(50002, EVENTS, "off", enabled="D"), trigger(50003, EVENTS, "replica_only", enabled="R")]))
        assert result.outcome == "MATCH"

    def test_non_unique_indexes_are_ignored(self):
        assert run(lambda s: s["indexes"].append(index(40009, EVENTS, [3], unique=False))).outcome == "MATCH"


# ------------------------------------------------------------------ columns


def outcome(result):
    return result.outcome, found(result)


class TestColumns:
    @pytest.mark.parametrize("snap,code", [
        (lambda s: attr(s, EVENTS, "capacity").update(atttypid=INT8), "type"),
        (lambda s: attr(s, EVENTS, "title").update(attnotnull=True), "nullability"),
        (lambda s: attr(s, EVENTS, "code").update(atttypmod=20), "varchar_length"),
        (lambda s: attr(s, EVENTS, "created").update(atthasdef=False), "default"),
        (lambda s: attr(s, VENUES, "id").update(attidentity="a"), "generated"),
        (lambda s: attr(s, VENUES, "id").update(attidentity="d"), "default"),
        (lambda s: attr(s, EVENTS, "slug").update(attgenerated=""), "generated"),
        (lambda s: attr(s, EVENTS, "title").update(attcollation=CI_COLL), "collation"),
        (lambda s: s["database"].update(encoding="LATIN1"), "encoding"),
    ])
    def test_mismatches(self, snap, code):
        result = run(snap)
        assert result.outcome == "MISMATCH" and ("mismatch", code) in found(result)

    def test_nondeterministic_collation_matches_when_declared(self):
        result = run(lambda s: attr(s, EVENTS, "title").update(attcollation=CI_COLL),
                     lambda c: col(c, "event", "title").update(collation="nondeterministic"))
        assert result.outcome == "MATCH"

    def test_opaque_may_stand_for_a_known_type_only_by_policy(self):
        def opaque(c):
            col(c, "event", "capacity").update(pg_type="opaque")
        assert run(ctx=opaque).outcome == "MATCH"
        strict = cv.VerificationPolicy(allow_opaque_for_known_types=False)
        assert ("mismatch", "type") in found(run(ctx=opaque, policy=strict))

    def test_unbounded_varchar_and_domains_are_unsupported(self):
        assert outcome(run(lambda s: attr(s, EVENTS, "code").update(atttypmod=-1))) == (
            "UNSUPPORTED", {("unsupported", "unbounded_varchar")})
        domain = run(lambda s: attr(s, EVENTS, "capacity").update(atttypid=DOMAIN))
        assert outcome(domain) == ("UNSUPPORTED", {("unsupported", "domain_type")})
        described = run(lambda s: attr(s, EVENTS, "capacity").update(atttypid=DOMAIN),
                        lambda c: col(c, "event", "capacity").update(pg_type="opaque"))
        assert described.outcome == "MATCH"

    def test_missing_and_extra_attributes(self):
        def drop(s):
            s["attributes"].remove(attr(s, EVENTS, "price"))
        assert ("mismatch", "column_missing") in found(run(drop))
        partial = run(lambda s: (drop(s), s["coverage"].update(attributes=False)))
        assert partial.outcome == "INCOMPLETE"
        assert {("incomplete", "column_missing_unknown"), ("incomplete", "attribute_coverage")} <= found(partial)
        extra = run(lambda s: s["attributes"].append(attribute(EVENTS, 9, "notes", TEXT, coll=DEFAULT_COLL)))
        assert ("mismatch", "column_not_in_context") in found(extra)

    def test_dropped_columns_are_ignored(self):
        def dropped(s):
            s["attributes"].append({**attribute(EVENTS, 9, "........pg.dropped.9........", TEXT),
                                    "attisdropped": True})
        assert run(dropped).outcome == "MATCH"

    @pytest.mark.parametrize("section", ["relations", "types", "collations"])
    def test_a_match_needs_complete_coverage_even_when_lookups_succeed(self, section):
        result = run(lambda s: s["coverage"].update({section: False}))
        assert outcome(result) == ("INCOMPLETE", {("incomplete", f"{section}_coverage")})

    def test_missing_types_and_collations(self):
        assert ("mismatch", "collation_missing") in found(run(lambda s: attr(s, EVENTS, "title").update(
            attcollation=99999)))
        unknown = run(lambda s: (attr(s, EVENTS, "title").update(attcollation=99999),
                                 s["coverage"].update(collations=False)))
        assert outcome(unknown) == ("INCOMPLETE", {("incomplete", "collation_missing_unknown"),
                                                   ("incomplete", "collations_coverage")})
        assert ("mismatch", "type_missing") in found(run(lambda s: attr(s, EVENTS, "price").update(atttypid=99998)))


# ------------------------------------------------------------------ relations


class TestRelations:
    @pytest.mark.parametrize("flags,code", [
        ({"relrowsecurity": True}, "row_level_security"), ({"relhasrules": True}, "rewrite_rules"),
        ({"relispartition": True}, "partition"), ({"relhassubclass": True}, "inheritance"),
        ({"relkind": "v"}, "relation_kind"), ({"relkind": "p"}, "relation_kind"),
    ])
    def test_unsupported_relation_features(self, flags, code):
        result = run(lambda s: s["relations"][0].update(flags))
        assert result.outcome == "UNSUPPORTED" and ("unsupported", code) in found(result)

    def test_missing_relation(self):
        def drop(s):
            s["relations"] = [r for r in s["relations"] if r["oid"] != VENUES]
        assert ("mismatch", "relation_missing") in found(run(drop))
        partial = run(lambda s: (drop(s), s["coverage"].update(relations=False)))
        assert partial.outcome == "INCOMPLETE" and ("incomplete", "relation_missing_unknown") in found(partial)
        assert ("incomplete", "foreign_key_target_missing_unknown") in found(partial)
        assert not any(kind == "mismatch" for kind, _ in found(partial)), "an unresolved key isn't a missing key"

    def test_unlisted_relations_follow_policy(self):
        def add(s):
            s["relations"].append(relation(AUDITS, "audits"))
        assert run(add).outcome == "MATCH"
        strict = cv.VerificationPolicy(allow_unlisted_relations=False)
        assert outcome(run(add, policy=strict)) == ("UNSUPPORTED", {("unsupported", "unlisted_relation")})
        unknown = run(lambda s: s["coverage"].update(relations=False), policy=strict)
        assert ("incomplete", "unlisted_relations_unknown") in found(unknown) and unknown.outcome == "INCOMPLETE"

    def test_postgres_version_policy(self):
        assert outcome(run(lambda s: s.update(postgres_major=14))) == ("UNSUPPORTED", {("unsupported",
                                                                                         "postgres_version")})
        assert run(lambda s: s.update(postgres_major=15)).outcome == "MATCH"


# ------------------------------------------------------------------ keys and uniqueness


def unique_index(s):
    return next(i for i in s["indexes"] if i["indexrelid"] == 40002)


class TestUniqueness:
    def test_null_handling_must_agree(self):
        result = run(lambda s: unique_index(s).update(indnullsnotdistinct=True))
        assert result.outcome == "MISMATCH"
        assert {("mismatch", "unique_not_in_context"), ("mismatch", "unique_missing")} <= found(result)
        agreed = run(lambda s: unique_index(s).update(indnullsnotdistinct=True),
                     lambda c: entity(c, "event")["unique"][0].update(nulls="not_distinct"))
        assert agreed.outcome == "MATCH"

    def test_a_context_unique_constraint_the_catalog_lacks_is_a_mismatch(self):
        result = run(ctx=lambda c: entity(c, "event")["unique"].append({"fields": ["title"], "nulls": "distinct"}))
        assert outcome(result) == ("MISMATCH", {("mismatch", "unique_missing")})

    def test_composite_uniqueness(self):
        def add(s):
            s["indexes"].append(index(40005, EVENTS, [2, 3]))
        assert outcome(run(add)) == ("MISMATCH", {("mismatch", "unique_not_in_context")})
        declared = run(add, lambda c: entity(c, "event")["unique"].append(
            {"fields": ["title", "capacity"], "nulls": "distinct"}))
        assert declared.outcome == "MATCH"

    @pytest.mark.parametrize("flags,code", [
        ({"has_predicate": True}, "partial_unique_index"),
        ({"has_expressions": True, "indkey": [0]}, "expression_unique_index"),
        ({"indisvalid": False}, "invalid_unique_index"),
        ({"indisready": False}, "invalid_unique_index"),
        ({"indimmediate": False}, "deferrable_unique_index"),
        ({"nondefault_semantics": True}, "nondefault_unique_semantics"),
    ])
    def test_unsupported_unique_indexes(self, flags, code):
        result = run(lambda s: s["indexes"].append(index(40006, EVENTS, [3], **flags)))
        assert result.outcome == "UNSUPPORTED" and ("unsupported", code) in found(result)

    def test_include_columns_are_not_part_of_the_unique_key(self):
        result = run(lambda s: unique_index(s).update(indkey=[4, 3], indnkeyatts=1))
        assert result.outcome == "MATCH", found(result)
        widened = run(lambda s: unique_index(s).update(indkey=[4, 3], indnkeyatts=2))
        assert ("mismatch", "unique_missing") in found(widened)

    def test_an_unsupported_index_behind_a_declared_constraint_is_unsupported_not_missing(self):
        for flags in ({"indimmediate": False}, {"has_predicate": True}, {"indisvalid": False}):
            result = run(lambda s, flags=flags: unique_index(s).update(flags))
            assert result.outcome == "UNSUPPORTED", (flags, found(result))
            assert not any(kind == "mismatch" for kind, _ in found(result))

    @pytest.mark.parametrize("flags,code", [
        ({"has_predicate": True}, "partial_key_index"), ({"nondefault_semantics": True}, "nondefault_key_semantics"),
        ({"indisvalid": False}, "invalid_key_index"), ({"has_expressions": True}, "expression_key_index"),
    ])
    def test_primary_key_indexes_are_checked_like_unique_ones(self, flags, code):
        result = run(lambda s: s["indexes"][0].update(flags))
        assert outcome(result) == ("UNSUPPORTED", {("unsupported", code)})

    def test_primary_keys(self):
        assert ("mismatch", "primary_key") in found(run(lambda s: s["constraints"][0].update(conkey=[2])))
        deferrable = run(lambda s: s["constraints"][0].update(condeferrable=True))
        assert outcome(deferrable) == ("UNSUPPORTED", {("unsupported", "deferrable_key")})
        missing = run(lambda s: s["constraints"].pop(0))
        assert ("mismatch", "primary_key") in found(missing)
        unknown = run(lambda s: (s["constraints"].pop(0), s["coverage"].update(constraints=False)))
        assert unknown.outcome == "INCOMPLETE" and ("incomplete", "primary_key_unknown") in found(unknown)

    @pytest.mark.parametrize("kind,code", [("c", "check_constraint"), ("x", "exclusion_constraint"),
                                           ("t", "constraint_trigger")])
    def test_constraints_the_planner_cant_evaluate(self, kind, code):
        result = run(lambda s: s["constraints"].append(constraint(30009, "events_more", kind, EVENTS, [2])))
        assert outcome(result) == ("UNSUPPORTED", {("unsupported", code)})

    def test_an_absent_list_entry_is_never_evidence_of_absence(self):
        result = run(lambda s: s["coverage"].update(constraints=False, indexes=False))
        assert result.outcome == "INCOMPLETE"
        assert {("incomplete", "constraint_coverage"), ("incomplete", "index_coverage")} <= found(result)


class TestForeignKeys:
    def test_missing_and_extra_foreign_keys(self):
        missing = run(lambda s: s["constraints"].pop(2))
        assert outcome(missing) == ("MISMATCH", {("mismatch", "foreign_key_missing")})
        extra = run(lambda s: s["constraints"].append(
            constraint(30010, "events_cap_fk", "f", EVENTS, [2], confrelid=VENUES, confkey=[1])))
        assert outcome(extra) == ("MISMATCH", {("mismatch", "foreign_key_not_in_context")})

    def test_unsupported_foreign_keys(self):
        composite = run(lambda s: s["constraints"].append(
            constraint(30011, "events_two_fk", "f", EVENTS, [2, 6], confrelid=VENUES, confkey=[1, 2])))
        assert ("unsupported", "composite_foreign_key") in found(composite)
        deferrable = run(lambda s: s["constraints"][2].update(condeferrable=True))
        assert outcome(deferrable) == ("UNSUPPORTED", {("unsupported", "deferrable_foreign_key")})
        replaced = run(lambda s: s["constraints"][2].update(conkey=[6, 2], confkey=[1, 2]))
        assert outcome(replaced) == ("UNSUPPORTED", {("unsupported", "composite_foreign_key")})

        def undescribed(s):
            s["relations"].append(relation(AUDITS, "audits"))
            s["constraints"].append(constraint(30012, "events_audit_fk", "f", EVENTS, [2], confrelid=AUDITS,
                                               confkey=[1]))
        assert ("unsupported", "foreign_key_to_undescribed") in found(run(undescribed))


# ------------------------------------------------------------------ triggers


class TestTriggers:
    def test_an_unrepresented_insert_trigger(self):
        result = run(lambda s: s["triggers"].append(trigger(50004, EVENTS, "stamp")))
        assert outcome(result) == ("MISMATCH", {("mismatch", "insert_triggers")})
        declared = run(lambda s: s["triggers"].append(trigger(50004, EVENTS, "stamp")),
                       lambda c: entity(c, "event").update(has_insert_triggers=True))
        assert declared.outcome == "MATCH"

    def test_a_trigger_writing_another_table(self):
        result = run(lambda s: s["triggers"].append(trigger(50005, EVENTS, "copy", writes=["venues"])),
                     lambda c: entity(c, "event").update(has_insert_triggers=True))
        assert outcome(result) == ("MISMATCH", {("mismatch", "trigger_written")})
        declared = run(lambda s: s["triggers"].append(trigger(50005, EVENTS, "copy", writes=["venues"])),
                       lambda c: (entity(c, "event").update(has_insert_triggers=True),
                                  entity(c, "venue").update(trigger_written=True)))
        assert declared.outcome == "MATCH"

    def test_unknown_trigger_effects_are_incomplete(self):
        result = run(lambda s: s["triggers"].append(trigger(50006, EVENTS, "dynamic", writes=None)),
                     lambda c: entity(c, "event").update(has_insert_triggers=True))
        assert result.outcome == "INCOMPLETE" and ("incomplete", "trigger_effect_unknown") in found(result)

    def test_trigger_coverage(self):
        schema_only = run(lambda s: s["coverage"].update(triggers="schema"))
        assert outcome(schema_only) == ("INCOMPLETE", {("incomplete", "trigger_writers_unknown")})
        none = run(lambda s: s["coverage"].update(triggers="none"))
        assert outcome(none) == ("INCOMPLETE", {("incomplete", "trigger_coverage")})


# ------------------------------------------------------------------ trust, malformed input and precedence


class TestTrust:
    def test_forged_or_stale_snapshot_pins(self):
        s = snapshot()
        c = context(digest_of(s))
        args = {"registry": make_registry(), "context_trust": pin_context(c)}
        forged = cv.TrustedSnapshot(exporter="x", source_label="y", exported_on="2026-11-03", pinned_digest="0" * 64)
        with pytest.raises(cv.UntrustedInput, match="pinned"):
            cv.verify_context(c, snapshot=s, snapshot_trust=forged, **args)
        stale = pin_snapshot(s)
        s["database"]["datname"] = "other_db"
        with pytest.raises(cv.UntrustedInput, match="pinned"):
            cv.verify_context(c, snapshot=s, snapshot_trust=stale, **args)

    @pytest.mark.parametrize("trust", [None, {"exporter": "x"}, "pin", cv.TrustedSnapshot.model_construct(
        exporter="Bad Name!", source_label="y", exported_on="2026-11-03", pinned_digest="0" * 64)])
    def test_snapshot_trust_comes_only_from_a_harness_object(self, trust):
        s = snapshot()
        c = context(digest_of(s))
        with pytest.raises(cv.UntrustedInput, match="TrustedSnapshot"):
            cv.verify_context(c, registry=make_registry(), context_trust=pin_context(c), snapshot=s,
                              snapshot_trust=trust)

    def test_the_context_must_verify(self):
        s = snapshot()
        c = context(digest_of(s))
        with pytest.raises(cv.UntrustedInput, match="schema context"):
            cv.verify_context(c, registry=make_registry(),
                              context_trust=sp.TrustedPlanning(reviewer="r", pinned_digest="0" * 64),
                              snapshot=s, snapshot_trust=pin_snapshot(s))

    def test_the_context_must_name_this_snapshot(self):
        result = run(ctx=lambda c: c.update(schema_digest="7" * 64))
        assert outcome(result) == ("MISMATCH", {("mismatch", "schema_digest")})

    def test_snapshot_content_cant_claim_trust(self):
        s = snapshot()
        s["trusted"] = True
        c = context("0" * 64)
        with pytest.raises(cv.SnapshotError, match="Extra inputs"):
            cv.verify_context(c, registry=make_registry(), context_trust=pin_context(c), snapshot=s,
                              snapshot_trust=cv.TrustedSnapshot(exporter="x", source_label="y",
                                                                exported_on="2026-11-03", pinned_digest="0" * 64))

    def test_wrong_types(self):
        s = snapshot()
        c = context(digest_of(s))
        common = {"context_trust": pin_context(c), "snapshot": s, "snapshot_trust": pin_snapshot(s)}
        with pytest.raises(TypeError):
            cv.verify_context(c, registry=make_registry().config, **common)
        with pytest.raises(TypeError):
            cv.verify_context(c, registry=make_registry(), policy={"allow_unlisted_relations": True}, **common)
        with pytest.raises(ValueError):
            cv.VerificationPolicy(supported_major_versions=[])


class TestMalformed:
    @pytest.mark.parametrize("mutate,phrase", [
        (lambda s: s["relations"].append(relation(EVENTS, "again")), "repeated oid"),
        (lambda s: s["attributes"].append(attribute(EVENTS, 1, "other", INT4)), "repeated attnum"),
        (lambda s: s["attributes"].append(attribute(EVENTS, 9, "title", TEXT)), "repeated column name"),
        (lambda s: s["attributes"][0].update(attnum=0), "attnum"),
        (lambda s: s["constraints"][0].update(contype="z"), "contype"),
        (lambda s: s["constraints"][0].update(conkey=[-1]), "conkey"),
        (lambda s: s["constraints"][2].update(confrelid=0), "malformed"),
        (lambda s: s["constraints"][2].update(confkey=[]), "malformed"),
        (lambda s: s["constraints"][0].update(condeferred=True), "deferred but not deferrable"),
        (lambda s: s["indexes"][0].update(indisunique=False), "must be unique"),
        (lambda s: s["attributes"][0].pop("atthasdef"), "atthasdef"),
        (lambda s: s["relations"][0].update(relname="events‮"), "relname"),
        (lambda s: s.update(export_format_version=2), "export_format_version"),
        (lambda s: (s.update(postgres_major=14), s["indexes"][1].update(indnullsnotdistinct=True)),
         "PostgreSQL 15"),
        (lambda s: s["triggers"][0].update(events=[]), "events"),
        (lambda s: s["database"].update(encoding="utf-8"), "encoding"),
        (lambda s: s["indexes"][1].update(indnkeyatts=3), "indnkeyatts"),
    ])
    def test_malformed_snapshots_are_refused(self, mutate, phrase):
        s = snapshot()
        mutate(s)
        c = context("0" * 64)
        trust = cv.TrustedSnapshot(exporter="x", source_label="y", exported_on="2026-11-03", pinned_digest="0" * 64)
        with pytest.raises(cv.SnapshotError) as caught:
            cv.verify_context(c, registry=make_registry(), context_trust=pin_context(c), snapshot=s,
                              snapshot_trust=trust)
        assert phrase in str(caught.value)


class TestPrecedenceAndDeterminism:
    def test_mismatch_outranks_unsupported_and_incomplete(self):
        result = run(lambda s: (attr(s, EVENTS, "title").update(attnotnull=True),
                                s["relations"][0].update(relrowsecurity=True),
                                s["coverage"].update(triggers="schema")))
        assert result.outcome == "MISMATCH" and {f.kind for f in result.findings} == {"mismatch", "unsupported",
                                                                                         "incomplete"}
        lower = run(lambda s: (s["relations"][0].update(relrowsecurity=True), s["coverage"].update(triggers="schema")))
        assert lower.outcome == "UNSUPPORTED"

    def test_identical_inputs_give_identical_results(self):
        assert len({run().model_dump_json() for _ in range(5)}) == 1

    def test_list_order_doesnt_matter(self):
        s = snapshot()
        shuffled = copy(s)
        rng = random.Random(7)
        for name in ("types", "relations", "attributes", "constraints", "indexes", "namespaces", "collations"):
            rng.shuffle(shuffled[name])
        assert digest_of(shuffled) == digest_of(s)
        c = context(digest_of(s))
        a = cv.verify_context(c, registry=make_registry(), context_trust=pin_context(c), snapshot=s,
                              snapshot_trust=pin_snapshot(s))
        b = cv.verify_context(c, registry=make_registry(), context_trust=pin_context(c), snapshot=shuffled,
                              snapshot_trust=pin_snapshot(shuffled))
        assert a == b

    def test_digest_is_pinned(self):
        """Changing the snapshot encoding changes every digest, so it must be a deliberate, versioned change."""
        assert digest_of(snapshot()) == PINNED_SNAPSHOT

    def test_digest_is_domain_separated_and_covers_content(self):
        s = snapshot()
        changed = copy(s)
        changed["database"]["datctype"] = "en_US.UTF-8"
        assert digest_of(changed) != digest_of(s)
        import hashlib

        plain = json.dumps(cv.CatalogSnapshot.model_validate(s).model_dump(mode="json"), sort_keys=True,
                           separators=(",", ":"))
        assert digest_of(s) != hashlib.sha256(plain.encode()).hexdigest()

    def test_findings_are_sorted(self):
        # The context lists title before code, so unsorted findings would come out in the other order.
        result = run(lambda s: (attr(s, EVENTS, "title").update(attnotnull=True),
                                attr(s, EVENTS, "code").update(atttypmod=20)))
        assert [f.field for f in result.findings] == ["code", "title"]
        keys = [(f.entity or "", f.field or "", f.kind, f.code, f.detail) for f in result.findings]
        assert keys == sorted(keys)


class TestIsolation:
    def test_imports_no_database_engine_oracle_or_phase_9b(self):
        code = ("import sys, deployspecter.live.catalog_verification;"
                "print(sorted(m for m in sys.modules if m.startswith(('psycopg', 'langgraph', 'live_app',"
                " 'deployspecter.live.runner', 'deployspecter.live.driver', 'deployspecter.live.supervisor',"
                " 'deployspecter.database', 'deployspecter.oracle', 'deployspecter.scenarios',"
                " 'deployspecter.state_constructor', 'deployspecter.seed_feasibility', 'deployspecter.triggers',"
                " 'deployspecter.meta_client'))))")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "[]"

    def test_no_dynamic_code_files_or_sql(self):
        tree = ast.parse(Path(cv.__file__).read_text())
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "input"}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "socket", "psycopg", "pathlib", "urllib", "sqlite3"}
        strings = " ".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        for keyword in ("SELECT ", "INSERT INTO", "FROM pg_", "CREATE ", "DROP "):
            assert keyword not in strings

    def test_no_network_or_database(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no network or database access is allowed here")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        assert run().outcome == "MATCH"

    def test_inputs_are_not_modified(self):
        s = snapshot()
        c = context(digest_of(s))
        before = json.dumps([s, c], sort_keys=True)
        cv.verify_context(c, registry=make_registry(), context_trust=pin_context(c), snapshot=s,
                          snapshot_trust=pin_snapshot(s))
        assert json.dumps([s, c], sort_keys=True) == before


PINNED_SNAPSHOT = "37335827dd9e118073ce536e190e1337fd695c69b1a82ecaaf268dd5896a7405"
