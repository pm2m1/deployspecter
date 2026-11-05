# Rolling-upgrade case: procrastinate 3.3.0 → 3.4.0 under its documented blue-green procedure

**Frozen before any workload ran.** This file and the case checker (`src/deployspecter/experimental/procrastinate_properties.py`) are committed before the first run, and every run report records both hashes.

**Classification: EXPLORATORY DEVELOPMENT CASE (NONBLIND), kept explicitly separate.**
- procrastinate is a **frozen-protocol development application** (`EVALUATION_PROTOCOL.md` §3.3), so it is never a prospective case.
- The frozen selection tool (`corpus_select.py` `3eaa449e…`, `results/corpus_selection_2_procrastinate`) reports its migration area as **NEEDS_PROTOCOL_RULING**. The rule knows only `.py` `migrations/` directories at the core package. Procrastinate's migrations are SQL files under `procrastinate/sql/migrations/`, and the tool sees only the Django contrib app's directory.
- The rule is **not** changed. The ruling it would need is proposed as issue #21: do versioned `.sql` migration files count as the core migrations directory?
- Its fix history was enumerated earlier by the main repository's Corpus v3 triage.

## Pair selection (a mechanical rule, fixed before either release's code or changelog was read)

"The most recent adjacent pair of migration-providing releases in which the newer release ships both a `pre` and a `post` migration." From the migration file names in the 3.10.0 sdist:
- the `pre`/`post` releases are 3.0.0, 3.1.0, 3.2.x and 3.4.0;
- 3.3.0 has a `pre` migration only;
- nothing after 3.4.0 has any.

So the pair is **3.3.0 (old) → 3.4.0 (new)**. No defect, fix or issue was used to choose it.

| | 3.3.0 | 3.4.0 |
|---|---|---|
| sdist sha256 | `5e03de13…88eb7` | `9cd3af41…7d684a4` |
| Tag commit | `cbaa7566…` | `41aec8f7…` |
| Uploaded | 2025-07-06 | 2025-07-18 |
| `queries.sql` | `c9b3fb0b…` | `e6b932bc…` |
| `schema.sql` | `bcc21931…` | `c796dca4…` |

`app.py`, `worker.py`, `manager.py` and `psycopg_connector.py` are byte-identical in both. The only query that differs is `retry_job`: `procrastinate_retry_job_v1` in 3.3.0, `procrastinate_retry_job_v2` in 3.4.0.

The new release's migrations:
- **`03.04.00_01_pre_add_retry_failed_job_procedure.sql`** (`6883b733…`) adds enum value `retried` and the function `procrastinate_retry_job_v2`, which retries `doing` **or `failed`** jobs.
- **`03.04.00_50_post_add_retry_failed_job_procedure.sql`** (`ac4021eb…`) replaces the status-event trigger function, so that `failed → todo` records a `retried` event.

## The documented contract (read at the 3.4.0 tag, `docs/howto/production/migrations.md`)

Verbatim:
- `pre` / `post` "indicates wether the migration should be applied before upgrading the code (`pre`) or after upgrading the code (`post`) in the context of a blue-green deployment."
- "The safer way, without service interruption … you'll need to stop at every version that provides migrations": apply `pre` → "deploy procrastinate" (the new version) → apply `post`.

**Reading.** Blue-green deployment means that old and new code run at the same time between "deploy" and the cutover. So, legally:
- 3.3.0 code runs on the 3.3.0 schema plus `pre`;
- 3.4.0 code runs on that same schema, alongside 3.3.0;
- `post` is applied only after every 3.3.0 process has stopped.

The document doesn't spell out a compatibility matrix. "Blue-green" is the stated context, and that is the reading used here.

## Phases (each a coordinated, corroborated run against the disposable database's `public` schema, because procrastinate's `post` migration hard-codes `public.`)

