# ds-nbc/1 case `chancy-b8a77df`: results (NONBLIND development, 2026-11-08)

**Classification (rule §5): REFUSED, by the instrument.** The buggy parent violated the property on two SOUND,
corroborated schedules, but on exactly those schedules (and on one more) the fixed control was **REFUSED** by the
server-log corroboration. Rule §5's REPRODUCED needs every R2 run under the same workloads and schedules to hold, and a
refused run doesn't hold. There is no R2 violation, so it isn't CONTROL FAILED either. Not a new finding in any case:
the defect is documented upstream (#89).

## Attempt `reproduction_1` (the only attempt)

| | |
|---|---|
| Command | `research_tools/pg_lock.py -- .venv/bin/python research_tools/nbc/run_nbc.py run --cases …/case_spec.json --arms RA-DET --out …/reproduction_1` |
| Code revision | `3422e97` (clean for every file the run reads) |
| Hashes (`RUN_CONFIG.json`) | spec `772cae78…`, integration `chancy_pushmany.py` `d45e0873…`, oracle `4922a28d…` (= `case_spec.json`) |
| Budgets | B = 12 (3 × 4), T = 900 s per case and arm; 102.5 s used, no T censoring |
| Ledger | 6 entries, hash chain verified by `summarize` |

| Replicate (workload) | Slot | Outcome | Schedules | Per schedule (seed: verdict/oracle) |
|---|---|---|---|---|
| 0 (`seed [0,1]`, `repush [1,0]`) | R1 `6716e0d` | REFUSED | 1 | 489678867: REFUSED (corroboration) |
| 0 | R2 `b8a77df` | REFUSED | 1 | 489678867: REFUSED (corroboration) |
| 1 (`seed [1,0]`, `repush [0,1]`) | R1 | **VIOLATED** at schedule 4 | 4 | HOLDS, HOLDS, HOLDS, 903536853: **VIOLATED** |
| 1 | R2 | REFUSED at schedule 4 | 4 | HOLDS, HOLDS, HOLDS, 903536853: REFUSED (corroboration) |
| 2 (`seed [0,1]`, `repush [1,5,6,7,8,0]`) | R1 | **VIOLATED** at schedule 1 | 1 | 259205025: **VIOLATED** |
| 2 | R2 | REFUSED at schedule 1 | 1 | 259205025: REFUSED (corroboration) |

**Corroboration totals:** SOUND schedules (8): 573 statements matched, 822 parameters compared, 0 mismatched,
0 unchecked. REFUSED schedules (4): 286 matched, 437 compared, 0 mismatched, 0 unchecked; each refused with the single
problem "the server's order of statements disagrees with the scheduler's grant order". All: 859 / 1259 / 0 / 0.

## The two violations (oracle detail in each run's `report.json`)

- **Replicate 1, schedule 4 (R1):** D1. The worker's batched job update (`executemany` UPDATE of both seed jobs) failed
  with SQLSTATE 40P01 `deadlock_detected`; Chancy re-queued and retried it, so the final rows are `succeeded` and the
  re-push returned the seed jobs' ids. The deadlock is visible only in the worker's own statement outcomes, which the
  server log corroborated (its ERROR row matched the traced error).
- **Replicate 2, schedule 1 (R1):** D1. The re-push's `INSERT … ON CONFLICT` failed with 40P01 and `push_many` raised
  `DeadlockDetected` ("Process … waits for ShareLock on transaction …; blocked by process …" both ways): the push
  failed, as the CHANGELOG says ("could fail either side").

Both sides of the documented defect were observed on the parent, on SOUND runs.

## Why the controls were refused (instrument limitation, from the traces)

In every refused run, the worker's update batch was granted while the re-push's transaction still held the
conflicting rows, waited on that lock, and finished only after the re-push's COMMIT, which the coordinator had granted
later (for example grants 47 and 48 in `r0__R2__s01`, completed in the order 48, 47). On the fixed tree this is the fix
working: the two transactions take the rows in the same order, so one waits instead of deadlocking. The corroborator
requires the server's statement order to equal the grant order and doesn't account for a lock wait inside the run, so
it refuses exactly the overlapping schedules. The R2 schedules that held (replicate 1, schedules 1-3) had no such
overlap, so they are no evidence about the overlapping case. Replicate 0 was refused on both slots for the same reason.
This risk was recorded in `CASE.md` before the run. No second attempt was made: the cause is in the shared
corroborator (`src/deployspecter/experimental/corroborate.py`, the final order check), which this track may not change,
and a rerun with the same workloads would be a retry.

`run_nbc.py summarize` reports `verified_detection: true` for this case because it counts only R2 VIOLATED runs as
false positives; it doesn't require the R2 runs to hold. That is a shared-tooling defect; the classification here
follows rule §5, not that flag.

## Evidence

`reproduction_1/` (committed without `package/cases/*/R?.tar`, which `research_tools/nbc/pin_chancy.py` reproduces;
`work/build/` not kept). Each schedule's directory has `0_setup/`, `A.trace.json`, `coordinator.json` and
`report.json` (final state, actors' results, oracle detail, corroboration problems). The instrument bring-up before
it (no case workload, no oracle) is in `bringup_1/`.
