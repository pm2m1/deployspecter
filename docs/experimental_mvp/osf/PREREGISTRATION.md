# OSF Preregistration: ready to paste, field by field (NOT REGISTERED; single-investigator design)

Template: OSF's standard "OSF Preregistration". Each `###` heading is the OSF field; paste the text under it.
Click-by-click steps are in `REGISTER_NOW.md`. Upload as files: `FREEZE.json`, `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`,
`R3_SHA256`, `SELECTION_RULE.md`, `SELECTION_RULE_SOLO.md`, `ANALYSIS_PLAN.md`, `ANALYSIS_PLAN_SOLO.md`,
`../protocol/AMENDMENT_003_r3_SOLO_ADOPTION.md`, `../protocol/OWNER_DECISIONS_r3_admission_2026-11-16.md`,
`../solo/CANDIDATES_FROZEN.json` and `../benchmark_prep/MUTANTS.md`. Where this text and r3 differ, r3 and the solo
adoption record govern. The earlier four-person plan is superseded.

## Study information

### Title

Do language-model-proposed workloads detect more historical database concurrency defects than an outcome-blind
generator? A paired, preregistered, single-investigator evaluation of DeploySpecter on real Python/PostgreSQL
applications

### Description

DeploySpecter steps real application code statement by statement against a disposable PostgreSQL, and checks each
run against PostgreSQL's server log. We compare workloads proposed by a language model (Muse Spark, `muse-spark-1.3`)
with those of an outcome-blind schema generator, under equal execution budgets. There are two separately reported
sources of cases:
- historical, already-fixed upstream defects, selected by frozen code with a sealed seed and judged by the
  maintainers' own regression tests;
- seeded mutants, judged by automatic differential oracles.

One person runs the study. Independence that r3 requires is replaced by automation where possible, and the rest
is declared. The protocol is Amendment 003, revision candidate r3 (sha256
`af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`), adopted under the attached solo adoption record.
`FREEZE.json` lists the sha256 of every frozen document and program, and of the two sealed seeds. Results will be
labelled "preregistered, automation-blinded, single-investigator. Not independent".

### Hypotheses

- **H1 (primary, RA-Q3):** on historical cases, the Muse workload arm detects a different number of cases than
  RA-DET-G (two-sided).
- **Secondary:** a difference in schedules to first detection (Muse arm vs RA-DET-G); detection against RA-DET
  (Holm-corrected); mutation score per arm (reported separately).
- **Descriptive:** the validity-rate difference (RA-Q1); the bridged-and-MATCH rate difference where a state stage
  exists (RA-Q2).

## Design plan

### Study type

Other: a paired computational experiment on software artefacts. No human-subject data are collected.

### Blinding

- **Case selection** is done by frozen code (`select_cases.py`) with a sealed seed whose hash alone is published
  here. It runs only after this registration.
- **Mutant identities** are sealed (encrypted store, seeded sample) until each mutant's verdict is recorded.
- **Not blind:** the investigator knows each historical case's fix commit, transcribes its regression test into the
  oracle, and writes every integration.

### Is there any additional blinding in this study?

No. Declared instead (see the solo adoption record):
- no independent curator, oracle author, oracle reviewer, integrator, triage reviewers or adjudicator;
- the integrator knows each defect (the main residual threat);
- the seeds' secrecy is a commitment, not a technical barrier against the investigator.

### Study design

- **Paired within case:** every arm runs on the same cases, builds, seeds and budgets.
- **Arms:** RA-MD (Muse workloads), RA-DET-G (outcome-blind generator), RA-DET (investigator-curated); RA-MS, RA-MDS
  and RA-DET-C only where the integration has a state stage.
- **Budgets:** K = 6 slots × S = 2 schedules per revision per arm. Muse: R = 2 requests, P = 6 proposals,
  C = $1 per case per arm.
- **Oracles:** historical cases use the maintainers' regression test from the fix commit, as a spec over the final
  database state (`maintainer_oracle.py`); mutants use a differential against the unmutated build under the
  identical schedule (`auto_oracles.py`).

### Randomization

- **Order of applications and candidate fixes:** fixed by the sealed selection seed.
- **Mutant sample:** fixed by the sealed mutant seed.
- **Schedules:** from a seeded explorer, with seeds `seed(case, slot, j)` identical across arms.
- **Arm order** per case: a frozen permutation.

## Sampling plan

