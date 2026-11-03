# Catalog snapshot verification (offline step 5C.1)

`src/deployspecter/live/catalog_verification.py`, tested by `tests/test_catalog_verification.py`.

- **Status:** research-only, pure, **unfrozen and uncommitted**.
- **What it does:** checks the step 5B planner's trusted schema context against a separately supplied, pinned catalog
  snapshot.
- **What it doesn't do:** connect to a database, read files or the network, run SQL, or import Phase 9B, the engine or
  any oracle.

## 1. What the planner trusts (task 1)

Every fact below is in the committed `state_planning.py` context, and each affects candidates, infeasibility or
typed equality:

| Planner fact | Used for | Catalog source checked here |
|---|---|---|
| Entity's `table` | Every row | `pg_class` (relkind, partition, row-level security, rules, inheritance) |
| Column set, `column` names | Rows; required-column detection | `pg_attribute` (live user columns) |
| `pg_type`, `max_length` | Typed equality, casts, ranges, length infeasibility | `pg_attribute.atttypid` and `atttypmod`, `pg_type` |
| `nullable` | `is_null`, NOT NULL infeasibility, omitted columns | `attnotnull` |
| `has_default`, `generated` | Omitted columns, unsupported constraints | `atthasdef`, `attidentity`, `attgenerated` |
| `collation` | String equality | `attcollation` → `pg_collation.collisdeterministic` |
| `primary_key` | Key assignment, duplicate infeasibility | `pg_constraint` (`p`) |
| `unique` with `nulls` | Duplicate infeasibility, NULL collisions | `pg_index` (unique, not primary), `indnullsnotdistinct`, `indnkeyatts` |
| `foreign_keys` | Dependencies and unresolved references | `pg_constraint` (`f`) |
| `has_insert_triggers` | Obligation | `pg_trigger` (firing, INSERT) |
| `trigger_written` | Obligation | `pg_trigger` plus the exporter's write analysis |
| `encoding` | varchar length infeasibility | `pg_database` encoding |
| `CONTEXT_EXACT` | All of the above | **This verifier is what discharges it**, relative to one snapshot |

Key assignment and fillers are harness policies, not catalog facts. Only their types are implied here.

## 2. API

```
verify_context(context, *, registry, context_trust, snapshot, snapshot_trust, policy=None) -> VerificationResult
snapshot_digest(snapshot: CatalogSnapshot) -> str
```

- **`context` and `context_trust`:** the step 5B `SchemaContextConfig` and its harness `TrustedPlanning` pin. They're
  checked with `state_planning.build_context` against `registry`.
- **`snapshot`:** a `CatalogSnapshot`, a mapping or the model.
- **`snapshot_trust`:** a harness-built `TrustedSnapshot(exporter, source_label, exported_on, pinned_digest)`.
- **`policy`:** a `VerificationPolicy`.

  | Setting | Default | Effect |
  |---|---|---|
  | `supported_major_versions` | 15 and 16 | Other PostgreSQL versions are unsupported |
  | `allow_opaque_for_known_types` | true | A context column described as opaque may stand for any type |
  | `allow_unlisted_relations` | true | Tables the context doesn't describe are allowed |

**Errors, not outcomes:**

| Error | Raised when |
|---|---|
| `TypeError` | The registry or policy is the wrong type |
| `UntrustedInput` | The context fails its pin or binding; `snapshot_trust` isn't a genuine `TrustedSnapshot`; or the snapshot's digest isn't the pinned one |
| `SnapshotError` | The snapshot is malformed. Nothing is compared |

**`VerificationResult`:**

- `outcome`;
- `snapshot`, a `SnapshotIdentity`;
- `context_digest`;
- `findings`, sorted, each `(kind, code, entity, field, detail)`;
- `checked_entities` and `assumptions`;
- `scope`, fixed to "consistent with the supplied catalog snapshot only; not a check of any live database".

## 3. The snapshot

A typed, bounded representation shaped like the PostgreSQL 15 and 16 catalogs. Every field is required, unknown
fields are refused, and strings follow the v2 text policy.

