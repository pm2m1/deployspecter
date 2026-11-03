"""Track A's parent-side catalog export, independent re-read, and pin construction. Research-only; never run yet.

Privilege boundary, stated plainly: the exporter runs in Gate 2A's parent on its admin connection, which is a
superuser on the disposable cluster. Its transaction is ``REPEATABLE READ READ ONLY``, but a read-only transaction is
not a privilege boundary: a superuser session can change it, and nothing in PostgreSQL stops this code from issuing
a write. What limits it is only (1) the fixed statement list below, executed by this module and nothing else, and
(2) reconciling the server log against that list. That is weaker than a role without write privileges.

Nothing here opens a connection. ``export_snapshot`` and ``cross_check_read`` take a connection-like object, so the
offline tests drive them with a recording fake. Real execution waits for approval (OP-2).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from deployspecter.live import catalog_verification as cv
from deployspecter.live import registry as rg
from deployspecter.live import state_planning as sp

EXPORTER = "track_a_parent_exporter"
SOURCE_LABEL = "supervised_parent_export"
SCHEMA = "ds_run"
EXPORT_FORMAT_VERSION = 1
EXPECTED_CLUSTER = "ds_live_disposable"
TABLE_LIKE = "('r', 'p', 'v', 'm', 'f')"

SCOPE_RELS = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
              f"WHERE n.nspname = '{SCHEMA}' AND c.relkind IN {TABLE_LIKE}")
SCOPE_ATTS = ("SELECT a.atttypid, a.attcollation FROM pg_attribute a WHERE a.attnum > 0 "
              f"AND a.attrelid IN ({SCOPE_RELS})")

# Fixed and reviewed. The schema name is a constant in the text; no statement takes a parameter.
EXPORT_QUERIES: tuple[tuple[str, str], ...] = (
    ("begin", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"),
    ("metadata", "SELECT current_database()::text, current_setting('server_version_num')::int, "
                 "current_setting('cluster_name')::text, current_setting('session_replication_role')::text, "
                 "pg_postmaster_start_time()::text, (SELECT system_identifier::text FROM pg_control_system())"),
    ("database", "SELECT d.datname::text, pg_encoding_to_char(d.encoding)::text, d.datlocprovider::text, "
                 "d.datcollate::text, d.datctype::text FROM pg_database d WHERE d.datname = current_database()"),
    ("relations", "SELECT c.oid::bigint, c.relname::text, n.nspname::text, c.relkind::text, c.relispartition, "
                  "c.relrowsecurity, c.relhasrules, c.relhassubclass FROM pg_class c "
                  f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '{SCHEMA}' ORDER BY c.oid"),
    ("attributes", "SELECT a.attrelid::bigint, a.attnum::int, a.attname::text, a.atttypid::bigint, a.atttypmod, "
                   "a.attnotnull, a.atthasdef, a.attidentity::text, a.attgenerated::text, a.attisdropped, "
                   f"a.attcollation::bigint FROM pg_attribute a WHERE a.attnum > 0 AND a.attrelid IN ({SCOPE_RELS}) "
                   "ORDER BY a.attrelid, a.attnum"),
    ("types", "SELECT t.oid::bigint, t.typname::text, n.nspname::text, t.typtype::text FROM pg_type t "
              f"JOIN pg_namespace n ON n.oid = t.typnamespace WHERE t.oid IN (SELECT atttypid FROM ({SCOPE_ATTS}) s) "
              "ORDER BY t.oid"),
    ("collations", "SELECT c.oid::bigint, c.collname::text, n.nspname::text, c.collprovider::text, "
                   "c.collisdeterministic FROM pg_collation c JOIN pg_namespace n ON n.oid = c.collnamespace "
                   f"WHERE c.oid = 100 OR c.oid IN (SELECT attcollation FROM ({SCOPE_ATTS}) s) ORDER BY c.oid"),
    ("namespaces", "SELECT n.oid::bigint, n.nspname::text FROM pg_namespace n "
                   f"WHERE n.nspname IN ('{SCHEMA}', 'pg_catalog') ORDER BY n.oid"),
    ("constraints", "SELECT k.oid::bigint, k.conname::text, k.contype::text, k.conrelid::bigint, "
                    "coalesce(k.conkey::int[], '{}'), k.confrelid::bigint, coalesce(k.confkey::int[], '{}'), "
                    "k.condeferrable, k.condeferred, k.convalidated, k.conindid::bigint FROM pg_constraint k "
                    f"WHERE k.conrelid IN ({SCOPE_RELS}) ORDER BY k.oid"),
    ("indexes", "SELECT i.indexrelid::bigint, i.indrelid::bigint, string_to_array(i.indkey::text, ' ')::int[], "
                "i.indnkeyatts::int, i.indisunique, i.indisprimary, i.indisvalid, i.indisready, i.indimmediate, "
                "i.indnullsnotdistinct, i.indexprs IS NOT NULL, i.indpred IS NOT NULL, "
                "EXISTS (SELECT 1 FROM generate_series(0, i.indnkeyatts - 1) s(k) "
                "JOIN pg_opclass o ON o.oid = i.indclass[s.k] WHERE NOT o.opcdefault) OR "
                "EXISTS (SELECT 1 FROM generate_series(0, i.indnkeyatts - 1) s(k) JOIN pg_attribute a "
                "ON a.attrelid = i.indrelid AND a.attnum = i.indkey[s.k] WHERE i.indcollation[s.k] <> a.attcollation) "
                f"FROM pg_index i WHERE i.indrelid IN ({SCOPE_RELS}) ORDER BY i.indexrelid"),
    ("triggers", "SELECT t.oid::bigint, t.tgrelid::bigint, t.tgname::text, t.tgenabled::text, t.tgisinternal, "
                 "t.tgtype::int FROM pg_trigger t ORDER BY t.oid"),
    ("commit", "COMMIT"),
)

# A second path through different catalog views, on a separate connection and transaction.
CROSS_CHECK_QUERIES: tuple[tuple[str, str], ...] = (
    ("begin", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"),
    ("tables", "SELECT table_name::text, table_type::text FROM information_schema.tables "
               f"WHERE table_schema = '{SCHEMA}' "
               "ORDER BY 1"),
    ("columns", "SELECT table_name::text, column_name::text, ordinal_position::int, data_type::text, "
                "is_nullable::text, column_default IS NOT NULL, is_identity::text, is_generated::text "
                f"FROM information_schema.columns WHERE table_schema = '{SCHEMA}' ORDER BY 1, 3"),
    ("keys", "SELECT tc.table_name::text, tc.constraint_name::text, tc.constraint_type::text, kcu.column_name::text, "
             "kcu.ordinal_position::int FROM information_schema.table_constraints tc "
             "JOIN information_schema.key_column_usage kcu ON kcu.constraint_schema = tc.constraint_schema "
             "AND kcu.constraint_name = tc.constraint_name AND kcu.table_name = tc.table_name "
             f"WHERE tc.table_schema = '{SCHEMA}' ORDER BY 1, 2, 5"),
    ("references", "SELECT rc.constraint_name::text, ccu.table_name::text, ccu.column_name::text "
                   "FROM information_schema.referential_constraints rc "
                   "JOIN information_schema.constraint_column_usage ccu "
                   "ON ccu.constraint_schema = rc.unique_constraint_schema "
                   "AND ccu.constraint_name = rc.unique_constraint_name "
                   f"WHERE rc.constraint_schema = '{SCHEMA}' ORDER BY 1, 3"),
    ("user_triggers", "SELECT trigger_name::text, event_object_table::text, event_manipulation::text, "
                      f"action_timing::text FROM information_schema.triggers WHERE trigger_schema = '{SCHEMA}' "
                      "ORDER BY 1, 3"),
    ("commit", "COMMIT"),
)


def query_set_digest(queries: tuple[tuple[str, str], ...]) -> str:
    return hashlib.sha256(json.dumps(queries).encode()).hexdigest()


EXPORT_QUERY_SET_SHA256 = query_set_digest(EXPORT_QUERIES)
CROSS_CHECK_QUERY_SET_SHA256 = query_set_digest(CROSS_CHECK_QUERIES)
INFORMATION_SCHEMA_TYPES = {"integer": "int4", "bigint": "int8", "smallint": "int2", "text": "text",
                            "character varying": "varchar", "double precision": "float8", "boolean": "bool",
                            "numeric": "numeric"}


class ExportError(ValueError):
    """The catalog read is inconsistent, unknown or incomplete. Nothing is pinned."""


@dataclass
class ExportResult:
    snapshot: dict[str, Any]
    digest: str
    identity: dict[str, Any]
    statements: list[str] = field(default_factory=list)


def _run(conn: Any, queries: tuple[tuple[str, str], ...]) -> tuple[dict[str, list[tuple]], list[str]]:
    """Execute exactly the fixed list, in order, and nothing else. Any error is raised, never swallowed."""
    raw: dict[str, list[tuple]] = {}
    statements: list[str] = []
    for section, text in queries:
        statements.append(text)
        cursor = conn.execute(text)
        if section not in {"begin", "commit"}:
            raw[section] = [tuple(r) for r in cursor.fetchall()]
    return raw, statements


def decode_tgtype(tgtype: int) -> tuple[str, str, list[str]]:
    if tgtype & ~0x7F:
        raise ExportError(f"tgtype {tgtype} has bits this exporter doesn't know")
    timing = "INSTEAD OF" if tgtype & 64 else ("BEFORE" if tgtype & 2 else "AFTER")
    level = "ROW" if tgtype & 1 else "STATEMENT"
    events = [name for bit, name in ((4, "INSERT"), (8, "DELETE"), (16, "UPDATE"), (32, "TRUNCATE")) if tgtype & bit]
    if not events:
        raise ExportError(f"tgtype {tgtype} names no event")
    return timing, level, events


def _expect(rows: list[tuple], width: int, section: str) -> None:
    for row in rows:
        if len(row) != width:
            raise ExportError(f"{section}: a row has {len(row)} columns, not {width}")


def assemble(raw: dict[str, list[tuple]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Raw rows to a ``catsnap1`` snapshot and identity evidence. Refuses anything inconsistent or unknown."""
    for section, width in (("metadata", 6), ("database", 5), ("relations", 8), ("attributes", 11), ("types", 4),
                           ("collations", 5), ("namespaces", 2), ("constraints", 11), ("indexes", 13),
                           ("triggers", 6)):
        if section not in raw:
            raise ExportError(f"{section}: not read")
        _expect(raw[section], width, section)
    if len(raw["metadata"]) != 1 or len(raw["database"]) != 1:
        raise ExportError("metadata and database must be exactly one row each")
    dbname, version_num, cluster, replication_role, started, system_id = raw["metadata"][0]
    identity = {"database": dbname, "server_version_num": version_num, "cluster_name": cluster,
                "session_replication_role": replication_role, "postmaster_start_time": started,
                "system_identifier": system_id}
    relations = [dict(zip(("oid", "relname", "nspname", "relkind", "relispartition", "relrowsecurity", "relhasrules",
                           "relhassubclass"), r, strict=True)) for r in raw["relations"]]
    in_scope = {r["oid"] for r in relations if r["relkind"] in {"r", "p", "v", "m", "f"}}
    attributes = [dict(zip(("attrelid", "attnum", "attname", "atttypid", "atttypmod", "attnotnull", "atthasdef",
                            "attidentity", "attgenerated", "attisdropped", "attcollation"), r, strict=True))
                  for r in raw["attributes"]]
    types = [dict(zip(("oid", "typname", "nspname", "typtype"), r, strict=True)) for r in raw["types"]]
    collations = [dict(zip(("oid", "collname", "nspname", "collprovider", "collisdeterministic"), r, strict=True))
                  for r in raw["collations"]]
    constraints = [dict(zip(("oid", "conname", "contype", "conrelid", "conkey", "confrelid", "confkey",
                             "condeferrable", "condeferred", "convalidated", "conindid"), r, strict=True))
                   for r in raw["constraints"]]
    indexes = [dict(zip(("indexrelid", "indrelid", "indkey", "indnkeyatts", "indisunique", "indisprimary",
                         "indisvalid", "indisready", "indimmediate", "indnullsnotdistinct", "has_expressions",
                         "has_predicate", "nondefault_semantics"), r, strict=True)) for r in raw["indexes"]]
    triggers = []
    for oid, tgrelid, tgname, tgenabled, internal, tgtype in raw["triggers"]:
        timing, level, events = decode_tgtype(tgtype)
        triggers.append({"oid": oid, "tgrelid": tgrelid, "tgname": tgname, "tgenabled": tgenabled,
                         "tgisinternal": internal, "timing": timing, "level": level, "events": events,
                         "writes": None})  # no reviewed write analysis exists, so every write set is unknown

    type_oids, coll_oids = {t["oid"] for t in types}, {c["oid"] for c in collations}
    for a in attributes:
        if a["attrelid"] not in in_scope:
            raise ExportError(f"attribute {a['attname']} belongs to no listed table")
        if not a["attisdropped"] and a["atttypid"] not in type_oids:
            raise ExportError(f"attribute {a['attname']} has an unlisted type")
        if a["attcollation"] and a["attcollation"] not in coll_oids:
            raise ExportError(f"attribute {a['attname']} has an unlisted collation")
    for k in constraints:
        if k["conrelid"] not in in_scope:
            raise ExportError(f"constraint {k['conname']} belongs to no listed table")
    index_oids = {i["indexrelid"] for i in indexes}
    for k in constraints:
        if k["contype"] in {"p", "u"} and k["conindid"] not in index_oids:
            raise ExportError(f"key constraint {k['conname']} names an unlisted index")
    namespaces = [{"oid": o, "nspname": n} for o, n in raw["namespaces"]]
    if SCHEMA not in {n["nspname"] for n in namespaces}:
        raise ExportError(f"schema {SCHEMA} isn't listed")
    snapshot = {
        "export_format_version": EXPORT_FORMAT_VERSION, "postgres_major": version_num // 10000,
        "schema_name": SCHEMA,
        "database": dict(zip(("datname", "encoding", "datlocprovider", "datcollate", "datctype"), raw["database"][0],
                             strict=True)),
        "coverage": {"relations": True, "attributes": True, "types": True, "collations": True, "constraints": True,
                     "indexes": True, "triggers": "database"},
        "namespaces": namespaces, "types": types, "collations": collations, "relations": relations,
        "attributes": attributes, "constraints": constraints, "indexes": indexes, "triggers": triggers,
    }
    try:
        cv.CatalogSnapshot.model_validate(snapshot)
    except ValueError as exc:
        raise ExportError(f"the snapshot isn't a valid catsnap1 document: {str(exc)[:300]}") from exc
    return snapshot, identity


