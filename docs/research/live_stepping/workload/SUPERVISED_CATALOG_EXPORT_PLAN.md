# Supervised catalog export and driver verification: design only

- **Status:** nonnormative design document. Nothing here is implemented or has run, and it isn't part of the
  protocol.
- **Approval:** writing or committing this design approves no action. Every step marked **[approval]** needs its own.
- **Not a safety claim.** Gate 2A's harness detects runs that escape control; it isn't a sandbox for hostile code. A
  read-only role, or two matching snapshot digests, doesn't by itself establish safety or trust.
- **Gate 2B:** this design doesn't use the blocked database-backed LangGraph integration, or any application.

**Purpose.** Produce evidence the offline chain can't produce itself:

- a real `catsnap1` catalog snapshot, so a context can reach `MATCH` against something exported rather than
  hand-written;
- tests of the bridge's driver assumptions, D1 to D6 in `PHASE9B_BRIDGE_IMPLEMENTATION.md`.

## 1. Environment

- **Cluster.** A disposable local PostgreSQL 16 cluster, started as `GATE2A_REPORT.md` describes:
  `cluster_name=ds_live_disposable`, CSV logging with `log_connections`, `log_disconnections`, `log_statement=all`
  and full parameter logging, and `autovacuum=off`. The Gate 2A preflight refuses any other cluster. **[approval]**
  to start one.
- **Database.** One database per export, named `ds_export_<run id>`, created and dropped by the parent, which holds
  admin credentials. The child never holds them.
- **Data.** Synthetic schemas only: hand-written DDL, separately reviewed, with no application, held-out material or
  confidential schema.
- **Child process.** A clean environment, a neutral working directory, and macOS `sandbox-exec` confinement, as in
  Gate 2A: no reads or writes under home except its code and Python. That limits accidents. It doesn't contain
  hostile code, which could still read outside home or signal processes of the same user.

## 2. Credentials and supervision

