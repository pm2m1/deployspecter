# Owner adoption record for Amendment 003, revision candidate r3 (DRAFT; NOT SIGNED; NOT ADOPTED)

**Candidate:** `docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`
**sha256:** `af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`. Check it before signing:
`shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`.

This record doesn't change r3 or any earlier candidate. It records which real people fill r3's roles, and where that
staffing **can't meet r3 as written**. Each such gap is listed with the closest honest substitute and exactly what
independence it loses. **Signing this record with any substitute in place does not make the evaluation
confirmatory under r3 §15.** The owner chose option (b) on 2026-11-15: sign with the declared gaps, and label the
results **"preregistered, partially blind"**.

Related owner records: `OWNER_DECISIONS_r3_admission.md` (the three open admission items and the accepted model
string) and `docs/experimental_mvp/osf/` (the registration package and `FREEZE.json`).

## Staffing (four people)

| Person | Who | Role taken | Sees the fixes? |
|---|---|---|---|
| Owner | Parth Mishra (repository owner and tool developer) | owner; integrator (gap G3); runner and evaluator; holds the arm key for triage | **never**, until the reveal |
| Labmate 1 | name: ______________________ | Label Curator | yes: chooses and seals the cases |
| Labmate 2 | name: ______________________ | Oracle Author ("oracle writer") | **never**: documentation and public API only |
| Faculty member | name: ______________________ | protocol reader | no case material at all |

## r3 roles mapped onto these people

