# Case `saq-c570ba4`: results (NONBLIND development case, 2026-11-08)

**Classification (rule §5): REPRODUCED.** It reproduces the documented upstream defect fixed in `c570ba4`, not a
new finding. The case was chosen after reading the fix; the oracle is developer-written. It is not blind,
confirmatory or independent evidence.

## Attempt `reproduction_1` (the only attempt)

`run_nbc.py run --arms RA-DET`, 2026-11-08T09:58:28Z-10:03:57Z, through `pg_lock.py`. Integration
`saq_burst_c570ba4.py` (sha256 `1cd0102e…`), oracle `oracle.py` (`7a88fd21…`), spec (`cf2763ea…`). The build fix of
ERRATA E1 was in place; no other change since the freeze (`f1cf4b8`). Code revision `76459bf`, then `7e867b8`
(clean) for the later runs: that commit only added files for another case.

| Replicate (workload) | R1 `59f32fc` | R2 `c570ba4` |
|---|---|---|
| 0 (W1: 1 job, timeout 0.5 s, poll 1 s) | **VIOLATED at schedule 1**: `default-0` left `active`, never run | HOLDS 4/4: `complete`, run once |
| 1 (W2: 2 jobs, timeout 0.5 s) | **VIOLATED at schedule 1**: `default-0` `active`, `default-1` `queued`, nothing run | HOLDS 4/4: both `complete` |
| 2 (W3: 1 job, timeout 0.25 s) | HOLDS 4/4: `default-0` stays `queued` (the claim never committed) | HOLDS 4/4: `complete` |

- Schedules to the first violation: **1** (W1, replicate 0; 2.7 s). R1 used 6 schedules in all (1 + 1 + 4).
- Fixed control: **12 of 12 schedules HOLD**; no R2 violation.
- All 18 runs SOUND. Corroboration totals: **715 statements matched, 791 typed parameters compared, 0 mismatched,
  0 unchecked**.
- Not censored by T (15.0 s of R1 run time, 69.1 s in all).

**What the R1 violations show** (`work/runs/*R1*s01*/coordinator.json`): the worker's schedule tick claims the job
(`WITH locked_job AS … UPDATE … SET status = 'active'`, then saq's own `_update`) and commits. Meanwhile the
dequeue's 0.5 s timeout fires while its poll loop sleeps, and the dequeue returns nothing. The burst worker stops
with the committed claim stranded `active`, and its handler never ran (B1). R2 takes the buffered job when the
timeout fires and completes it.

**W3 holds in both trees.** With a 0.25 s timeout the worker stops before the tick's claim commits, so the claim
rolls back and the job stays `queued`. This is CASE.md competing explanation 2, a schedule that doesn't open the
window. The CASE.md control risk (R2 stranding a job when the timeout fires before the claim returns) didn't occur
in 12 schedules.

**Seeds.** As predicted, the coordinator had little freedom: every schedule of a given workload and tree gave the
same final state. The race is between the worker's own tasks on its one connection.

## What this does not show

- Independent discovery or independent oracle authorship.
- Behaviour with LISTEN/NOTIFY (poll mode only; the harness can't instrument saq's listener connection).
- Statistical claims: one case, n is too small.
