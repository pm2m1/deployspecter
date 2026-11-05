# DRAFT amendment 003: prospective evaluation on real third-party applications

**Status: DRAFT. Not in effect. Not adopted.**
- The frozen protocol (`docs/research/live_stepping/EVALUATION_PROTOCOL.md`, version 1.2 after amendments 001 and 002) is unchanged.
- The protocol's own rule (§14) is that an amendment takes effect only when its file is committed next to the protocol. This draft is deliberately kept outside that directory.
- Adoption is the owner's decision. It happens after the issues in `REVIEW_ISSUES.md` are resolved, by committing a reviewed copy as `docs/research/live_stepping/EVALUATION_PROTOCOL_AMENDMENT_003.md`.
- It applies only prospectively, to evaluation runs that start after that commit.

The §14 statement:

- **Number and date:** 003 (draft), 2026-11-05.
- **Sections it would change:** none are replaced. It adds a separate evaluation, the "real-application evaluation" (RA). Its arms, corpus and analysis are defined here.
- **Reason:** the frozen evaluation targets historical held-out defects in the held-out applications, through Gate 2A's supervised path. The experimental MVP has since added stepping for real third-party code paths: async psycopg, SQLAlchemy, a job queue's Driver port, and a pooled job runtime. That needs its own preregistered design, which mustn't borrow or blur the frozen arms.
- **Held-out material or results seen when written:** no held-out application's source has been read. What the authors have seen is listed in "Exposure", below.
- **Affects a primary hypothesis of protocol 1.2:** no. RA's hypotheses are its own, and none of P1, P2 or S1 to S8 changes.

## 1. Questions, kept distinct

| | Question | Unit | Primary outcome |
|---|---|---|---|
| RA-Q1 | Does Muse improve the generation of **valid workload drivers** (registered handlers and arguments)? | proposal, paired by case | Share of proposals the deterministic validator accepts, under equal request budgets, plus distinct handlers and migration-touching statements reached |
| RA-Q2 | Holding the driver fixed, does Muse improve **initial-state construction**? | case × fixed driver | Share of state-goal sets that the unmodified WorkloadSpec v2 chain bridges and the live catalog verifies (MATCH), and that then reach the case's target statements |
| RA-Q3 | Does Muse improve **independently verified defect detection**? | case, paired | Detection on the defective version with no violation on the corrected version; time to first detection, in schedules, right-censored at the budget |
| RA-Q4 | Does Muse reduce **manual repository onboarding**? | application | Minutes and handwritten lines (§10 of the frozen protocol, measured the same way) |

All four are preregistered together. RA-Q4 can't be promoted after RA-Q3's results are seen.

## 2. Arms

These labels are new. Each is defined in full here, and none is a frozen arm.

| RA arm | Driver | State | Analogous in intent to a frozen arm (not the same arm) |
|---|---|---|---|
| RA-DET | Strong deterministic baseline: curated cases, plus the systematic grid over registered handler pairs and argument boundaries | The deterministic constructor (WorkloadSpec v2 → planner → bridge) over deterministic goals | E |
| RA-MD | Muse proposals, validated | The same deterministic constructor over RA-DET's goals | G |
| RA-MS | RA-DET's drivers | Muse state goals, through the same constructor | F |
| RA-MDS | Muse proposals | Muse state goals, through the constructor | D |
| RA-DET-C | RA-DET's drivers | **Constructor-isolation control:** RA-DET's goals with the constructor replaced by literal translation, with no search. An infeasible goal is counted, not repaired | none |

**How the arms isolate each question.** RA-Q1 compares RA-MD with RA-DET. RA-Q2 compares RA-MS with RA-DET, with the driver held fixed. RA-DET-C measures what the constructor itself contributes, so that a state-construction gain isn't credited to Muse when the constructor caused it.

## 3. Cases

- **Corpus:** cases come only from `CORPUS_MANIFEST.json`, selected under the frozen rules in `CORPUS_SHORTLIST.json`.
- **Required for each case:**
  - a public pinned source (version, sdist SHA-256 and tag commit);
  - a migration or backfill area selected by the frozen rule, or by a written reviewer ruling made before any of it is read;
  - an oracle written from the application's documentation by someone who hasn't seen the fix;
  - a defective version and a corrected version.
- **Development cases (pgqueuer, chancy):** compatibility targets only, marked NONBLIND. They never count in RA-Q1 to RA-Q3.
- **Exploratory smoke tests (label `RA-SMOKE`, never confirmatory):** every existing fixture written with the prototype is one: `reservations_mixed` (sync and async), `ledger_orm` (sync and async), Gate 2A's `live_app.reservations` including its release path, the corrected reference and mutants, and the pgqueuer and chancy lifecycles. The `release_seats` finding is NONBLIND.
- **Paired design:** every arm runs on the identical case set, the identical builds and the identical budgets.

