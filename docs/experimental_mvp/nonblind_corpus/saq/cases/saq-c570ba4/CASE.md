# Case `saq-c570ba4`: a timed-out dequeue leaves its claimed job stranded `active` (NONBLIND development case)

**Frozen before the case's first live run.** Later corrections go in `ERRATA.md`.

## The upstream defect

| | |
|---|---|
| Repository | https://github.com/tobymao/saq (MIT) |
| Fix | `c570ba4ee8366404db97e21074390361328f0d8b`: "fix(queue): a timed-out postgres dequeue must take its buffered job". "The worker's schedule tick fetches on behalf of a waiting dequeue. Under load its transaction can outlast the waiter's dequeue_timeout, so the waiter reports an empty queue while the fetched job lands in the buffer unclaimed. A burst worker then stops with the job stranded active and never processed." 1 file in saq/, +6/-1, plus the maintainers' test `test_dequeue_takes_job_buffered_while_waiting`. First release with it: v0.26.5 |
| Parent | Includes `ad8d1c8`'s dequeue changes (jobs are buffered before any await) |

| Slot | Commit | Tree | Archive sha256 (normalized `git archive` of `saq/`) |
|---|---|---|---|
| R1 (buggy, the fix's first parent) | `59f32fce50428e5fb0073204f31cbe7ffc84c293` | `c7e084c9e3530c7adaab09e0809fb3a902bdc876` | `5adf904ddbb9360865e59bf8d2adc06fedd50eb7c438447fdb2622927203dd65` |
| R2 (fixed, the fix commit) | `c570ba4ee8366404db97e21074390361328f0d8b` | `2816d46a401cc85dd30d04c8ec8912353b7f14c1` | `d5de08ee0daa62500ded34fdf8224d853774c30b4bedc3ce65e71b7e8d850a42` |

Reproduce the archives: `python -I research_tools/nbc/pin_saq.py --repo <mirror of https://github.com/tobymao/saq> --out <dir>` (verifies commit, tree and sha256).

## Contract and property

Single-version. The property (`oracle.py`, B0-B3, the same as `saq-ad8d1c8`'s) comes from the Worker docstring, the maintainers' burst tests and the fix's own test ("a dequeue that times out must still take it instead of reporting an empty queue"): after the burst worker stops, no job is left `active`. Not asserted: that every job completes; a burst worker that times out before any job left the queue stops by design.

## Workloads (RA-DET, frozen in `saq_burst_c570ba4.py`)

Shape `burst` (as in `saq-ad8d1c8`), burst worker without a cap, the dequeue timeout shorter than the poll interval, so the waiter's timeout can fire while its poll loop sleeps after the schedule tick's claim has landed:
- W1: 1 job, timeout 0.5 s, poll 1.0 s, schedule timer 1 s.
- W2: 2 jobs, otherwise W1.
- W3: 1 job, timeout 0.25 s, otherwise W1.

## Predictions (written before running)

- R1: the schedule tick claims the job (about 2 to 4 grants) while the waiter sleeps in its 1 s poll; the timeout fires first; the waiter returns nothing; the burst worker stops; the committed claim leaves the job `active` (B1).
- R2: the waiter's timeout path takes the job from the buffer; it completes.

## Competing explanations, recorded before running

1. **Control risk.** The fix takes the job only if the claim landed in the buffer before the timeout handler runs. If the timeout fires before the claim query returns, R2 also returns nothing; if the claim then commits before the worker's stop cancels the tick, R2 strands it too. That would be CONTROL FAILED: an R2 violation under this harness's slow, coordinated statements, recorded as such and not explained away.
2. If the worker's stop cancels the tick before its COMMIT, the claim rolls back in both revisions and nothing is stranded (no verdict difference).
3. As in `saq-ad8d1c8`, the race is in-process timing on one connection; the coordinator's seeds have little influence.

## How it runs

`run_nbc.py run --arms RA-DET` under `pg_lock.py`, with the plan's §5 budgets (B = 12 per workload: 3 replicates × 4 schedules, one workload per replicate; T = 900 s per case and arm; never retried). Each schedule is one restricted container (CPython 3.12.16 runner image, saq tree copied in, hash-pinned dependencies, no network) plus the coordinator, on a fresh schema `saq_nbc` migrated by that revision's own `connect()`; every statement is granted by the coordinator under the seeded explorer (`real_executor.seed_for`); the merged trace is corroborated against the server log; the oracle decides only a SOUND run. Integration machinery: `research_tools/nbc/integrations/saq_common.py`, `saq_shapes.py`; container side `src/deployspecter/experimental/thirdparty/saq_coord_adapter.py` (a declared variant of the pilot adapter, with no code deviation in saq) and the `saq_coordinated` mode of `smoke_inside.py`.

**Label: NONBLIND development.** The case was chosen after reading the fix. The oracle is developer-written from the documented contract. A REPRODUCED result reproduces a documented upstream defect; it is never a new finding.

## Exposure (who read what, before which step)

All by the Track A agent on 2026-11-08 (UTC), recorded in `case_spec.json` `exposure`: the 12 screened candidates' messages and stats (04:49Z); this fix's diff and the neighbouring fixes' diffs (04:51-04:54Z); saq's docs, the Worker docstring and the maintainers' burst tests (04:54Z); the full parent `postgres.py`/`worker.py` sources and the diffs between the pinned revisions (05:00-09:40Z); bring-up runs on v0.26.5 (`../../bringup/`). All of it before the integration, workloads and oracle were written, and before any live run of R1 or R2. Training-data exposure of the public repository can't be excluded.
