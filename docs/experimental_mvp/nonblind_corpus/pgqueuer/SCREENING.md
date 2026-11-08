# pgqueuer screening, ds-nbc/1 (NONBLIND development)

Rule: `SELECTION_RULE.md` §4, most recent first, at most 12 of the 33 candidates. Each row records the first failing criterion. The full record, including what was read for each candidate, is in `SCREENING.json`. Everything here was read by the pgqueuer Track A agent on 2026-11-08, before any live run.

| # | Commit | Subject | E1 | E2 | E3 | E4 | E5 | E6 | Outcome |
|---|---|---|---|---|---|---|---|---|---|
| 0 | `5c2e7c14` | fix(qm): retry a failed retry_job write until shutdown (#1039) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |
| 1 | `bb275006` | fix(executors): cap a retry backoff that overflows timedelta (#1040) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |
| 2 | `8dfadf4e` | fix(queries): keep a picked row when a job is cancelled (#1032) | pass | pass | pass | pass | pass | pass | ELIGIBLE |
| 3 | `64b71e93` | test: reproduce a stale worker resolving a re-picked job (#957) | pass | pass | **fail** | – | – | – | EXCLUDED (E3) |
| 4 | `2e3f9c95` | fix(qm): retry a job when a TaskGroup raises only RetryRequested (#927) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |
| 5 | `6128d934` | fix(sm): fail fast when the scheduler loop raises or is cancelled (#918) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |
| 6 | `bcf8f617` | fix(sm): keep the schedule heartbeat alive through errors and drain (#917) | pass | pass | **fail** | – | – | – | EXCLUDED (E3) |
| 7 | `0c8fedee` | fix(tm): let a caller's cancel propagate while a child task unwinds (#916) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |
| 8 | `7ce9f9bd` | fix(qm): release a retried job's context before re-queueing it (#913) | pass | pass | **fail** | – | – | – | EXCLUDED (E3) |
| 9 | `b370f9b0` | fix(completion): don't raise on exit when a waiter was cancelled (#907) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |
| 10 | `ebb01895` | fix: propagate cancellation in listener health checks (#905) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |
| 11 | `df04f1de` | fix(tm): make task completion removal idempotent (#906) | pass | **fail** | – | – | – | – | EXCLUDED (E2) |

Notes (first failing criterion, or eligibility):

- **0 `5c2e7c14`**: Behaviour after a failed retry_job write. The trigger is a failing database statement (the maintainers' test monkeypatches retry_job to raise ConnectionError); no sequence of public API calls by concurrent actors makes the write fail, and the engine has no fault injection.
- **1 `bb275006`**: Pure in-process arithmetic: initial_delay * multiplier**attempts overflowing timedelta (OverflowError).
- **2 `8dfadf4e`**: ELIGIBLE. Cancelling a picked job (mark_job_as_cancelled) deleted its row in the parent, freeing its concurrency_limit slot while the handler still ran. Public API (enqueue, mark_job_as_cancelled, QueueManager); database-mediated (the dequeue counts picked rows); runs under a declared variant of the Driver-port adapter (same driver file sha256 006aed38 in both trees); contract: concurrency-control.md and the entrypoint docstring at the parent, plus the maintainers' test; decidable from the handler's recorded intervals. Case pgqueuer-8dfadf4.
- **3 `64b71e93`**: Database-mediated and public (a stale worker resolving a job another worker re-picked after the heartbeat timeout). E3: the precondition is a worker that stays alive but sends no heartbeat for heartbeat_timeout of wall-clock time (the maintainers' test backdates the heartbeat with direct SQL). Under the lockstep coordinator a handler that blocks its event loop blocks every actor (no grants while any actor computes), so the takeover is reachable only by a gate that holds every statement of one worker until another claims; that is a forced schedule, not the stepping seam's exploration, and was not built in this sprint. Recorded as failing E3 (a contestable judgement: a holding gate might make it buildable as a forced-schedule case) rather than claimed.
- **4 `2e3f9c95`**: In-process exception handling: unwrapping a TaskGroup's ExceptionGroup of RetryRequested.
- **5 `6128d934`**: In-process task supervision of the SchedulerManager loop (fail fast on a raising or cancelled loop).
- **6 `bcf8f617`**: A schedule re-run when the SchedulerManager heartbeat stops during drain or after an error. The SchedulerManager isn't covered by the adapter, staleness is a hard-coded 30 s wall-clock interval and schedules are cron-granular; reaching it needs >30 s of in-process waiting and fault injection, beyond the coordinator's epoch and stop windows. The engine isn't extended for it.
- **7 `0c8fedee`**: In-process asyncio cancellation propagation (tm.cancel_and_wait, Heartbeat.__aexit__).
- **8 `7ce9f9bd`**: A retried job (RetryRequested, delay 0) re-picked by its own worker before the first dispatch task's done callback dropped its context, then skipped. The window is between a task's done-callback and the same worker's next dequeue result: an event-loop interleaving inside one process, which the seam doesn't control (CAPABILITY_MATRIX: grants are controlled, in-process task timing is not). It is also a liveness delay that stale recovery eventually ends.
- **9 `b370f9b0`**: In-process: CompletionWatcher.__aexit__ gathering a cancelled waiter.
- **10 `ebb01895`**: In-process: cancellation propagation in listener_healthy's wait.
- **11 `df04f1de`**: In-process: TaskManager done-callback set.remove vs discard.

Candidates 12 to 32 (21 commits): **NOT SCREENED (capacity)**.

One eligible candidate: `8dfadf4` → case `cases/pgqueuer-8dfadf4/`.
