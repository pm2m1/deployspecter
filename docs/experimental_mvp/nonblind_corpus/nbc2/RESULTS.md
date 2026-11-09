# ds-nbc/2: D1 vs D2 vs Muse on the four ds-nbc/1 cases (RA-NONBLIND EXPLORATORY, 2026-11-10)

**This is a separate, prospective experiment.** It does not replace ds-nbc/1's primary result (RA-DET 4/4 vs RA-MD
1/4, `../track_b/RESULTS.md`), which `../track_b/AUDIT_1.md` explains: an executor that runs only 3 workloads, and
fix-informed deterministic workloads.

**Frozen before any Muse request or run.**
- **The manifest:** `MANIFEST.json` (sha256 `b0f19bb2…`, commit `86d0d36`).
- **Muse's proposals:** one request per case, journaled in `proposals/` and committed (`fb7540f`) before any run.
- **The D2 workloads:** computed into the manifest at freeze time.
- **Unchanged inputs:** the integrations, oracles and pinned trees are ds-nbc/1's, checked by hash at every run.

## Design (identical for every arm)

| | D1 | D2 | M |
|---|---|---|---|
| Source | ds-nbc/1's developer-written, fix-informed workloads (3 per case) | outcome-blind generator: the workload JSON schema only, seeded by case id | one Muse request (`nbc-muse-workloads/1`, `MUSE_CONTEXT` plus schema), all valid proposals admitted in model order |
| Fix, oracle or results visible | yes (written after reading the fix) | no | no |
| Workloads | 3 (cycled into 6 slots) | 6 | 6 (6 of 6 valid in every case) |
| Schedules | 6 slots × 2 per revision = 12 on R1 and 12 on R2 | same | same |

- **Seeds:** `seed(case, slot, j)`, the same for all arms.
- **No early stopping:** every admitted workload ran on both revisions.
- **Arm order per case:** a frozen permutation.
- **Runs:** 288 in total (4 cases × 3 arms × 6 slots × 2 schedules × 2 revisions). 268 were SOUND and 20 INCOMPLETE
  (chancy two-job workloads whose cancel landed before any claim). None was REFUSED or ERROR, and none was retried.
- **Corroboration:** every SOUND run matched the server log, with **0 parameters mismatched and 0 unchecked**
  (22,570 typed parameters compared).
- **Images:** every image matched its slot's archive file by file.

## Results (predeclared definitions)

| Case | D1: detected / schedules / seconds | D2 | M | Fixed-version violation |
|---|---|---|---|---|
| chancy-2895c4a | yes / 1 / 4.9 | yes / 9 / 49.3 | yes / 1 / 5.1 | none |
| pgqueuer-8dfadf4 | yes / 1 / 5.5 | yes / 9 / 44.4 | yes / 11 / 60.9 | none |
| saq-c570ba4 | yes / 1 / 2.6 | yes / 11 / 64.1 | yes / 5 / 30.0 | **D2 and M** (clause B3, see below) |
| saq-addee31 (post-hoc v2 encoding) | yes / 1 / 2.5 | yes / 1 / 2.6 | yes / 7 / 16.9 | none |

**Paired analysis (`analysis_1/ANALYSIS.json`).**

| Comparison | Detection | McNemar | Gehan sign (faster: x vs y) | Kaplan–Meier median schedules | Verdict |
|---|---|---|---|---|---|
| **M vs D2 (primary)** | 4/4 vs 4/4 (each 95% CI 0.398–1.000) | no discordant pairs, p = 1.0 | 2 vs 2, p = 1.0 | 5 vs 9 | **INSUFFICIENT SAMPLE**; both arms have a fixed-version violation |
| D1 vs D2 | 4/4 vs 4/4 | p = 1.0 | 3 vs 0, 1 tie, p = 0.25 | 1 vs 9 | INSUFFICIENT SAMPLE |
| D1 vs M | 4/4 vs 4/4 | p = 1.0 | 3 vs 0, 1 tie, p = 0.25 | 1 vs 5 | INSUFFICIENT SAMPLE |
| M vs D2 without saq-addee31 (sensitivity) | 3/3 vs 3/3 (95% CI 0.292–1.000) | p = 1.0 | 2 vs 1, p = 1.0 | | INSUFFICIENT SAMPLE |

**The saq-c570ba4 fixed-version violations.** D2 workload 5 and M workload 2 (concurrency 2, `max_burst_jobs` 2–3,
4 jobs) violate on **both** revisions.
- **The clause:** B3. Saq completed more jobs than `max_burst_jobs` (3 > 2, 4 > 3), which is the burst overrun that
  made saq-ad8d1c8 CONTROL FAILED in ds-nbc/1.
- **Not this case's defect:** it is a property of saq at the fix commit too.
- **The oracle is unchanged.** It was not narrowed to remove B3.

**POST-HOC: discriminating detection** (`analysis_1/POST_HOC_discriminating.json`; not predeclared). A detection
counts only from a workload slot whose fixed-revision runs are all SOUND and hold.

| Case | D1 | D2 | M |
|---|---|---|---|
| chancy-2895c4a | 1 | 9 | 1 |
| pgqueuer-8dfadf4 | 1 | 9 | 11 |
| saq-c570ba4 | 1 | **none** | 9 (workload 4: a job left `active`, clause B1, the defect itself; it holds on the fix) |
| saq-addee31 | 1 | 1 | 7 |
| **detected** | **4/4** | **3/4** | **4/4** |

Under this post-hoc reading, M and D2 differ on one case only (saq-c570ba4: M yes, D2 no). That isn't significant
(p = 1.0), and they split the time comparisons 2–2.

## Utilization and cost

| Arm | Workloads executed | Workloads that violated on R1 (any clause) | Invalid proposals | R1 execution seconds | Model cost |
|---|---|---|---|---|---|
| D1 | 12 (3 per case) | 10 | n/a | 370.8 | $0 |
| D2 | 24 | 6 | 0 (generator rejections: 1 of 27 draws) | 311.0 | $0 |
| M | 24 | 8 | 0 of 24 | 386.5 | **$0.059209** (4 requests; 6,489 tokens in, 12,023 out) |

## What this shows, and what it doesn't

1. **Detection.** With the executor's 3-workload limit removed and equal budgets, Muse's workloads detected all 4
   development defects. So did the outcome-blind generator, and so did the fix-informed workloads. On 4 cases no
   detection difference is detectable. ds-nbc/1's 1/4 for Muse came mostly from proposal order under a 3-workload
   executor, not from Muse being unable to propose triggering workloads.
2. **Time to detection.** The fix-informed D1 was fastest (1 schedule in every case), as expected of regression
   workloads written after reading the fix. Between the two fix-blind sources, Muse was faster on 2 cases and the
   generator on 2. Kaplan–Meier medians were 5 (M) and 9 (D2), and the sign test is null (p = 1.0).
3. **Muse is neither superior nor inferior here.** The sample is 4 developer-selected, NONBLIND cases from 3
   applications, with small, explicit workload spaces where random sampling is strong. That is far below the 10
   cases the predeclared rule needs.
4. **Threats.**
   - The cases were chosen and encoded by developers who knew the fixes, and they shape both fix-blind arms'
     workload spaces through the integration's schema.
   - saq-addee31's encoding is post hoc.
   - D2 saw less information than M (the schema only), which favours M.
   - One run per arm and case.
