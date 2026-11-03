# Track A: execution manifest, version 1, for the first disposable PostgreSQL 16 run

- **Status: WAITING_FOR_EXECUTION_APPROVAL.** Nothing here has run, and no gate is claimed to pass.
- **Machine-readable form:** `execution_manifest_v1.json`. `tests/research_tracks/test_track_a_milestone2.py::TestManifest`
  checks every digest in it against the code it names.
- **Context:** `synthetic_demonstration`. It is never an evaluation arm, and never held-out or primary evidence.
- **Supervision model:** Gate 2A's accepted model only:
    - synchronous psycopg 3;
    - registered entrypoints in a supervised child;
    - one disposable PostgreSQL 16 cluster;
    - synthetic data.

  It is not a sandbox for hostile code. Gate 2B and Phase 9B aren't used.

## 1. Fixed configuration

| Item | Value |
|---|---|
| Cluster | `postgres:16@sha256:1beaa308…`, container `deployspecter-live-pg`, bound to `127.0.0.1:55433`, data on tmpfs. The `GATE2A_REPORT.md` server settings (`cluster_name=ds_live_disposable`, CSV logging with `log_statement=all`, `autovacuum=off`, …) |
| Isolation boundary | One disposable cluster. Admin credentials only in the git-ignored `.env`. `MODEL_API_KEY` unset. macOS `sandbox-exec` confinement for the child. The child runs as the same OS user, so this is detection, not a security boundary |
| Database and schema | Gate 2A's `_test` database, schema `ds_run`, rebuilt by its bounded reset |
| Setup | `tests/live_app/reservations.py:SETUP_SQL`, SHA-256 `3bb29d21…`. There are no time-typed columns, which the pinned export must confirm |
| Application | `tests/live_app/reservations.py`, unchanged: `reserve_seats(event_id, holder, qty)` and `release_seats(holder)`. The workload uses `reserve_seats` only |
| Variants | `buggy`: `lock_row=False`. `fixed`: `lock_row=True`. A harness-owned keyword argument, added after validation, and never a registered argument |
| Workload | Two actors: `reserve_seats(1, "alice", 2)` and `reserve_seats(1, "bob", 2)`. Spec `af319631…`, plan identity `vplan1:048a3a0c…` |
| Initial rows | `events(id=1, capacity=3, reserved=0)`. `reservations` and `rejections` empty |
| Scenario digests | buggy `6985443f…`, fixed `7ec485c2…` |

**Expected final states:**

| Path | events (id, capacity, reserved) | reservations | rejections | Oracle |
|---|---|---|---|---|
| Serial a then b | (1, 3, 2) | alice 2 | bob, 1 available | clean |
| Serial b then a | (1, 3, 2) | bob 2 | alice, 1 available | clean |
| Lost update (buggy only) | (1, 3, 2) | alice 2, bob 2 | none | R1, R2, R4 |

**Oracle:** `tests/live_app/properties.py`, pinned by SHA-256, imports nothing from the tool or the application.

- `check_invariants`, which covers R1 and R2;
- `check_results`, which covers R3;
- `check_serializable` against the two serial states from OP-3a, which covers R4.

The target requirements are R1 and R2.

## 2. Operations

**P = parent, the supervisor process with admin. C = child, the Gate 2A worker with the per-run role.**

