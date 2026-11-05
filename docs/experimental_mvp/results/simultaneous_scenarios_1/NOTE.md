# simultaneous_scenarios_1: the first full scenario set (NONBLIND), at revision `665bf74`

One run per scenario, each under the `alternate` policy (process round-robin).

| Scenario | Verdict | Outcome |
|---|---|---|
| `old_to_new` | SOUND | 321 statements corroborated, 0 mismatched; 0.26.0 ran jobs 0–2, pushed by 0.25.1; no violation |
| `new_to_old` | SOUND | 318; 0.25.1 ran jobs 0–2, pushed by 0.26.0; no violation |
| `distinct` | SOUND | 292; each version ran its own queue; no violation |
| `cancel_new_new` | SOUND | 68; the cancel held: the job ends `failed` and attempts 0, though its handler ran. This is the 0.26.0 guarantee |
| `cancel_old_old` | SOUND | 68; **P7**: the 0.25.1 cancel was overwritten by 0.25.1's own late completion (`succeeded`) |
| `cancel_new_old` | SOUND | 68; **P7**: the 0.26.0 cancel was overwritten by the 0.25.1 worker's unfenced completion |
| `cancel_old_new` | SOUND | 68; **P7**: the 0.25.1 cancel (claim not invalidated) was overwritten by the 0.26.0 worker's fenced completion |
| `compete` | **REFUSED** | see below |
| `retry_handoff` | **REFUSED** | see below |

## Why the two runs were refused (both are harness faults; neither has a property verdict)

- **`compete`.** `AsyncRun.quiesced` held a 0.26.0 maintenance task's **COMMIT**: its claim query had completed, and the task was exiting its transaction.
  - The session then sat idle in a transaction, and after 15 s the server ended it.
  - The corroborator refused the run on that unexplained FATAL row. The stop hung, and the coordinator aborted on the epoch timeout.
  - **Fix (next commit):** only work starting on an idle connection is held; statements inside an open transaction, and its exit, go on. The regression test `test_a_transaction_open_when_the_hold_starts_still_commits_and_only_new_work_is_held` fails without the fix.
  - This was latent in the rc1 quiesce. It could only cause refusals, never a false acceptance.
- **`retry_handoff`.** My scenario gated the 0.25.1 worker's later claim queries.
  - A gated claim sits inside its transaction (BEGIN granted), and Chancy's one-connection pool blocks the worker's update flush behind it.
  - So the failed attempt was never persisted (the job stays `running`), the session idled to the server's timeout, and the coordinator reported a stall.
  - **Fix:** the scenario keeps one short gate only (0.26.0's claim waits for 0.25.1's first claim), with `max_attempts = 2` and a count-based stop.

Neither run is retried in place. The whole set is re-run at the fixed revision as `simultaneous_scenarios_2`.

## What the P7 results mean

See `docs/experimental_mvp/simultaneous/PROPERTIES.md`. Of the four cancel cells, only `cancel_new_new` has a P7 that is a documented guarantee (**G**), and it held.

- **`cancel_old_old`.** This reproduces the behaviour described by the 0.26.0 changelog's "Fixed cancellation races that could overwrite cancelled results", on real 0.25.1 code. But:
  - the case was chosen after reading that changelog (NONBLIND);
  - notifications are disabled, and 0.25.1's `cancel_job` says that is required for cancelling a running job.

  So it is a reproduction of a *described* historical behaviour, under a configuration the release doesn't promise to support. It is not an independently discovered defect.
- **The mixed cells.** They show that the 0.26.0 cancellation guarantee holds only when every worker is 0.26.0, and every canceller too. The documented upgrade procedure ("Stop your workers" before migrating) excludes this deployment. This is a mixed-deployment hazard, not a defect of either release.
