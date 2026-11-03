"""Offline check of a state-planning schema context against a catalog snapshot. Research only, pure, unfrozen (5C.1).

The step 5B planner trusts a ``SchemaContextConfig`` that harness code writes and pins. ``verify_context`` compares that
context with a ``CatalogSnapshot``: a typed, bounded description of PostgreSQL catalog metadata, supplied separately
and pinned by its own digest. Nothing here connects to a database, reads a file or the network, or runs SQL.

Four outcomes:

* ``MATCH``: every fact the planner relies on agrees with the snapshot, and the snapshot shows nothing the planner
  can't represent. This is consistency with **the supplied snapshot only**, never with a live database.
* ``MISMATCH``: the context and the snapshot disagree on a fact the planner relies on.
* ``UNSUPPORTED``: the snapshot shows behaviour the planner can't represent and that could affect a candidate or an
  infeasibility claim, such as a CHECK constraint, a partial or expression unique index, or row-level security.
* ``INCOMPLETE``: the snapshot doesn't say enough to decide. A section not declared complete, or a missing entry,
  is never taken as evidence that a feature is absent.

Trust failures aren't outcomes: a context or snapshot that doesn't match its harness pin raises ``UntrustedInput``,
and a malformed snapshot raises ``SnapshotError``. A snapshot's digest proves integrity relative to its pin, not that
the snapshot came from where its provenance says, or that it's current.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator

from . import registry as rg
from . import state_planning as sp
from . import workload_spec as ws

EXPORT_FORMAT_VERSION = 1
IDENTITY_VERSION = 1
SNAPSHOT_DOMAIN = b"deployspecter:catalog-snapshot:v1\x00"
PREFIX = "catsnap1:"
SCOPE = "consistent with the supplied catalog snapshot only; not a check of any live database"
TRIGGER_ASSUMPTION = "triggers fire as under session_replication_role = origin: enabled O and A fire, R and D don't"

Oid = Annotated[int, Field(strict=True, ge=1, le=2**32 - 1)]
OptionalOid = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]  # 0 means none, as in the catalogs
AttNum = Annotated[int, Field(strict=True, ge=1, le=1600)]
PgName = Annotated[str, Field(min_length=1, max_length=63, pattern=ws.SAFE_TEXT)]
Setting = Annotated[str, Field(max_length=128, pattern=ws.SAFE_TEXT)]

# pg_catalog base types the planner can describe exactly. Every other type is "opaque" to it.
SUPPORTED_TYPES = {"int2": "smallint", "int4": "integer", "int8": "bigint", "float8": "double precision",
                   "bool": "boolean", "text": "text", "varchar": "varchar", "numeric": "numeric"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ------------------------------------------------------------------ the snapshot (PostgreSQL 15 and 16 shaped)


class DatabaseInfo(_Strict):  # pg_database
    datname: PgName
    encoding: str = Field(pattern=r"^[A-Z0-9_]{1,20}$")  # pg_encoding_to_char(encoding)
    datlocprovider: Literal["c", "i", "b"]  # libc, ICU, builtin
    datcollate: Setting
    datctype: Setting


class NamespaceInfo(_Strict):  # pg_namespace
    oid: Oid
    nspname: PgName


class TypeInfo(_Strict):  # pg_type
    oid: Oid
    typname: PgName
    nspname: PgName
    typtype: Literal["b", "c", "d", "e", "m", "p", "r"]  # base, composite, domain, enum, multirange, pseudo, range


class CollationInfo(_Strict):  # pg_collation
    oid: Oid
    collname: PgName
    nspname: PgName
    collprovider: Literal["d", "c", "i", "b"]
    collisdeterministic: StrictBool


class RelationInfo(_Strict):  # pg_class
    oid: Oid
    relname: PgName
    nspname: PgName
    relkind: Literal["r", "p", "v", "m", "f", "i", "I", "S", "t", "c"]
    relispartition: StrictBool
    relrowsecurity: StrictBool
    relhasrules: StrictBool
    relhassubclass: StrictBool


class AttributeInfo(_Strict):  # pg_attribute, user columns only (attnum > 0)
    attrelid: Oid
    attnum: AttNum
    attname: PgName
    atttypid: Oid
    atttypmod: StrictInt = Field(ge=-1, le=2**31 - 1)
    attnotnull: StrictBool
    atthasdef: StrictBool
    attidentity: Literal["", "a", "d"]
    attgenerated: Literal["", "s"]
    attisdropped: StrictBool
    attcollation: OptionalOid


class ConstraintInfo(_Strict):  # pg_constraint
    oid: Oid
    conname: PgName
    contype: Literal["p", "u", "f", "c", "x", "t", "n"]
    conrelid: Oid
    conkey: list[AttNum] = Field(default_factory=list, max_length=32)
    confrelid: OptionalOid = 0
    confkey: list[AttNum] = Field(default_factory=list, max_length=32)
    condeferrable: StrictBool
    condeferred: StrictBool
    convalidated: StrictBool
    conindid: OptionalOid = 0


class IndexInfo(_Strict):  # pg_index, with whether indexprs and indpred are set
    indexrelid: Oid
    indrelid: Oid
    indkey: list[Annotated[int, Field(strict=True, ge=0, le=1600)]] = Field(min_length=1, max_length=32)  # 0: expr
    indnkeyatts: StrictInt = Field(ge=1, le=32)  # key columns; the rest of indkey are INCLUDE columns
    indisunique: StrictBool
    indisprimary: StrictBool
    indisvalid: StrictBool
    indisready: StrictBool
    indimmediate: StrictBool
    indnullsnotdistinct: StrictBool  # PostgreSQL 15 and later
    has_expressions: StrictBool
    has_predicate: StrictBool
    # A non-default operator class, or an index collation that differs from its column's, can change equality.
    nondefault_semantics: StrictBool


class RelationRef(_Strict):
    nspname: PgName
    relname: PgName


class TriggerInfo(_Strict):  # pg_trigger, plus an exporter's analysis of what the function writes
    oid: Oid
    tgrelid: Oid
    tgname: PgName
    tgenabled: Literal["O", "D", "R", "A"]
    tgisinternal: StrictBool
    timing: Literal["BEFORE", "AFTER", "INSTEAD OF"]
    level: Literal["ROW", "STATEMENT"]
    events: list[Literal["INSERT", "UPDATE", "DELETE", "TRUNCATE"]] = Field(min_length=1, max_length=4)
    writes: list[RelationRef] | None = Field(max_length=64)  # None: unknown, as for dynamic SQL or other calls


class Coverage(_Strict):
    """Which sections list everything in their scope. An incomplete section can't prove anything is absent."""

    relations: StrictBool
    attributes: StrictBool
    types: StrictBool
    collations: StrictBool
    constraints: StrictBool
    indexes: StrictBool
    triggers: Literal["none", "schema", "database"]