| # | Purpose | Performed by | SQL | Role and privileges | Scope | Filesystem and process effects | P or C | Supervision and reconciliation | Cleanup | Failure classification | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| OP-1 | Start the cluster | The operator, with the exact `docker run` | Server start | Container root; admin password set once | The container only | Starts one container; tmpfs data | — | Gate 2A preflight refuses any other cluster | OP-7 | Preflight problem: stop | Image digest, settings |
| OP-2 | Two catalog exports and the independent re-read, after one bounded reset with `SETUP_SQL` | `research/track_a/catalog_export.py` (new) | `EXPORT_QUERIES` (`EXPORT_QUERY_SET_SHA256`) twice, and `CROSS_CHECK_QUERIES` once, each in `REPEATABLE READ READ ONLY`. Read-only catalog SELECTs, `pg_control_system()` | **Parent admin, a superuser.** A read-only transaction is not a privilege boundary. Only the fixed list and log reconciliation limit it | Catalogs of the `_test` database. `ds_run` relations; triggers database-wide | None outside evidence files | P | **New:** a parent-session window between markers, reconciled against the fixed lists. Gate 2A doesn't reconcile parent statements today | None needed | Inconsistent, unknown or disagreeing data: `ExportError`, stop, nothing pinned | `export_1.json`, `export_2.json`, `cross_check.json`, identity |
| OP-2b | Pins | `pin_from_evidence` with a reviewer `Attestation`, then `pin_context` | None | Reviewer action | — | — | Reviewer | Refuses unless exports agree, the re-read agrees, cluster, database and role are right, statements are exact, the attestation names the digest, and the result is MATCH | — | Any refusal: stop | `pins.json` |
| OP-3a | Serial reference states for R4 | `SupervisedRunner.plain_serial(buggy, ["a", "b"])` and `(["b", "a"])`, Gate 2A unchanged | The allowed application statements below | Gate 2A per-run role in `deployspecter_runner` (SELECT, INSERT, UPDATE, DELETE on `ds_run`) | `ds_run` | One child process per run | C, P resets and reads | Gate 2A: session counter, statement reconciliation, escalation | Gate 2A teardown per run | A flag or inconclusive: stop | Serial states |
| OP-3 | Explore the buggy version | `explore_live(SupervisedRunner, buggy, LiveBudget(200, 120 s))` with the oracle as `check` | As OP-3a | As OP-3a | `ds_run` | As OP-3a | C, P | As OP-3a, every run | As OP-3a | A flagged run fails G3 and G8. The candidate is the first R1 or R2 violation, in discovery order | Run records |
| OP-4 | Explore the fixed version (negative control) | As OP-3, fixed scenario | Fixed statements (with `FOR UPDATE`) | As OP-3a | `ds_run` | As OP-3a | C, P | As OP-3a | As OP-3a | Not exhausted, any inconclusive run, or any target violation: G6 fails | Run records |
| OP-5 | Confirm by replay | `SupervisedRunner.run(buggy, ExplicitSchedule(candidate.chosen_schedule, strict=True))`, one at a time, under `replay_policy.confirm` | As OP-3 | As OP-3a | `ds_run` | Up to 125 child runs | C, P | As OP-3a | As OP-3a | `failed_reproduction`, `rare_outcome_not_encountered` or `inconclusive_reproduction`: G7 fails | `replay.json` |
| OP-6 | Independent state check after each reset read, and on each comparable attempt's final state | `research/track_a/evidence_binding.py` (new). Templates SHA-256 `70d8585a…` | `count(*)` per table, server-side `md5(string_agg(t::text ORDER BY t::text))`, typed `IS NOT DISTINCT FROM $n::int4` predicates, in `READ ONLY` | **Parent admin, separate connection**, same caveat as OP-2 | `ds_run` | None | P | **New:** a parent-session reconciliation, as OP-2 | None | Disagreement: G2 fails, stop | `independent_checks.json` |
| OP-7 | Teardown | Gate 2A teardown, stale `ds_run_` role sweep, survivor scan, then `docker stop` | Gate 2A's own | Parent admin | Cluster | The container stops; tmpfs data is lost | P, operator | Survivor scan | — | A leftover role, session or process: fails, recorded | Teardown record |

## 3. Allowed statements and actions

**Application statements, by variant,** as the server logs them:

1. `BEGIN`
2. `SELECT capacity, reserved FROM events WHERE id = $1`, with ` FOR UPDATE` in the fixed variant
3. `UPDATE events SET reserved = $1 WHERE id = $2`
4. `INSERT INTO reservations (event_id, holder, qty) VALUES ($1, $2, $3)`
5. `INSERT INTO rejections (event_id, holder, seats_available) VALUES ($1, $2, $3)`
6. `COMMIT`

