# Amendment 003: recommended rulings, responsibilities and evidence requirements (review package v2, 2026-11-05)

**Every ruling here is PROPOSED.** No person has been named, contacted, or has approved anything. No signature, attestation, seal or registration exists. Amendment 003 is **not adopted**, and confirmatory real-application research stays **blocked**.

This file supersedes nothing: `DECISIONS_003.md` (v1) is kept unchanged as a record. The prior frozen A–G and R registrations, protocol 1.2, and amendments 001 and 002 are untouched.

Status ladder, applied separately to each decision:

| Status | What it requires | Who can move it there |
|---|---|---|
| PROPOSED | this text | the tool developers (done) |
| ACCEPTED | a named person in the stated role, and the owner, approve the ruling in writing (a signed commit or a signed letter, archived with the protocol) | that person and the owner, never the tool developers |
| IMPLEMENTED | the artefacts the ruling requires exist (listed per decision) | as listed |
| VERIFIED | the evidence listed per decision shows the process worked on a real case | an auditor other than the tool developers |

## Real availability, as of this package

| Role | Named person | Status |
|---|---|---|
| Label curator (#6) | none | **unfilled** |
| Oracle author or reviewer (#7) | none | **unfilled** |
| Triage reviewers (two) and adjudicator (#8) | none | **unfilled** |
| Registration approver (#9) | the owner (only they can approve public registration) | not asked |
| Gate 2A/A2 decision owner (#14) | the owner | not asked |

The only people involved so far are the owner and the tool developer, who is the author of this package. Splitting one developer's work into roles is **not** independence, and it is not presented as such anywhere.

---

## #6: Label curation and sealing

**Recommended ruling.** A Label Curator selects each RA case's historical defect from candidates already admitted by the frozen selection rules, or by a reviewer ruling made before any migration file is read. The curator then:
- records the buggy commit, the fix commit, the fix paths, a one-paragraph defect summary, the exposure-sensitive keywords and the curator's own exposure;
- seals the labels before any evaluation arm runs.

Fixes and labels never enter the Muse pipeline, the deterministic generators, the oracle author's materials or the evaluation runner's inputs.

**Qualification and independence:**
- Not a developer of DeploySpecter, its Muse integration or its deterministic generators.
- Not a reviewer under #8 for the same case.
- No access to RA arm outputs before the reveal.
- PostgreSQL and application-upgrade competence, declared in a one-page CV note.
- A conflict-of-interest declaration.

**Procedure:**
1. The curator works from `CORPUS_MANIFEST.json` and the selection tool's report only, in a checkout without the runner's working directories.
2. Labels follow `handoff/templates/labels.json`.
3. `research_tools/seal_labels.py seal` produces the commitment. The reveal file is held by the curator outside the repository; the tool refuses to write it inside the working tree.
4. The commitment's sha256 goes into the adoption commit (#9).
5. Before each arm, the curator runs `seal_labels.py leak-check` over the runner's frozen inputs (prompts, registries, workload sources). Any hit blocks the run.

**Evidence:**
- **ACCEPTED:** the curator's signed acceptance of this procedure, and the owner's.
- **IMPLEMENTED:** a committed `SEAL.json` per case, and the curator's statement that the reveal is held outside the repository.
- **VERIFIED:**
  - a leak-check transcript (hits = 0) dated before each arm's first run;
  - at scoring, `seal_labels.py verify` matches the commitment;
  - an audit of the runner's input bundle against the reveal.

**Until ACCEPTED:** no case can be BLIND_ELIGIBLE, and RA-Q3 can't run confirmatory.

## #7: Oracle authorship

**Recommended ruling:**
- **Blind cases.** An Oracle Author writes each case's oracle before any arm runs. They work from the application's public documentation and its corrected release, but not from the fix diff, the fix commit's identity, the defect report, the curator's labels, or any Muse proposal or output. Each oracle is hashed into the adoption commit.
- **Development (NONBLIND) cases.** The oracle may be derived from the maintainers' own regression test or documentation (as in `historical/chancy_2895c4a`), provided the derivation cites the exact test and blob. It is reviewed by the Oracle Reviewer, and it is labelled "maintainer-sourced, developer-encoded".

**An open tension for the reviewer.** "The corrected release" can reveal the fix. A maintainer regression test always does, because it is written with the fix. So:
- for blind cases, the author may read the corrected release's documentation and public API, but **not** its tests or changelog entries about the defect;
- that boundary is the reviewer's to confirm or tighten.

**Qualification and independence:**
- Not a tool developer.
- Not the curator for the same case.
- No access to arm outputs.
- Signs an attestation listing exactly what was read (`handoff/templates/oracle_spec.md`, "Sources read").

**Evidence:**
- **ACCEPTED:** the author's and the owner's written acceptance.
- **IMPLEMENTED:** a signed `oracle_spec.md` and its encoded checker per case, both hashed into the freeze.
- **VERIFIED:**
  - an independent re-implementation, or a review, of the encoding by the Oracle Reviewer;
  - the oracle holds on the corrected version under the full budget before any arm is scored (otherwise it's an oracle error, and the case drops for every arm).

**Until ACCEPTED:** every existing oracle is developer-authored. That covers Q1–Q3, C1–C3, K1–K2, R1/R2/R5, L1/L2, M1–M4, P1–P8 and H1–H2. None is independent.

## #8: Triage of corrected-version violations

**Recommended ruling.** Two Triage Reviewers each classify every corrected-version violation without seeing the other's verdict, the arm that produced it, or the curator's labels. The classes are:
- false flag (a harness or environment cause);
- oracle error;
- new finding (an undocumented application defect on the corrected version).

A predeclared Adjudicator resolves disagreements. Both verdicts, the adjudication, and Cohen's κ over all triaged violations are reported.

**Qualification and independence:** not tool developers, not the curator, and not the oracle author for that case.

**Procedure:**
1. Each violation is exported as a self-contained bundle: the corroborated trace, the final state, the oracle text and the run's refusal reasons.
2. The arm identity is stripped and replaced by a random token.
3. The verdicts are recorded in `handoff/templates/triage_record.json`, as in `handoff/ACCESS_SEPARATION.md` §4.

**Evidence:**
- **ACCEPTED:** written acceptance by both reviewers, the adjudicator and the owner.
- **IMPLEMENTED:** the bundle exporter and token map exist, the token map is held by the adjudicator, and the triage templates are frozen.
- **VERIFIED:** a dry run on a development case with a known corrected-version violation, such as `release_seats` in `RELEASE_PATH.md`, shows that two independent verdicts were recorded before the token map was opened.

## #9: Timestamped prospective freeze

**Recommended ruling.** Before the first evaluation run, the adoption commit's sha256 is registered through a third-party timestamp. The registration includes:
- the full amendment text;
- the budgets B, T, R, P and C;
- the model identifier, and the prompt and schema hashes;
- the oracle hashes and the sealed-label commitments.

**Preferred:** an OSF Registries preregistration (an embargo is allowed). **Additionally:** an RFC 3161 timestamp token, or an OpenTimestamps proof over the same sha256, committed beside the amendment. Every report states the registration date against the first run's date.

**Who:** only the owner can approve, because registration is public. Nothing has been registered, and approval has not been sought.

**Evidence:**
- **ACCEPTED:** the owner's written approval of the venue.
- **IMPLEMENTED:** the registration's identifier and the timestamp token, committed.
- **VERIFIED:** an auditor checks that the token verifies over the adoption commit's sha256, and that its date precedes the first run.

## #14: Relation to the Gate 2A/A2 path

**Recommended ruling.** The real-application evaluation (RA):
- runs on the experimental platform (`src/deployspecter/experimental`, `research_tools/isolated`);
- makes **no claim** to satisfy the frozen Gate 2A supervised path, or the Gate A2 verification boundary.

Specifically:
- The Gate A2 demonstration stays blocked, and its decision is unaffected.
- No experimental code enters a frozen gate without a separate numbered amendment.
- Every RA report states that it is not Gate A2 evidence.
- RA and the frozen evaluation's results are never pooled.

**Who:** the owner.

**Evidence:**
- **ACCEPTED:** the owner's written approval.
- **IMPLEMENTED:** already true by construction: the frozen-path check in every regression run, and the labels in every evidence bundle.
- **VERIFIED:** `git diff` over the frozen paths is empty between adoption and the final report.

---

## The other 13 issues (recommendations, PROPOSED; each needs the stated decider)

| # | Recommendation | Decider |
|---|---|---|
| 1 | Choose A1a, A1b or A1c (`PROPOSED_SELECTION_RULE_AMENDMENTS.md`) **before** any weblate migration file is read. The curator applies it | owner + curator |
| 2 | For Alembic, A2a (the revision graph's head at the pinned release) applied to the graph the application's own `alembic.ini` names | owner + curator |
| 3 | A3a (an extra-declared driver counts), applied uniformly: it keeps pgqueuer's past admission consistent. A3b would retroactively remove it, and must be stated if chosen | owner |
| 4 | RA-Q3 preregistered as descriptive unless at least 10 eligible blind cases exist (draft v2 §13) | owner |
| 5 | Primary: historical fix pairs at commit level (feasible: `historical/chancy_2895c4a` ran its fix commit and parent). Mutants: secondary, scored separately per frozen §4.2 operator, and never called defects | owner |
| 10 | A per-application adapter counts as RA-Q4 onboarding work. A generic engine capability counts as tool development | owner |
| 11 | Polling mode is faithful for database-statement semantics. A case whose defect needs the LISTEN path is excluded and reported | owner + oracle reviewer |
| 12 | The Django seam remains development-only until a development Django application of weblate's scale has run through it | owner |
| 13 | Restricted runc is acceptable **only** for pinned, publicly released, statically reviewed code, and is disclosed in every result. Anything else waits for gVisor or a microVM | owner |
| 15 | Fix B, T, R, P and C on the development cases (Chancy simultaneous and historical, pgqueuer). Candidate values are given in `PROSPECTIVE_MUSE_PLAN.md` | owner, before the freeze |
| 16 | Zero model budget for the deterministic arms (the current draft) | owner |
| 17 | Hypothesis scoring stays exploratory | owner |
| 18 | `RA-SMOKE`, the simultaneous pilot and `historical/chancy_2895c4a` are cited only as engineering and development evidence | owner |

## New issues raised by this work (not in the 18; need rulings before adoption)

**19. Documented compatibility contract.** The simultaneous Chancy pilot showed something an RA case must account for. A mixed-version "violation" can lie entirely outside what the application supports: Chancy's changelog says "Stop your workers" before migrating.

Recommendation:
- a case's mixed-version operations count only if the application documents them as supported;
- otherwise the case is single-version, as `chancy_2895c4a` is, or its mixed-version results are exploratory.

The curator records the contract, with a citation, in the case's labels.

**20. Training-data exposure.** The tool developer (here, an AI assistant) and Muse are language models trained on public repositories. Neither can attest that it hasn't seen a public fix.

Recommendation:
- exposure ledgers record "training-data exposure not excludable" for every public case;
- blindness rests on the *process* (curator, sealed labels, runner inputs leak-checked);
- results are interpreted with that limitation stated.

The reviewer may decide that cases fixed after the model's training cutoff are the stronger blind cases. That needs the cutoff date to be known.
