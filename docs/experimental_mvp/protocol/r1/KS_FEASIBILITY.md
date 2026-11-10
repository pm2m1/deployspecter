# K = 6, S = 2 feasibility under revision r1 (development evidence only; 2026-11-10)

**Purpose.** This checks whether the proposed allocation (#24) is executable and keeps equal budgets. It uses only
the existing ds-nbc/2 development evidence and offline tests. **K and S were not tuned to any outcome:** they are
the values ds-nbc/2 froze before running, and they are evaluated here only against instrument limits.

## Evidence (ds-nbc/2: 288 runs, 4 development cases × 3 arms × 24 runs per arm and case)

| Measure | Value |
|---|---|
| Runs per arm and case | 24: 6 slots × 2 schedules × 2 revisions, in every cell (equal) |
| Execution seconds per arm and case, both revisions | min 61.8, median 126.2, **max 474.5** (chancy, M) |
| SOUND run duration | median 4.3 s, max 10.1 s (268 runs) |
| INCOMPLETE run duration | median 47.8 s, **max 48.1 s** (20 runs): the coordinator's fixed stop |
| Distinct workloads executed per cell | D1: 3 (each in 2 slots); D2: 6; M: 6 |

## Findings

1. **K × S = 12 per revision is executable.** Every observed cell finished within 475 s.
2. **The current T = 900 s per case and arm (#15) is not safe as a cap.**
   - The worst case is every run stopping at the coordinator limit: 2 × 6 × 2 × 48.1 s = **1,154 s**, which is over
     900 s.
   - Under T = 900, an arm with many INCOMPLETE runs would lose schedules. That would break #24's equal-schedule
     budget, and the loss would depend on outcomes.
   - **A prospective alternative** derived from the instrument, not from results: **T = 1,200 s per arm and case**,
     or T defined as a safety stop that never truncates the K × S schedules (exceeding it becomes an infrastructure
     failure, not censoring).
   - `prep.time_cap_problems` and its test encode this check.
3. **Equal schedules hold by construction.** `prep.PrepExecutor.run_arm` always produces K × S runs per revision,
   whatever the workload count, and `score_cell` refuses any cell that doesn't. This is tested.
4. **Distinct vs repeated workloads:**
   - **Arms with fewer than K workloads cycle them** with new seeds: curated RA-DET, or a Muse arm with fewer than K
     valid proposals.
   - **RA-DET-G always supplies K distinct workloads,** or reports a shortfall and never pads it.
   - **The count of distinct workloads executed is reported per arm** (tested).
5. **Invalid and missing proposals.**
   - Invalid proposals are counted and never executed (tested).
   - If an arm has at least one valid workload, its slots are filled by cycling.
   - If it has **none**, every slot is a REFUSED run with "no valid workload". It takes 0 s but consumes all K × S
     schedules per revision, and is a censored nondetection (tested).
   - No additional request is made (#24a).
6. **No early stopping or outcome-dependent substitution.**
   - The allocation is fixed before any run, and every slot and schedule runs on both revisions.
   - Admission is in model order, and the journal is written before any run (ds-nbc/2 practice; `prep.admit`).
7. **INCOMPLETE and REFUSED runs** consume their schedules and count as nondetections (tested). In ds-nbc/2, all 20
   INCOMPLETE runs were one deterministic engine limit: chancy two-job workloads whose second job was cancelled
   before its claim.

## Open for the owner (not changed here)

- **T:** adopt T = 1,200 s per arm and case, or a non-truncating safety stop, in place of #15's 900 s.
- **Eligibility rule (#28):** whether integrations may reject known-INCOMPLETE workload shapes. If they may, the
  rule is fixed before the freeze and applies to every arm.
