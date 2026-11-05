"""EXPERIMENTAL read-only catalog export of the disposable MVP database, in ``catalog_verification.CatalogSnapshot``'s
format, so the unmodified ``verify_context`` can check the planner's trusted context against the live PostgreSQL
catalog instead of Track A's hand-written dry-run snapshot.

* Only reads ``pg_catalog``, in one REPEATABLE READ READ ONLY transaction, on the harness's admin connection to the
  disposable target (``harness.Target`` refuses anything else). It is not Gate 2A's supervised exporter and its output
  is not a pinned Gate 2A snapshot.
* Coverage it claims, and why it can: relations, attributes, types, collations, constraints and indexes for every
  relation of the schema that the planner can describe (tables, partitioned tables, views, materialized views and
  foreign tables); triggers for the whole **database**, because a trigger elsewhere could write into this schema.
  A trigger's ``writes`` is always ``None`` (unknown): this exporter doesn't analyse trigger functions, so it never
  claims a trigger writes nothing.
* ``canonical`` replaces OIDs with names, so two exports of the same structure (say before and after a reset, which
  recreates every object with new OIDs) compare equal exactly when their structure is the same.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

EXPORT_FORMAT_VERSION = 1
RELKINDS = ("r", "p", "v", "m", "f")


def _rows(conn: Any, query: str, params: Any = None) -> list[dict[str, Any]]:
    cur = conn.execute(query, params)
    names = [d.name for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def export_snapshot(conn: Any, schema: str) -> dict[str, Any]:
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        major = int(conn.execute("SELECT current_setting('server_version_num')::int / 10000").fetchone()[0])
        database = _rows(conn, "SELECT datname, pg_encoding_to_char(encoding) AS encoding, datlocprovider::text, "
                               "datcollate, datctype FROM pg_database WHERE datname = current_database()")[0]
        relations = _rows(conn, "SELECT c.oid::int AS oid, c.relname, n.nspname, c.relkind::text, c.relispartition, "
                                "c.relrowsecurity, c.relhasrules, c.relhassubclass FROM pg_class c "
                                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                                "WHERE n.nspname = %s AND c.relkind = ANY(%s) ORDER BY c.oid", (schema, list(RELKINDS)))
        rel_oids = [r["oid"] for r in relations]
        attributes = _rows(conn, "SELECT attrelid::int, attnum::int, attname, atttypid::int, atttypmod, attnotnull, "
                                 "atthasdef, attidentity::text, attgenerated::text, attisdropped, "
                                 "attcollation::int FROM pg_attribute WHERE attrelid = ANY(%s::oid[]) AND attnum > 0 "
                                 "ORDER BY attrelid, attnum", (rel_oids,))
        type_oids = sorted({a["atttypid"] for a in attributes})
        types = _rows(conn, "SELECT t.oid::int AS oid, t.typname, n.nspname, t.typtype::text FROM pg_type t "
                            "JOIN pg_namespace n ON n.oid = t.typnamespace WHERE t.oid = ANY(%s::oid[]) ORDER BY t.oid",
                      (type_oids,))
        coll_oids = sorted({a["attcollation"] for a in attributes if a["attcollation"]})
        collations = _rows(conn, "SELECT c.oid::int AS oid, c.collname, n.nspname, c.collprovider::text, "
                                 "c.collisdeterministic FROM pg_collation c JOIN pg_namespace n "
                                 "ON n.oid = c.collnamespace WHERE c.oid = ANY(%s::oid[]) ORDER BY c.oid",
                           (coll_oids,))
        constraints = _rows(conn, "SELECT oid::int, conname, contype::text, conrelid::int, "
                                  "coalesce(conkey::int[], '{}') AS conkey, confrelid::int, "
                                  "coalesce(confkey::int[], '{}') AS confkey, condeferrable, condeferred, "
                                  "convalidated, conindid::int FROM pg_constraint WHERE conrelid = ANY(%s::oid[]) "
                                  "ORDER BY oid", (rel_oids,))
        indexes = _rows(conn, """
            SELECT i.indexrelid::int, i.indrelid::int, i.indkey::int[] AS indkey, i.indnkeyatts::int, i.indisunique,
                   i.indisprimary, i.indisvalid, i.indisready, i.indimmediate, i.indnullsnotdistinct,
                   i.indexprs IS NOT NULL AS has_expressions, i.indpred IS NOT NULL AS has_predicate,
                   (EXISTS (SELECT 1 FROM unnest(i.indclass) AS oc(oid) JOIN pg_opclass o ON o.oid = oc.oid
                            WHERE NOT o.opcdefault)
                    OR EXISTS (SELECT 1 FROM unnest(i.indcollation, i.indkey::int[]) AS ic(coll, attnum)
                               JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ic.attnum
                               WHERE ic.coll <> 0 AND ic.coll <> a.attcollation)
                    OR EXISTS (SELECT 1 FROM unnest(i.indoption) AS io(opt) WHERE io.opt <> 0))
                   AS nondefault_semantics
            FROM pg_index i WHERE i.indrelid = ANY(%s::oid[]) ORDER BY i.indexrelid""", (rel_oids,))
        triggers = _rows(conn, """
            SELECT t.oid::int, t.tgrelid::int, t.tgname, t.tgenabled::text, t.tgisinternal,
                   CASE WHEN t.tgtype & 2 <> 0 THEN 'BEFORE' WHEN t.tgtype & 64 <> 0 THEN 'INSTEAD OF'
                        ELSE 'AFTER' END AS timing,
                   CASE WHEN t.tgtype & 1 <> 0 THEN 'ROW' ELSE 'STATEMENT' END AS level,
                   array_remove(ARRAY[CASE WHEN t.tgtype & 4 <> 0 THEN 'INSERT' END,
                                      CASE WHEN t.tgtype & 16 <> 0 THEN 'UPDATE' END,
                                      CASE WHEN t.tgtype & 8 <> 0 THEN 'DELETE' END,
                                      CASE WHEN t.tgtype & 32 <> 0 THEN 'TRUNCATE' END], NULL) AS events
            FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') ORDER BY t.oid""")
        for t in triggers:
            t["writes"] = None  # never claimed: trigger functions aren't analysed here
        nsp_names = {schema} | {t["nspname"] for t in types} | {c["nspname"] for c in collations}
        namespaces = _rows(conn, "SELECT oid::int, nspname FROM pg_namespace WHERE nspname = ANY(%s) ORDER BY oid",
                           (sorted(nsp_names),))
    return {"export_format_version": EXPORT_FORMAT_VERSION, "postgres_major": major, "schema_name": schema,
            "database": database,
            "coverage": {"relations": True, "attributes": True, "types": True, "collations": True,
                         "constraints": True, "indexes": True, "triggers": "database"},
            "namespaces": namespaces, "types": types, "collations": collations, "relations": relations,
            "attributes": attributes, "constraints": constraints, "indexes": indexes, "triggers": triggers}


