# Gate A2.1: the Gate A2 trust boundary and the transferred Gate A1 controls

**Status: offline only. Gate A2 is not executable.** `execution_manifest_v3.json` lists eight execution blockers, and the driver refuses `export` and `run` while any remain. `run` also needs a code change, because `GATE2A_SECTION_IMPLEMENTED` is `False`. Nothing here connects to a database, starts a container or calls a model.

Nothing here is host-level isolation or a hostile-code sandbox. The parent is a superuser on a disposable cluster, and Gate 2A's child runs as the same OS user. Reconciliation detects deviation; it doesn't prevent it.

## 1. What changed and what didn't

| File | Change |
|---|---|
| `src/deployspecter/research/track_a/gate_a2_controls.py` | **new**, pure: the fixed target, credential and model refusal, the preparation policy, the Gate 2A reset model, per-attempt state records |
| `research_tools/gate_a2_run.py` | **new** Gate A2 driver (manifest version 3). It imports Gate A1's `Batch`, `RealEffects`, `record_approval` and statement builders unchanged |
| `docs/research/live_stepping/track_a/execution_manifest_v3.json` | **new**. It supersedes version 2, which must not be approved |
| `tests/research_tracks/test_gate_a2_1.py` | **new** offline acceptance tests |
| `research_tools/track_a_run.py` (the version 2 driver) | **unchanged**. Gate A1's version 4 manifest pins it, so editing it would invalidate the Gate A1 evidence's source check |
| Gate A1 code (`gate_a1_characterize.py`, `characterization.py`, `parent_reconciliation.py`, ...) | unchanged |
| Gate 2A (`src/deployspecter/live/{driver,runner,trace,explore,supervisor,worker,serverlog,workload}.py`), `database.py`, WorkloadSpec v2 | unchanged |

## 2. The call graph as the code stands

### 2.1 The version 2 driver (`track_a_run.py`), as written

`main` parses the arguments, checks the manifest digest, the approval (digest and phase) and the source digests, creates the evidence directory, and constructs `RealEffects`, the only place anything connects. `RealEffects.__init__` (line 380):

1. `load_settings()` reads `.env` into `os.environ`. There is no fixed-target check and no model-key refusal.
2. `Sandbox(settings)` (`database.py:64`):
   - `ensure_test_database`: on the **initial database**, `CREATE DATABASE deployspecter_test` if it is missing, and a `DO` block that runs `CREATE ROLE deployspecter_runner`;
   - the **admin** session on the test database: `SET lock_timeout`, `pg_advisory_lock`, `SET lock_timeout = 0`;
   - the **monitor** session.

   Both sessions keep psycopg's default preparation (prepare after five executions), and neither has an application name.
3. `SupervisedRunner(box, LiveLimits, SupervisorLimits)`.
4. A **log-reader** session on `settings.test_dsn`, with default preparation, named `ds_parent_log_reader`.

`export_phase`:

- `effects.preflight()` runs `runner.preflight_problems()` on the admin session;
- `effects.reset(SETUP_SQL, [])` runs `Sandbox.reset` on the admin session, **unreconciled**;
- op2a, op2b and op2c run as tagged parent sessions, each reconciled.

`run_phase`:

- pins and offline-chain checks;
- twice: `effects.reset(seed)` (`Sandbox.reset`, unreconciled), `effects.snapshot()` (`Sandbox.snapshot`, unreconciled), then op6i reconciled;
- two `plain_serial` and two `explore` calls into Gate 2A, with op6f reconciled after every run;
- strict replays;
- op2d reconciled.

### 2.2 One Gate 2A run (`SupervisedRunner._execute` and `_run_child`, `supervisor.py:622-690`)

Every row in this table runs on the **Sandbox admin session**, through the supervisor's bounded executor.

