# DRAFT amendment 003, REVISION CANDIDATE r2 (PROPOSED): prospective evaluation on real third-party applications

**Status: PROPOSED REVISION CANDIDATE r2 (2026-11-15). Not in effect, not adopted, not registered, not signed.** It is derived from revision candidate r1 (sha256 `204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727`) by the asserted edits in `r2/build_candidate_r2.py`, and states as proposed text the study design recommended for #23–#30 plus #24a and the time rule replacing #15's T. Those recommendations are **not** a recorded owner approval. r1 and the original candidate are unchanged and remain separate candidates. Its approval forms are in `approval_r2/`; the forms in `approval/` and `approval_r1/` don't apply to it. The owner chooses at most one candidate to take forward.

**r1's status paragraph, kept as a record:** PROPOSED REVISION CANDIDATE r1 (2026-11-10). It is derived from the adoption candidate sha256 `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`, which is unchanged and remains a separate candidate. It adds proposed decisions #23–#30 (§19), drawn from the NONBLIND development experiments (`nonblind_corpus/track_b/AUDIT_1.md`, `nonblind_corpus/nbc2/AUDIT_2.md`, `onboarding/pilot_3/RESULTS.md`). Its approval forms are in `approval_r1/`; the original candidate's forms in `approval/` don't apply to it. The owner chooses at most one candidate to take forward.

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
| RA-DET-G [RULINGS 25, PROPOSED: see §19] | **Outcome-blind generator:** K distinct workloads drawn from the case's frozen workload schema only, seeded from the case id; it sees no fix, oracle, result or Muse context prose | The deterministic constructor over RA-DET's goals | none |

**Who writes RA-DET [RULINGS 25, 29, PROPOSED: see §19]:** RA-DET's curated workloads are written by the fix-blind Integrator (§15), never by anyone who has seen the fix. Fix-informed workloads are never a confirmatory arm.

**How the arms isolate each question.** RA-Q1 compares RA-MD with RA-DET. RA-Q2 compares RA-MS with RA-DET, with the driver held fixed. RA-DET-C measures what the constructor itself contributes, so that a state-construction gain isn't credited to Muse when the constructor caused it.

## 3. Cases

- **Corpus:** cases come only from `CORPUS_MANIFEST.json`, selected under the frozen rules in `CORPUS_SHORTLIST.json`.
- **Independence [RULINGS 26, PROPOSED: see §19]:** one case per upstream defect; at most 3 cases per application; at least 5 distinct applications among the eligible cases. Applications exposed during development (chancy, pgqueuer, procrastinate, saq, taskiq-postgresql, pgtq, postgres-tq, InvenTree, paperless-ngx, langgraph-checkpoint-postgres) and anything GitLab-derived are never eligible.
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

- **Execution [RULINGS 24, PROPOSED: see §19]:** every arm gets K workload slots × S schedules per revision (equal schedules, not equal workloads), with **K = 6 and S = 2** [RULINGS 24, PROPOSED in r2: see §19], fixed before any confirmatory run from development feasibility only (`r2/TIME_BUDGET_r2.md`) and hashed into the adoption commit. Slot k runs the arm's workload list entry k mod its length; seeds are seed(case, k, j), identical across arms; there is no early stopping; every admitted proposal is executed; nothing is substituted or reordered after outcomes. RA-DET-G always supplies K distinct workloads; other arms cycle when they have fewer, and the number of distinct workloads executed is reported. This replaces the three-replicate shape for RA arms.
- **Proposals and slots [RULINGS 24a, PROPOSED in r2: see §19]:** each Muse arm makes exactly R requests per case, all scheduled before any run of that case, in a fixed order that doesn't depend on any reply's content or on any run. No additional, replacement or retry request is ever made, and no slot is ever filled from another arm, another request, a later generation or a different generator. Admission takes valid proposals in model order up to K. Every invalid proposal stays in the denominator with its reason. With between 1 and K − 1 valid workloads, the slots are filled by cycling, and the slots filled by repetition are counted and reported. With no valid workload, every slot is a REFUSED run ("no valid workload") that consumes all its schedules and is a censored nondetection.
- **Time [RULINGS 15, 24, PROPOSED in r2: see §19]:** no time limit truncates an arm's K × S schedules.
  - **Per run:** every run of every arm has the same frozen limits: the coordinator's stop at 45 s, after which an uncovered run is INCOMPLETE; the host's container limit of 240 s with a kill; and an executor limit L = 300 s of wall-clock per run, under which a run whose recorded wall-clock exceeds L is INCOMPLETE whatever its verdict. A run that reaches any limit consumes its schedule and is a nondetection.
  - **Per case and arm:** a safety ceiling of 7,500 s of wall-clock time, above the bound the per-run limit implies (2 × K × S = 24 runs × L = 7,200 s, plus at most 1 s between runs). It can be reached only if runs overrun L, which is itself an instrument failure. Reaching it is an infrastructure failure: the case is excluded for **every** arm, with the reason reported. It is never censoring of one arm, and nothing is rerun.
  - **Excluded from these clocks:** image builds and pinned-tree preparation, done once per revision before any arm runs and shared by every arm. Lock waits are inside the safety clock but excluded from execution seconds.
  - **Planning figure, not a cap:** at most 1,200 s per case and arm is expected from development evidence (observed worst case 1,175 s if every run stopped at 48.1 s).