class CatalogSnapshot(_Strict):
    export_format_version: StrictInt
    postgres_major: StrictInt = Field(ge=10, le=99)
    database: DatabaseInfo
    schema_name: PgName
    coverage: Coverage
    namespaces: list[NamespaceInfo] = Field(max_length=256)
    types: list[TypeInfo] = Field(max_length=4096)
    collations: list[CollationInfo] = Field(max_length=1024)
    relations: list[RelationInfo] = Field(max_length=4096)
    attributes: list[AttributeInfo] = Field(max_length=65_536)
    constraints: list[ConstraintInfo] = Field(max_length=16_384)
    indexes: list[IndexInfo] = Field(max_length=16_384)
    triggers: list[TriggerInfo] = Field(max_length=4096)

    @model_validator(mode="after")
    def _sound(self) -> CatalogSnapshot:
        if self.export_format_version != EXPORT_FORMAT_VERSION:
            raise ValueError(f"export_format_version must be {EXPORT_FORMAT_VERSION}")
        for name, rows, key in (("namespaces", self.namespaces, "oid"), ("types", self.types, "oid"),
                                ("collations", self.collations, "oid"), ("relations", self.relations, "oid"),
                                ("constraints", self.constraints, "oid"), ("indexes", self.indexes, "indexrelid"),
                                ("triggers", self.triggers, "oid")):
            keys = [r.model_dump()[key] for r in rows]
            if len(set(keys)) != len(keys):
                raise ValueError(f"repeated {key} in {name}")
        live = [a for a in self.attributes if not a.attisdropped]
        if len({(a.attrelid, a.attnum) for a in self.attributes}) != len(self.attributes):
            raise ValueError("repeated attnum in a relation")
        if len({(a.attrelid, a.attname) for a in live}) != len(live):
            raise ValueError("repeated column name in a relation")
        if self.postgres_major < 15 and any(i.indnullsnotdistinct for i in self.indexes):
            raise ValueError("NULLS NOT DISTINCT needs PostgreSQL 15 or later")
        for constraint in self.constraints:
            if constraint.contype == "f" and (not constraint.confrelid or len(constraint.conkey) !=
                                              len(constraint.confkey) or not constraint.conkey):
                raise ValueError(f"foreign key {constraint.conname} is malformed")
            if constraint.contype in {"p", "u"} and not constraint.conkey:
                raise ValueError(f"key constraint {constraint.conname} names no columns")
            if constraint.condeferred and not constraint.condeferrable:
                raise ValueError(f"{constraint.conname} is deferred but not deferrable")
        if any(i.indisprimary and not i.indisunique for i in self.indexes):
            raise ValueError("a primary-key index must be unique")
        if any(i.indnkeyatts > len(i.indkey) for i in self.indexes):
            raise ValueError("indnkeyatts is larger than indkey")
        return self