| Section | Fields |
|---|---|
| `database` (`pg_database`) | `datname`, `encoding`, `datlocprovider`, `datcollate`, `datctype` |
| `namespaces` | `oid`, `nspname` |
| `types` | `oid`, `typname`, `nspname`, `typtype` |
| `collations` | `oid`, `collname`, `nspname`, `collprovider`, `collisdeterministic` |
| `relations` (`pg_class`) | `oid`, `relname`, `nspname`, `relkind`, `relispartition`, `relrowsecurity`, `relhasrules`, `relhassubclass` |
| `attributes` (`pg_attribute`) | `attrelid`, `attnum`, `attname`, `atttypid`, `atttypmod`, `attnotnull`, `atthasdef`, `attidentity`, `attgenerated`, `attisdropped`, `attcollation` |
| `constraints` (`pg_constraint`) | `oid`, `conname`, `contype`, `conrelid`, `conkey`, `confrelid`, `confkey`, `condeferrable`, `condeferred`, `convalidated`, `conindid` |
| `indexes` (`pg_index`) | `indexrelid`, `indrelid`, `indkey`, `indnkeyatts`, `indisunique`, `indisprimary`, `indisvalid`, `indisready`, `indimmediate`, `indnullsnotdistinct`, plus `has_expressions`, `has_predicate` and `nondefault_semantics` |
| `triggers` (`pg_trigger`) | `oid`, `tgrelid`, `tgname`, `tgenabled`, `tgisinternal`, `timing`, `level`, `events`, plus `writes`: relations the trigger function may write, or `null` for unknown |
| `coverage` | Per section, whether it lists everything in scope. Triggers: `none`, `schema` or `database` |

**Refused as malformed:**

- repeated oids, attnums or column names;
- foreign keys without a target or with mismatched key lengths;
- `condeferred` without `condeferrable`;
- a non-unique primary-key index;
- `indnkeyatts` larger than `indkey`;
- `NULLS NOT DISTINCT` before PostgreSQL 15;
- an unknown `contype`, `relkind`, `typtype`, provider or enabled state;
- a lowercase or invalid encoding name;
- export format versions other than 1.

## 4. Snapshot identity and trust

- **Digest:** `snapshot_digest` is SHA-256 over `b"deployspecter:catalog-snapshot:v1\0"` plus canonical JSON. Lists
  are sorted by their keys, so their order in the input doesn't matter, while order within `conkey` and `indkey` is
  kept. The result is reported as `catsnap1:<hex>`, and one digest is pinned in the tests.
- **`SnapshotIdentity` records:**
    - the identity and export format versions;
    - the PostgreSQL major version;
    - the database name and schema;
    - the encoding, locale provider, `datcollate` and `datctype`;
    - the harness-supplied provenance: `exporter`, `source_label` and `exported_on`.
- **Binding:** the context's `schema_digest` must equal the snapshot digest, or the result is `MISMATCH`, so a
  context is tied to the snapshot it was written from.
- **Trust comes only from harness objects.** Snapshot content can't carry trust (an extra `trusted` field is
  refused), and a dict or an unvalidated `TrustedSnapshot` is refused.
- **What a digest proves.** It proves the snapshot is the one pinned. It doesn't prove the snapshot came from where its
  provenance says, that it's complete beyond what `coverage` declares, or that it's current. A `MATCH` is never a
  statement about a live database.

## 5. Outcomes

- **`MATCH`:** no finding at all. That requires every coverage section to be complete, triggers to be covered for the
  whole database, and every check to succeed.
- **`MISMATCH`:** the context and the snapshot disagree on a fact the planner uses. For example:
    - a column's type, nullability, default, generated status or collation determinism;
    - a varchar length;
    - a column, relation, primary key, unique set (including its NULL handling) or single-column foreign key that one
      side has and the other doesn't;
    - the insert-trigger or trigger-written flags;
    - the encoding, or the context naming a different snapshot.
- **`UNSUPPORTED`:** the snapshot shows behaviour the planner can't represent and that could affect a candidate or an
  infeasibility claim:
    - CHECK, exclusion and constraint-trigger constraints;
    - partial, expression, invalid or not-ready, deferrable, and non-default-semantics unique indexes, and
      primary keys backed by such an index;
    - deferrable keys;
    - composite or deferrable foreign keys, or foreign keys to undescribed tables;
    - domains, unless described as opaque;
    - unbounded varchar;
    - views, partitioned tables and partitions, row-level security, rules and inheritance;
    - an unsupported PostgreSQL version;
    - unlisted relations, under a strict policy.
- **`INCOMPLETE`:** the snapshot doesn't say enough:
    - any section not declared complete, even when every lookup succeeded;
    - an expected entry missing from an incomplete section;
    - triggers listed only for the schema, or not at all;
    - a firing trigger whose writes are unknown;
    - a key or unique index naming columns the context doesn't describe.

**Precedence:** `MISMATCH`, then `UNSUPPORTED`, then `INCOMPLETE`, then `MATCH`. Every finding is listed whatever the
outcome.

**Absence is never evidence.** Something missing from a section that isn't declared complete is `INCOMPLETE`, never a
mismatch and never a match. A constraint present in the catalog but not representable, such as a deferrable foreign
key or a partial unique index on a declared column, is reported as `UNSUPPORTED`, never as the context's constraint
being missing.

## 6. Unsupported catalog features (documented, not modelled)

- **Data:** row data, existing rows and sequence values, so key collisions with setup rows aren't checked.
- **Expressions:** CHECK expressions, default expressions, generated-column expressions, domain constraints, enum
  labels and numeric typmods.