def export_snapshot(conn: Any) -> ExportResult:
    raw, statements = _run(conn, EXPORT_QUERIES)
    snapshot, identity = assemble(raw)
    digest = cv.snapshot_digest(cv.CatalogSnapshot.model_validate(snapshot))
    return ExportResult(snapshot, digest, identity, statements)


@dataclass
class CrossCheck:
    agrees: bool
    problems: list[str]
    statements: list[str]


def cross_check_read(conn: Any, snapshot: dict[str, Any]) -> CrossCheck:
    raw, statements = _run(conn, CROSS_CHECK_QUERIES)
    problems = compare_cross_check(raw, snapshot)
    return CrossCheck(not problems, problems, statements)


def compare_cross_check(raw: dict[str, list[tuple]], snapshot: dict[str, Any]) -> list[str]:
    """The information_schema view must agree with the pg_catalog snapshot on every fact both can show."""
    problems: list[str] = []
    rels = {r["oid"]: r for r in snapshot["relations"]}
    tables = {r["relname"] for r in snapshot["relations"] if r["relkind"] in {"r", "p"}}
    seen = {name for name, kind in raw.get("tables", []) if kind == "BASE TABLE"}
    if seen != tables:
        problems.append(f"tables differ: catalog {sorted(tables)}, information_schema {sorted(seen)}")
    type_names = {t["oid"]: t["typname"] for t in snapshot["types"]}
    live = {(rels[a["attrelid"]]["relname"], a["attname"]): a for a in snapshot["attributes"]
            if not a["attisdropped"] and rels[a["attrelid"]]["relkind"] in {"r", "p"}}
    columns = {(t, c): rest for t, c, *rest in raw.get("columns", [])}
    if set(columns) != set(live):
        problems.append("column sets differ")
    for key in sorted(set(columns) & set(live)):
        _pos, data_type, is_nullable, has_default, is_identity, is_generated = columns[key]
        a = live[key]
        mapped = INFORMATION_SCHEMA_TYPES.get(data_type)
        if mapped is None:
            problems.append(f"{key}: data_type {data_type!r} has no reviewed mapping")
        elif mapped != type_names.get(a["atttypid"]):
            problems.append(f"{key}: type differs")
        if (is_nullable == "NO") != a["attnotnull"]:
            problems.append(f"{key}: nullability differs")
        generated = is_generated == "ALWAYS"
        if generated != (a["attgenerated"] == "s") or (is_identity == "YES") != (a["attidentity"] != ""):
            problems.append(f"{key}: generated or identity differs")
        if not generated and has_default != a["atthasdef"]:
            problems.append(f"{key}: default differs")
    by_attnum = {(a["attrelid"], a["attnum"]): a["attname"] for a in snapshot["attributes"]}
    catalog_keys = {}
    for k in snapshot["constraints"]:
        if k["contype"] in {"p", "u", "f"}:
            kind = {"p": "PRIMARY KEY", "u": "UNIQUE", "f": "FOREIGN KEY"}[k["contype"]]
            catalog_keys[(rels[k["conrelid"]]["relname"], k["conname"], kind)] = [
                by_attnum[(k["conrelid"], n)] for n in k["conkey"]]
    schema_keys: dict[tuple[str, str, str], list[str]] = {}
    for table, name, kind, column, _pos in raw.get("keys", []):
        schema_keys.setdefault((table, name, kind), []).append(column)
    if catalog_keys != schema_keys:
        problems.append("key constraints differ")
    catalog_refs = sorted((k["conname"], rels[k["confrelid"]]["relname"], by_attnum[(k["confrelid"], n)])
                          for k in snapshot["constraints"] if k["contype"] == "f" for n in k["confkey"]
                          if k["confrelid"] in rels)
    if catalog_refs != sorted(tuple(r) for r in raw.get("references", [])):
        problems.append("foreign-key targets differ")
    user = sorted((t["tgname"], rels[t["tgrelid"]]["relname"]) for t in snapshot["triggers"]
                  if not t["tgisinternal"] and t["tgrelid"] in rels)
    if user != sorted({(n, tbl) for n, tbl, _e, _t in raw.get("user_triggers", [])}):
        problems.append("user triggers differ")
    return problems


