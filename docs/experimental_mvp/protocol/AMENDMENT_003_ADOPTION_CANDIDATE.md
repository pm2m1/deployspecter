# DRAFT amendment 003, ADOPTION CANDIDATE: prospective evaluation on real third-party applications

**Status: ADOPTION CANDIDATE (2026-11-06). Not in effect. Not adopted.** It takes effect only if every decider listed in `approval/` approves **this exact text** (identified by its sha256 in `approval/CANDIDATE_SHA256`), it is committed as `docs/research/live_stepping/EVALUATION_PROTOCOL_AMENDMENT_003.md`, and the owner registers its sha256 with a trusted timestamp before the first evaluation run (§15, ruling #9). If any decider amends or rejects a ruling, a new candidate is prepared, with a new sha256, and every approval is repeated.

**Review-ready** means that every open decision is now stated with a recommended ruling, an owner, and the evidence it needs (`DECISIONS_003_v2.md`). It does **not** mean that any decision is made. §15–§18 are new in version 3. Version 2 (`AMENDMENT_003_DRAFT_v2.md`) and version 1 are kept unchanged as records. Every `[RULINGS n, PROPOSED: see §19]` marker below must be replaced by an accepted ruling, with the signatory named, before this text can be committed as `docs/research/live_stepping/EVALUATION_PROTOCOL_AMENDMENT_003.md`.

- Version 2 supersedes version 1 (`AMENDMENT_003_DRAFT.md`, kept unchanged as a record). It adds §12 (model and prompt freeze), §13 (predeclared primary outcomes) and §14 (source and exposure records).
- It can't be adopted until the decisions in `ADOPTION_READINESS.md` are made.
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
- **Development cases (pgqueuer, chancy):** compatibility targets only, marked NONBLIND. They never count in RA-Q1 to RA-Q3. These are development evidence only:
  - the simultaneous mixed-version Chancy pilot (`simultaneous/RESULTS.md`);
  - the historical case `historical/chancy_2895c4a`.
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

## 12. Model and prompt freeze

Frozen in the adoption commit, by hash:
- the model identifier (`muse-spark-1.3`), the reasoning effort (`medium`) and the storage setting (`store=False`);
- every prompt template and context builder (`experimental/muse_apps.py`, `experimental/muse.py`), recorded as source sha256 and prompt sha256 per target;
- every JSON schema the model is constrained to.

A model or prompt change after adoption is a new numbered amendment and starts a new evaluation. Every request logs the model the provider reports. A reported model that differs from the frozen one makes that request a provider failure.

## 13. Predeclared primary outcomes

- **Primary:** RA-Q3. A paired case-level detection difference, RA-MDS against RA-DET, with an exact McNemar test (§8). It's confirmatory only with at least 10 eligible blind cases; otherwise it's descriptive and stated as such before any run.
- **Co-primary, descriptive:** RA-Q1, the validity rate difference RA-MD − RA-DET; and RA-Q2, the bridged-and-MATCH rate difference RA-MS − RA-DET, with RA-DET-C reported alongside.
- **Secondary:** schedules to first detection (Kaplan–Meier and the paired censored median difference); coverage measures (§7); RA-Q4 onboarding measures; cost per detection.
- **Not an outcome:** the number of proposals, the number of model calls, or proposal diversity.

## 14. Source and exposure records

For every case, the adoption commit contains:
- the selection tool's report (`research_tools/corpus_select.py`, with its sha256): sdist sha256, tag commit, licence, drivers, migration layout and rule verdict;
- the exposure ledger (§10);
- the oracle's sha256 and its author's attestation that they haven't seen the fix;
- the sealed label's sha256.

A case without all of these is excluded before any arm runs, and the reason is reported.


## 15. Governance: roles, independence and the prospective freeze [RULINGS 6, #7, #8, #9, #14, PROPOSED: see §19]

- **Label curation [RULINGS 6, PROPOSED: see §19]:** a named Label Curator, independent of the tool developers, seals each case's labels with `seal_labels.py`. Leak-checks run before every arm. Procedure and evidence: `DECISIONS_003_v2.md` §6.
- **Oracle authorship [RULINGS 7, PROPOSED: see §19]:** a named Oracle Author writes each blind case's oracle without seeing the fix, its commit, its tests or its changelog entry. An Oracle Reviewer checks the encoding. Each oracle is hashed into the freeze.
- **Triage [RULINGS 8, PROPOSED: see §19]:** two named Triage Reviewers and an Adjudicator. Verdicts are blinded to the arm and recorded independently, with κ reported.
- **Freeze [RULINGS 9, PROPOSED: see §19]:** the adoption commit's sha256 is registered (OSF Registries, plus an RFC 3161 or OpenTimestamps token) before the first run, with the owner's approval.
- **Gate separation [RULINGS 14, PROPOSED: see §19]:** RA makes no Gate 2A/A2 claim, the Gate A2 demonstration stays blocked, and results are never pooled with the frozen evaluation's.

Until every marker in this section is replaced by an accepted ruling, **no RA run is confirmatory**, and no case is BLIND_ELIGIBLE.

## 16. Documented compatibility contract [RULINGS 19, PROPOSED: see §19]

A case's mixed-version operations (old and new application code running together on one schema) count only where the application's own documentation supports them. The curator records the citation in the case labels. Otherwise:
- the case runs single-version;
- any mixed-version result is reported as exploratory, and as outside the documented contract.

The simultaneous Chancy pilot is the reason for this rule. Chancy documents "Stop your workers" before migrating, and every mixed-version P7 "violation" there fell outside that contract.

## 17. What a historical case is [RULINGS 5, PROPOSED: see §19]

- **The unit is a commit-level fix pair:** the fix commit and its first parent, each built from a hash-pinned tree. Release-level pairs (the last release before the fix and the first after) may be run as well, but are reported as confounded by every other change between the releases.
- **Single-version runs:** each revision runs on a schema migrated by that revision alone.
- **Schedules:** the forced schedule, constructed in the order a maintainers' regression test describes (when one exists), and a seeded exploration with the same number of schedules for every revision.
- **Detection:** a server-log-corroborated violation on the parent, with no violation on the fix commit, under the same schedules.
- **Feasibility shown on a development case:** `historical/chancy_2895c4a`.

## 18. Exposure from language-model training [RULINGS 20, PROPOSED: see §19]

The tool developer and Muse are trained on public repositories, so neither can attest non-exposure to a public fix. Each exposure ledger records "training-data exposure not excludable".

Blindness rests on the process:
- sealed labels;
- independent oracle authorship;
- leak-checked runner inputs.

The reviewer decides whether cases whose fix postdates a stated model training cutoff are analysed separately.

## 19. Rulings incorporated by this candidate (each PROPOSED until its decider approves this text)

- **#1.** For a Django project with several app-level `migrations/` directories, the core migration area is the `migrations/` directory of the app that holds the most `NNNN_*.py` files, determined from file names alone, before any file in it is read (ties: the app whose label sorts first). The selected area is that directory's highest-numbered file, plus `django.db.migrations.executor`.
- **#2.** For Alembic, the selected area is the single head of the revision graph named by the project's own `alembic.ini` `script_location`, as `alembic heads` would report it, read from the `revision` and `down_revision` headers only. If the project has several graphs, the one its default server or CLI upgrades is the core graph. If that is ambiguous, the case needs a written curator ruling before inclusion.
- **#3.** A PostgreSQL driver declared only under an optional extra whose name designates PostgreSQL counts as a declared dependency. The extra is recorded. This applies to every candidate alike.
- **#4.** RA-Q3 is preregistered as descriptive unless at least 10 eligible blind cases exist at the freeze.
- **#5.** A historical case is a commit-level fix pair (the fix commit and its first parent), each built from a hash-pinned tree. Release-level pairs may be run in addition, but are reported as confounded. Mutants are secondary, scored per frozen §4.2 operator, and never called defects.
- **#19.** Mixed-version operations count only where the application's own documentation supports them (a rolling or blue-green procedure). The curator records the citation in the case labels. Otherwise the case runs single-version, or its mixed-version results are exploratory and stated as outside the documented contract.
- **#21.** A directory of version-named migration files (`.py` or `.sql`) inside the core package (e.g. `procrastinate/sql/migrations/xx.yy.zz_ab_{pre|post}_*.sql`) counts as its migration area. The selected area is the highest version's files, plus the documented procedure that applies them.
- **#A4.** 'The module that applies migrations' is recorded by framework, as metadata: Django `django.db.migrations.executor`; Alembic `alembic.command.upgrade` and the project's `env.py`; custom: the module the tool finds by name, file name only.
- **#A5.** The selection tool's two content reads (`INSTALLED_APPS` literals; Alembic `revision` and `down_revision` headers) are metadata-level exposure, and don't make a case non-blind.
- **#6.** An independent Label Curator selects and seals each case's labels with `seal_labels.py`, and submits cases through `research_tools/corpus_intake.py` (sealed revision pair, contract, exposure). The runner never sees the labels before scoring.
- **#7.** An independent Oracle Author writes each blind case's properties from documentation and the public API only, never from the fix, its tests or its changelog entry. An Oracle Reviewer checks the encoding. Each spec is hashed into the freeze.
- **#8.** Two Triage Reviewers, blind to the arm, classify every corrected-version violation. A predeclared Adjudicator resolves disagreements, and κ is reported.
- **#9.** The adoption commit's sha256 is registered (OSF Registries, plus an RFC 3161 or OpenTimestamps token) before the first evaluation run.
- **#14.** RA makes no Gate 2A/A2 claim. The Gate A2 demonstration stays blocked, and results are never pooled.
- **#20.** Each exposure ledger records "training-data exposure not excludable" for public cases. Blindness rests on the process (sealing, independent oracles, leak checks).
- **#10.** A per-application adapter counts as RA-Q4 onboarding work. A generic engine capability counts as tool development.
- **#11.** A run with LISTEN/NOTIFY disabled (polling) is faithful for database-statement semantics. A case whose defect needs the notification path is excluded and reported.
- **#12.** The Django seam stays development-only until a development Django application of comparable scale has run through it.
- **#13.** The restricted runc container is acceptable only for pinned, publicly released, statically reviewed code. It is disclosed in every result.
- **#15.** B = 12 schedules per workload per build (forced plus seeds 0–10), T = 900 s per case per arm, 3 replicates; R = 2 requests, P = 6 proposals, C = $1 per case per arm (`PROSPECTIVE_MUSE_PLAN.md` §5).
- **#16.** The deterministic arms' model budget is zero.
- **#17.** Model-proposed invariant hypotheses stay exploratory.
- **#18.** `RA-SMOKE`, the simultaneous Chancy pilot, `historical/chancy_2895c4a` and `rolling_upgrade/procrastinate_3.3_3.4` are cited only as development evidence.
- **#22.** **Server-log corroboration of typed parameters.** A run's corroboration reports compared, mismatched and unchecked values separately. A confirmatory result requires 0 mismatched. Unchecked values are disclosed per type, and never counted as matched (record arrays and float8 are now compared exactly: `rolling_upgrade/.../runs_typed_verification`).
