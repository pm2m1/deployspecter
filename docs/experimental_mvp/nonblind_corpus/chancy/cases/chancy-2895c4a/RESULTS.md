# ds-nbc/1 case `chancy-2895c4a`: results (NONBLIND development, 2026-11-08)

**Classification (rule §5): REPRODUCED**, for RA-DET workloads 1 and 2. Workload 3 gave INCOMPLETE on both slots
(no verdict either way). This reproduces a **documented upstream defect** (fixed in `2895c4a`, 0.26.0 CHANGELOG
"Fixed cancellation races that could overwrite cancelled results"); it is not a new finding, and it is NONBLIND
development evidence only (prior exposure: the historical case).

## Attempt `reproduction_1` (the only attempt)

| | |
|---|---|
| Command | `research_tools/pg_lock.py -- .venv/bin/python research_tools/nbc/run_nbc.py run --cases …/case_spec.json --arms RA-DET --out …/reproduction_1` |
| Code revision | `b38142e` (clean for every file the run reads) |
| Hashes in `RUN_CONFIG.json` | spec `ae62dad3…`, integration `chancy_sv1.py` `25c8ae50…`, oracle `ec4c8591…` (= `case_spec.json`) |
| Budgets (`BUDGETS.json`, plan §5) | B = 12 (3 replicates × 4 schedules), T = 900 s per case and arm; total wall time used 149 s, no T censoring |
| Ledger | `RESULTS_LEDGER.jsonl`, 6 entries, hash chain verified by `summarize` |

| Replicate (workload) | Slot | Outcome | Schedules used | First violation | Per schedule |
|---|---|---|---|---|---|
| 0 (`n=0` cancelled, max_attempts 1) | R1 (`80f570f`) | **VIOLATED** | 1 | schedule 1 (seed 151452969) | SOUND/VIOLATED |
| 0 | R2 (`2895c4a`) | HOLDS | 4 | — | 4 × SOUND/HOLDS |
| 1 (`n=0` cancelled, max_attempts 2) | R1 | **VIOLATED** | 1 | schedule 1 (seed 1717204255) | SOUND/VIOLATED |
| 1 | R2 | HOLDS | 4 | — | 4 × SOUND/HOLDS |
| 2 (`n=0` kept, `n=1` cancelled) | R1 | INCOMPLETE | 1 | — | INCOMPLETE |
| 2 | R2 | INCOMPLETE | 1 | — | INCOMPLETE |

`SUMMARY.json`: detected, `schedules_to_detection` 1 (5.1 s), no false positive, `verified_detection` true; 3 buggy
and 9 fixed schedules.

**Corroboration totals (all 12 schedules):** 1665 statements matched against the server log; 1298 typed parameters
compared, 0 mismatched, 0 unchecked.

## What the traces show

- Workloads 1 and 2, both slots, same seeds: the worker claims the job (grant 22-23 on schedule 1), the cancel
  updates 1 row (grant 24-25), and the worker's queued completion flush (`state = 'succeeded'`) follows (grant 43).
  On R1 the oracle reports H1 violated (the cancelled job ends `succeeded`); on R2 the same order holds (the flush is
  fenced by the claim). The fixed control held on all 8 of its schedules for these workloads, including the two that
  share the violating seeds.
- Workload 3: the window and cancel gates wait only for the worker's first claim, which was job 0's. The cancel of
  job 1 landed while job 1 was still pending (grant 49), so job 1 never ran, the coordinator's stop condition (every
  pushed job's handler returned) was never met, and the run stopped on its 45 s limit: INCOMPLETE, on both slots
  alike. This was predicted in `CASE.md`. By executor policy an INCOMPLETE schedule ends its replicate; it was not
  retried.

## Limits

- **Recorded detail.** `chancy-sv/1`'s `run` (unchanged, hash-pinned) returns only the oracle's verdict string and
  writes the traces and the coordinator report, not the final state or the oracle's per-property detail. The
  verdicts above are the oracle's on the host's independent final-state read; the final rows themselves were not
  persisted. Reported as a shared-tooling limitation.
- **Rule §5 and workload 3.** "Every R2 run holds" is met by every SOUND R2 run; workload 3's R2 run is INCOMPLETE,
  not HOLDS. It gave no verdict on either slot.
- Notifications disabled, one worker, `AsyncExecutor`, single-version contract; NONBLIND (fix read first, oracle
  developer-encoded from the maintainers' test).

## Evidence

`reproduction_1/` (committed without `package/cases/*/R?.tar`, which `research_tools/nbc/pin_chancy.py` reproduces
byte for byte; `work/build/` is not kept). Per schedule: `work/runs/<case>__RA-DET__r<rep>__<slot>__s<i>/`
(`0_setup/report.json`, `A.trace.json`, `coordinator.json`).