def canonical(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The structure with every OID replaced by a name (relation, attribute or type name), sorted."""
    rel = {r["oid"]: r["relname"] for r in snapshot["relations"]}
    typ = {t["oid"]: f"{t['nspname']}.{t['typname']}" for t in snapshot["types"]}
    col = {c["oid"]: f"{c['nspname']}.{c['collname']}" for c in snapshot["collations"]}
    att = {(a["attrelid"], a["attnum"]): a["attname"] for a in snapshot["attributes"]}
    # an index's own constraint (a foreign key's conindid is the referenced key's index, not its own)
    idx_of = {c.get("conindid", 0): c["conname"] for c in snapshot["constraints"]
              if c.get("conindid", 0) and c["contype"] in ("p", "u", "x")}

    def cols(relid: int, nums: list[int]) -> list[str]:
        return [att.get((relid, n), f"#{n}") for n in nums]
    out = {"postgres_major": snapshot["postgres_major"], "schema": snapshot["schema_name"],
           "database": {k: v for k, v in snapshot["database"].items() if k != "datname"},
           "relations": sorted([r["relname"], r["relkind"], r["relispartition"], r["relrowsecurity"],
                                r["relhasrules"], r["relhassubclass"]] for r in snapshot["relations"]),
           "attributes": sorted([rel.get(a["attrelid"], "?"), a["attnum"], a["attname"], typ.get(a["atttypid"], "?"),
                                 a["atttypmod"], a["attnotnull"], a["atthasdef"], a["attidentity"], a["attgenerated"],
                                 a["attisdropped"], col.get(a["attcollation"], a["attcollation"] or None)]
                                for a in snapshot["attributes"]),
           "constraints": sorted([rel.get(c["conrelid"], "?"), c["conname"], c["contype"],
                                  cols(c["conrelid"], c.get("conkey", [])), rel.get(c.get("confrelid", 0)),
                                  cols(c.get("confrelid", 0), c.get("confkey", [])), c["condeferrable"],
                                  c["condeferred"],
                                  c["convalidated"]] for c in snapshot["constraints"]),
           "indexes": sorted([rel.get(i["indrelid"], "?"), idx_of.get(i["indexrelid"], "(no constraint)"),
                              cols(i["indrelid"], [k for k in i["indkey"] if k]), i["indnkeyatts"], i["indisunique"],
                              i["indisprimary"], i["indisvalid"], i["indisready"], i["indimmediate"],
                              i["indnullsnotdistinct"], i["has_expressions"], i["has_predicate"],
                              i["nondefault_semantics"]] for i in snapshot["indexes"]),
           "triggers": sorted([rel.get(t["tgrelid"], f"(other schema) oid {t['tgrelid']}")
                               if t["tgisinternal"] else rel.get(t["tgrelid"], "(other schema)"),
                               t["tgname"] if not t["tgisinternal"] else "(internal)", t["tgenabled"],
                               t["tgisinternal"], t["timing"], t["level"], sorted(t["events"])]
                              for t in snapshot["triggers"])}
    return json.loads(json.dumps(out, default=str))


def canonical_digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(canonical(snapshot), sort_keys=True).encode()).hexdigest()


def diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Section by section, what one canonical form has that the other doesn't."""
    ca, cb = canonical(a), canonical(b)
    out: dict[str, Any] = {}
    for key in sorted(set(ca) | set(cb)):
        x, y = ca.get(key), cb.get(key)
        if x == y:
            continue
        if isinstance(x, list) and isinstance(y, list):
            out[key] = {"only_first": [e for e in x if e not in y], "only_second": [e for e in y if e not in x]}
        else:
            out[key] = {"first": x, "second": y}
    return out