class TrustedSnapshot(_Strict):
    """Built by harness code: who exported the snapshot, a label for its source, when, and its pinned digest.

    These are recorded, not verified. The digest proves the snapshot is the one pinned, not where it came from.
    """

    exporter: sp.Identifier
    source_label: sp.Identifier
    exported_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    pinned_digest: str = Field(pattern=sp.HEX64)


class VerificationPolicy(_Strict):
    supported_major_versions: list[Annotated[int, Field(strict=True, ge=15, le=99)]] = Field(
        default_factory=lambda: [15, 16], min_length=1, max_length=8)
    # An "opaque" context column claims nothing about its type, so it may stand for any type.
    allow_opaque_for_known_types: StrictBool = True
    # Relations in the schema the context doesn't describe. Triggers and foreign keys are checked either way.
    allow_unlisted_relations: StrictBool = True


class UntrustedInput(ValueError):
    pass


class SnapshotError(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# ------------------------------------------------------------------ identity and results


def snapshot_digest(snapshot: CatalogSnapshot) -> str:
    """Domain-separated SHA-256 of the canonical snapshot. List order carries no meaning, except within keys."""
    data = snapshot.model_dump(mode="json")
    order = {"namespaces": ("oid",), "types": ("oid",), "collations": ("oid",), "relations": ("oid",),
             "attributes": ("attrelid", "attnum"), "constraints": ("oid",), "indexes": ("indexrelid",),
             "triggers": ("oid",)}
    for name, keys in order.items():
        data[name].sort(key=lambda row, keys=keys: tuple(row[k] for k in keys))
    for trigger in data["triggers"]:
        trigger["events"].sort()
        if trigger["writes"] is not None:
            trigger["writes"].sort(key=lambda r: (r["nspname"], r["relname"]))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(SNAPSHOT_DOMAIN + text.encode("utf-8")).hexdigest()


class SnapshotIdentity(_Strict):
    identity_version: Literal[1] = 1
    digest: str
    export_format_version: int
    postgres_major: int
    database: str
    schema_name: str
    encoding: str
    locale_provider: str
    datcollate: str
    datctype: str
    exporter: str  # provenance from the trusted object, recorded and not verified
    source_label: str
    exported_on: str


Outcome = Literal["MATCH", "MISMATCH", "UNSUPPORTED", "INCOMPLETE"]


class Finding(_Strict):
    kind: Literal["mismatch", "unsupported", "incomplete"]
    code: str
    entity: str | None = None
    field: str | None = None
    detail: str


class VerificationResult(_Strict):
    outcome: Outcome
    snapshot: SnapshotIdentity
    context_digest: str
    findings: tuple[Finding, ...]
    checked_entities: tuple[str, ...]
    assumptions: tuple[str, ...]
    scope: Literal["consistent with the supplied catalog snapshot only; not a check of any live database"] = SCOPE


# ------------------------------------------------------------------ verification


class _Verifier:
    def __init__(self, context: sp.PlanningContext, snapshot: CatalogSnapshot, policy: VerificationPolicy) -> None:
        self.context, self.snapshot, self.policy = context, snapshot, policy
        self.cover = snapshot.coverage
        self.findings: list[Finding] = []
        self.relations = {(r.nspname, r.relname): r for r in snapshot.relations}
        self.attributes: dict[int, list[AttributeInfo]] = {}
        for attribute in snapshot.attributes:
            if not attribute.attisdropped:
                self.attributes.setdefault(attribute.attrelid, []).append(attribute)
        self.types = {t.oid: t for t in snapshot.types}
        self.collations = {c.oid: c for c in snapshot.collations}
        self.constraints: dict[int, list[ConstraintInfo]] = {}
        for constraint in snapshot.constraints:
            self.constraints.setdefault(constraint.conrelid, []).append(constraint)
        self.indexes = {i.indexrelid: i for i in snapshot.indexes}
        self.indexes_on: dict[int, list[IndexInfo]] = {}
        for index in snapshot.indexes:
            self.indexes_on.setdefault(index.indrelid, []).append(index)
        self.firing = [t for t in snapshot.triggers if not t.tgisinternal and t.tgenabled in {"O", "A"}]

    def add(self, kind: str, code: str, detail: str, entity: str | None = None, field: str | None = None) -> None:
        self.findings.append(Finding(kind=kind, code=code, entity=entity, field=field, detail=detail))

    def absent(self, complete: bool, code: str, detail: str, entity=None, field=None) -> None:
        """Something the context expects isn't in the snapshot: a mismatch only if the section is complete."""
        self.add("mismatch" if complete else "incomplete", code if complete else f"{code}_unknown",
                 detail if complete else f"{detail}; the snapshot doesn't list everything here", entity, field)

    def run(self) -> list[Finding]:
        snapshot, config = self.snapshot, self.context.config
        if snapshot.postgres_major not in self.policy.supported_major_versions:
            self.add("unsupported", "postgres_version", f"PostgreSQL {snapshot.postgres_major} isn't in the policy")
        if snapshot.database.encoding != config.encoding:
            self.add("mismatch", "encoding", f"the database encoding is {snapshot.database.encoding}, the context "
                                             f"says {config.encoding}")
        if config.schema_digest != snapshot_digest(snapshot):
            self.add("mismatch", "schema_digest", "the context was written from a different catalog snapshot")
        if not any(n.nspname == snapshot.schema_name for n in snapshot.namespaces):
            self.absent(self.cover.relations, "schema_missing", f"schema {snapshot.schema_name} isn't listed")
        # A MATCH needs a complete snapshot: an incomplete section is reported even when every lookup succeeded.
        for section, complete in (("relations", self.cover.relations), ("types", self.cover.types),
                                  ("collations", self.cover.collations)):
            if not complete:
                self.add("incomplete", f"{section}_coverage", f"the snapshot may not list every {section[:-1]}")
        for entity in sorted(config.entities, key=lambda e: e.entity):
            self._entity(entity)
        self._unlisted()
        return sorted(self.findings, key=lambda f: (f.entity or "", f.field or "", f.kind, f.code, f.detail))

    # ---------------------------------------------------------------- relations and columns

    def _entity(self, entity: sp.TrustedEntity) -> None:
        name = entity.entity
        relation = self.relations.get((self.snapshot.schema_name, entity.table))
        if relation is None:
            self.absent(self.cover.relations, "relation_missing", f"{entity.table} isn't in the snapshot", name)
            return
        if relation.relkind != "r":
            self.add("unsupported", "relation_kind", f"{entity.table} has relkind {relation.relkind!r}; only "
                                                     "ordinary tables are supported", name)
        for flag, code in ((relation.relispartition, "partition"), (relation.relrowsecurity, "row_level_security"),
                           (relation.relhasrules, "rewrite_rules"), (relation.relhassubclass, "inheritance")):
            if flag:
                self.add("unsupported", code, f"{entity.table} uses {code.replace('_', ' ')}", name)
        attributes = {a.attname: a for a in self.attributes.get(relation.oid, [])}
        if not self.cover.attributes:
            self.add("incomplete", "attribute_coverage", "the snapshot may not list every column", name)
        by_column = {c.column: c for c in entity.columns}
        for column in entity.columns:
            attribute = attributes.get(column.column)
            if attribute is None:
                self.absent(self.cover.attributes, "column_missing", f"{entity.table}.{column.column} isn't in the "
                                                                     "snapshot", name, column.field)
                continue
            self._column(entity, column, attribute)
        if self.cover.attributes:
            for extra in sorted(set(attributes) - set(by_column)):
                self.add("mismatch", "column_not_in_context", f"{entity.table}.{extra} exists but the context "
                                                              "doesn't describe it", name, extra)
        self._keys(entity, relation, attributes)
        self._triggers(entity, relation)

    def _column(self, entity: sp.TrustedEntity, column: sp.TrustedColumn, attribute: AttributeInfo) -> None:
        name, field = entity.entity, column.field
        kind = self.types.get(attribute.atttypid)
        if kind is None:
            self.absent(self.cover.types, "type_missing", f"type {attribute.atttypid} isn't listed", name, field)
        else:
            self._type(entity, column, attribute, kind)
        generated = attribute.attgenerated == "s" or attribute.attidentity == "a"
        default = (attribute.atthasdef or attribute.attidentity == "d") and not generated
        if column.nullable != (not attribute.attnotnull):
            actual = "NOT NULL" if attribute.attnotnull else "nullable"
            self.add("mismatch", "nullability", f"the snapshot says {actual}", name, field)
        if column.generated != generated:
            self.add("mismatch", "generated", f"the snapshot says generated={generated}", name, field)
        if column.has_default != default:
            self.add("mismatch", "default", f"the snapshot says default={default}", name, field)
        if column.collation is not None:
            collation = self.collations.get(attribute.attcollation) if attribute.attcollation else None
            if collation is None:
                self.absent(self.cover.collations, "collation_missing", f"collation {attribute.attcollation} isn't "
                                                                        "listed", name, field)
            else:
                actual = "deterministic" if collation.collisdeterministic else "nondeterministic"
                if column.collation != actual:
                    self.add("mismatch", "collation", f"{collation.collname} is {actual}", name, field)

    def _type(self, entity, column, attribute, kind: TypeInfo) -> None:
        name, field = entity.entity, column.field
        if column.pg_type == "opaque":
            if kind.nspname == "pg_catalog" and kind.typname in SUPPORTED_TYPES and \
                    not self.policy.allow_opaque_for_known_types:
                self.add("mismatch", "type", f"{kind.typname} is described as opaque", name, field)
            return
        if kind.typtype == "d":
            self.add("unsupported", "domain_type", f"{kind.typname} is a domain, which may add constraints; describe "
                                                   "it as opaque", name, field)
            return
        expected = SUPPORTED_TYPES.get(kind.typname) if kind.nspname == "pg_catalog" else None
        if expected != column.pg_type:
            self.add("mismatch", "type", f"the snapshot type is {kind.nspname}.{kind.typname}", name, field)
            return
        if column.pg_type == "varchar":
            if attribute.atttypmod < 0:
                self.add("unsupported", "unbounded_varchar", "varchar without a length can't be described exactly",
                         name, field)
            elif attribute.atttypmod - 4 != column.max_length:
                self.add("mismatch", "varchar_length", f"the snapshot length is {attribute.atttypmod - 4}", name, field)

    # ---------------------------------------------------------------- keys, uniqueness and foreign keys

    def _names(self, entity, attributes: dict[str, AttributeInfo], attnums) -> list[str] | None:
        by_num = {a.attnum: a.attname for a in attributes.values()}
        fields = {c.column: c.field for c in entity.columns}
        names = [fields.get(by_num.get(n, ""), None) for n in attnums]
        return None if any(n is None for n in names) else names

    def _keys(self, entity: sp.TrustedEntity, relation: RelationInfo, attributes) -> None:
        name = entity.entity
        if not self.cover.constraints:
            self.add("incomplete", "constraint_coverage", "the snapshot may not list every constraint", name)
        if not self.cover.indexes:
            self.add("incomplete", "index_coverage", "the snapshot may not list every index", name)
        constraints = self.constraints.get(relation.oid, [])
        primary = [c for c in constraints if c.contype == "p"]
        if len(primary) > 1:
            self.add("mismatch", "primary_key", "the snapshot lists two primary keys", name)
        catalog_pk = self._names(entity, attributes, primary[0].conkey) if primary else []
        if primary and catalog_pk is None:
            self.add("incomplete", "primary_key_columns", "the primary key names columns the context lacks", name)
        elif primary or self.cover.constraints:
            if sorted(catalog_pk or []) != sorted(entity.primary_key):
                self.add("mismatch", "primary_key", f"the snapshot primary key is {catalog_pk or 'none'}", name)
        elif entity.primary_key:
            self.add("incomplete", "primary_key_unknown", "the primary key isn't listed", name)
        for constraint in constraints:
            if constraint.contype in {"p", "u"}:
                if constraint.condeferrable:
                    self.add("unsupported", "deferrable_key", f"{constraint.conname} is deferrable", name)
                if self.cover.indexes and constraint.conindid not in self.indexes:
                    self.add("incomplete", "key_index_missing", f"{constraint.conname}'s index isn't listed", name)
                key_index = self.indexes.get(constraint.conindid)
                if constraint.contype == "p" and key_index is not None:
                    self._key_index_semantics(name, constraint.conname, key_index)
            elif constraint.contype == "c":
                self.add("unsupported", "check_constraint", f"{constraint.conname} is a CHECK constraint, which the "
                                                            "planner can't evaluate", name)
            elif constraint.contype == "x":
                self.add("unsupported", "exclusion_constraint", f"{constraint.conname} is an exclusion constraint",
                         name)
            elif constraint.contype == "t":
                self.add("unsupported", "constraint_trigger", f"{constraint.conname} is a constraint trigger", name)
        self._unique(entity, relation, attributes)
        self._foreign_keys(entity, constraints, attributes)

    def _key_index_semantics(self, name: str, conname: str, index: IndexInfo) -> None:
        """A primary key can be attached to an existing index, so its index is checked like any unique index."""
        problems = [(index.has_expressions or 0 in index.indkey[: index.indnkeyatts], "expression_key_index"),
                    (index.has_predicate, "partial_key_index"),
                    (index.nondefault_semantics, "nondefault_key_semantics"),
                    (not (index.indisvalid and index.indisready), "invalid_key_index")]
        for flag, code in problems:
            if flag:
                self.add("unsupported", code, f"{conname}'s index {code.replace('_', ' ')}", name)

    def _unique(self, entity, relation, attributes) -> None:
        name = entity.entity
        found: set[tuple[tuple[str, ...], str]] = set()
        uninterpreted: set[tuple[str, ...]] = set()  # unique sets present in the catalog but not representable
        for index in self.indexes_on.get(relation.oid, []):
            if not index.indisunique or index.indisprimary:
                continue
            label = f"unique index {index.indexrelid}"
            key = index.indkey[: index.indnkeyatts]  # INCLUDE columns don't take part in uniqueness
            problems = [(index.has_expressions or 0 in key, "expression_unique_index", "is on an expression"),
                        (index.nondefault_semantics, "nondefault_unique_semantics",
                         "uses a non-default operator class or collation"),
                        (index.has_predicate, "partial_unique_index", "is partial"),
                        (not (index.indisvalid and index.indisready), "invalid_unique_index", "isn't valid and ready"),
                        (not index.indimmediate, "deferrable_unique_index", "is deferrable")]
            fields = self._names(entity, attributes, [k for k in key if k])
            if any(flag for flag, _, _ in problems):
                for flag, code, text in problems:
                    if flag:
                        self.add("unsupported", code, f"{label} {text}", name)
                if fields is not None:
                    uninterpreted.add(tuple(sorted(fields)))
                continue
            if fields is None:
                self.add("incomplete", "unique_columns", f"{label} names columns the context lacks", name)
                continue
            found.add((tuple(sorted(fields)), "not_distinct" if index.indnullsnotdistinct else "distinct"))
        declared = {(tuple(sorted(u.fields)), u.nulls) for u in entity.unique}
        if self.cover.indexes:
            for fields, nulls in sorted(found - declared):
                self.add("mismatch", "unique_not_in_context", f"unique ({', '.join(fields)}) NULLS "
                                                              f"{nulls.replace('_', ' ').upper()} isn't in the context",
                         name)
        for fields, nulls in sorted(declared - found):
            if fields in uninterpreted:
                continue  # the catalog has a unique index here that isn't representable: reported as unsupported
            label = f"unique ({', '.join(fields)}) NULLS {nulls.replace('_', ' ').upper()}"
            self.absent(self.cover.indexes, "unique_missing", f"the context's {label} isn't in the snapshot", name)

    def _foreign_keys(self, entity, constraints, attributes) -> None:
        name = entity.entity
        tables = {e.table: e for e in self.context.config.entities}
        relations = {r.oid: r for r in self.snapshot.relations}
        found = set()
        uninterpreted: set[str] = set()  # child fields of foreign keys present but not representable or resolvable
        for constraint in (c for c in constraints if c.contype == "f"):
            uninterpreted.update(self._names(entity, attributes, constraint.conkey) or [])
            if len(constraint.conkey) != 1:
                self.add("unsupported", "composite_foreign_key", f"{constraint.conname} spans several columns", name)
                continue
            if constraint.condeferrable:
                self.add("unsupported", "deferrable_foreign_key", f"{constraint.conname} is deferrable", name)
                continue
            target = relations.get(constraint.confrelid)
            if target is None:
                self.absent(self.cover.relations, "foreign_key_target_missing",
                            f"{constraint.conname} references a relation that isn't listed", name)
                continue
            referenced = tables.get(target.relname) if target.nspname == self.snapshot.schema_name else None
            if referenced is None:
                self.add("unsupported", "foreign_key_to_undescribed", f"{constraint.conname} references "
                                                                      f"{target.relname}, which the context doesn't "
                                                                      "describe", name)
                continue
            child = self._names(entity, attributes, constraint.conkey)
            parent_attributes = {a.attname: a for a in self.attributes.get(target.oid, [])}
            parent = self._names(referenced, parent_attributes, constraint.confkey)
            if child is None or parent is None:
                self.add("incomplete", "foreign_key_columns", f"{constraint.conname} names undescribed columns", name)
                continue
            found.add((child[0], referenced.entity, parent[0]))
            uninterpreted.discard(child[0])
        declared = {(k.field, k.references_entity, k.references_field) for k in entity.foreign_keys}
        if self.cover.constraints:
            for key in sorted(found - declared):
                self.add("mismatch", "foreign_key_not_in_context", f"{key[0]} references {key[1]}.{key[2]} in the "
                                                                   "snapshot only", name, key[0])
        for key in sorted(declared - found):
            if key[0] in uninterpreted:
                continue  # a foreign key on this column exists but couldn't be interpreted: already reported
            self.absent(self.cover.constraints, "foreign_key_missing", f"{key[0]} → {key[1]}.{key[2]} isn't in the "
                                                                       "snapshot", name, key[0])

    # ---------------------------------------------------------------- triggers and unlisted relations

    def _triggers(self, entity: sp.TrustedEntity, relation: RelationInfo) -> None:
        name = entity.entity
        if self.cover.triggers == "none":
            self.add("incomplete", "trigger_coverage", "the snapshot doesn't list triggers", name)
            return
        on_insert = [t for t in self.firing if t.tgrelid == relation.oid and "INSERT" in t.events]
        if entity.has_insert_triggers != bool(on_insert):
            self.add("mismatch", "insert_triggers", f"the snapshot has {len(on_insert)} firing INSERT trigger(s)",
                     name)
        if self.cover.triggers != "database":
            self.add("incomplete", "trigger_writers_unknown", "triggers outside the schema aren't listed, so writes "
                                                              "into this table can't be ruled out", name)
            return
        unknown = sorted(t.tgname for t in self.firing if t.writes is None)
        if unknown:
            self.add("incomplete", "trigger_effect_unknown", f"triggers with unknown writes: {', '.join(unknown)}",
                     name)
            return
        target = (self.snapshot.schema_name, entity.table)
        written = any((w.nspname, w.relname) == target for t in self.firing for w in t.writes)
        if entity.trigger_written != written:
            self.add("mismatch", "trigger_written", f"the snapshot says triggers {'do' if written else 'do not'} "
                                                    "write this table", name)

    def _unlisted(self) -> None:
        if self.policy.allow_unlisted_relations:
            return
        if not self.cover.relations:
            self.add("incomplete", "unlisted_relations_unknown", "the policy forbids undescribed relations, and the "
                                                                 "snapshot may not list every relation")
            return
        described = {e.table for e in self.context.config.entities}
        for relation in sorted(self.snapshot.relations, key=lambda r: r.relname):
            if relation.nspname == self.snapshot.schema_name and relation.relkind in {"r", "p"} and \
                    relation.relname not in described:
                self.add("unsupported", "unlisted_relation", f"{relation.relname} isn't described by the context")


def verify_context(context: Any, *, registry: rg.Registry, context_trust: Any, snapshot: Any, snapshot_trust: Any,
                   policy: VerificationPolicy | None = None) -> VerificationResult:
    """Compare a pinned state-planning context with a pinned catalog snapshot. Pure: no database, file or network."""
    if not isinstance(registry, rg.Registry):
        raise TypeError("verify_context takes the trusted Registry the context is bound to")
    try:
        built = sp.build_context(context, registry=registry, trusted=context_trust)
    except sp.ContextError as exc:
        raise UntrustedInput(f"the schema context doesn't verify: {exc}") from exc
    try:
        trusted = rg.revalidate_model(TrustedSnapshot, snapshot_trust) if isinstance(
            snapshot_trust, TrustedSnapshot) else None
    except Exception:  # a forged trust object may fail to serialize in several ways
        trusted = None
    if trusted is None:
        raise UntrustedInput("a TrustedSnapshot built by harness code is required")
    try:
        parsed = rg.revalidate_model(CatalogSnapshot, snapshot)
    except ValidationError as exc:
        raise SnapshotError([f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]) from exc
    except Exception as exc:  # text policy, or a forged instance that can't be serialized
        raise SnapshotError([ws.safe_repr(str(exc), 300)]) from exc
    digest = snapshot_digest(parsed)
    if digest != trusted.pinned_digest:
        raise UntrustedInput("the catalog snapshot's digest isn't the pinned one")
    policy = VerificationPolicy() if policy is None else policy
    if not isinstance(policy, VerificationPolicy):
        raise TypeError("verify_context takes a VerificationPolicy")
    policy = rg.revalidate_model(VerificationPolicy, policy)
    findings = _Verifier(built, parsed, policy).run()
    kinds = {f.kind for f in findings}
    outcome: Outcome = ("MISMATCH" if "mismatch" in kinds else "UNSUPPORTED" if "unsupported" in kinds
                        else "INCOMPLETE" if "incomplete" in kinds else "MATCH")
    database = parsed.database
    identity = SnapshotIdentity(
        digest=PREFIX + digest, export_format_version=parsed.export_format_version,
        postgres_major=parsed.postgres_major, database=database.datname, schema_name=parsed.schema_name,
        encoding=database.encoding, locale_provider=database.datlocprovider, datcollate=database.datcollate,
        datctype=database.datctype, exporter=trusted.exporter, source_label=trusted.source_label,
        exported_on=trusted.exported_on,
    )
    return VerificationResult(
        outcome=outcome, snapshot=identity, context_digest=built.digest, findings=tuple(findings),
        checked_entities=tuple(sorted(e.entity for e in built.config.entities)),
        assumptions=(TRIGGER_ASSUMPTION, sp.CONTEXT_EXACT.replace("the trusted context", "the snapshot")),
    )