- **Trigger internals:** `WHEN` conditions, BEFORE triggers that modify rows, statement-level effects, and function
  bodies. Write analysis comes from the exporter, as `writes`.
- **Configuration:** `session_replication_role`, assumed to be `origin`, privileges, row-level security policies and
  event triggers.
- **Collations:** behaviour beyond determinism.
- **Newer catalogs:** PostgreSQL 17 and later shapes (not-null constraints in `pg_constraint`, the builtin locale
  provider). These are outside the default policy.

## 7. Tests and breakage

| Check | Result |
|---|---|
| Ruff | Clean |
| Verifier tests | **92 passed** |
| Contract, planner and verifier suites, root environment, `-m "not live"` | **978 passed**, 1 skipped (the LangGraph adapter file; `langgraph` isn't installed there) |
| Same, isolated environment | **1,026 passed** |
| v1 offline tests | 37 passed |
| Frozen-contract integrity | Passed; the 14 manifest files and the committed step 5B files are unchanged |
| Live tests | 0 collected |

`MODEL_API_KEY` was unset throughout.

**Fixtures,** all synthetic and hand-written:

- **Columns:** identical pairs; type, nullability, default, generated and identity mismatches; varchar length,
  unbounded varchar and domains; missing, extra and dropped attributes.
- **Types and collations:** missing types and collations, with and without coverage; deterministic and
  nondeterministic collations; encodings.
- **Trust:** forged, stale and untrusted pins; the context's own pin; snapshot binding.
- **Uniqueness and keys:** NULLS DISTINCT and NOT DISTINCT; composite, partial, expression, invalid, deferrable,
  INCLUDE and non-default-semantics unique indexes; primary keys; CHECK, exclusion and constraint triggers.
- **Foreign keys:** missing, extra, composite, deferrable and undescribed-target keys.
- **Triggers:** firing, internal, disabled and replica triggers; unknown writes; schema-only and absent trigger
  coverage.
- **Relations:** row-level security, rules, partitions, views and inheritance; unlisted relations; versions.
- **Robustness:** 17 malformed structures; precedence; determinism, list-order independence and the pinned digest;
  import, AST and network isolation; inputs left unchanged.

**Deliberate breakage: 68 of 68 caught.** Two early survivors were weak tests, not equivalent mutants:

- **Unsorted findings:** the test's columns happened to sort the same either way.
- **The digest's domain tag:** the test compared against differently canonicalized JSON.

Both were fixed by a sharper ordering test and a pinned digest.

**Defects found and fixed during development:**

1. **A constraint present in the catalog but not interpretable was also reported as missing,** for example an
   unresolved foreign-key target or a deferrable or partial unique index on a declared column. That turned
   `UNSUPPORTED` or `INCOMPLETE` into `MISMATCH`.
2. **`INCLUDE` columns were read as part of a unique key,** making the real constraint look weaker. `indnkeyatts` is
   now required.
3. **A unique index with a non-default operator class or collation could change equality silently.** It's now an
   explicit, unsupported flag.

**Found in the final review, and fixed:**

4. **A `MATCH` didn't require complete coverage.** Incomplete relation, type or collation sections were silent
   whenever lookups happened to succeed. Under the strict unlisted-relations policy, incomplete relation coverage
   skipped the check instead of reporting it as `INCOMPLETE`.
5. **Primary-key indexes weren't checked.** A primary key can be attached to an existing index, so a partial,
   expression, invalid or non-default-semantics index behind a primary key is now `UNSUPPORTED`.

## 8. A future catalog exporter

A separately approved, independently reviewed exporter would connect like this:

- **Export:** run read-only catalog queries against a disposable database, under Gate 2A supervision, and emit this
  `CatalogSnapshot` format.
- **Analyse triggers:** fill `writes` from a trigger-function analysis, such as Phase 9B's `triggers.py` approach,
  reviewed separately. Anything unclear stays `null`.
- **Declare coverage honestly:** a section is complete only if its query had no limit.
- **Pin:** the harness pins the digest and records provenance, and the context's `schema_digest` names the snapshot.
- **Treat a `MATCH` as dated:** it holds for that snapshot only. Every executed scenario should record the snapshot
  identity, and a schema change means a new snapshot and a new verification.

## 9. Remaining limitations

- **Trust in the exporter is required.** The digest doesn't verify origin, freshness or completeness, and trigger
  write analysis is the exporter's claim.
- **`MATCH` is about consistency, not database behaviour.** It doesn't establish that candidate rows can be built:
  CHECK constraints make a snapshot `UNSUPPORTED`, and data and sequences aren't represented.
- **Schemas that use CHECK constraints or row-level security can't reach `MATCH` today.** That's conservative, and
  deliberate.
- **Not proof against hostile code in the same process.**