## 4. Budgets, equal and stated before any run

- **Execution:** B schedules per workload per build, and T seconds per case. These are fixed on the development cases and hashed into the adoption commit. The shape is three replicates, as frozen §8 prescribes.
- **Model:** R requests per case per Muse arm, P proposals per request, and a cap C on estimated cost. Deterministic arms have no model budget, so they get the same execution budget and nothing more.
- **Accounting:**
  - A failed or timed-out request counts against R, with its unknown usage recorded.
  - Every reply is saved when it arrives.
  - No retry is allowed except a logged transport failure.

## 5. Validity and refusal accounting

Every proposal and every run keeps its place in the denominator.

**Proposal outcomes:**
- shape-rejected;
- validator-rejected, with a reason code;
- chain outcomes: invalid, unsupported, infeasible, indeterminate or refused, with stage and code;
- live-catalog mismatch;
- bridged and executed.

**Run outcomes:** completed, timeout, divergence, refused by the server log, actor error (with class), or blocked by an unsupported path.

**Reporting:** rejection rates are reported per arm, and never only for accepted proposals.

## 6. Detection, negative controls and oracles

- **Detected:** an oracle violation on the defective version, in at least one replicate, that the server log corroborates, with no oracle violation on the corrected version under the same budget.
- **Negative control:** a violation on the corrected version voids that case for that arm. Two reviewers triage it as a false flag, an oracle error or a new finding, under frozen §9.
- **Oracle independence:**
  - Oracles are written before any arm runs, from the specification or documentation, by someone who hasn't seen the fix.
  - Each is hashed into the adoption commit.
  - An oracle error drops the case for every arm.
- **Hypotheses:** a candidate invariant a model proposes is never an oracle. It's reported only if it holds on the corrected version, and then it's scored on mutant discrimination.
- **Mutants:** generated by the frozen §4.2 operators on the corrected version, in isolated copies. Results are reported per operator, including equivalent and undetermined mutants.

## 7. Coverage

For each arm, against the same denominators:
- registered handlers reached;
- migration-touching statements reached;
- conflicting statement pairs reached on the same row;
- runs with an observed lock wait;
- distinct final-state digests.

## 8. Analysis (paired)

- **RA-Q3 detection:**
  - Exact two-sided McNemar on discordant cases, for RA-MDS against RA-DET (primary) and RA-MD against RA-DET (secondary), with Holm correction.
  - The test is run only with at least 10 eligible cases; otherwise the result is descriptive.
  - A non-significant result isn't evidence of no difference.
- **Time to detection:** schedules to first detection, right-censored at B.
  - Report Kaplan–Meier curves per arm, and the paired median difference with censored values at B.
  - An exact Wilcoxon signed-rank test applies only with at least 10 cases both arms detect.
- **RA-Q1 and RA-Q2:** paired differences in proportions, with exact (Clopper–Pearson or mid-p) intervals.
- **RA-Q4:** frozen §10 and §11 measures. A claim needs a median reduction of at least 30% in both minutes and lines across at least 3 applications, with no lost detection.
- **Reporting:** there's no winner from a single case, and no rate stands alone: exact counts are always shown.

## 9. Manual work and cost

- **Onboarding log:** timestamped during the work, never reconstructed afterwards.
- **What it records:**
  - setup minutes;
  - handwritten adapter, registration and allowlist lines, counted by script;
  - corrections;
  - unsupported features met;
  - whether the first execution succeeded;
  - review burden.
- **Cost per Muse arm:** requests, tokens, latency, estimated cost (not billing data), and cost per detection.

## 10. Exposure tracking

Each case has an exposure ledger, committed before its first arm runs. It records, per person and tool, what was read: metadata, file names, source modules or migration bodies, issues, fixes, or traces containing the application's SQL.

- **NONBLIND:** any case where the fix, its defect report or the defect's code path was seen, or where the defect was found during development. It's excluded from RA-Q3's confirmatory analysis.
- **Prompts:** Muse prompts may contain only what the registry and schema expose. That excludes docstrings, fixes, oracles and results.

## 11. Third-party execution

- **Where:** third-party code runs only in the isolated runner (`research_tools/isolated`), against a disposable PostgreSQL, with pinned and hashed dependencies.
- **Boundary:** this is a restricted container, not hostile-grade isolation. It shares the Docker Desktop VM's kernel, and that is a stated limitation of every RA result.
- **Previously unknown problems:** frozen §13 applies.
