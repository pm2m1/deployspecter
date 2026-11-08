# Case `pgqueuer-8dfadf4`: results (NONBLIND development, 2026-11-08)

**Classification (rule §5): REPRODUCED.**
- On R1, every replicate has a SOUND, corroborated run that violates H1: 3 of 3 replicates.
- Every R2 run holds: 12 of 12 schedules.

This reproduces a **documented upstream defect**, fixed by the maintainers in `8dfadf4` (#1032). It is not a new
finding. It is NONBLIND development evidence: the developer chose the case after reading the fix, and wrote the
oracle and the integration. It isn't blind, confirmatory or independent.

## Provenance

| | |
|---|---|
| Frozen before any live run | `CASE.md`, `oracle.py` (`cb59b573…`), the 3 RA-DET workloads; commit `359c2aa` |
| Code revision of every run | `359c2aa` (clean) |
| Integration `pgqueuer-cancel-sv/1` | `pgqueuer_cancel.py` `138dc272…`; `pgqueuer_inside.py` `045277dc…`; adapter variant `pgqueuer_hist_adapter.py` `6199345e…` |
| R1 `f258cbc` | archive `378ee759…`, tree digest `4581516c…`, verified inside the container |
| R2 `8dfadf4` | archive `d985c468…`, tree digest `de03f708…`, verified inside the container |
| Budgets | plan §5 (`BUDGETS.json`): B = 12, 3 replicates × 4 schedules, T = 900 s; nothing censored by T |
| Seeds | `real_executor.seed_for`, the same for R1 and R2 |

## Runs (`reproduction_1/`, the only attempt)

| Replicate (workload) | R1 schedules | R1 verdict | R2 schedules | R2 verdict |
|---|---|---|---|---|
| 0 (2 jobs, steps 1) | 2 (seed 241987274 HOLDS, then 1441995128 **VIOLATED**) | VIOLATED at schedule 2 | 4 | HOLDS ×4 |
| 1 (2 jobs, steps 2) | 1 (seed 4160863781 **VIOLATED**) | VIOLATED at schedule 1 | 4 | HOLDS ×4 |
| 2 (3 jobs, steps 2) | 1 (seed 708699469 **VIOLATED**) | VIOLATED at schedule 1 | 4 | HOLDS ×4 |

- **Case level** (`SUMMARY.json`): detected at **schedule 2** (12.6 s). 4 buggy schedules, 12 fixed-control
  schedules, no false positive.
- **Soundness:** all 16 runs are SOUND.
- **Corroboration:** 1489 statements, 3665 typed parameters compared, **0 mismatched, 0 unchecked**.

**Mechanism, read from each violating run's final state:**
1. Job 1 is picked.
2. The cancel's `log_job` logs it `canceled` while its handler is still running.
3. The same worker's next dequeue (`w1`) picks job 2 and starts it before job 1's `end`. Peak running is 2 under
   `concurrency_limit = 1`.

On R2 the same seeds leave job 1's row held until its worker logs it, and job 2 is picked only after that.

In all three violations, the overlap is inside one worker: its own dequeue loop took the freed slot. The second
worker never ran a job alongside a cancelled one. R1's one holding schedule (replicate 0, schedule 1) ended job 1's
handler before the next dequeue.

## Bring-up (`bringup_1/`, not part of the case)

One R2 run with a non-cancelling workload outside the RA-DET set (seed 1), made before the reproduction. It was
SOUND, with 82 statements, 196 values compared, 0 mismatched and 0 unchecked, and H1 held. It tested the machinery
only.

## Limits

- **NONBLIND.** The fix, its docs and its test were read before the integration and the oracle were written.
- **Gates.** Four gates make the operations meaningful (CASE.md): the install first, the window, the cancel after
  the first claim, and the first claim by `w1`. The explorer decided the rest.
- **One declared adapter deviation.** The notification watcher polls for 0.05 s instead of 1.0 s.
- **Process scope.** One process holds all actors, and in-process task timing isn't controlled.
- **Isolation.** runc only. This is not hostile-grade isolation.
- **Training-data exposure** of the public repository can't be excluded.

## Evidence (sha256 over the sorted files' sha256 lines, archive copies excluded)

| Directory | Digest |
|---|---|
| `bringup_1` | `3e1df30bd093707e` |
| `reproduction_1` | `9a66d27892c13dfa` |

Note: `research_tools/nbc/integrations/pgqueuer_bringup.py` ran `bringup_1` at `359c2aa` (uncommitted then). Afterwards, one unused `import sys` was removed for ruff. No behaviour changed.