- **Per-run login role,** created by the parent: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`,
  `CONNECTION LIMIT 1`, a random password, and `default_transaction_read_only = on`.
    - It gets `CONNECT` on the export database and `USAGE` on the target schema. The system catalogs are readable by
      default.
    - Its `CREATE ROLE` runs with statement logging suppressed, as Gate 2A already does, so no password verifier is
      logged.
- **Supervision** follows Gate 2A's model:
    - `pg_stat_activity` polling;
    - the session counter;
    - CSV-log reconciliation: **every statement the role ran must be one of the exporter's fixed queries**, so
      anything else fails the export;
    - escalation from cancel to terminate to SIGKILL;
    - a stale-role sweep and a survivor scan for the run marker;
    - dropping the role at the end.
- **Code.** Implementing this with Gate 2A's existing primitives must not edit `supervisor.py`, `worker.py`,
  `serverlog.py` or any other frozen file. If they lack a read-only export mode, a **new** wrapper module calls them
  unchanged. **[approval]** for that module.

## 3. Read-only exporter

A new module, unfrozen, with a **fixed, reviewed list of queries**. No text from a proposal, request or context
reaches SQL. **[approval]** to implement, and separately to run.

| `catsnap1` section | Source |
|---|---|
| `database` | `pg_database` for `current_database()`: `datname`, `pg_encoding_to_char(encoding)`, `datlocprovider`, `datcollate`, `datctype` |
| `namespaces` | `pg_namespace` |
| `types` | `pg_type` joined to `pg_namespace` |
| `collations` | `pg_collation`: `collprovider`, `collisdeterministic` |
| `relations` | `pg_class`: `relkind`, `relispartition`, `relrowsecurity`, `relhasrules`, `relhassubclass` |
| `attributes` | `pg_attribute` with `attnum > 0`, **dropped columns included**: type, `atttypmod`, `attnotnull`, `atthasdef`, `attidentity`, `attgenerated`, `attisdropped`, `attcollation` |
| `constraints` | `pg_constraint`: `contype`, `conkey`, `confrelid`, `confkey`, `condeferrable`, `condeferred`, `convalidated`, `conindid` |
| `indexes` | `pg_index`: `indkey`, `indnkeyatts`, `indisunique`, `indisprimary`, `indisvalid`, `indisready`, `indimmediate`, `indnullsnotdistinct`, and whether `indexprs` and `indpred` are set |
| `triggers` | `pg_trigger`: `tgtype` decoded into timing, level and events, `tgenabled`, `tgisinternal` |

**Two fields are derived:**

- **`nondefault_semantics`** is true if any key column's `indclass` isn't its type's default operator class
  (`pg_opclass.opcdefault`), or its `indcollation` differs from the column's.
- **Trigger `writes`** comes from a separately reviewed analysis of each trigger function's body. Anything it can't
  resolve is `null`, which the verifier treats as unknown. Until that analysis is approved, `writes` is always `null`,
  so trigger-written facts stay `INCOMPLETE`.

**Coverage flags** are true only for queries with no row limit, over the whole database for triggers, and only when
the query returned without error.

**Completeness and unsupported features.** The exporter reports row counts per section and the queries it ran. It
classifies nothing: unsupported features (CHECK constraints, row-level security, partial indexes and so on) appear
as catalog facts, and the offline verifier reports them as `UNSUPPORTED`.

## 4. A consistent snapshot

- **One transaction:** `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`, so every catalog query sees one MVCC
  snapshot.
- **Recorded inside it:** `pg_current_snapshot()`, `now()`, `pg_postmaster_start_time()` and
  `current_setting('server_version_num')`.
- **Nothing else connects.** The session counter and log reconciliation must show no other session in the database
  during the export. Any other session fails the export.
- **Exported twice.** Two exports, before and after the driver tests, must give identical snapshot digests, or the
  run fails closed.

## 5. Identity evidence

Collected by the **parent**, because some of these functions need privileges the exporter role doesn't have:

- `version()` and `server_version_num`;
- `cluster_name`;
- `system_identifier` from `pg_control_system()`;
- `current_database()`;
- `server_encoding` and the database encoding;
- `datlocprovider`, `datcollate`, `datctype`;
- the ICU version where used;
- `session_replication_role`;
- the psycopg version and its libpq version.

They're stored beside the snapshot as an evidence file, hashed, and referenced from the harness's `TrustedSnapshot`
provenance (`exporter`, `source_label`, `exported_on`). As the verifier documents, this records origin. It doesn't
prove it.

## 6. Pinning

1. The exporter writes the snapshot and identity evidence to a new evidence directory,
   `docs/research/live_stepping/evidence/catalog_export/<run id>/`. It never writes into existing Gate 2A evidence.
2. Harness code computes `snapshot_digest`, and a reviewer records it in a pinned configuration file.
   **[approval]** for each pin.
3. The schema context for that schema is written from the snapshot, its `schema_digest` set to the snapshot digest,
   and pinned in the same way. **[approval]**
4. `verify_context` runs offline against both pins. Only a `MATCH` lets the context be used, and only for that
   snapshot.

## 7. Driver behaviour tests (assumptions D1 to D3)

The same disposable cluster, a separate database, and a separate per-run role with `INSERT` and `SELECT` on one test
table. **[approval]** to run.

**Bindings.** Insert each projected scalar with psycopg parameters, as Phase 9B's `_insert_seed` does, then read it
back two ways: through a plain `SELECT`, and through `SELECT to_jsonb(t)` with the default jsonb loader. Each must
equal the typed value exactly.

| Value | Columns | Expected |
|---|---|---|
| `2^53 − 1`, `−(2^53 − 1)`, `2^53`, `2^53 + 1`, ±int64 extremes | `bigint` | Exact via `SELECT`. Through `to_jsonb`, record whether large values decode exactly. The bridge already refuses \|v\| ≥ 2^53 |
| `2^31 − 1`, `−2^31` | `integer` | Exact |
| `5e-324`, `±1.7976931348623157e308`, `0.1`, `−2.5` | `double precision` | Exact `float.hex()` round trip. Integral values may decode as `int` through `to_jsonb`; record it |
| −0.0, NaN, ±∞, inserted directly | `double precision` | Record `to_jsonb`'s output (`-0`, `"NaN"`, `"Infinity"`). These are stored-side cases the matcher meets |
| `True`, `False` | `boolean` | `bool`, never `int` |
| `None`, and an omitted column | nullable | NULL in both cases. A default applies only when the column is omitted |
| Unicode: combining marks, joiners, supplementary characters, right-to-left text | `text`, `varchar(n)` | Exact code points. `varchar` length counted in characters |
| Numeric-looking strings `"01"`, `"1e3"` | `text` | Stored exactly. The bridge still refuses them because of the matcher |

**Recorded from the CSV log:** the parameter types psycopg sent for each Python type.

**One documentation-only check:** that binding a Python `str` to an `integer` column would be coerced by the server.
That's the reason for the bridge's no-strings-for-non-text rule. The bridge itself never does this.

## 8. Triggers and session settings (assumption D5)

- **Session setting:** assert `session_replication_role = origin` in every test session.
- **A synthetic schema** with one trigger in each of the states `O`, `A`, `D` and `R`, each writing a marker row.
  Insert a row, and check that only `O` and `A` fired.
- **Each trigger's enabled state and the setting** are recorded with the snapshot.

## 9. Cleanup and failing closed

- **Teardown runs on every exit path,** including after the escalation steps: drop the export and test databases, drop
  the run roles, sweep stale roles, and scan for survivors.
- **Any failure stops the batch.** The run is marked not scoreable, and nothing is pinned. That covers an unexpected
  statement, another session, a digest that differs between the two exports, a privilege error, a timeout, or a
  teardown error.
- **No automated cluster recreation,** matching Gate 2A's accepted deviation. A poisoned cluster is replaced by hand.

## 10. Preserving Gate 2A

- **No edits to frozen code:** `driver.py`, `runner.py`, `trace.py`, `explore.py`, `supervisor.py`, `worker.py`,
  `serverlog.py`, `workload.py` and their tests stay unchanged, and the frozen-files guard test must keep passing.
- **Existing evidence stays as it is:** `GATE2A_REPORT.md`, `evidence/*.json` and the mutation records are never
  rewritten.
- **New code and evidence go in new files,** with their own freeze if adopted.

## 11. Actions needing separate approval

1. Starting the disposable PostgreSQL 16 cluster.
2. Implementing the supervised export wrapper, if Gate 2A's primitives need one.
3. Implementing the read-only exporter, and its fixed query list.
4. Implementing the trigger-function write analysis.
5. Writing the synthetic schemas used for export and driver tests.
6. Running an export.
7. Running the driver and trigger tests.
8. Pinning a snapshot, and pinning a context.
9. Committing any evidence produced.
10. Any later use of an exported context by the bridge, or by Phase 9B integration.

## 12. Not covered

- A Phase 9B run, an evaluation arm, a model request.
- Any external, held-out or confidential application.
- The blocked Gate 2B LangGraph path.