| r3 role (rule) | r3 requires | Filled by | Meets r3? |
|---|---|---|---|
| Label Curator (#6, §15) | independent of the tool developers; seals labels; account and machine separate from the evaluator | Labmate 1 | **yes**, if they never worked on DeploySpecter or Muse and use their own machine |
| Oracle Author (#7, #23) | writes oracles **without seeing the fix**, its tests or changelog; declares target clauses before sealing | Labmate 2 | **yes**, with one residual channel (G1, below) |
| Oracle Reviewer (#7) | a different person from the author; fix-blind until the oracle is frozen | nobody | **no** (gap G2) |
| Fix-blind Integrator (#29) | different from **every** other role **and from the developers** | the owner, a developer | **no** (gap G3) |
| Triage Reviewers A and B (#8) | two people, blind to the arm, recording independently | Labmates 1 and 2, arm-blind | **partly** (gap G4) |
| Adjudicator (#8) | a third person, blind to the arm | nobody | **no** (gap G5) |
| Owner (#9, §15) | approves last, commits the text beside the frozen protocol, registers its hash with a trusted timestamp | Owner | yes |
| Protocol reader | not an r3 role | Faculty member | not applicable: adds review, not a role r3 counts |

## G1: fixed by splitting the roles, with one residual channel

**How it works:**
- Labmate 1 chooses a case and writes a short **contract-area statement** for it: which documented behaviour of
  which public API the case concerns (for example "the documented semantics of cancelling a job through
  `Queue.cancel`"). It's written from the documentation, in neutral words, never from the fix, its tests, its
  changelog entry, the issue or PR text.
- Labmate 2 receives only the application name, the pinned **defective** revision's tree (unlabelled as such), its
  public documentation, and the contract-area statement. They write the oracle from those (`ORACLE_WRITER_QUICKSTART.md`),
  declare its target clauses (#23) as the clauses that encode the named contract area, hash it and send the hash to
  labmate 1.
- Only after receiving that hash does labmate 1 pin the fix pair and seal the case (`corpus_intake.py`).

**What remains lost:** the contract-area statement is written by someone who has read the fix, so it can carry
information about the defect. The oracle writer is fix-blind, but their choice of target clauses depends on that
statement. To limit this, labmate 1 records each statement in the exposure ledger before sending it, so the
reveal can show exactly what was passed.

## Gaps that remain (G2–G5): the substitute, and what each loses

**G2. No oracle reviewer.**
- **Substitute:**
  - Before sealing, **labmate 1** runs each oracle on the **corrected** revision under the serial workloads on their
    own machine, a check that needs no second fix-blind person. (The owner can't do this: the owner doesn't know
    which revision is corrected.)
  - The faculty member may read oracle *text* for encoding errors, if they agree to see oracle text and nothing
    else. That is recorded if it happens.
- **What is lost:** an independent, fix-blind check of each oracle's encoding and of its target-clause declaration.
  Errors that make an oracle too strict show up as corrected-version violations, which void the case for that arm.
  Errors that make it too lax aren't caught by anyone.

**G3. No independent integrator.**
- **Substitute:** the owner integrates each case:
  - from the public API, the documentation and the two pinned revisions, without knowing which is defective and
    without diffing them (a diff would show the fix; this is self-attested);
  - without ever seeing the fix, the labels, the oracle text or its target clauses before the freeze. Labmate 1 holds
    the reveal files, and the owner's exposure ledger is committed before the freeze.
- **What is lost:** #29's separation from the developers.
  - **Tailoring:** the tool developer can, consciously or not, shape workload schemas and eligibility rules toward
    what the engine handles well.
  - **Muse context:** the person who wrote Muse's context also wrote RA-DET-G's schema.
  - **Leak checks** (`seal_labels.py`) still catch copied label text, but not tailoring.

**G4. Triage reviewers aren't independent of the case.**
- **Substitute:** the owner relabels arms with a random key, kept sealed by the owner, who doesn't triage. Labmates 1
  and 2 classify each corrected-version violation independently and blind to the arm (`handoff/templates/triage_record.json`).
- **What is lost:**
  - **Labmate 2 wrote the oracle,** so category (b) "oracle error" is judged partly by the oracle's author.
  - **Labmate 1 knows the fix,** so categories (a) and (d) are judged partly by someone who isn't fix-blind.

**G5. No adjudicator.**
- **Substitute:** no adjudication. A disagreement is resolved **conservatively**, and both verdicts are reported:
  - **Voiding stays:** r3's primary rule already voids a case for an arm on any corrected-version violation, so
    detection needs no triage.
  - **Oracle error needs agreement:** a case is dropped for every arm as an oracle error only if both reviewers say
    (b).
- **What is lost:** a third vote, and with it some cases an adjudicator would have dropped as oracle errors.

## r3 rules that still can't be met

1. **#7:** an oracle reviewer distinct from the author (G2).
2. **#29:** an integrator distinct from every other role and from the developers (G3).
3. **#8:** two triage reviewers independent of the case, and a predeclared adjudicator (G4, G5).
4. **§15's closing rule:** "until every marker in this section is replaced by an accepted ruling, no RA run is
   confirmatory". With G2–G5 the governance markers can't be accepted as written.
5. **approval_r3/:** `CURATOR_ACCEPTANCE.md` (labmate 1) can be signed as written, and so can the "Oracle author"
   row of `ORACLE_REVIEWER_ACCEPTANCE.md` (labmate 2). Its "Oracle reviewer" row, `INTEGRATOR_ACCEPTANCE.md` and
   `TRIAGE_REVIEWERS_ACCEPTANCE.md` can't be signed by people who meet them. They stay unsigned, and this record is
   the substitute.

## What signing would and would not mean

- **Would:** the owner commits to r3's design (arms, K = 6 × S = 2 budgets, admission rule, analysis, stopping
  rules), to the substitutes above, and to `OWNER_DECISIONS_r3_admission.md`, **before** any case is selected. The
  commitment is registered on OSF with `osf/FREEZE.json`, this record and r3's hash.
- **Would not:** an evaluation that r3 calls confirmatory. Results must be labelled **"preregistered, partially
  blind (r3 with declared staffing deviations G2–G5 and the G1 residual channel)"**, never "confirmatory under r3".

## Signatures (blank)

I approve revision candidate r3, sha256 `af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`, with
the staffing and deviations recorded above, and I understand that the results will not be confirmatory under r3 §15.

Owner: ______________________________   Date (UTC): ____________________

Labmate 1, Label Curator, acknowledging the contract-area channel (G1), G2 and G4: ______________________________   Date: __________

Labmate 2, Oracle Author, attesting they will not see any fix, its tests, changelog, issue or PR, and acknowledging G4: ______________________________   Date: __________

Faculty member, protocol reader, acknowledging what they will and won't see: ______________________________   Date: __________

OSF registration DOI or URL: ______________________   Timestamp token (RFC 3161 or OpenTimestamps): ____________