### Existing data

Registration prior to creation of data: no blind case has been selected, and no run has been made.

### Explanation of existing data

The development cases from ds-nbc/1 and ds-nbc/2 (4 cases from chancy, pgqueuer and saq) are NONBLIND, excluded,
and never pooled. The solo tooling was checked on them (`solo/DEV_CASE_CHECK.md`). Their maintainer tests admitted 2
of the 4 cases, which were then detected with holding controls; the adapter refused the other 2.

### Data collection procedures

- **Selection:** `select_cases.py` over the 20 frozen candidate applications (`CANDIDATES_FROZEN.json`): commits
  matching the frozen topic and fix expressions, the driver rule (psycopg 3, or rule S4: a Django application on
  psycopg2 that runs unmodified with psycopg 3 under Django 4.2 or later, checked per application before any of its
  fixes is read), and a regression test changed by the fix.
- **Oracles:** each admitted case's maintainer spec is committed and hashed before any arm runs. A case is refused
  if no assertion is expressible over the final state. A case is excluded for every arm if its oracle fails on the
  corrected revision under the serial schedule.
- **Runs:** restricted containers with pinned trees, a disposable PostgreSQL 16, and server-log corroboration that
  requires 0 mismatched parameters. Nothing is retried.

### Sample size

At least 10 eligible historical cases from at least 5 applications, at most 3 per application; at most 100 screens.
Mutants: up to 5 per operator per application of the selected cases.

### Sample size rationale

r3 runs McNemar only with at least 10 cases. Screening estimates are in `FEASIBILITY.md`. The adapter's refusal
rate (2 of 4 development cases) may lower the yield. With 10–15 cases, power is low, and a non-significant result
won't be read as equivalence.

### Stopping rule

Selection stops at 10 admitted cases from at least 5 applications, or after 100 screens. With fewer than 10 cases,
RA-Q3 is descriptive. Within a case, every admitted workload runs all its schedules; there is no early stopping.

## Variables

### Manipulated variables

The workload source (arm).

### Measured variables

- Detection per case and arm.
- Defective-revision schedules to the first detection, right-censored at 12.
- Execution seconds (secondary) and end-to-end seconds including generation (tertiary).
- Proposal counts.
- Coverage (r3 §7).
- Mutant verdicts (VIOLATED, EQUIVALENT-SUSPECT, UNREACHABLE; serial-killed separately).
- Adapter refusals by reason, and integration time per application.

### Indices

- **Historical detection:** a SOUND, corroborated VIOLATED of the maintainer-test oracle on the defective revision,
  and no violation on the corrected revision for that arm.
- **Mutation score:** killed / (sampled − invalid − unreachable), with and without equivalent-suspects.

## Analysis plan

### Statistical models

`ANALYSIS_PLAN.md` and `ANALYSIS_PLAN_SOLO.md`:
- exact two-sided McNemar, Muse arm vs RA-DET-G (primary) and vs RA-DET (secondary);
- leave-one-application-out sensitivity;
- Kaplan–Meier and the paired censored median difference for time to detection, with an exact Wilcoxon only if
  both arms detect at least 10 cases;
- for mutants, scores per arm with exact counts, and a separate, secondary McNemar.

### Transformations

None.

### Inference criteria

α = 0.05, two-sided; Holm correction across the two detection comparisons. Exact counts are always reported.

### Data exclusion

A case is excluded for every arm:
- on an oracle error (the oracle VIOLATED on the corrected revision under the serial schedule, checked before any
  arm runs);
- on a breach of the 7,500 s safety ceiling;
- on missing required records, including a missing model request (owner decision 3).

A corrected-revision violation voids that case for that arm.

### Missing data

INCOMPLETE and REFUSED runs consume their schedules and count as nondetections. Failed and unparseable model
requests count against R. A provider-reported model other than `muse-spark-1.3` makes that request a provider
failure.

### Exploratory analysis

Mutation scores per operator; slot-level non-target filtering; cost per detection; adapter refusal reasons.

## Other

### Other

- **Label for all results:** "preregistered, automation-blinded, single-investigator. Not independent".
- **Isolation:** the containers aren't hostile-grade; they share the Docker Desktop VM kernel.
- **Model exposure:** training-data exposure to the historical fixes can't be excluded (r3 #20).
- **People:** one investigator, Parth Mishra, holds every role.
- **Funding and conflicts of interest:** [owner].
