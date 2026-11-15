# OSF Preregistration: answers ready to paste (DRAFT; nothing submitted)

The answers follow the field order of OSF's standard "OSF Preregistration" template. Attach
`AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`, `R3_SHA256`, `PREREGISTRATION_SUMMARY.md` and
`protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md` (signed). Bracketed items are for the owner.

## Study information

**Title.** Do language-model-proposed workloads detect more historical database concurrency defects than an
outcome-blind generator? A paired, preregistered evaluation of DeploySpecter on real Python/PostgreSQL applications.

**Description.** DeploySpecter steps real application code statement by statement against a disposable PostgreSQL,
and checks each run against the server log. We compare workloads proposed by Muse Spark with those of an
outcome-blind schema generator, under equal execution budgets. The cases are historical, already-fixed upstream
defects, chosen and sealed by an independent curator. The full protocol is Amendment 003, revision candidate r3,
sha256 af9b2205…fe1c (attached).

**Hypotheses.** H1 (primary, RA-Q3): the Muse workload arm detects a different number of cases than RA-DET-G,
two-sided. Secondary: a difference in schedules to first detection. Descriptive: the validity-rate difference
(RA-Q1); the bridge-and-MATCH rate difference where a state stage exists (RA-Q2).

## Design plan

**Study type.** Other: a paired computational experiment on software artefacts. No human subjects for RA-Q3.

**Blinding.**
- The curator seals which revision is defective, and the runner never sees the labels before scoring.
- Triage reviewers are blind to the arm.
- Deviations from r3's independence requirements are declared in the attached owner adoption record (G1–G5).

**Study design.**
- Within-case paired design: every arm runs on the same sealed cases, builds, seeds and budgets (K = 6 slots ×
  S = 2 schedules per revision).
- Arms: RA-MD (Muse), RA-DET-G (outcome-blind generator), RA-DET (integrator-curated); state-stage arms where
  applicable.

**Randomization.** The curator orders each case's two revisions randomly. Schedules come from a seeded explorer,
with seeds `seed(case, slot, j)` identical across arms. The arm order per case is a frozen permutation.

## Sampling plan

**Existing data.** Registration before any data are collected. The development cases (ds-nbc/1, ds-nbc/2) are
NONBLIND, excluded, and never pooled.

**Data collection procedures.** Runs use restricted containers with pinned trees, a disposable PostgreSQL 16 and
server-log corroboration (0 mismatched parameters required). Nothing is retried.

**Sample size.** At least 10 eligible sealed cases from at least 5 applications, at most 3 per application. The
screening budget is about 100 candidates (`benchmark_prep/FEASIBILITY.md`).

**Sample size rationale.** McNemar on discordant pairs needs at least 10 cases to be run at all (r3 #4). With
10–15 cases, power is low, and a non-significant result won't be read as equivalence.

**Stopping rule.** Case recruitment ends at 10 or more sealed eligible cases, or when the screening budget is
exhausted. No early stopping within a case. Breaching the per-case safety ceiling, or an oracle error, excludes
the case for every arm.

## Variables

**Manipulated variables.** The workload source (arm).

**Measured variables.**
- Detection (binary, per case and arm).
- Schedules to first target-clause detection, censored at 12.
- Execution seconds and end-to-end seconds.
- Proposal validity counts.
- Coverage measures (r3 §7).

**Indices.** Detection requires a SOUND, corroborated target-clause violation on the defective revision and none
on the corrected revision.

## Analysis plan

**Statistical models.**
- Exact two-sided McNemar: Muse arm vs RA-DET-G (primary), and vs RA-DET (secondary, Holm-corrected).
- Leave-one-application-out sensitivity analysis.
- Kaplan–Meier curves and the paired censored median difference for time to detection, with an exact Wilcoxon
  signed-rank test only if both arms detect at least 10 cases.

**Inference criteria.** α = 0.05, two-sided, with Holm correction across the two comparisons. Exact counts are always
reported.

**Data exclusion.** A case is excluded for every arm on an oracle error, a breach of the safety ceiling, or missing
required records (r3 §14). A corrected-version violation voids the case for that arm.

**Missing data.** INCOMPLETE and REFUSED runs consume their schedules and count as nondetections. Failed model
requests count against R.

**Exploratory analysis.** The slot-level non-target filter, mutants (reported separately, never pooled) and cost
per detection.

## Other

- **Labelling:** results are labelled "preregistered, partially blind (r3 with declared staffing deviations
  G1–G5)" unless r3 §15 is fully met.
- **Isolation:** not hostile-grade.
- **Model exposure:** training-data exposure can't be excluded (r3 #20).
- **Funding and conflicts:** [owner].
