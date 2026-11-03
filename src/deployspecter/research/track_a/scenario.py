"""The Track A scenario: Gate 2A's synthetic reservations application, with its lost update.

Everything here is hand-written synthetic configuration. The application code is the existing
``tests/live_app/reservations.py``, which the Gate 2A suite already runs under supervision. It is referenced by
operation name only and never imported here.

* Defect: ``reserve_seats`` reads ``reserved``, computes in Python and writes back. Without ``FOR UPDATE`` two
  concurrent calls can both succeed, losing one update (requirements R1 and R2 in ``tests/live_app/properties.py``).
* Buggy and fixed versions are a harness-owned code switch (``lock_row``), injected after validation. It is not a
  registered argument, so no workload can choose its own version.
* The catalog snapshot below is a hand-written dry-run fixture. It is never evidence that any database matches: a
  pinned snapshot must come from the supervised exporter, which waits for approval.
"""

from __future__ import annotations

from typing import Any

APPLICATION = "reservations"
SCHEMA = "ds_run"  # Gate 2A's run schema (release ``database.RUN_SCHEMA``)
SETUP_SQL_SHA256 = "3bb29d210ef4f54a2d5f37d5c9c9b9726a00ddc0e194f55655b6fabe0f39e272"  # live_app.reservations.SETUP_SQL
APP_MODULE = "live_app.reservations"

# Harness-owned code versions. Never workload arguments.
VARIANTS: dict[str, dict[str, Any]] = {"buggy": {"lock_row": False}, "fixed": {"lock_row": True}}

REGISTRY_CONFIG: dict[str, Any] = {
    "registry_version": 1,
    "application": APPLICATION,
    "registered_by": {"registrar": "track_a_harness", "source": "harness_config", "config_label": "track_a_v1"},
    "min_actors": 2,
    "max_actors": 3,
    "entrypoints": [
        {"id": "reserve_seats", "operation": f"{APP_MODULE}:reserve_seats", "versions": ["v1"], "roles": ["request"],
         "max_instances": 3,
         "arguments": [{"name": "event_id", "types": ["int"]}, {"name": "holder", "types": ["str"]},
                       {"name": "qty", "types": ["int"]}]},
        {"id": "release_seats", "operation": f"{APP_MODULE}:release_seats", "versions": ["v1"], "roles": ["request"],
         "max_instances": 2, "arguments": [{"name": "holder", "types": ["str"]}]},
    ],
}

HOLDER = {"kind": "str", "min_length": 1, "max_length": 16, "charset": "lower_identifier"}
CONTRACTS_CONFIG: dict[str, Any] = {
    "contracts_version": 1, "application": APPLICATION, "registry_digest": "",
    "contracts": [
        {"entrypoint": "reserve_seats", "versions": ["v1"], "roles": ["request"],
         "arguments": [{"name": "event_id", "constraints": [{"kind": "int", "minimum": 1, "maximum": 1000}]},
                       {"name": "holder", "constraints": [HOLDER]},
                       {"name": "qty", "constraints": [{"kind": "int", "minimum": 1, "maximum": 10}]}]},
        {"entrypoint": "release_seats", "versions": ["v1"], "roles": ["request"],
         "arguments": [{"name": "holder", "constraints": [HOLDER]}]},
    ],
}


def _typed(kind: str, value: Any) -> dict[str, Any]:
    return {"type": kind, "value": value}


# Two buyers ask for 2 of 3 seats each. A serial order rejects one; the lost update lets both succeed.
DOCUMENT: dict[str, Any] = {
    "schema_version": 2, "name": "track_a_two_buyers", "application": APPLICATION,
    "rationale": "Two concurrent reservations for the same event, which together exceed its capacity.",
    "provenance": {"origin": "hand_written", "author": "track_a_harness"},
    "actors": [
        {"name": "a", "entrypoint": "reserve_seats", "application_version": "v1", "role": "request",
         "arguments": [{"name": "event_id", "value": _typed("int", 1)},
                       {"name": "holder", "value": _typed("str", "alice")},
                       {"name": "qty", "value": _typed("int", 2)}]},
        {"name": "b", "entrypoint": "reserve_seats", "application_version": "v1", "role": "request",
         "arguments": [{"name": "event_id", "value": _typed("int", 1)},
                       {"name": "holder", "value": _typed("str", "bob")},
                       {"name": "qty", "value": _typed("int", 2)}]},
    ],
    "state_goals": [
        {"entity": "event", "label": "event_one", "count": 1, "constraints": [
            {"field": "id", "op": "eq", "value": _typed("int", 1)},
            {"field": "capacity", "op": "eq", "value": _typed("int", 3)},
            {"field": "reserved", "op": "eq", "value": _typed("int", 0)}]},
    ],
}


def _column(field: str, pg_type: str, *, nullable: bool, **extra: Any) -> dict[str, Any]:
    if pg_type in {"text", "varchar"}:
        extra.setdefault("collation", "deterministic")
    return {"field": field, "column": field, "pg_type": pg_type, "nullable": nullable, **extra}


