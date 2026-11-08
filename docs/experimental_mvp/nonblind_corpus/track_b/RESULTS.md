# ds-nbc/1 Track B: RA-NONBLIND paired time-to-detection (2026-11-08/09)

**Label: RA-NONBLIND EXPLORATORY.** The cases are developer-selected and the oracles developer-written, after the
fixes were read. Never confirmatory, and never pooled with confirmatory results.

**Frozen inputs:**
- **Before any RA-MD request:** `MANIFEST.json` (sha256 `d8826c26…`, commit `e7e33ad`).
- **Each case** was admitted in `ADMISSIONS.jsonl` before its first RA-MD request.
- **Analysis:** `track_b.py analyze`, which is `paired_eval.py` plus exact intervals (`analysis_1/`).

**Arms.**
- **Run:** RA-DET and RA-MD, as defined in draft v2 §2.
- **Not applicable:** RA-MS, RA-MDS and RA-DET-C. These integrations have no state-goal or constructor stage, so all
  state comes from each workload's own API calls (`MANIFEST.json` `arms_not_applicable`).
- **Held equal for both arms:** the plan's §5 budgets (B = 12 = 3 replicates × 4 schedules; T = 900 s), the seeds
  (`seed_for`), the seeded explorer, the integration, the builds and the oracle.

## Case-level results

| Case | Arm (order) | Verified detection | Schedules to first detection | Seconds to first detection | Fixed control SOUND schedules (violations) | R1 replicate verdicts | Muse requests, valid/total proposals, est. USD |
|---|---|---|---|---|---|---|---|
| chancy-2895c4a | RA-MD (1st) | yes | 1 | 5.1 | 12 of 12 (0) | V, V, H | 2, 12/12, $0.028995 |
| | RA-DET (2nd) | yes | 1 | 5.1 | 8 of 9 (0); 1 INCOMPLETE replicate (workload 3) | V, V, INCOMPLETE | 0 |
| pgqueuer-8dfadf4 | RA-DET (1st) | yes | 2 | 12.9 | 12 of 12 (0) | V, V, V | 0 |
| | RA-MD (2nd) | **no** (censored at 12) | — | — | 12 of 12 (0) | H, H, H | 2, 12/12, $0.029462 |
| saq-c570ba4 | RA-DET (1st) | yes | 1 | 2.6 | 12 of 12 (0) | V, V, H | 0 |
| | RA-MD (2nd) | **no** (censored at 12) | — | — | 12 of 12 (0) | H, H, H | 2, 12/12, $0.026194 |
| saq-addee31 (v2, POST-HOC re-encoding) | RA-MD (1st) | **no** (censored at 12) | — | — | 12 of 12 (0) | H, H, H | 2, 12/12, $0.029517 |
| | RA-DET (2nd) | yes | 1 | 2.6 | 12 of 12 (0) | V, V, V | 0 |

**Corroboration.** Every run was isolated and server-log corroborated: 0 parameters mismatched and 0 unchecked, in
every case and arm. Totals per case and arm are in `runs/<case>/SUMMARY.json`.

## Predeclared analysis (`analysis_1/ANALYSIS.json`)

| | RA-DET | RA-MD |
|---|---|---|
| Cases detected | 4 of 4 (exact 95% CI 0.398–1.000) | 1 of 4 (exact 95% CI 0.006–0.806) |
| Kaplan–Meier median schedules to detection | 1 | not reached |
| Fixed-version false positives | 0 | 0 |
| Invalid proposals | 0 | 0 of 48 |
| Muse requests, tokens in/out, est. USD | 0 | 8; 12,978 / 23,046; **$0.114168** |

- **Discordant cases:** 3 detected only by RA-DET and 0 only by RA-MD. RA-MD's share of discordant pairs has an exact
  95% CI of 0.000–0.708.
- **Tests:** exact McNemar p = 0.25; Gehan sign test 0 vs 3, with 1 tie, p = 0.25.
- **Verdict: INSUFFICIENT SAMPLE** (4 paired defect cases, fewer than 10): descriptive only. No significance is
  claimed, and 4 cases provide little power.

**Reading.**
- In this sample, Muse-generated workloads did not detect more defects than the deterministic workloads, and found
  three fewer.
- The null and negative results are reported as results.
- No detection advantage for Muse survives an equal-budget comparison here.
- The model cost ($0.11) is small, but it bought no additional detection.

## Threats to validity (why RA-DET is favoured here, beyond chance)

1. **The deterministic workloads saw the fix.** RA-DET's 3 workloads were written by developers who had read each
   fix. The protocol allows curated workloads, but in a NONBLIND setting they encode knowledge of the defect.
   Muse saw only the neutral `MUSE_CONTEXT`.
2. **Only the first 3 Muse workloads run.** The executor runs one workload per replicate, so only RA-MD's first 3
   valid proposals of 12 ran. On pgqueuer-8dfadf4, 2 unused Muse proposals had the triggering shape (`runs/pgqueuer-8dfadf4.POST_HOC.md`;
   POST-HOC, nothing rerun).
3. **saq-addee31 is a post-hoc encoding.** Its integration v2, which shapes both arms' workload space, was written
   after an earlier attempt's outcome.
4. **Few cases.** There are 4 cases from 3 applications, all exposed development apps; the sample is small and not
   representative.
5. **One run per arm and case.** Wall-clock times come from a single run under shared-database lock contention.

## Evidence

- `runs/<case>/`: the hash-chained `RESULTS_LEDGER.jsonl`, `SUMMARY.json`, `RUN_CONFIG.json`, Muse replies and
  `calls.jsonl`, and every run's coordinator output under `work/runs/`.
- Archive copies are excluded from git; `case_spec.json` pins their sha256.
- `runs/_refused_invocation_1/`: a refused first invocation of the saq cases (the shell passed the arm list as one
  argument; no run and no request).
- **Sprint Meta ledger:** `../META_LEDGER.jsonl`.