| Line | Step | Statements on the admin session |
|---|---|---|
| 636 | `preflight_problems` | 10 × `SELECT current_setting(%s, true)`; the `pg_db_role_setting` count |
| 640 | `_harden_cluster`, once per runner | `SELECT datname FROM pg_database WHERE datallowconn`; `REVOKE ALL ON DATABASE <each> FROM PUBLIC` |
| 653 | `_bounded_reset` | `_sweep_stale_roles` (`ALTER ROLE ... NOLOGIN`); `_end_outside_sessions` (`_ACTIVITY_SQL`, then `pg_terminate_backend` for **every client session that isn't admin, monitor or a parent actor**); `DROP OWNED` and `DROP ROLE` for stale roles; `pg_prepared_xacts` and `ROLLBACK PREPARED`; `SET lock_timeout` and `SET statement_timeout`; **`Sandbox.reset`**: DROP SCHEMA, CREATE SCHEMA, SET search_path, BEGIN, the setup SQL, the seed INSERTs, COMMIT, then 3 GRANTs to `deployspecter_runner`; restoring the limits |
| 654 | initial state | **`Sandbox.snapshot`**: `information_schema.tables`, then `SELECT * FROM <table> ORDER BY 1` for each table; timestamps masked |
| 658-659 | Gate 2A's window | `ServerLog.position` (`pg_current_logfile`, `pg_stat_file`); `SELECT 'ds_mark_start_<run>'` |
| 663 | session counter | `pg_stat_database.sessions` |
| 667 | `_create_role` | `SET log_statement = 'none'`, then **unlogged** `CREATE ROLE ds_run_<run> LOGIN ... PASSWORD <verifier>` and `RESET log_statement`; `GRANT CONNECT`; 5 × `ALTER ROLE ... SET` |
| 673 | `_spawn` | **child process**: a clean environment (no `DATABASE_URL`, `PG*` or API key), macOS `sandbox-exec` confinement, the login on stdin |
| 393-452 | `_supervise` | `_check_activity` every `poll_s`, which **flags every unregistered client session**; registration and privilege checks; on deadlines `pg_cancel_backend` or `pg_terminate_backend`, then a process-group kill |
| 678 | `_tear_down` | `ALTER ROLE NOLOGIN`; kill the process group; scan for survivors (`ps`); `_end_outside_sessions` again; `ROLLBACK PREPARED`; a quiescence query |
| 680 | final state | **`Sandbox.snapshot`** |
| 681 | `_drop_role` | `DROP OWNED`, `DROP ROLE`, then a `pg_roles` count |
| 689 | `_reconcile` | its end marker and log reads; `serverlog.reconcile` **skips every row of the harness's own sessions** (line 175) |

The **child** (`worker.py`) connects as `ds_run_<run>` with `prepare_threshold=None`. It opens one actor session per actor and a monitor session, named `ds:<run>:<name>`. It runs `live_app.reservations` through Gate 2A's stepped connection. `explore_live` (`explore.py:63`) calls `runner.run` for each schedule and then the driver's `check` callback, which is where op6f runs.

### 2.3 Classification

| Operation | Class |
|---|---|
| Manifest, approval and source checks; `plan`; the offline chain; pins; scenario digests; acceptance | offline-only planning or verification |
| Gate A2 parent sessions (op0p, op0r, op2a/b/c/d, op6i, op6f, op9c) and the Gate A2 log reader | trusted administrative parent action, **reconciled** (Gate A1's code) |
| `Sandbox.__init__` (`ensure_test_database`, the advisory lock) | trusted administrative parent action, **unreconciled** (B6) |
| `_harden_cluster`, `_bounded_reset` (including `Sandbox.reset`), `Sandbox.snapshot`, `_create_role` and `_configure_role`, `_tear_down`, `_drop_role`, the supervisor's log reads, markers and counters | trusted supervisor control, **unreconciled** (B1, B2, B6) |
| `_check_activity`, `_escalate`, `_kill_group`, `_kill_survivors` | trusted supervisor control (detection and enforcement) |
| The child process, its actor and monitor sessions, and `live_app.reservations` | untrusted execution, reconciled per run by Gate 2A's `serverlog.reconcile`; capabilities unchanged |

### 2.4 The design conflict, found in the code

The supervisor ends every client session that isn't its own:

- `_end_outside_sessions` runs before each reset and again at teardown;
- `_check_activity` flags any unregistered session while the child runs.

`_harness_pids` contains only the admin session, the monitor session and parent actors.

So **version 2's persistent log reader would be terminated at the first supervised run**. The next parent operation would fail with a lost connection, an `unknown_effect_stop`. `TestTheTrustBoundary::test_the_supervisor_ends_a_client_session_it_doesnt_own_such_as_a_log_reader` shows this against Gate 2A's own `_end_outside_sessions`. It is blocker **B3**.

## 3. Target and credential controls (implemented, offline-tested)

Before any connection, `gate_a2_run.preconditions`, called by `RealEffects` ahead of Gate A1's `RealEffects`, does the following:

1. **Model refusal.** Any environment variable named `MODEL_*`, `*API_KEY*`, `OPENAI_*`, `ANTHROPIC_*` or `META_AI_*` is refused. So is any such key in `.env`, and any model-related module already imported (`deployspecter.meta_client`, `guided`, `analyzer`, `api`, `cli`, `benchmark`, the release `pipeline`, `openai`, `anthropic`, `httpx`). A fresh-interpreter test confirms that the driver's own import closure contains none of these.
2. **libpq overrides.** Any `PG*` environment variable is refused: `PGHOSTADDR` reroutes, `PGOPTIONS` can turn logging off, and `PGSERVICE`, `PGPASSFILE` and `PGPASSWORD` are other secret or routing sources.
3. **One credential source.** `DATABASE_URL` must be in `.env` (read without changing `os.environ`), with no other key, and must **not** also be in the process environment.
4. **The fixed target.** The URL may contain only host, port, database, user and password, and must equal the manifest's `target`: `127.0.0.1:55433`, initial database `deployspecter`, user `deployspecter`. Port `55432` (`deployspecter-pg`) is a **forbidden target** and is refused by name; there is no fallback. Messages name keys, never values.
5. **After connecting**, Gate A1's `RealEffects` checks the following unchanged:
   - host, port, database and user, from `conn.info`;
   - `prepare_threshold`;
   - the authentication method, through reconciliation.
6. **Server instance.** The export's `system_identifier` and `pg_postmaster_start_time` (already in the export's verified metadata query) must be the same in both exports and in the run batch's re-export. The cluster name must be `ds_live_disposable`, the version PostgreSQL 16, and the database the test database.

For Gate 2A, `harness_settings` builds `Settings` from the target alone. It sets `model_api_key=None` and uses URLs that name `hostaddr` and `application_name=ds_gate2a_harness`, so `PGHOSTADDR` can't reroute them and the server log names the harness sessions. A test checks the host and port that the supervisor would hand the child. Nothing the command line reaches uses it yet.

## 4. psycopg preparation policy

| Connection | Decision |
|---|---|
| Gate A2 parent sessions and the log reader | `prepare_threshold=None` through Gate A1's `RealEffects`, checked before use. Statements logged under a prepared name fail reconciliation. Pinned in the manifest's `client_connection_policy` |
| Gate 2A's admin and monitor sessions | **Proposed** (B5): `apply_harness_policy` sets `prepare_threshold=None` through psycopg's public attribute after `Sandbox` opens them, then checks each session's target. Without this, the supervisor's repeated statements (10 settings reads per run, activity polls every 10 ms) would be logged as `execute _pg3_N`. Not characterized and not yet used |
| Gate 2A's child sessions | Already `prepare_threshold=None` in `worker.py`; checked from source |
| Application connections | None beyond the child's actor connections. No conclusion is extended to any other client |

Prepared-statement names are never allowlisted. The float8 log model is Gate A1 version 4's own `literal` and `float8_log_text`. Parameters without an established form are refused before any SQL runs.

## 5. Reset and per-run state assurance

**Implemented (offline):**

- **Driver-initiated resets and reads are reconciled.** Every reset Gate A2 makes itself is Gate A1's op0r on a tagged session, with a fixed statement sequence and fixed parameters. The same holds for its initial-state checks (op6i) and reads (op6f). All eight parent statement sets have the same digests as Gate A1 version 4's (`shared_with_gate_a1_v4` in the manifest, checked by a test). Version 2 did these on the unreconciled `Sandbox` admin session.
- **No repair.** A reset that fails part-way stops the batch at the failing statement. There is no repair, no retry and no further SQL, and a `failure.json` is left.
- **Server and catalog binding.** The construction batch requires the attested server instance and the attested structural catalog.
- **The typed initial state, twice.** The bridged state is verified twice, by the typed check and the typed read. Its `verified_initial_digest` equals what `Sandbox.snapshot` returns for the same state. That equality holds only for the types in `COMPARABLE_TYPES`; other types are refused.
- **Per-attempt records.** `Attempts` gives each Gate 2A attempt an id and ties it to its run id and scenario digest. It compares the attempt's initial state with the verified digest and with the bridge's typed rows, and its final state with the reconciled op6f read. It refuses a run id seen twice. Each record's `initial_source` says that this initial read is Gate 2A's own and unreconciled.
- **A model of Gate 2A's reset.** `gate2a_reset_statements` is Gate A1's reset plus the three GRANTs. A test runs Gate 2A's own `Sandbox.reset` against a recording session and checks that it sends exactly that sequence.

**Not implemented: the design conflict (B1).** Gate 2A's per-run reset, initial read and role setup can't be moved onto a reconciled session without bypassing a Gate 2A boundary:

- They run inside `SupervisedRunner._run_child`, between `_end_outside_sessions` (which verifies that no outside session exists) and the child's spawn.
- The only hooks there are `SupervisorHooks.after_reset`, `on_tick` and `before_reconcile`, which are test seams.
- Injecting a `Sandbox` subclass with a reconciled `reset` would open new sessions that aren't the supervisor's, right after the supervisor verified that none exist. That defeats `_end_outside_sessions`. The session would also run outside the reset limits that `_bounded_reset` sets on the admin session, and would need a log reader that the supervisor's next sweep would terminate.

So, as instructed, this is reported, not built. Every Gate 2A privileged action therefore **blocks execution** through B1, B2 and B6.

## 6. Tests and mutation testing

- `tests/research_tracks/test_gate_a2_1.py`: **104 tests**, offline. Fake connections and Gate A1's fake server log (which logs PostgreSQL 16.15's real op7b parameter details) are used; nothing is real. The test function names and their `parametrize` entries are the case list.
  - **Positive:**
    - the manifest and target;
    - the DSN and the Gate 2A settings;
    - the clean preconditions;
    - statement sets equal to Gate A1 version 4's;
    - the Gate 2A reset model equal to `Sandbox.reset`'s;
    - the export and construction batches fully reconciled and audited;
    - the verified digest equal to the `Sandbox.snapshot` form;
    - the per-attempt records.
  - **Negative:**
    - wrong host, port, database, user or keys;
    - the forbidden port;
    - libpq variables, `DATABASE_URL` in the environment, model credentials in the environment or `.env`, model modules;
    - a manifest that loosens any control;
    - a wrong preparation policy (in the manifest, a harness session, a parent statement logged prepared, a default-prepared reader);
    - an unexpected administrative statement;
    - a missing reset statement;
    - an unapproved reset parameter;
    - a mismatched tag;
    - an extra session in a window or between operations;
    - a missing cleanup statement;
    - a partial reset;
    - a changed initial state between repeats or in an attempt;
    - another server instance;
    - a structurally changed catalog;
    - a cross-check disagreement;
    - an export from the wrong server;
    - a tampered approval;
    - a changed source file;
    - a duplicate run id.

  Every refusal raises at once. The partial-reset and failure-record tests check that no further SQL runs and that nothing is retried. The target, environment and `.env` tests check that the refusal message contains no secret value.
