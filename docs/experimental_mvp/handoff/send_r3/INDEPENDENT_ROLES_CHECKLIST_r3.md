# Independent evaluation roles for revision candidate r3: operational checklist

**Status.** For the owner, to recruit the people revision candidate r3 needs (sha256 `af9b2205…`, PROPOSED, unsigned,
unregistered). **Nobody has been named, contacted or invited.** No confirmatory case exists. Track recruitment in a
private copy of `handoff/templates/RECRUITMENT_TRACKER_TEMPLATE.md` kept outside this repository.

## Rules for every role

- **Declaration first:** `handoff/templates/participation_declaration.md` (participation and conflicts of interest),
  before any material is sent.
- **Verify before reading:** `shasum -a 256 -c docs/experimental_mvp/handoff/send_r3/SHA256SUMS_r3`, and the
  candidate's hash (`EXTERNAL_RESEARCHER_SETUP.md` §1).
- **One role per person per case.** Nobody who is a DeploySpecter or Muse developer, or who has worked on this
  project's development experiments.
- **Never eligible for confirmatory work:** the development and pilot applications (chancy, pgqueuer, procrastinate,
  saq, taskiq-postgresql, pgtq, postgres-tq, InvenTree, paperless-ngx, langgraph-checkpoint-postgres), anything
  GitLab-derived, and the frozen protocol's held-out applications.
- **Sign only the r3 forms** in `protocol/approval_r3/`, after checking the hash. The owner signs last.
- **Read for every role:** `send_r3/REVIEWER_PACKAGE_r3.md` (your section), `EXTERNAL_RESEARCHER_SETUP.md`,
  `ACCESS_SEPARATION.md`.

## The seven roles

