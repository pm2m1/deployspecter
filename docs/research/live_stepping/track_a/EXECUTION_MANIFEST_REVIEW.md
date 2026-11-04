# Track A: execution manifest review and readiness matrix (milestone 3)

- **Status:** nonnormative review. **No PostgreSQL has run.** No gate is claimed to pass.
- **Manifest v1** (`execution_manifest_v1.json`, committed at `fb0b4fd`) is **EXECUTION_BLOCKED** (section 1).
- **Manifest v2** (`execution_manifest_v2.json`) is the enforceable replacement. It is **WAITING_FOR_EXECUTION_APPROVAL**.
- **The driver** (`research_tools/track_a_run.py`) enforces v2. Both the manifest and the driver were reviewed and committed for
  separate review.

## 1. Why version 1 can't be enforced

| v1 item | Problem | v2 replacement |
|---|---|---|
| OP-6 "after each reset read" | Gate 2A's bounded reset and its initial-state read happen inside `SupervisedRunner.run`. **No public hook** runs between them and the child. `SupervisorHooks.after_reset` is a test seam, not part of the accepted interface | **OP-6i:** construction verified on 2 driver-initiated resets with the bridged seed (parent read plus independent check). **Every run's** parent-read initial state is checked exactly. **OP-6f:** an independent final-state read after every run, through `explore_live`'s public `check` callback, and after each confirmation attempt |
| OP-2 pin used for every run | Each Gate 2A reset drops and recreates `ds_run`, so **OIDs change, and the pinned snapshot digest can't be re-matched later** | The pin covers the batch-start export. **OP-2d:** a batch-end export whose OID-free `structural_digest` must equal the pinned export's. Per-run schema identity rests on the hashed setup SQL in each scenario digest |
| `chain_digests` checked after export | Request, context and bridge digests include the snapshot digest, so they **can't** equal v1's dry-run values | `invariant_digests`: registry, contracts, spec, plan identity, legacy seed. `scenario_digests` are unchanged |
| Parent statements | No mechanism reconciled them | `research/track_a/parent_reconciliation.py`, applied to every parent session |
| Driver | Didn't exist | `research_tools/track_a_run.py` |

## 2. Version 2, verified item by item