**Other statements:**

- **Gate 2A's own** harness and monitor statements, reconciled by Gate 2A as accepted.
- **The parent's new statements:** exactly `EXPORT_QUERIES`, `CROSS_CHECK_QUERIES` and the OP-6 templates, each set pinned by hash.

**Process actions, with no wildcards:**

- OP-1's exact `docker run`;
- `docker stop deployspecter-live-pg`;
- `env -u MODEL_API_KEY uv run python research_tools/track_a_run.py --manifest docs/research/live_stepping/track_a/execution_manifest_v1.json`. **This driver doesn't exist yet.**

## 4. Safety ceilings

| Limit | Value |
|---|---|
| `LiveLimits` | statement 5,000 ms, lock 3,000 ms, deadlock 50 ms, idle in transaction 15,000 ms, Python step 5 s, run 30 s |
| `SupervisorLimits` | Gate 2A defaults: handshake 20 s, escalation grace 1 s, kill wait 5 s, database call 15 s, reset lock 2,000 ms, reset statement 10,000 ms, quiescence 5 s, log wait 5 s |
| Exploration | 200 runs or 120 s per variant |
| Confirmation | At most 125 attempts |
| Whole batch | 1,800 s wall clock |

A ceiling reached is inconclusive or a stop, and is never success.

## 5. Fail-closed conditions

Any of these stops the batch, and nothing from it is scored:

- a preflight problem;
- a flagged or unscoreable run;
- a statement outside the allowed lists;
- two exports differing;
- the re-read disagreeing;
- a context verification other than MATCH;
- a time-typed column;
- an initial state that differs between runs or from the expected one;
- the independent check disagreeing;
- a fixed exploration that isn't exhausted, or has any inconclusive run;
- a binding error;
- an infrastructure failure;
- a teardown leftover.

**There is no automatic retry or replacement.**

## 6. Acceptance gates

`research/track_a/acceptance.py`, all `not_run` today except G0:

| Gate | Requires |
|---|---|
| G0 | Validated proposal: stages 1 to 3, recheck, plan identity, bridged |
| G1 | Catalog identity and completeness: supervised parent export, MATCH, two exports and the re-read agree, disposable cluster, full coverage, statements reconciled, no time types |
| G2 | Initial-state construction: exact rows, identical across runs, independently confirmed |
| G3 | Operation execution: every run supervised, scoreable and unflagged |
| G4 | Oracle independence: pinned source, no tool or application import, reads the parent's and the independent state |
| G5 | Buggy violation: a candidate violating R1 or R2 |
| G6 | Fixed negative control: the same workload, exhausted, no target violation, no inconclusive run |
| G7 | Semantic replay: `reproduced` under amendment 002, each comparable attempt independently checked |
| G8 | Refusal of unknown effects and incomplete evidence |
| G9 | Attribution and binding: every digest (including the binding digest and source hashes) and every run id |

## 7. Evidence destination

`docs/research/live_stepping/evidence/track_a/<run id>/`, a new directory:

- the manifest digest;
- exports, cross-check and pins;
- serial states;
- run records;
- confirmation;
- independent checks;
- the bound bundle and its acceptance judgement.

Nothing is written under the existing Gate 2A evidence. The cluster's logs stay on its tmpfs, apart from reconciled
excerpts.

## 8. Blockers: new supervision authority, and missing pieces

1. **Parent-side statements** (OP-2, OP-6) are new admin-privileged harness code. **Gate 2A reconciles the child's
   sessions, not the parent's,** so a parent-session reconciliation is new and needs independent verification.
2. **The driver `research_tools/track_a_run.py` doesn't exist.** It is the only component that connects to PostgreSQL.
3. **OP-2 and OP-6 SQL has never run on PostgreSQL 16.** For example, `indclass[s.k]` subscripting and the
   `information_schema` column semantics are untested.
4. **The replay policy's server-choice signature** is an unreviewed candidate.
5. **Gate 2A's parent read masks timestamps.** That is harmless here only because the schema has no time types.