### 1. Label curator
- **Expertise:** Python, PostgreSQL and schema migrations; reading upstream issue trackers and fix commits; git.
- **Responsibilities:** select at least 10 cases, one per upstream defect, from at least 5 applications with at
  most 3 per application (#26). Pin each commit-level fix pair; write and seal the labels; submit them through
  `research_tools/confirmatory/intake_v2.py`; run the leak check over the frozen runner inputs.
- **Independence:** works on an account or machine separate from the evaluator; never the Integrator (#29) or any
  other role.
- **Files:** `REVIEWER_PACKAGE_r3.md` (curator), `handoff/CURATOR.md`, `templates/labels.json`.
- **Form:** `approval_r3/CURATOR_ACCEPTANCE.md` (#1, #2, #6, #19, #26, #29).
- **Disqualified if:** a developer of DeploySpecter or Muse; a maintainer of a candidate application (for that
  application's cases); has seen this project's NONBLIND results in a way that steers selection.
- **Kept hidden from others:** the selected cases, fix commits, labels and reveal files, until the owner authorizes
  the reveal after scoring. They live outside this repository.

### 2. Fix-blind Integrator
- **Expertise:** Python, PostgreSQL, JSON Schema; reading a library's public API and documentation.
- **Responsibilities:** from the public API, the documentation and the **defective** revision's pinned tree only:
  each case's workload schema, Muse context, curated RA-DET workloads and any eligibility rule (#28), all before the
  freeze. Exposure records before the freeze; leak checks; with the owner, the exact accepted provider model string
  (#27); each Muse arm's admission record and journal hash before its first run (step r3-h).
- **Independence:** a person different from every other role (#29); never sees any fix, fix test, changelog entry for
  the fix, label or outcome.
- **Files:** `REVIEWER_PACKAGE_r3.md` (Integrator), `EXTERNAL_RESEARCHER_SETUP.md` §6,
  `protocol/DECISION_COMPARISON_r3.md`, `protocol/r3/ADMISSION_REVIEW_r3.md`.
- **Form:** `approval_r3/INTEGRATOR_ACCEPTANCE.md` (#24a, #25, #27, #28, #29).
- **Disqualified if:** has seen any confirmatory case's fix or labels; holds another role; is the curator.
- **Kept hidden from the Integrator:** fixes, labels, oracles' target clauses, and every result.

### 3. Oracle author
- **Expertise:** the application domain (job queues, transactional invariants), Python, writing executable
  specifications.
- **Responsibilities:** write each case's oracle from documentation and the public API only; list its clauses and
  declare the **target** clauses before sealing (#23); report violated clause ids through
  `check_clauses(final_state, raw_results)`.
- **Independence:** fix-blind; not the oracle reviewer or any other role.
- **Files:** `REVIEWER_PACKAGE_r3.md` (oracle author), `templates/oracle_spec.md`, `handoff/ORACLE_REVIEWER.md`.
- **Form:** `approval_r3/ORACLE_REVIEWER_ACCEPTANCE.md`, author row (#7, #11, #23).
- **Disqualified if:** has seen the fix, its tests or its changelog entry; holds another role.
- **Kept hidden from the author:** fixes, labels, Muse outputs and every result.

### 4. Oracle reviewer
- **Expertise:** as the oracle author, plus code review.
- **Responsibilities:** check each oracle's encoding and its target-clause declaration; confirm that it holds on the
  corrected revision under the full budget before it is approved and hashed.
- **Independence:** a different person from the author; fix-blind until the oracle is frozen.
- **Files:** `handoff/ORACLE_REVIEWER.md`, `REVIEWER_PACKAGE_r3.md` (oracle reviewer).
- **Form:** `approval_r3/ORACLE_REVIEWER_ACCEPTANCE.md`, reviewer row.
- **Disqualified if:** is the oracle author; holds another role.
- **Kept hidden from the reviewer:** fixes, labels and every arm's results.

### 5. Triage reviewer A and 6. Triage reviewer B
- **Expertise:** PostgreSQL concurrency and transaction semantics; reading run traces and server logs.
- **Responsibilities:** each classifies every corrected-revision violation **independently**, as (a) a false flag,
  (b) an oracle error, (c) a pre-existing violation of a non-target clause, or (d) a new finding, with
  `templates/triage_record.json`.
- **Independence:** blind to the arm; A and B don't confer before recording; neither holds another role.
- **Files:** `REVIEWER_PACKAGE_r3.md` (triage), `ACCESS_SEPARATION.md` §4, `templates/triage_record.json`.
- **Form:** `approval_r3/TRIAGE_REVIEWERS_ACCEPTANCE.md` (#8, #23), one row each.
- **Disqualified if:** knows which arm produced a violation; is the adjudicator; holds another role.
- **Kept hidden from them:** the arm labels (RA-DET-G, RA-DET, the Muse arms) and the other reviewer's verdicts
  until both are recorded.

### 7. Adjudicator
- **Expertise:** as the triage reviewers; experience resolving review disagreements.
- **Responsibilities:** named before the freeze; rules on each disagreement between A and B; all three verdicts are
  kept and κ is reported.
- **Independence:** not a triage reviewer or any other role; blind to the arm.
- **Files:** `ACCESS_SEPARATION.md` §4, `REVIEWER_PACKAGE_r3.md` (triage).
- **Form:** the adjudicator's row in `approval_r3/TRIAGE_REVIEWERS_ACCEPTANCE.md`.
- **Disqualified if:** was a triage reviewer; knows the arms.
- **Kept hidden from the adjudicator:** the arm labels.

## Hand-off order

1. Declarations and conflict checks for all seven.
2. Send the r3 package (with checksums) to each; each verifies the hashes.
3. Reviewers confirm or reject C1–C4 and the three admission items. If none is a substantive problem, r3 stays
   the candidate.
4. Each signs their own r3 form; the owner signs last, commits the text beside the frozen protocol and registers its
   hash with a trusted timestamp.
5. Only then: curator selection and sealing, integration, oracles, freeze, runs, triage, reveal.