| Phase | Containers | What runs | Precondition (checked by the host) |
|---|---|---|---|
| 0 | A (3.3.0) | 3.3.0 applies its own schema (`schema_manager.apply_schema_async`). An old producer defers jobs, and an old worker processes them, including one job that always fails | an empty `public` schema |
| 1 | B (3.4.0, operator step) | the `pre` migration file, verbatim from the 3.4.0 package (sha256-checked), executed on one stepped session, as `psql` would | `retry_job_v1` present, `v2` absent |
| 2 | A (3.3.0) | old code still works on the `pre`-migrated schema: defer and process | `v2` present, trigger without `retried` |
| 3 | A (3.3.0) **and** B (3.4.0), simultaneously | old and new producers, old and new workers on one queue, and the new version's administrative retry of a *failed* job (its new feature, through v2). Failing and flaky jobs make both v1 retries (old worker) and v2 retries (new worker) happen | `v2` present, trigger without `retried` |
| 4 | B (operator step) | the `post` migration, applied only after container A has exited | every A process ended; `v2` present |
| 5 | B (3.4.0) | new code alone: defer, process, and retry another failed job | trigger with `retried` |

Operations use only public APIs:
- `App.open_async(pool=…)`, with a one-connection stepped pool;
- `task.defer_async`;
- `run_worker_async(wait=True, listen_notify=False, install_signal_handlers=False)`, stopped by cancellation at a safe point;
- `job_manager.retry_job_by_id_async`.

No job is written with SQL by the harness.

The tasks are trusted harness tasks, defined with procrastinate's own task API:
- `ds_ok(n)`;
- `ds_flaky(n, fail_in)`: raises when run by release `fail_in`, `retry=1`;
- `ds_fail(n)`: always raises, `retry=1`.

**Unsupported here, recorded as such:** LISTEN/NOTIFY (`listen_notify=False`), the sync connector, Django, aiopg/psycopg2.

## Properties (frozen; classification G = documented guarantee, H = documented harness assumption, X = exploratory)

| Id | Property | Class and source |
|---|---|---|
| R1 `terminal` | after a quiescent completion, every deferred job is `succeeded` or `failed` (none `todo` or `doing`) | H |
| R2 `retry_bound` | a job runs at most `max_attempts + 1` times, and `attempts` equals the number of retries (executions − 1) | **G** for the bound: `RetryStrategy.max_attempts` is "the maximum number of attempts the job should be retried". The equality is H, from `retry_job_v1`/`v2` (`attempts = attempts + 1`) |
| R3 `outcome` | a job ends `succeeded` exactly when its last execution returned normally | H (the worker calls `finish_job` with the status) |
| R4 `cross_version` | jobs deferred by one release and executed by the other ran with their arguments intact | X (implied by the blue-green contract) |
| R5 `admin_retry` | a `failed` job retried by 3.4.0's `retry_job_by_id_async` (v2) becomes `todo` with `attempts + 1`, and is then executed | **G**: the 3.4.0 `pre` migration's stated purpose ("Procedure to retry failed jobs") and the v2 body |
| R6 `events` | each job's `procrastinate_events` begin with `deferred` and end consistently with its status. A `failed → todo` transition records `retried` **only after** `post`. Before `post`, the old trigger records nothing for it, which is consistent with `post` being "after upgrading the code" | X: observed and reported. A missing `retried` event before `post` is **not** a violation by this contract |
| R7 `contract_order` | `post` is applied only after every 3.3.0 process has stopped | procedural, checked by the harness |

## Predictions, written before running

- R1–R5 hold. This was a published release pair, and the contract was followed.
- R6: `retried` events appear only for admin retries in phase 5, never in phase 3.

A clean result is the expected and acceptable outcome. No defect is being sought from the fix history.

## Competing explanations, recorded before running

1. **Disabled LISTEN.** `listen_notify=False` changes how jobs are picked up (polling), not the SQL a job's life sends.
2. **The `public` schema.** Required by the `post` migration's hard-coded `public.` prefix.
3. **The migration step.** Executing a migration file's content on one session in a transaction differs from `psql`'s statement-per-transaction. The `pre` file's `ALTER TYPE … ADD VALUE` is allowed inside a transaction on PostgreSQL 16, and the new value isn't used in the same transaction.
4. **The schedule.** Uncontrolled in-process task timing, as in every coordinated run.