- **Isolated mutation testing.** The scratchpad harness gives each mutant a fresh copy of the tree and re-pins the manifests to that copy, so a source-digest check can't kill a mutant. A per-mutant watchdog of 60 s kills the process group. The 40 mutants on the new code (21 on the controls module, 19 on the driver) were all killed by test failures, with no timeouts and the worktree unchanged. Three survived a first pass (k-20, g-09, g-19). Each pointed to a missing test, which was added before the full rerun.

## 7. Remaining blockers

They are listed in `execution_manifest_v3.json`:

| Blocker | What it needs |
|---|---|
| B1 | Gate 2A's per-run reset and initial and final snapshots are on the unreconciled admin session (section 5) |
| B2 | Role creation runs with `log_statement='none'`, so that interval can't be audited from the log |
| B3 | The supervisor terminates and flags any outside session, including a Gate A2 log reader; the reader's lifecycle across runs is undesigned |
| B4 | The whole-batch Gate A2 audit (section 8) is a design only |
| B5 | Turning off preparation on the harness sessions is proposed, not reviewed or characterized |
| B6 | `Sandbox` construction and `_harden_cluster` are privileged and unreconciled |
| B7 | Amendment 002's manual confirmations |
| B8 | A separately reviewed, signed approval |

## 8. Whole-batch Gate A2 audit: proposed design (for separate review)

