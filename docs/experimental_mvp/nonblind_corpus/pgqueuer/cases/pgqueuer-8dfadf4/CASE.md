# Case `pgqueuer-8dfadf4`: cancelling a running job frees its concurrency slot (NONBLIND development)

**Frozen before any live run of this case.** Later corrections go in `ERRATA.md`; this file is never edited.

**Label: NONBLIND development** (`ds-nbc/1`). The developer chose the case after reading the fix, its docs and the
maintainers' test, and wrote the integration and oracle. It is not blind, confirmatory or independent evidence. A
reproduction re-observes a **documented upstream defect**; it is not a new finding.

## The upstream defect

| | |
|---|---|
| Repository | https://github.com/janbjorge/pgqueuer (MIT) |
| Fix (R2) | `8dfadf4ea3a6fb6d60ac970d799faf8338f9d03f`, 2026-10-24, "fix(queries): keep a picked row when a job is cancelled (#1032)"; tree `d9d7b31b…`, `pgqueuer/` tree `9b7989b3…` |
| Buggy parent (R1) | `f258cbcafab840612b8c42b1db60ab553b67c093` (first parent); tree `f9fbfbac…`, `pgqueuer/` tree `3c624764…` |
| Archives | `git archive --format=tar <commit> pgqueuer`, normalized with `sealed_evaluation.normalize_archive`: R1 `378ee759…6310e2`, R2 `d985c468…fe260b`. Reproduce with `research_tools/nbc/integrations/pgqueuer_pin.py` |
| Change | 3 source files, +17/−7: `log_job`'s cancel path (a NULL owner) no longer deletes a `picked` row; the in-memory adapter follows. No schema change: both trees render the same install DDL |
| Issue | #1031, as cited by the maintainers' test docstring (the issue itself wasn't read) |
| Release | none yet contains it (after v1.6.0) |

## Contract (single-version)

No mixed-version deployment is documented for this pair, so each run uses **one revision on a fresh schema**.

The property, **H1: `concurrency_limit` is a global cap on the jobs of an entrypoint that are running.** Its sources,
all present at the buggy parent:
- `docs/guides/concurrency-control.md`: "at most 5 jobs run across your entire fleet"; "Setting
  `concurrency_limit=1` guarantees that only one job of this entrypoint runs at a time across all workers."
- The `QueueManager.entrypoint` docstring: "Limits are enforced at the database level across all workers."
- `docs/guides/job-cancellation.md`: "Cancellations are best-effort and may not halt a job already underway." A
  handler that goes on after a cancel request is documented behaviour, and it is still running.
- The maintainers' test added with the fix, `test_cancel_keeps_picked_row_until_worker_logs`: after the running job
  of a `concurrency_limit=1` entrypoint is cancelled, a dequeue returns nothing until its worker logs.

## Oracle (`oracle.py`, sha256 `cb59b573…9a80a6`, frozen with this file)

The trusted handler writes `start`, `handler_steps` × `step` and `end` rows to `ds_pgq_runs`, one statement each,
tagged by execution. The rows are ordered by their sequence, which is the order the statements executed.
- **VIOLATED:** at some row, more executions have started and not ended than the limit the workers registered.
- **NO_VERDICT:** the evidence is incomplete.
- **HOLDS:** otherwise.

The handler interval lies inside the job's real running interval, so an overlap of handler intervals is a real
overlap. The oracle doesn't refer to the fix's code.

## Instrument (`research_tools/nbc/integrations/pgqueuer_cancel.py`, `pgqueuer-cancel-sv/1`)

- **One coordinated run per schedule.**
  - Containers: one restricted container holding the revision, plus the coordinator.
  - Actors, each on its own stepped connection:
    - `setup`: PGQueuer's `Queries.install`, plus the trusted handler's table.
    - `w1` (and `w2`): a `QueueManager` each, in continuous mode, batch size 1, entrypoint `ds_job` with the workload's limit.
    - `prod`: public `Queries.enqueue`, then one `Queries.mark_job_as_cancelled`.
  - The run is seeded by `real_executor.seed_for`.
