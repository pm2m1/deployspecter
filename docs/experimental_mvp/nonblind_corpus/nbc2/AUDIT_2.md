# Audit of ds-nbc/2 (2026-11-10, after all outcomes)

**No outcome, primary result or correctness check is changed.** The **primary** result is the predeclared one in
`analysis_1/ANALYSIS.json` and `RESULTS.md`: D1, D2 and M each detect 4/4; M vs D2 McNemar p = 1.0; INSUFFICIENT
SAMPLE. Every other reading here is **secondary**: post-hoc or sensitivity only.

## 1. The raw evidence

| Check | Result |
|---|---|
| Run records | **288**: 4 cases × 3 arms × 6 slots × 2 schedules × 2 revisions; 72 unique (arm, slot, schedule, revision) keys per case, no duplicates |
| Hash chains (4 ledgers) | intact |
| Verdicts | 268 SOUND, 20 INCOMPLETE, 0 REFUSED, 0 ERROR |
| Seeds | `seed(case, k, j)` on every record, and identical across the three arms at every (k, j) |
| Allocation | every record's workload equals the frozen list entry `list[k % len(list)]` (D1 from the manifest, D2 from the manifest, M from the journal committed before the run) |
| Arm order | the frozen permutation in each case |
| Budget | 12 schedules per revision per arm and case, for every arm; no early stopping |
| Corroboration | 0 parameters mismatched and 0 unchecked across all SOUND runs |
| Images | each slot's image matched its pinned archive file by file |

## 2. The 20 INCOMPLETE runs

All 20 are in **chancy-2895c4a**: D1 8, D2 4, M 8. Every one is a two-job workload whose **second** job is cancelled.

| Arm | Slot(s) | Workload |
|---|---|---|
| D1 | 2 and 5 (one workload, cycled) | `[{n:0, cancel:false}, {n:1, cancel:true}]` |
| D2 | 5 | `[{n:96, cancel:false}, {n:99, cancel:true}]` |
| M | 4 | `[{n:6, cancel:false}, {n:7, cancel:true}]` |
| M | 5 | `[{n:8, cancel:true}, {n:9, cancel:true}]` |

**Cause.**
- The cancel lands before the worker claims that job, so the job never runs.
- The coordinator's completion condition (every pushed job handled) is never met, and the run stops at its deadline
  as INCOMPLETE.
- This is the engine limitation already recorded in ds-nbc/1. It is deterministic: both schedules on both revisions
  of each such slot are INCOMPLETE.

**Effect.**
- Each INCOMPLETE slot uses 2 of an arm's 12 buggy-revision schedules without the chance of a verdict. Under the
  predeclared rules this counts as a nondetection for those schedules.
- It cost D1 and M 4 schedules each and D2 2, but changed no case-level detection: every arm detected chancy-2895c4a
  at schedules 1 (D1, M) and 9 (D2).

## 3. saq-c570ba4's fixed-version violations: three kinds of finding kept apart

Every violation in saq-c570ba4 was attributed to the oracle clause that fails, from each run's final database state:

| Kind | Clause | Where | Interpretation |
|---|---|---|---|
| **True defect detection** | B1: a job left `active`, never run (the c570ba4 defect) | D1 slots 0, 1, 3, 4 and M slot 4: buggy revision only; the same workloads hold on the fix | detection of the target defect |
| **Violation of an unrelated property** | B3: more jobs completed than `max_burst_jobs` (3 > 2; 4 > 3) | D2 slot 5 and M slot 2: **both** revisions, every schedule | saq overruns `max_burst_jobs` at the fix commit too (the same behaviour that made saq-ad8d1c8 CONTROL FAILED). The oracle is right that the property is violated, but this is not the case's defect |
| **Instrument false positive** (a wrong verdict) | none found | | |

Under the predeclared S0 rule the two B3 violations count as R1 detections, with a fixed-version violation reported
beside them. This made D2's predeclared saq-c570ba4 "detection" (schedule 11) a B3 overrun, not the defect.

## 4. Scoring treatments (`analysis_1/SENSITIVITY.json`, secondary)

| Treatment | Status | D1 | D2 | M | M vs D2 |
|---|---|---|---|---|---|
| **S0: any SOUND buggy-revision violation** (predeclared) | **PRIMARY** | 4/4 | 4/4 | 4/4 | p = 1.0 |
| S1: discriminating slot (the slot's fixed-revision runs hold) | post-hoc | 4/4 | 3/4 | 4/4 | 1 vs 0 discordant, p = 1.0 |
| S2: a fixed-version violation voids the case for that arm (plan §6 wording) | sensitivity | 4/4 | 3/3 (1 void) | 3/3 (1 void) | 3 paired cases, p = 1.0 |
| S3: clause-attributed (only the defect's clause counts) | post-hoc | 4/4 | 3/4 | 4/4 | p = 1.0 |

No treatment yields a detectable M vs D2 difference.

## 5. The scoring rule that needs independent approval before confirmatory testing

1. **Fixed-version violation adjudication.**
   - **The conflict:** the plan's §6 says a fixed-version violation *voids the case for that arm* and goes to blinded
     two-reviewer triage (decision #8). But the executor and `paired_eval` count it as a detection with a warning (S0).
   - **The gap:** neither defines what happens when the fixed version violates a property **unrelated** to the
     target defect.
   - **Needed:** a ruling by the owner and triage reviewers.
2. **Clause attribution.**
   - **Needed:** each confirmatory oracle should state, before sealing, which clause encodes the target defect, with
     detections scored on that clause (S3) or per slot (S1).
   - **Owner:** this belongs to the independent oracle author (decision #7), not the developers.

Neither is decided here.

## 6. Does D2 embed case-specific knowledge?

**At run time, no.**
- `d2_workloads` reads only the integration's `WORKLOAD_JSON_SCHEMA`, plus a seed derived from the case id.
- It never reads the fix, the oracle, `MUSE_CONTEXT`'s prose or any result.
- The code has no case-specific branch.

**Through its inputs, yes, indirectly.** Each schema was written by a developer who had read the fix, so the
workload space itself is fix-shaped:
- **chancy:** a per-job `cancel` flag.
- **pgqueuer:** `cancel` with `concurrency_limit` ∈ {0, 1, 2}.
- **saq-c570ba4:** `dequeue_timeout_s` and `poll_interval_s` enumerations, and `max_burst_jobs`.
- **saq-addee31 v2:** two table sets, with a second queue that only connects. This schema was re-encoded after
  outcomes.

So D2 is outcome-blind and fix-blind in its *selection*, but its *search space* is fix-informed. M shares the same
schemas and `MUSE_CONTEXT`, so the D2-vs-M comparison is fair between those two arms. But neither arm demonstrates
discovery of an unknown defect from an unshaped API.

**Other choices** made after ds-nbc/1's outcomes and before ds-nbc/2's freeze:
- D2's boundary emphasis and K = 6, S = 2 are generic, not per case.
- D2's output was printed once before the freeze (`MANIFEST.json` `d2_disclosure`).

**For confirmatory use:** the workload schema of each case must come from someone who hasn't seen the fix. That
means the curator or an independent integrator, under the protocol's leakage rules.
