# Simultaneous mixed-version Chancy pilot: frozen application properties (NONBLIND)

**Frozen before any simultaneous execution.** This file, and its checker `src/deployspecter/experimental/chancy_properties.py`, are committed before the first simultaneous run. Every run report records both files' sha256. A change after the first run would be a new property version (v2), and its results would be reported separately.

**Authorship.** These properties were written by the same developer who wrote the harness, after reading Chancy 0.25.1 and 0.26.0's source, docstrings and the 0.26.0 changelog. They are **not** independently authored. The pilot is NONBLIND.

**Interpretation.** No violation counts as an application defect until three things have been checked:
- the property's classification;
- the execution trace (server-log corroborated);
- the competing legitimate explanations listed here.

## What Chancy documents, and what it doesn't

| Source | Statement | Consequence here |
|---|---|---|
| 0.26.0 `CHANGELOG.md` | "This release requires that you run migrations… **Stop your workers** and use `chancy.migrate()`" | Running 0.25.1 and 0.26.0 workers at the same time on the migrated schema is **outside the documented upgrade procedure**. A violation that needs both versions running is a mixed-deployment hazard, not a defect of either release |
| 0.26.0 `cancel_job` docstring | "Marks the job failed and invalidates its execution claim, preventing a late completion from overwriting the cancellation" | A documented 0.26.0 guarantee, when every worker is 0.26.0 |
| 0.25.1 and 0.26.0 `cancel_job` docstrings | "This will attempt to cancel a job that is currently running… Notifications must be enabled for this to work" | The harness runs `notifications=False`, because the listener opens an unseamed connection. Cancelling a running job is therefore outside what 0.25.1 promises in this configuration |
| 0.26.0 `CHANGELOG.md`, Fixes | "Fixed cancellation races that could overwrite cancelled results" | A defect class that 0.26.0 says was present before. **Known before testing** (NONBLIND) |
| Both releases, executor `on_job_completed` | A failure gives `attempts + 1` and `RETRYING` until `attempts + 1 >= max_attempts`, then `FAILED`. Success gives `SUCCEEDED` with `completed_at` | Read from the code, not a documented guarantee |
| Both releases, fetch | `UPDATE … FROM (SELECT … FOR UPDATE OF jobs SKIP LOCKED)`: an atomic claim | Read from the code. Chancy does not document exactly-once execution |

## Properties

Classification key:
- **G**: documented application guarantee;
- **H**: documented test-harness assumption;
- **X**: exploratory hypothesis.

| Id | Property | Class | Applies to |
|---|---|---|---|
| P1 `unique_ids` | Every pushed job id is distinct, and present exactly once in the final table | H (structural: the primary key) | all |
| P2 `executions_match_attempts` | For every job not cancelled, the number of handler executions equals its final `attempts`, and is at most `max_attempts`. So with `max_attempts = 1`, a job runs at most once | H. The atomic claim makes a second execution impossible without a retry. Chancy promises no exactly-once delivery | all |
| P3 `no_lost_success` | A job whose handler returned without error, and that was not cancelled, ends `succeeded` | H | all |
| P4 `final_consistency` | `succeeded` implies `attempts >= 1` and `completed_at` set. `failed` implies `attempts = max_attempts`, or the job was cancelled | H (from the executor code) | all |
| P5 `cross_version_readable` | Every job pushed by one version and claimed by the other ran its handler: unpacked and executed, with no worker exception | X (the 0.26.0 migration's compatibility for old writers and readers) | jobs that cross versions |
| P6 `no_stuck_jobs` | After a quiescent completion (every worker stopped cleanly), no job is `pending`, `running` or `retrying` | H | all |
| P7 `cancel_respected` | A job whose `cancel_job` updated one row ends `failed` | **G** for the 0.26.0 canceller with a 0.26.0 worker. **X** for the other cells (see below) | the cancel scenarios |
| P8 `retry_semantics` | A retried job's final `attempts` equals its executions. Its errors number its failed executions, and it ends `succeeded` exactly when one execution succeeded | H (from the executor code) | the retry scenario |

## Competing legitimate explanations, recorded before running

- **P7 with a 0.25.1 worker or a 0.25.1 canceller:**
  - notifications are disabled, so 0.25.1 makes no running-job cancel promise;
  - for a mixed cell, the deployment is outside the documented upgrade procedure;
  - a P7 failure there is evidence about behaviour, not a defect, unless the trace shows something else.
- **P2 or P3 under the declared mutant:** an injected control, never a Chancy defect.
- **Any property after a refused or aborted run:** nothing is concluded. A refused run (by corroboration, isolation or the coordinator) has no property verdict.

## The previously authored oracles, kept unchanged

`run_isolated.chancy_check` (C1–C3), `chancy_concurrent_check` (K1/K2) and `chancy_mixed_check` (M1–M4) are kept unchanged and unedited. Their provenance:
- written by this developer;
- K2 is the concurrent-worker form of P2;
- M2 and M3 are the sequential pilot's forms of P3 and P2.

## Amendments (before the first simultaneous execution)

1. **The checker's read query.** `JOBS_DETAIL_SQL` now reads `json_array_length(errors::json)` instead of `json_array_length(errors)`.
   - **Why:** after the migrations, `errors` is `jsonb`, and the first bring-up attempt failed reading the initial state.
   - **When:** before the window's start marker, so no simultaneous statement had run.
   - **Effect:** no property's definition or logic changed.
   - **Record:** amended in its own commit; the original freeze is `03967c4`.
