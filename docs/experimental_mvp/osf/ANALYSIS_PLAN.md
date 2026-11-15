# Analysis plan for the r3 evaluation (predeclared; part of `FREEZE.json`)

The binding analysis is r3 §8 and §13, quoted verbatim below from `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md` (sha256
`af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`). If this file and r3 differ, r3 governs. The quoted sections'
own sha256 values (UTF-8, as quoted) are in `FREEZE.json`.

## How it is computed

- **Code:** `research_tools/paired_eval.py`, sha256 `a6207b8e910057ac6d21935dac1969cc32d4bcf6cea4a04bcbc59efe1fd0f379`, the same path used in
  ds-nbc/1. Any change to it before the freeze changes this hash in `FREEZE.json`. After the freeze it doesn't change.
- **Primary comparison:** the Muse workload arm (RA-MD, or RA-MDS where the case's integration has a state stage)
  against RA-DET-G, exact two-sided McNemar on discordant cases, α = 0.05; RA-DET is the secondary comparison, with
  Holm correction across the two.
- **#10, paired time to detection, on the same sealed cases:** r3's predeclared secondary outcome (#28). It comes
  from the same runs as detection (same cases, arms, seeds and K × S = 12 schedules per revision, in slot order). An
  arm that doesn't detect is right-censored at 12. No extra run, case or budget is needed.
- **Labelling:** "preregistered, partially blind (r3 with declared staffing deviations G2–G5 and the G1 residual
  channel)", plus the number of cases admitted under selection rule S4.
- **Never pooled:** development cases (ds-nbc/1, ds-nbc/2) and mutants (`benchmark_prep/MUTANTS.md`) are reported
  separately and never combined with the blind historical cases.

## r3 §8, verbatim

## 8. Analysis (paired)

- **RA-Q3 detection [RULINGS 25, 26, PROPOSED: see §19]:**
  - Exact two-sided McNemar on discordant cases, for the Muse workload arm against **RA-DET-G** (primary: RA-MDS where the case's integration has a state stage, otherwise RA-MD), and against RA-DET (secondary), with Holm correction.
  - A leave-one-application-out sensitivity analysis is reported for every comparison.
  - The test is run only with at least 10 eligible cases; otherwise the result is descriptive.
  - A non-significant result isn't evidence of no difference.
- **Time to detection [RULINGS 28, PROPOSED: see §19]:** defective-revision schedules to the first target-clause detection, in slot order, right-censored at K × S (primary). Execution seconds (run time only, excluding image builds and lock waits) are secondary; end-to-end seconds including each arm's generation time (model latency, generator time) are tertiary. INCOMPLETE and REFUSED runs consume their schedules, count as nondetections, are never retried, and are reported per arm and per workload.
  - Report Kaplan–Meier curves per arm, and the paired median difference with censored values at K × S.
  - An exact Wilcoxon signed-rank test applies only with at least 10 cases both arms detect.
- **RA-Q1 and RA-Q2:** paired differences in proportions, with exact (Clopper–Pearson or mid-p) intervals.
- **RA-Q4:** frozen §10 and §11 measures. A claim needs a median reduction of at least 30% in both minutes and lines across at least 3 applications, with no lost detection.
- **Reporting:** there's no winner from a single case, and no rate stands alone: exact counts are always shown.

## r3 §13, verbatim

## 13. Predeclared primary outcomes

- **Primary [RULINGS 25, PROPOSED: see §19]:** RA-Q3. A paired case-level detection difference, the Muse workload arm against RA-DET-G, with an exact McNemar test (§8). It's confirmatory only with at least 10 eligible blind cases; otherwise it's descriptive and stated as such before any run.
- **Co-primary, descriptive:** RA-Q1, the validity rate difference RA-MD − RA-DET; and RA-Q2, the bridged-and-MATCH rate difference RA-MS − RA-DET, with RA-DET-C reported alongside.
- **Secondary:** schedules to first detection (Kaplan–Meier and the paired censored median difference); coverage measures (§7); RA-Q4 onboarding measures; cost per detection.
- **Not an outcome:** the number of proposals, the number of model calls, or proposal diversity.