**Markers.**

- `ds_parent_mark_batch_start_<nonce>` and `..._batch_end_<nonce>` bracket the whole Gate A2 batch.
- Each Gate 2A run keeps its own `ds_mark_start_<run>` and `ds_mark_end_<run>`.
- Each parent operation keeps its `ds_parent_mark_start` and `ds_parent_mark_end`.

**Session attribution.** Every session whose rows fall between the batch markers must be exactly one of the following, or the audit fails:

1. **A Gate A2 parent session**: application name `ds_parent_<op>_<nonce>`, reconciled by Gate A1's `reconcile_parent_session`, as now.
2. **A Gate A2 log reader.** Because the supervisor terminates outside sessions (B3), the reader can't persist across Gate 2A runs. The proposal is a *segment* reader: one reader per stretch between Gate 2A calls.
   - It is opened after a run returns and closed before the next call.
   - Each segment is identified by its own markers, and its rows must be only the allowed reader statements, on consecutive session lines.
   - The audit requires the segments to be disjoint and ordered, and each to be disconnected before the next Gate 2A window begins.
   - A reader the supervisor terminates would be a `FATAL` row, and it fails the audit.
3. **Gate 2A's harness admin session**: application name `ds_gate2a_harness`, with the backend pid taken from `conn.info` before the batch. Its rows are matched against a fixed per-run template derived from `supervisor.py`:
   - preflight;
   - hardening (first run only);
   - the sweep (variable counts of `_ACTIVITY_SQL` and `pg_terminate_backend`, bounded and allowed only against non-batch pids);
   - `gate2a_reset_statements`, exactly, with parameters;
   - the snapshot reads;
   - the run markers and log reads;
   - the session counter;
   - `SET log_statement = 'none'` followed by `GRANT CONNECT` as the **next logged** row. The gap between them is the declared, unauditable B2 interval;
   - `ALTER ROLE ... SET` ×5;
   - the supervision polls (bounded by `run_s / poll_s`);
   - teardown;
   - the final snapshot;
   - `DROP OWNED` and `DROP ROLE`.

   All of this requires B5, the unprepared harness.