- **Model:** R requests per case per Muse arm, P proposals per request, and a cap C on estimated cost. Deterministic arms have no model budget, so they get the same execution budget and nothing more.
- **Accounting:**
  - A failed or timed-out request counts against R, with its unknown usage recorded.
  - Every reply is saved when it arrives.
  - No retry is allowed [RULINGS 24a, PROPOSED in r2: see §19]: a logged transport failure counts against R, keeps its worst-case charge, and is not retried.

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

- **Target clauses [RULINGS 23, PROPOSED: see §19]:** each oracle lists its clauses, and the Oracle Author declares before sealing which clauses encode the target defect. The oracle reports the violated clause ids.
- **Detected:** a SOUND, server-log-corroborated violation of a **target** clause on the defective version, with no oracle violation of any clause on the corrected version under the same budget.
- **Eligibility rules [RULINGS 28, PROPOSED in r2: see §19]:** an integration may reject a workload shape (for example one known to stop INCOMPLETE on both revisions) only under an eligibility rule that is written by the Integrator without outcome knowledge, fixed and hashed into the adoption commit, and applied identically by every arm's validator, including RA-DET-G's generator and the curated RA-DET workloads. A rejected proposal counts as invalid in the denominator, and a rejected generator draw is counted. No rule is added, removed or changed after any confirmatory run. Outside such a rule, INCOMPLETE and REFUSED runs are nondetections.
- **Negative control:** a violation of any clause on the corrected version voids that case for that arm (the primary rule). Two reviewers triage it as (a) a false flag, (b) an oracle error (the case drops for every arm), (c) a pre-existing violation of a non-target clause (reported; the arm's case stays void), or (d) a new finding, under frozen §9. A slot-level analysis that disregards slots whose corrected runs violate only non-target clauses is secondary.
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

## 9. Manual work and cost

**RA-Q4 protocol [RULINGS 30, PROPOSED: see §19]:** the independent-participant study in `onboarding/participant_study/` (onb-study/1), with Muse assistance defined as a mandatory consultation at a fixed workflow point (after the public documentation, before any source reading or code), its response recorded as accepted, modified or rejected and never forced; the control condition has the same checkpoint without the model. Time is reported with and without the checkpoint's model latency. Applications are never ones exposed in development. The application count and participant minimum follow the owner's choice in ruling #30.

**RA-Q4 design [RULINGS 30, PROPOSED in r2: see §19]:** option A. Three applications never exposed in development, selected by a written rule fixed before recruitment by the study's data owner or an independent coordinator; each participant onboards two different applications, one under each condition, in one of six sequences (3 application pairs × 2 condition orders); at least 18 participants (3 per sequence), 24 preferred (adding the six mirror sequences, which balance application order); a common unscored calibration task first; end-to-end time primary and execution-only time (excluding logged Muse call durations) secondary; two blind scorers and an adjudicator; an exact two-sided sign test on application-standardized paired differences. Protocol and sample-size justification: `onboarding/participant_study/PROTOCOL_r2_3APP.md` and `SAMPLE_SIZE_r2.md`. Below the minimum, the verdict is INSUFFICIENT SAMPLE. Consent, ethics review, data ownership and independent scoring are preconditions of recruitment.

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
- every JSON schema the model is constrained to;
- [RULINGS 27, PROPOSED: see §19] the proposer module and its instructions, each case's Muse context and workload schema (sha256 of canonical JSON), and `max_output_tokens`. Each request verifies these hashes before it is sent and is refused on any mismatch. Provider sampling is unseeded; every raw reply is saved on arrival.

A model or prompt change after adoption is a new numbered amendment and starts a new evaluation. Every request logs the model the provider reports. A reported model that differs from the frozen one makes that request a provider failure.

**Model identity [RULINGS 27, PROPOSED in r2: see §19]:** the adoption commit records the exact provider-reported model string that is accepted (by default the frozen model identifier). Before every request, the client verifies the requested model, effort, `store`, `max_output_tokens`, and the proposer, instructions, context and schema hashes against the freeze, and refuses without sending on any mismatch. After it, a provider-reported model other than the accepted string makes the request a provider failure: the reply is saved, never used, and the request counts against R with its cost; no replacement request is made (#24a). Client: `research_tools/confirmatory_prep/verified_request.py`.

## 13. Predeclared primary outcomes

- **Primary [RULINGS 25, PROPOSED: see §19]:** RA-Q3. A paired case-level detection difference, the Muse workload arm against RA-DET-G, with an exact McNemar test (§8). It's confirmatory only with at least 10 eligible blind cases; otherwise it's descriptive and stated as such before any run.
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
- **Integration [RULINGS 29, PROPOSED in r2: see §19]:** a named Integrator without fix exposure, who is a different person from the Label Curator, the Oracle Author, the Oracle Reviewer, the Triage Reviewers, the Adjudicator and the tool developers, writes or approves each case's adapter use, workload schema, Muse context and curated RA-DET workloads, from the public API, documentation and the defective revision's pinned tree only. The Integrator's exposure is ledgered (an independent input-exposure record per case, committed before the freeze), and every frozen input is leak-checked with `seal_labels.py`.
- **Target clauses [RULINGS 23, PROPOSED: see §19]:** the Oracle Author declares each oracle's target clauses before sealing; the Oracle Reviewer confirms the declaration.
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
- **#15 (amended by #24 in r1, and in r2).** The execution budget is K × S schedules per revision per arm (#24: K = 6, S = 2, i.e. 12, the former B); R = 2 requests, P = 6 proposals, C = $1 per case per arm (`PROSPECTIVE_MUSE_PLAN.md` §5). The three-replicate shape no longer applies to RA arms. **r2 replaces r1's T = 900 s per case per arm**, which could truncate an all-INCOMPLETE arm (worst case 1,154 s of run time), with §4's time rule: uniform per-run limits (45 s coordinator stop, 240 s container limit, L = 300 s executor limit), no truncation of any arm's schedules, and a 7,500 s per case and arm safety ceiling whose breach excludes the case for every arm as an infrastructure failure.
- **#16.** The deterministic arms' model budget is zero.
- **#17.** Model-proposed invariant hypotheses stay exploratory.
- **#18.** `RA-SMOKE`, the simultaneous Chancy pilot, `historical/chancy_2895c4a` and `rolling_upgrade/procrastinate_3.3_3.4` are cited only as development evidence.
- **#23.** Oracles declare target clauses before sealing. Detection requires a target-clause violation on the defective version. Any corrected-version violation voids the case for that arm (primary); triage adds category (c), pre-existing violation of a non-target clause. Slot-level non-target filtering is secondary only.
- **#24.** Equal budgets of K slots × S schedules per revision for every arm; slot k runs entry k mod length; identical seeds; no early stopping; every admitted proposal executed; RA-DET-G supplies K distinct workloads, other arms cycle. K = 6 and S = 2, fixed in r2 from development feasibility only (`r2/TIME_BUDGET_r2.md`), not from any outcome.
- **#24a.** Exactly R requests per case per Muse arm, scheduled before any run; no additional, replacement or retry request after any outcome; invalid proposals stay in the denominator; slots filled by repetition are counted and reported; with no valid workload every slot is a REFUSED, schedule-consuming, censored nondetection; no slot is filled from another source.
- **#25.** New arm RA-DET-G (outcome-blind schema generator) is the primary comparator for the Muse workload arm; RA-DET (curated by the fix-blind Integrator) is secondary; fix-informed workloads are never confirmatory.
- **#26.** One case per upstream defect; at most 3 cases per application; at least 5 applications; a leave-one-application-out sensitivity analysis; development-exposed applications are never eligible.
- **#27.** §12's frozen set adds the proposer, each case's Muse context and workload schema, and `max_output_tokens`; every request verifies them before it is sent; the provider-reported model must equal the exact accepted string recorded at adoption, or the request is a provider failure (saved, never used, counted against R, not replaced). Client implemented, development-only: `confirmatory_prep/verified_request.py`.
- **#28.** Primary time is defective-revision schedules to first target-clause detection, censored at K × S; execution seconds secondary; end-to-end seconds tertiary. INCOMPLETE and REFUSED runs consume schedules, count as nondetections and are never retried, unless a workload shape is excluded by a prospective eligibility rule fixed before the freeze and applied identically to every arm (§6); excluded proposals count as invalid.
- **#29.** A named fix-blind Integrator, a different person from the curator, the oracle author and reviewer, the triage reviewers, the adjudicator and the developers, writes or approves each case's workload schema, Muse context and curated workloads; exposure ledgered; inputs leak-checked.
- **#30.** RA-Q4 uses onb-study/1 with a mandatory Muse checkpoint (recorded accepted/modified/rejected, never forced) and the same checkpoint without the model as control; r2 proposes option (A): 3 applications, six crossover sequences, at least 18 participants, 24 preferred (`PROTOCOL_r2_3APP.md`, `SAMPLE_SIZE_r2.md`). Options (B) and (C) of r1 are not part of r2.
- **#22.** **Server-log corroboration of typed parameters.** A run's corroboration reports compared, mismatched and unchecked values separately. A confirmatory result requires 0 mismatched. Unchecked values are disclosed per type, and never counted as matched (record arrays and float8 are now compared exactly: `rolling_upgrade/.../runs_typed_verification`).
