# Preregistration summary: Amendment 003 r3, real-application evaluation (DRAFT; NOT REGISTERED)

The binding text is `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md` in this folder, a byte-identical copy of
`protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`. Its sha256 is
`af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c` (`R3_SHA256`). This summary is a reading aid;
where it and r3 differ, r3 governs. Staffing deviations: `protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md` (G1–G5).

## Selection rule (r3 §3, #26; `CORPUS_SHORTLIST.json`; widened pool in `benchmark_prep/FEASIBILITY.md`)

- **Who selects:** an independent curator chooses commit-level fix pairs (#5): one case per upstream defect, at
  most 3 per application, at least 5 applications.
- **Pool:** psycopg 3 Python applications that are not held out, not development applications, not GitLab-derived,
  and use versioned migrations.
- **Eligibility:** criteria E1–E6.
- **Sealing:** each case is sealed through `corpus_intake.py`. Exposure is ledgered, and leak checks run before
  every arm.

## Arms (r3 §2)

| Arm | Role here |
|---|---|
| RA-MD | Muse workload arm (primary comparator) |
| RA-DET-G | outcome-blind schema generator (primary reference) |
| RA-DET | curated by the integrator (secondary reference) |
| RA-MS, RA-MDS, RA-DET-C | only where the integration has a state stage; not applicable otherwise (recorded per case) |

## Budgets (r3 §4, #15, #24, #24a)

- **Execution:** K = 6 slots × S = 2 schedules per revision per arm, with seeds `seed(case, k, j)` identical across
  arms.
- **Muse:** R = 2 requests and P = 6 proposals, under a cap of C = $1 per case per arm. The first 6 distinct valid
  workloads are admitted.
- **Per-run limits:** a 45 s coordinator stop, a 240 s container limit and L = 300 s.
- **Safety ceiling:** 7,500 s per case and arm. Breaching it excludes the case for every arm.

## Primary metric (r3 §13)

RA-Q3 case-level detection: a SOUND, corroborated target-clause violation on the defective revision, with no
violation on the corrected revision. The primary comparison is the Muse workload arm against RA-DET-G.

## Analysis plan (r3 §8)

- **Detection:** exact two-sided McNemar, with RA-DET as a Holm-corrected secondary comparison, plus
  leave-one-application-out sensitivity.
- **Conditions:** confirmatory only with at least 10 eligible blind cases, and r3 §15 satisfied. Otherwise
  descriptive. Under G1–G5 it is "preregistered, partially blind".
- **Secondary, time to detection:** schedules to the first target-clause detection, right-censored at 12, with
  Kaplan–Meier per arm, the paired censored median difference, and an exact Wilcoxon test only if at least 10 cases
  are detected by both arms.

## Stopping rules (r3 §4, §6, §8)

- **No early stopping:** every admitted workload runs all of its schedules, and nothing is retried, substituted or
  reordered after any outcome.
- **A case is excluded for every arm** on any of:
  - an oracle error (triage b);
  - a breach of the safety ceiling;
  - a missing record (owner decision pending; see `protocol/r3/ADMISSION_REVIEW_r3.md` item 3).
- **A corrected-version violation voids that case for that arm.**
- **Recruitment of cases ends** when the curator has sealed at least 10 eligible cases from at least 5 applications,
  or when the screening budget (about 100 screens) runs out. If there are fewer than 10, RA-Q3 is descriptive,
  declared before any run.

## #10, paired time to detection, on the same sealed cases

No extra protocol is needed:
- Time to detection is r3's predeclared secondary outcome (#28, §8). It is measured from the same runs as
  detection: the same sealed cases, arms, seeds and K × S schedules, in slot order.
- Each case gives one paired observation per comparison (Muse arm vs RA-DET-G). An arm that doesn't detect is
  censored at 12 schedules.
- Execution seconds (secondary) and end-to-end seconds including generation time (tertiary) come from the same run
  records.
- No additional run, case or budget is required, and the analysis code is the same `paired_eval.py` path used in
  ds-nbc/1.