4. **Gate 2A's harness monitor session**: same name, a different pid. During supervised runs it must issue nothing.
5. **Gate 2A child sessions**: user `ds_run_<run>`, application name `ds:<run>:<actor|monitor>`, inside that run's Gate 2A window. Attributed only if the run's trace is in the evidence with Gate 2A's `serverlog.reconcile` clean, and if `<run>` is an attempt recorded by `Attempts`.
6. **Cancel probes and terminations.** A bare `connection received` is allowed only inside a halted run's window (as Gate 2A already allows). A `FATAL: terminating connection due to administrator command` row is allowed only for a pid that the admin session's logged `pg_terminate_backend` named in the same window.
7. **Background processes**: the checkpointer only, as in Gate A1.

**Order across the batch:**

1. the parent export and construction batches, as implemented;
2. for each attempt: Gate 2A's reset, initial snapshot, role, child and teardown, all inside the harness template; then op6f on a new reader segment; then `Attempts.record`;
3. the batch-end op2d export, compared structurally;
4. op9c cleanup;
5. the batch end marker, a bounded wait for the log flush, the final whole-batch reconciliation.

**Evidence provenance.**

- One directory per batch, written with exclusive creates.
- The approval is copied byte for byte.
- Each artifact names the manifest digest and the batch nonce.
- The audit records each session's class.

**What still can't be audited:**

- the B2 interval on the admin session;
- anything the superuser parent does that PostgreSQL doesn't log, such as client-side effects or `log_statement` set outside a session;
- processes that scrub Gate 2A's run marker;
- file reads on the host (Gate 2A's macOS confinement reduces this risk; it is not a sandbox).

## 9. The smallest next step

Review section 8 and decide B2 and B3. That means choosing between accepting the unlogged role-creation interval explicitly and changing how role creation is recorded, and approving the segment-reader design.

Then implement the harness template matcher (B4) offline, against a recorded Gate 2A run made with fakes, and characterize B5 in a separately approved Gate A1-style run.

Gate A2's buggy/fixed demonstration stays blocked until then.