@dataclass(frozen=True)
class Attestation:
    """A reviewer retyping the digest they reviewed. A pin needs this; the exporter can't supply it itself."""

    reviewer: str
    snapshot_digest: str
    exported_on: str


def pin_from_evidence(first: ExportResult, second: ExportResult, cross: CrossCheck, attestation: Attestation,
                      *, expected_major: int = 16) -> cv.TrustedSnapshot:
    """A pinned snapshot only from two agreeing exports, an agreeing cross-check, the right cluster and a reviewer."""
    problems = []
    if first.digest != second.digest:
        problems.append("the two exports differ")
    if first.identity.get("system_identifier") != second.identity.get("system_identifier") or \
            first.identity.get("postmaster_start_time") != second.identity.get("postmaster_start_time"):
        problems.append("the exports came from different cluster lifetimes")
    if not cross.agrees:
        problems.append(f"the independent re-read disagrees: {cross.problems}")
    if first.identity.get("cluster_name") != EXPECTED_CLUSTER:
        problems.append("not the disposable cluster")
    if not str(first.identity.get("database", "")).endswith("_test"):
        problems.append("not a test database")
    if first.identity.get("session_replication_role") != "origin":
        problems.append("session_replication_role isn't origin")
    if first.snapshot.get("postgres_major") != expected_major:
        problems.append("unexpected PostgreSQL major version")
    if first.statements != [t for _, t in EXPORT_QUERIES] or cross.statements != [t for _, t in CROSS_CHECK_QUERIES]:
        problems.append("a statement list differs from the reviewed one")
    if attestation.snapshot_digest != first.digest or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", attestation.reviewer):
        problems.append("the reviewer's attestation doesn't name this digest")
    if problems:
        raise ExportError("; ".join(problems))
    return cv.TrustedSnapshot(exporter=EXPORTER, source_label=SOURCE_LABEL, exported_on=attestation.exported_on,
                              pinned_digest=first.digest)


def pin_context(context: dict[str, Any], *, registry: rg.Registry, snapshot: dict[str, Any],
                snapshot_trust: cv.TrustedSnapshot, reviewer: str) -> tuple[sp.TrustedPlanning, Any]:
    """Pin a context to a pinned snapshot, and require MATCH. Anything else refuses."""
    if snapshot_trust.source_label != SOURCE_LABEL:
        raise ExportError("only a supervised parent export can back a context pin")
    if context.get("schema_digest") != snapshot_trust.pinned_digest:
        raise ExportError("the context names a different snapshot")
    trust = sp.TrustedPlanning(reviewer=reviewer, pinned_digest=sp.context_digest(
        sp.SchemaContextConfig.model_validate(context)))
    result = cv.verify_context(context, registry=registry, context_trust=trust, snapshot=snapshot,
                               snapshot_trust=snapshot_trust)
    if result.outcome != "MATCH":
        raise ExportError(f"context verification gave {result.outcome}: {[f.code for f in result.findings][:5]}")
    return trust, result
