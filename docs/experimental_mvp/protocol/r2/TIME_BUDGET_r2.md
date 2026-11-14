# Time budget for K = 6, S = 2 under revision candidate r2 (development evidence only; 2026-11-15)

**Purpose.** This reviews #15's 900 s ceiling against #24's equal budgets, evaluates a prospective 1,200 s ceiling,
and documents the rule r2 proposes instead. It uses only the ds-nbc/2 development ledgers and the instrument's code.
**No outcome was used, and K and S were not tuned**: they are the values ds-nbc/2 froze before running.

## 1. Evidence

From the four ds-nbc/2 hash-chained ledgers (288 runs; 12 case × arm cells of 24 runs each):

| Measure | Value | Source |
|---|---|---|
| Runs per case and arm | 24 = 2 revisions × K × S | every cell |
| Execution seconds per case and arm | min 61.8, median 126.2, max 474.5 (chancy, M) | `wall_seconds` |
| Wall-clock span per case and arm | within 0.8 s of the execution seconds in every cell | ledger `at` stamps |
| Gap between consecutive runs | median 0.04 s, max 0.9 s (1 s stamp resolution) | ledger `at` stamps |
| Setup outside the container, per run | median 0.36 s, max 0.42 s (216 runs with a coordinator record) | `container_seconds` |
| SOUND run | max 10.1 s | `wall_seconds` |
| INCOMPLETE run | 47.8–48.1 s (20 runs, all chancy) | `wall_seconds` |

So each run's `wall_seconds` already includes its database preparation, containers and teardown. Image builds and
pinned-tree preparation happen once per revision, before the arms, and are shared.

**Instrument limits** (`research_tools/isolated/run_coordinated.py`, used by every ds-nbc integration):
- the coordinator stops an uncovered run at 45 s (`stop.after_s`), which gives INCOMPLETE at about 48 s;
- the host waits at most 240 s per run for the containers, then kills them, with a further 30 s grace per process;
- before and after the containers, the Docker CLI calls have their own timeouts (up to 300 s each).

The 48 s figure is the **soft** stop. A run that stalls past it (for example a hung container) can take longer: at
least 270 s, and more with several processes or a slow Docker daemon. The code has no single hard per-run bound.

## 2. Candidate ceilings per case and arm

The worst case observed so far is every run stopping at 48.1 s, with 0.9 s gaps: 24 × 48.1 + 23 × 0.9 = **1,175.1
s**. (`prep_r2.ceiling_report`; tested.)

| Ceiling | Margin over the observed worst case | Can a run within the per-run limit L = 300 s still push an arm over it? | Verdict |
|---|---|---|---|
| 900 s (#15 in r1) | **−275 s** | yes | **unsafe**: an all-INCOMPLETE arm is cut short |
| 1,200 s (proposed for evaluation) | +24.9 s (2%) | yes: one stalled run (≥ 73 s) in an all-INCOMPLETE arm is enough | **unsafe as a cap** |
| 7,500 s (r2) | +6,325 s | no: 24 × 300 s + 23 × 1 s = 7,223 s | safe |

**Why 1,200 s fails.** It covers the observed soft-stop worst case by only 25 s. Any arm whose runs mostly stop
INCOMPLETE would lose schedules if a single run stalled past the soft stop, or if the soft stop drifted by a second
per run. That loss would fall on whichever arm happened to have the stalled runs, so it would depend on outcomes and
break #24's equal budgets. Equal budgets can't be claimed under a 1,200 s cap.

## 3. The rule r2 proposes (§4 "Time", §19 #15)

1. **No time limit truncates an arm's K × S schedules.**
2. **Per run, identical for every arm:** the coordinator's 45 s stop and the host's 240 s container limit, as now,
   plus an **executor limit L = 300 s**: a run whose recorded wall-clock exceeds L is INCOMPLETE whatever its
   verdict, consumes its schedule, and is a nondetection. This is uniform and doesn't depend on which arm ran it.
3. **Per case and arm, a safety ceiling of 7,500 s.** It sits above the bound L implies (7,223 s), so it can be
   reached only if runs overrun L, which is itself an instrument failure. Reaching it is an **infrastructure
   failure**: the case is excluded for every arm and reported. It is never censoring of one arm, and nothing is
   rerun.
4. **1,200 s stays as a planning figure** for scheduling and cost, not as a cap.
5. **Excluded from the clocks:** image builds and pinned-tree preparation (once per revision, shared, before any arm).
   Lock waits are inside the safety clock but excluded from execution seconds (#28).

Implemented, development-only: `research_tools/confirmatory_prep/prep_r2.py` (`R2Executor`, `L_RUN_SECONDS`,
`SAFETY_CEILING_SECONDS`, `InfrastructureFailure`, `time_rule_problems`, `ceiling_report`), with tests in
`tests/experimental/test_confirmatory_prep_r2_offline.py`.

## 4. Accounting reported per arm (equal schedules, not equal seconds)

- schedules used on each revision: always K × S = 12 (any difference is refused);
- execution seconds in total, and to the first target-clause detection;
- generation seconds (Muse latency, or RA-DET-G's generator), for end-to-end time;
- INCOMPLETE, REFUSED and ERROR counts, and runs over the executor limit;
- distinct workloads executed, slots filled by repetition and slots unfilled (REFUSED).

Seconds are reported, never equalized: the budget is schedules.

## 5. Residual risks for the owner

- **L = 300 s and 7,500 s are instrument-derived choices,** not results. If the owner prefers a tighter L, the
  ceiling must be recomputed as 24 × L + 23 s and stay above it.
- **Docker-daemon stalls** outside the container window aren't bounded by the code. Under this rule they show up as
  runs over L (INCOMPLETE) or, at worst, an infrastructure failure for the case. Neither favours an arm.
- **A wholly INCOMPLETE arm** costs about 20 minutes per case. With 10 cases × several arms this is hours, not
  days.
