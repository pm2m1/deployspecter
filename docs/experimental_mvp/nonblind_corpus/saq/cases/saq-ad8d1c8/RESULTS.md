# Case `saq-ad8d1c8`: results (NONBLIND development case, 2026-11-08)

**Classification (rule §5): CONTROL FAILED.** One R2 run violated the frozen property. The targeted defect was also
seen on R1 (below), but under the rule a control failure decides the case. Nothing here is a new finding,
blind, confirmatory or independent evidence.

## Attempt `reproduction_1` (the only attempt)

`run_nbc.py run --arms RA-DET`, 2026-11-08T09:57:02Z-09:58:28Z, through `pg_lock.py`. Integration
`saq_burst_ad8d1c8.py` (sha256 `97e125a3…`), oracle `oracle.py` (`36fef84d…`), spec (`76f75594…`). ERRATA E1's
build fix in place; nothing else changed since the freeze (`f1cf4b8`). Code revision `76459bf` (clean).

| Replicate (workload) | R1 `525acc9` | R2 `ad8d1c8` |
|---|---|---|
| 0 (W1: 3 jobs, concurrency 1, `max_burst_jobs` 1) | **VIOLATED at schedule 1 (B1)**: `default-0` complete, **`default-1` left `active`**, `default-2` queued | HOLDS 4/4: one complete, two queued (as `test_max_burst_jobs`) |
| 1 (W2: 3 jobs, concurrency 2, `max_burst_jobs` 2) | VIOLATED at schedule 1 (**B3**): all 3 complete | **VIOLATED at schedule 1 (B3)**: all 3 complete |
| 2 (W3: 2 jobs, no cap) | HOLDS 4/4 | HOLDS 4/4 |

- Schedules to the first violation: 1 (W1, replicate 0; 4.5 s). R1 used 6 schedules, R2 9.
- All 15 runs SOUND. Corroboration totals: **859 statements matched, 1139 typed parameters compared,
  0 mismatched, 0 unchecked**. Not censored by T (37.3 s of R1 run time, 85.5 s in all).

**The targeted defect (W1).** R1 claimed a second job, which no consumer took. The burst worker stopped after its
one allowed job and left that job `active` (B1). This is the stranding the fix describes. R2 left both remaining jobs
`queued` in 4 of 4 schedules.

**The control failure (W2).** With `concurrency=2` and `max_burst_jobs=2`, **both** trees processed all 3 jobs. When
the first job finishes, the worker starts a replacement process task, which takes the third job before the second
finishes and the cap is reached. Oracle clause B3 ("at most `max_burst_jobs` complete") is the developer's reading of
the Worker docstring ("max_burst_jobs: the maximum number of jobs to process in burst mode"). The maintainers' own
`test_max_burst_jobs` only exercises concurrency capped to 1. So W2 found behaviour present at the fix commit too,
outside this case's defect. Whether upstream considers it a defect is unknown. It is **not** reported as a finding.
No job was stranded in W2.

**Not done, deliberately.** The oracle wasn't narrowed to B1 after seeing these results, and the run wasn't
repeated: either would turn a control failure into a post-hoc REPRODUCED. The confound recorded in CASE.md also
stands (a mixed commit; the commit pair, not a single hunk, carries the attribution).

## What this does not show

- Independent discovery or oracle authorship; LISTEN mode; any statistic (one case).
