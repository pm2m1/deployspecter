# OSF Preregistration: ready to paste, field by field (NOT REGISTERED)

Template: OSF's standard "OSF Preregistration". Each `###` heading is the OSF field; paste the text under it.
Upload as files: `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`, `R3_SHA256`, `FREEZE.json`, `SELECTION_RULE.md`,
`ANALYSIS_PLAN.md`, `../benchmark_prep/MUTANTS.md`, and, once signed and initialled,
`../protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md` and `../protocol/OWNER_DECISIONS_r3_admission.md`. Bracketed items
are the owner's. Where this text and r3 differ, r3 governs.

## Study information

### Title

Do language-model-proposed workloads detect more historical database concurrency defects than an outcome-blind
generator? A paired, preregistered evaluation of DeploySpecter on real Python/PostgreSQL applications

### Description

DeploySpecter steps real application code statement by statement against a disposable PostgreSQL, and checks each
run against PostgreSQL's server log. We compare workloads proposed by a language model (Muse Spark,
`muse-spark-1.3`) with those of an outcome-blind schema generator, under equal execution budgets, on historical,
already-fixed upstream defects that an independent curator selects and seals. The protocol is Amendment 003,
revision candidate r3, sha256 `af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`. `FREEZE.json`
lists the hash of every frozen document. Staffing doesn't meet r3's independence rules in full; the deviations are
declared in the attached adoption record, and results will be labelled "preregistered, partially blind".

### Hypotheses

- **H1 (primary, RA-Q3):** the Muse workload arm detects a different number of cases than RA-DET-G (two-sided).
- **Secondary:** a difference in defective-revision schedules to first detection (Muse arm vs RA-DET-G); detection
  against RA-DET (Holm-corrected).
- **Descriptive:** the validity-rate difference (RA-Q1); the bridged-and-MATCH rate difference where a state stage
  exists (RA-Q2).

## Design plan

### Study type

Other: a paired computational experiment on software artefacts. No human-subject data are collected for the outcomes.

### Blinding

- The Label Curator (labmate 1) selects and seals the cases. Each case's two revisions are handed over in random
  order, so the runner (the owner) doesn't know which is defective until scoring is final.
- The Oracle Author (labmate 2) never sees any fix, its tests, changelog, issue or PR text.
- Triage reviewers see arms only under a random relabelling.

### Is there any additional blinding in this study?

The owner, who writes integrations and runs every arm, is blind to labels, oracle text and target clauses until the
freeze. Declared limits: no oracle reviewer (G2), the owner is a tool developer acting as integrator (G3), triage
reviewers aren't independent of the case (G4), no adjudicator (G5), and the curator's contract-area statement is a
residual channel to the oracle author (G1). See the adoption record.

### Study design

- **Within-case paired design:** every arm runs on the same sealed cases, builds, seeds and budgets.
- **Arms:** RA-MD (Muse workloads), RA-DET-G (outcome-blind generator), RA-DET (integrator-curated); RA-MS, RA-MDS
  and RA-DET-C only where the integration has a state stage.
- **Budgets:** K = 6 workload slots × S = 2 schedules per revision per arm. Muse: R = 2 requests, P = 6 proposals,
  C = $1 per case per arm; the first 6 distinct valid workloads are admitted.

### Randomization

The curator orders each case's two revisions randomly. Schedules come from a seeded explorer, with seeds
`seed(case, slot, j)` identical across arms. The arm order per case is a frozen permutation.

## Sampling plan

### Existing data

Registration prior to creation of data: no blind case has been selected, and no run has been made.

### Explanation of existing data

Development cases from ds-nbc/1 and ds-nbc/2 (4 cases from chancy, pgqueuer and saq) are NONBLIND, are excluded,
and are never pooled with the blind cases.

### Data collection procedures

- **Selection:** `SELECTION_RULE.md`: commit-level fix pairs from Python applications on psycopg 3 (directly,
  SQLAlchemy 2, or Django), including Django applications pinned to psycopg2 that run unmodified with psycopg 3
  under Django 4.2 or later (S4, verified per application before any fix is read). Screening criteria E1–E6.
- **Runs:** restricted containers with pinned source trees, a disposable PostgreSQL 16, and server-log
  corroboration that requires 0 mismatched parameters. Nothing is retried.

### Sample size

At least 10 eligible sealed cases from at least 5 applications, at most 3 per application. Screening budget: about
100 candidate fixes.

### Sample size rationale

r3 runs McNemar only with at least 10 cases. From ds-nbc/1's eligibility rate (5 of 34), about 66 screens are
needed at the median and 94 at the 90th percentile. With 10–15 cases, power is low, and a non-significant result
won't be read as equivalence.

### Stopping rule

Selection ends when at least 10 eligible cases from at least 5 applications are sealed, or the screening budget
is spent. With fewer than 10, RA-Q3 is reported descriptively, declared before any run. Within a case, every
admitted workload runs all its schedules; there is no early stopping.

## Variables

### Manipulated variables

The workload source (arm).

### Measured variables

- Detection per case and arm (binary).
- Defective-revision schedules to the first target-clause detection, right-censored at 12.
- Execution seconds (secondary) and end-to-end seconds including generation (tertiary).
- Proposal counts: generated, valid, invalid by reason, distinct valid, admitted.
- Coverage (r3 §7): handlers reached, migration-touching statements reached, conflicting pairs on one row, runs with
  a lock wait, distinct final-state digests.
- Cost per case and arm.

### Indices

Detection: a SOUND, server-log-corroborated violation of a declared target clause on the defective revision, and no
oracle violation on the corrected revision for that arm.

## Analysis plan

### Statistical models

`ANALYSIS_PLAN.md` (r3 §8 and §13 verbatim; code `research_tools/paired_eval.py`, hash in `FREEZE.json`):
- exact two-sided McNemar, Muse arm vs RA-DET-G (primary) and vs RA-DET (secondary);
- leave-one-application-out sensitivity for every comparison;
- Kaplan–Meier per arm and the paired censored median difference for time to detection; an exact Wilcoxon
  signed-rank test only if both arms detect at least 10 cases;
- RA-Q1 and RA-Q2: paired differences in proportions with exact intervals.

### Transformations

None.

### Inference criteria

α = 0.05, two-sided; Holm correction across the two detection comparisons. Exact counts are always reported, and
no rate stands alone.

### Data exclusion

A case is excluded for every arm on an oracle error (agreed by both triage reviewers), a breach of the 7,500 s
safety ceiling, or missing required records (r3 §14), including a missing model request if the owner initials that
reading (`OWNER_DECISIONS_r3_admission.md` item 3). A corrected-revision violation voids that case for that arm.

### Missing data

INCOMPLETE and REFUSED runs consume their schedules and count as nondetections. Failed and unparseable model
requests count against R. A provider-reported model other than `muse-spark-1.3` makes that request a provider
failure.

### Exploratory analysis

Slot-level non-target filtering; mutants (`MUTANTS.md`, reported separately, never pooled); cost per detection.

## Other

### Other

- **Label for all results:** "preregistered, partially blind (r3 with declared staffing deviations G2–G5 and the G1
  residual channel)".
- **Isolation:** the containers aren't hostile-grade; they share the Docker Desktop VM kernel.
- **Model exposure:** training-data exposure to the historical fixes can't be excluded (r3 #20).
- **Roles:** owner [name]; Label Curator [labmate 1]; Oracle Author [labmate 2]; protocol reader [faculty member].
- **Funding and conflicts of interest:** [owner].
