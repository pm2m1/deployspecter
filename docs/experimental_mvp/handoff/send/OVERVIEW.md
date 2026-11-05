# DeploySpecter: overview for prospective independent evaluators

*Draft, 2026-11-06. Not sent to anyone. It names no participants. Nothing here has been adopted or registered.*

## 1. What DeploySpecter does

During an online upgrade, a PostgreSQL-backed application often runs **two code versions against one database at once**. Old workers keep running while new ones start, and schema migrations happen around them. Some defects appear only under particular interleavings of the two versions' SQL.

DeploySpecter runs the **real, unmodified application code** of both versions inside restricted containers, against a disposable PostgreSQL 16. It works in four steps:

1. **Stepping.** It intercepts each statement at the database driver (psycopg 3) and decides, from one global schedule across all processes, which statement runs next.
2. **Corroboration.** It checks the run against PostgreSQL's own server log: every session, statement, error, the grant order, and typed parameters by exact value. A run whose log doesn't match is *refused*, not repaired.
3. **Contract.** It only tests deployments that the application's **own documentation** says are supported, such as "stop workers, then migrate" or a blue-green `pre`/`post` sequence.
4. **Checking.** It evaluates application properties on the result, using oracles written from documentation.

A language model (Muse) can propose workloads as JSON. They are validated against a fixed schema; model-generated SQL or Python is never executed.

## 2. What is already demonstrated (developer-run, exploratory)