def context_config(*, registry_digest: str, schema_digest: str) -> dict[str, Any]:
    return {
        "context_version": 1, "application": APPLICATION, "registry_digest": registry_digest,
        "schema_digest": schema_digest, "encoding": "UTF8",
        "entities": [
            {"entity": "event", "table": "events", "primary_key": ["id"],
             "columns": [_column("id", "integer", nullable=False), _column("capacity", "integer", nullable=False),
                         _column("reserved", "integer", nullable=False)]},
            {"entity": "reservation", "table": "reservations", "primary_key": ["id"],
             "foreign_keys": [{"field": "event_id", "references_entity": "event", "references_field": "id"}],
             "columns": [_column("id", "integer", nullable=False, has_default=True),
                         _column("event_id", "integer", nullable=False), _column("holder", "text", nullable=False),
                         _column("qty", "integer", nullable=False)]},
            {"entity": "rejection", "table": "rejections", "primary_key": ["id"],
             "columns": [_column("id", "integer", nullable=False, has_default=True),
                         _column("event_id", "integer", nullable=False), _column("holder", "text", nullable=False),
                         _column("seats_available", "integer", nullable=False)]},
        ],
    }


NSP, EVENTS, RESERVATIONS, REJECTIONS = 2201, 17001, 17002, 17003
INT4, TEXT, DEFAULT_COLL = 23, 25, 100


def _attr(rel: int, num: int, name: str, typ: int, *, notnull: bool = True, hasdef: bool = False,
          coll: int = 0) -> dict[str, Any]:
    return {"attrelid": rel, "attnum": num, "attname": name, "atttypid": typ, "atttypmod": -1, "attnotnull": notnull,
            "atthasdef": hasdef, "attidentity": "", "attgenerated": "", "attisdropped": False, "attcollation": coll}


def _rel(oid: int, name: str) -> dict[str, Any]:
    return {"oid": oid, "relname": name, "nspname": SCHEMA, "relkind": "r", "relispartition": False,
            "relrowsecurity": False, "relhasrules": False, "relhassubclass": False}


def _con(oid: int, name: str, kind: str, rel: int, key: list[int], **extra: Any) -> dict[str, Any]:
    return {"oid": oid, "conname": name, "contype": kind, "conrelid": rel, "conkey": key, "condeferrable": False,
            "condeferred": False, "convalidated": True, **extra}


def _idx(oid: int, rel: int) -> dict[str, Any]:
    return {"indexrelid": oid, "indrelid": rel, "indkey": [1], "indnkeyatts": 1, "indisunique": True,
            "indisprimary": True, "indisvalid": True, "indisready": True, "indimmediate": True,
            "indnullsnotdistinct": False, "has_expressions": False, "has_predicate": False,
            "nondefault_semantics": False}


def _ri(oid: int, rel: int, name: str, events: list[str]) -> dict[str, Any]:
    return {"oid": oid, "tgrelid": rel, "tgname": name, "tgenabled": "O", "tgisinternal": True, "timing": "AFTER",
            "level": "ROW", "events": events, "writes": None}


def catalog_snapshot_fixture() -> dict[str, Any]:
    """What a supervised export of SETUP_SQL into ds_run is expected to contain. A dry-run fixture, never a pin."""
    return {
        "export_format_version": 1, "postgres_major": 16, "schema_name": SCHEMA,
        "database": {"datname": "deployspecter_test", "encoding": "UTF8", "datlocprovider": "c",
                     "datcollate": "en_US.utf8", "datctype": "en_US.utf8"},
        "coverage": {"relations": True, "attributes": True, "types": True, "collations": True, "constraints": True,
                     "indexes": True, "triggers": "database"},
        "namespaces": [{"oid": NSP, "nspname": SCHEMA}, {"oid": 11, "nspname": "pg_catalog"}],
        "types": [{"oid": INT4, "typname": "int4", "nspname": "pg_catalog", "typtype": "b"},
                  {"oid": TEXT, "typname": "text", "nspname": "pg_catalog", "typtype": "b"}],
        "collations": [{"oid": DEFAULT_COLL, "collname": "default", "nspname": "pg_catalog", "collprovider": "d",
                        "collisdeterministic": True}],
        "relations": [_rel(EVENTS, "events"), _rel(RESERVATIONS, "reservations"), _rel(REJECTIONS, "rejections")],
        "attributes": [
            _attr(EVENTS, 1, "id", INT4), _attr(EVENTS, 2, "capacity", INT4), _attr(EVENTS, 3, "reserved", INT4),
            _attr(RESERVATIONS, 1, "id", INT4, hasdef=True), _attr(RESERVATIONS, 2, "event_id", INT4),
            _attr(RESERVATIONS, 3, "holder", TEXT, coll=DEFAULT_COLL), _attr(RESERVATIONS, 4, "qty", INT4),
            _attr(REJECTIONS, 1, "id", INT4, hasdef=True), _attr(REJECTIONS, 2, "event_id", INT4),
            _attr(REJECTIONS, 3, "holder", TEXT, coll=DEFAULT_COLL), _attr(REJECTIONS, 4, "seats_available", INT4),
        ],
        "constraints": [
            _con(31001, "events_pkey", "p", EVENTS, [1], conindid=41001),
            _con(31002, "reservations_pkey", "p", RESERVATIONS, [1], conindid=41002),
            _con(31003, "reservations_event_id_fkey", "f", RESERVATIONS, [2], confrelid=EVENTS, confkey=[1]),
            _con(31004, "rejections_pkey", "p", REJECTIONS, [1], conindid=41003),
        ],
        "indexes": [_idx(41001, EVENTS), _idx(41002, RESERVATIONS), _idx(41003, REJECTIONS)],
        "triggers": [
            _ri(51001, RESERVATIONS, "RI_ConstraintTrigger_c_51001", ["INSERT"]),
            _ri(51002, RESERVATIONS, "RI_ConstraintTrigger_c_51002", ["UPDATE"]),
            _ri(51003, EVENTS, "RI_ConstraintTrigger_a_51003", ["DELETE"]),
            _ri(51004, EVENTS, "RI_ConstraintTrigger_a_51004", ["UPDATE"]),
        ],
    }