- **Gates.** They make the operations meaningful. The explorer decides everything else.
  1. Nothing runs before the install.
  2. The producer waits until every worker has polled once.
  3. The cancel statement (`log_job`) waits for `w1`'s first claim.
  4. `w2` claims only after `w1`'s first claim. Workers are identical, so this only names which one claims first.
- **Stop.** The coordinator stops when every job's handler has returned (or the job was cancelled before it started).
- **Verdicts.** Only a SOUND run gets an oracle verdict: isolated, corroborated against the server log, and coordinated to completion.
- **Adapter.** `pgqueuer_hist_adapter` is a declared variant of the 1.6.0 Driver-port adapter.
  - It verifies the image's tree against the integration's declaration: archive sha256 and a digest over every file.
  - It accepts only the reviewed driver file (sha256 `006aed38…`, identical in R1 and R2).
  - **One deviation:** the notification watcher polls with a 0.05 s timeout instead of 1.0 s, because psycopg holds its connection lock while it waits. The SQL, the callbacks and the dequeue/log paths are PGQueuer's own.
- **Image.** Built by the integration: the runner's Dockerfile and hash-locked requirements, plus one added in-container mode. No `research_tools/isolated` file is changed.

## RA-DET workloads (exactly 3, fixed here)

All three use two workers, `concurrency_limit = 1` (the tightest limit) and cancel job 0, which is enqueued first
and so is claimed first:
1. `{"jobs": [{"n":0,"priority":0,"cancel":true},{"n":1,"priority":0,"cancel":false}], "workers":2, "concurrency_limit":1, "handler_steps":1}`
2. the same with `"handler_steps":2`
3. `{"jobs": [{"n":0,…,"cancel":true},{"n":1,…},{"n":2,…}], "workers":2, "concurrency_limit":1, "handler_steps":2}`

Budget (plan §5): B = 12 per workload slot pair, 3 replicates × 4 schedules, T = 900 s per case and arm. Runs are
never retried. Replicate r uses workload r.

**Bring-up.** Before the reproduction, harness bring-up runs may be made:
- on **R2 only**;
- with a workload outside this set and no cancel;
- under `bringup_<N>/`.

They test the machinery, carry no verdict for this case, and are all recorded. Any change to the workloads or the
oracle after the first reproduction run is a new numbered attempt with a written reason.

## Predictions

- **R1 (`f258cbc`).** When the explorer grants the cancel's `DELETE` while job 0's handler is between `start` and
  `end`, and then grants a worker's dequeue and job 1's `start` before job 0's `end`, two executions overlap: H1 is
  VIOLATED. Other orders hold.
- **R2 (`8dfadf4`).** The cancelled job's row stays `picked`, so its slot stays held until its worker logs. Job 1
  starts only after that, and H1 holds in every run.

## Competing explanations, recorded before running

1. **Heartbeat staleness.** A stale re-pick (heartbeat older than 30 s) could also run a job twice. Runs are much
   shorter than 30 s, and heartbeats are flushed every 3.75 s. Any such run would show the same job in two executions.
2. **The watcher deviation.** It changes only how long psycopg's lock is held per poll, not which statements are
   sent or what they do.
3. **Gate 4 or the window gate.** They order statements and never write anything; both trees run under the same gates.

## Exposure

- **What was read.** The Track A agent read the fix's message, its whole `pgqueuer/` diff, the docs and RELEASE
  hunks and the maintainers' SQL test hunk. During design it read the parent's `qm.py`, `qb.py`, `queries.py`,
  `buffers.py`, `heartbeat.py`, `composer.py`, `schema_ddl.py` and psycopg driver, and its concurrency and
  cancellation guides.
- **When.** All of it on 2026-11-08, before this file and before any live run.
- **Training data.** Exposure of the public repository through training data can't be excluded.