| Claim | Evidence class |
|---|---|
| A known, maintainer-documented defect (Chancy `2895c4a`, a late completion overwriting a cancellation) reproduces on the parent commit under a forced schedule, 3/3. The fix holds 3/3. Seeded exploration gives 9/10 against 0/10. Ablations show each part of the fix is necessary. | The defect and fix are **independent** (the maintainers' commit, regression test and changelog). The reproduction, oracle and case choice are **developer-written**. |
| A documented blue-green upgrade (procrastinate 3.3.0 → 3.4.0) ran all six contract phases with old and new code overlapping. 7/7 runs were sound, with no violation (a negative result). Two errors in the developer's own oracle were found and recorded as errata. | **Developer-written**. |
| Server-log corroboration compares every logged parameter type used in these runs, exactly (0 unchecked, 0 mismatched on fresh runs). | **Developer-written** tool; the instrument is PostgreSQL's own log. |

**None of this is a novel discovery, and none of it shows that Muse beats deterministic baselines.** Every case was chosen by a developer who had already read the fix.

## 3. What remains scientifically unverified

- **Detection under blindness.** Whether the method finds defects it was not shown in advance. No blind case exists yet.
- **Independent oracles.** Every oracle so far was written by the developer.
- **Generality.** Only two applications and one historical defect have been tested.
- **Muse vs deterministic workloads.** The pilots had no defect present, so they cannot speak to detection.
- **Isolation.** It is restricted, not hostile-grade.

**The protocol amendment that would govern a confirmatory study (Amendment 003) is a proposal, not an adopted protocol.** The text for approval is fixed: `protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md`, sha256 `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`. Approval would make it binding.

## 4. The curator's work

The full instructions are in `CURATOR_PACKAGE_v2.md`.

- **Selection.** Choose historical upgrade-related defects in eligible applications by the selection rules, before seeing any results. The development and held-out applications are excluded.
- **Per case.** Supply:
  - the buggy and fixed commits, archived and normalized;
  - the documented upgrade contract, with a citation;
  - sealed labels;
  - the hash of the independent oracle specification;
  - an exposure record: what the curator saw, and what is public.
- **Submission and freeze.** Submit each case through `research_tools/corpus_intake.py`, then freeze the registry before the first run.
- **Validation.** Run the fixed revision against each oracle and report only "holds" or "doesn't hold".
- **Leak check.** Run the label-leak check on every frozen evaluator input and model prompt.

## 5. The oracle reviewer's work

The full instructions are in `ORACLE_REVIEW_PACKAGE_v2.md`.

- **An Oracle Author** writes each case's properties from the public documentation and API only, never from the fix, its tests or its changelog entry. Each property has a statement, a class (guaranteed, historical, or excluded), a scope, competing explanations, and how atomic the observation is.
- **An Oracle Reviewer** checks that the encoding matches the specification, and records disagreements in the package's table.
- **Freezing.** Specifications are hashed into the freeze. After results exist, an oracle can't be changed: scoring refuses a changed oracle.

## 6. The triage reviewers' work

- **Who.** Two reviewers, blind to which arm (Muse or deterministic) produced a run.
- **What they classify.** Every violation observed on a corrected (fixed) revision, as one of the template's three verdicts: `false_flag` (a harness or out-of-contract artefact), `oracle_error`, or `new_finding`. Each verdict carries a written reason.
- **Disagreements.** A predeclared adjudicator resolves them, and inter-rater agreement (κ) is reported.
- **Record.** `handoff/templates/triage_record.json`; acceptance form `protocol/approval/TRIAGE_REVIEWERS_ACCEPTANCE.md`.

## 7. How historical labels are protected

| Mechanism | What it stops |
|---|---|
| **Opaque revision pairs.** The evaluator gets `R1.tar`/`R2.tar` in random order. | Learning the buggy revision from names or order. |
| **Archive normalization.** Rebuilding removes git's pax header (which holds the commit id), commit-time mtimes, owners, and member order. | Identifying the commit or the newer revision from tar metadata. |
| **A sealed reveal.** Commits, which revision is buggy, fix paths and keywords sit in a salted commitment, with the reveal kept outside the repository (mode 0600). | The runner reading labels. |
| **Hash-chained, frozen registries** per split, with disjoint development and confirmatory splits. | Editing, replacing or adding cases after the freeze, or reusing a development case. |
| **Content leak scans** over evaluator metadata and model prompts: commits, prefixes, fix paths, keywords, reveal paths. | Solution metadata leaking into inputs. |
| **A blinded results ledger.** Results are hash-chained and committed before the reveal. The reveal authorization is bound to the ledger head and the oracle hash. | Revealing before scoring, changing results, or changing oracles afterwards. |

**Limits (stated, not solved).**
- Diffing R1 against R2 shows where the fix is, and a changelog entry or added test can name the newer revision.
- A public repository's history can be searched.
- Model training data may contain the fix ("training-data exposure not excludable").
- On one machine with one user, file permissions do not separate the curator from the evaluator; separate accounts or machines do.

So blindness rests on the **process**: the runner never diffs, inputs are leak-checked, and oracles are independent. It does not rest on the archives being indistinguishable.

These mechanisms have been rehearsed on synthetic cases only (`tests/experimental/test_sealed_workflow_offline.py`).

## 8. Expected artifacts and acceptance criteria

| Role | Delivers | Accepted when |
|---|---|---|
| Curator | Submission JSON and normalized archives per case; a frozen registry; corrected-version validation results; leak-check output | `corpus_intake.py verify` and `verify-registry` pass; the registry is frozen; zero leak hits; every fixed revision reports "holds" (or the case is withdrawn **before** the freeze) |
| Oracle author and reviewer | `oracle_spec.md` per case (template in `handoff/templates/`); the reviewer's sign-off and disagreements table | Every property cites documentation, not the fix; the spec hash matches the submission; the reviewer signs |
| Triage reviewers and adjudicator | `triage_record.json` per corrected-version violation | Both classifications recorded blind; disagreements adjudicated; κ reported |
| Owner | A signed `OWNER_APPROVAL.md`; the candidate committed byte-identically; a registered timestamp | `research_tools/verify_amendment.py` reports no integrity problem and "adopted, consistent" |

## 9. How participation and conflicts of interest are recorded

Each participant fills in `handoff/templates/participation_declaration.md` **before** they receive any case material. The declaration covers:

- the role, and an acknowledgement of the candidate's sha256;
- any relationship to the applications' maintainers or to this project;
- prior exposure to each candidate defect: fix, tests, issue thread;
- any financial interest.

Role-separation rules:
- No developer of DeploySpecter holds any role.
- No one holds two roles for the same case.
- A participant with prior exposure to a case is excluded from that case, and the exclusion is recorded.

Declarations are committed alongside the signed acceptance forms in `protocol/approval/`, and they are referenced in the registration. Withdrawals are recorded, not deleted.