| Item | Finding |
|---|---|
| PostgreSQL 16 image | `postgres:16@sha256:1beaa3084eee…`, the Gate 2A reproduction digest. The operator starts it (OP-1); the driver never runs a command |
| Cluster and schema target | Gate 2A preflight: `cluster_name=ds_live_disposable`, logging settings, a `_test` database. Admin user `deployspecter`, database `deployspecter_test` (`config.TEST_DB_NAME`). Schema `ds_run` |
| Allowed SQL and counts, parent | **op2a and op2b:** 12 statements each (`BEGIN`, 10 catalog SELECTs, `COMMIT`). **op2c:** 7. **op6i:** 9 (`BEGIN`, 3 × count and md5, 1 typed row predicate with 3 parameters, `COMMIT`). **op6f:** 5 per run. **op2d:** 12. Each set pinned by hash. Exact text, order and parameters, reconciled from the server log |
| Allowed SQL, application | 6 statement texts per variant (`FOR UPDATE` only in fixed), reconciled by Gate 2A unchanged. Counts per run depend on the schedule, and Gate 2A checks each run's exactly |
| Role privileges | **Child:** Gate 2A's per-run role in `deployspecter_runner` (SELECT, INSERT, UPDATE, DELETE on `ds_run`). **Parent:** admin superuser. **Read-only transactions on the parent are not a privilege boundary.** Reconciliation detects deviation; it doesn't prevent it |
| Reconciliation coverage | **Child:** every supervised run (Gate 2A). **Parent:** op2a, op2b, op2c, op2d, op6i (2), op6f (every run and attempt). **Not reconciled (residuals):** the driver-initiated `Sandbox.reset` calls (Gate 2A's own code), the log reader's position and read queries (outside the windows), and Gate 2A's own parent statements inside supervised runs (its accepted model) |
| Initial-state and catalog identity | The pinned export needs two agreeing exports, an agreeing `information_schema` re-read, cluster, database and role checks, a reviewer attestation, and a MATCH. Construction: 2 independent checks and parent reads. Every run: an exact initial state. Batch end: equal structural digest. **No time-typed columns** (enforced), because Gate 2A's parent read masks timestamps |
| Independent oracle | `tests/live_app/properties.py`, SHA-256 pinned in `source_sha256`. It imports nothing from the tool or the application. It runs on the parent read, and the independent read must equal it (OP-6f) |
| Buggy and fixed equivalence | The scenarios differ **only** in `lock_row` (test `test_both_variants_share_one_workload`). Digests are pinned. The manifest must state exactly `{buggy: False, fixed: True}` or the driver refuses |
| Replay | Candidate: the first buggy run violating R1 or R2, in `explore_live` discovery order. Strict replay of its `chosen_schedule`, one attempt at a time, `replay_policy.confirm`: 3 comparable attempts or at most 125. See section 4 |
| Bounded runs and timeouts | Exploration: 200 runs or 120 s per variant. Confirmation: 125 attempts. `LiveLimits` and `SupervisorLimits` as listed. **Batch: 3,600 s, now enforced by the driver** before every run and parent operation |
| Cleanup | Parent connections close in context managers. `RealEffects.close` runs in `finally`. Gate 2A tears down every run and sweeps stale `ds_run_` roles before the next. The operator's `docker stop` discards the tmpfs data. Any exception writes `failure.json`, classified `refused_fail_closed` or `unknown_effect_stop`, with `retried: false` |

## 3. Issues blocking execution

1. **Parent SQL is uncharacterized.** The OP-2, OP-6i and OP-6f statements have never run on PostgreSQL 16. Unverified
   points include:
    - `indclass[s.k]` and `indkey[s.k]` subscripting;
    - `string_to_array(indkey::text, ' ')`;
    - `information_schema` default and identity semantics for `serial`;
    - whether psycopg sends any statement on connect;
    - whether parameterless statements log as `statement:` or `execute <unnamed>:` (both are accepted).

   All are **NEEDS_DATABASE_CHARACTERIZATION.**
2. **The CSV log layout** is assumed to be PostgreSQL 16's 26 columns, the same assumption Gate 2A makes.
3. **Background log lines:** only checkpointer lines are allowed inside a parent window. Any other background line
   fails closed. That is safe, but may cause false stops.
4. **The parent is a superuser.** A genuinely least-privilege exporter (the child read-only role in the earlier gate
   review) remains an alternative needing its own implementation.
5. **No public post-reset hook,** so the catalog isn't re-verified between Gate 2A's reset and each run. The residual
   is disclosed in the manifest.

## 4. Replay and oracle findings

- **Implemented, automated:** amendment 002's candidate rule, comparable-attempt classes, stopping rule and 125 limit,
  plus an independent oracle read on every attempt.
- **The signature is a candidate.** `server_choice_signature` records deadlock and serialization failures by actor and
  step, and wakeups only where two or more actors waited. For this scenario:
    - buggy runs take no locks, so the signature is empty;
    - the fixed version has a single waiter, which isn't recorded.

  **It needs review before it is frozen with the tool.**
- **Not automated, and required by 002 for a detection claim:**
    1. the oracle author's review that the requirement was actually breached;
    2. because the path is plain psycopg, reproduction on uninstrumented connections, using up to 125 attempts if it
       depends on a PostgreSQL choice.

  Neither is in the manifest. **So gate G7 can at most establish 002's automated confirmation. Calling the result a
  "detection" in 002's sense needs both manual steps.** This is a synthetic demonstration, not an evaluation, but the
  wording in any report must respect this.
- **Criterion 3 of 002** ("server choice not observed on fix") needs the fixed runs' signatures reported. The bundle
  holds each run's records, so this can be computed, but no report field for it exists yet.

## 5. Readiness matrix

| Component | Status | Basis |
|---|---|---|
| Catalog exporter (`catalog_export.export_snapshot`, `assemble`, `structural_digest`) | IMPLEMENTED_AND_OFFLINE_TESTED; NEEDS_DATABASE_CHARACTERIZATION | Fake rows shaped like PostgreSQL 16 reach MATCH, and inconsistent data is refused. The SQL itself is untested |
| Independent re-read (`cross_check_read`) | IMPLEMENTED_AND_OFFLINE_TESTED; NEEDS_DATABASE_CHARACTERIZATION | As above |
| Parent statement reconciliation (`parent_reconciliation`) | IMPLEMENTED_AND_OFFLINE_TESTED; NEEDS_DATABASE_CHARACTERIZATION | 13 parametrised breakages plus 5 log, marker and tag refusals. Every mutant of its checks caught (11 of 11). The real log format is unverified |
| State verification (OP-6i, OP-6f, `check_initial_state`) | IMPLEMENTED_AND_OFFLINE_TESTED; NEEDS_DATABASE_CHARACTERIZATION | Fake connections. The typed-predicate SQL is untested |
| Gate 2A execution (public `run`, `plain_serial`, `explore_live`, `preflight_problems`) | Reused unchanged; the driver's use is IMPLEMENTED_AND_OFFLINE_TESTED with mocks | No private attribute used (AST test). Gate 2A itself is accepted only within its model |
| Buggy and fixed variants | IMPLEMENTED_AND_OFFLINE_TESTED | Harness-owned `lock_row`, refused if altered |
| Independent oracle | IMPLEMENTED_AND_OFFLINE_TESTED | Source hash pinned, import independence tested, two reads compared |
| Semantic replay | IMPLEMENTED_AND_OFFLINE_TESTED (automated part); **BLOCKED for a 002 detection claim** | The manual oracle-author review and uninstrumented reproduction are absent (section 4) |
| Evidence binding | IMPLEMENTED_AND_OFFLINE_TESTED | Binding errors on foreign scenarios or unverified states |
| Cleanup | IMPLEMENTED_AND_OFFLINE_TESTED (driver side); NEEDS_DATABASE_CHARACTERIZATION (Gate 2A teardown on this run) | Context managers, `close` in `finally`, `failure.json` classification. The operator stops the container |

**By operation:**

| Operation | Status |
|---|---|
| OP-1 | Operator action, ready once approved |
| OP-2 | NEEDS_DATABASE_CHARACTERIZATION |
| OP-2b | IMPLEMENTED_AND_OFFLINE_TESTED |
| OP-2d | NEEDS_DATABASE_CHARACTERIZATION |
| OP-3a | IMPLEMENTED_AND_OFFLINE_TESTED |
| OP-3 | IMPLEMENTED_AND_OFFLINE_TESTED |
| OP-4 | IMPLEMENTED_AND_OFFLINE_TESTED |
| OP-5 | IMPLEMENTED_AND_OFFLINE_TESTED. BLOCKED as a 002 detection claim |
| OP-6i | NEEDS_DATABASE_CHARACTERIZATION |
| OP-6f | NEEDS_DATABASE_CHARACTERIZATION |
| OP-7 | Operator and Gate 2A |

## 6. Offline evidence

`tests/research_tracks/test_track_a_milestone3.py`, 37 tests, all passing. Every external effect is mocked, and the
fake server log is written from what the driver actually executed.

**Driver mutants** (behavioural tests only, the source-hash checks excluded so they can't catch a mutant trivially):

| Result | Count |
|---|---|
| Caught | 9 |
| Caught by its targeted test (`verify_sources`) | 1 |
| Equivalent | 1: an unreachable defence-in-depth line, which only matters if the preceding refusal is removed |

**Gap found and closed:** before the targeted run-phase test was added, a reconciliation failure inside the run phase
was caught only by a later defence.
