# Owner adoption record for Amendment 003, revision candidate r3 (DRAFT; NOT SIGNED; NOT ADOPTED)

**Candidate:** `docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`
**sha256:** `af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`. Check it before signing:
`shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`.

This record doesn't change r3 or any earlier candidate. It records which real people would fill r3's roles with the
staffing available, and where that staffing **can't meet r3 as written**. Each such gap is listed with the closest
honest substitute and exactly what independence it loses. **Signing this record with any substitute in place does
not make the evaluation confirmatory under r3 §15.** The consequences are spelled out in "What signing would and
would not mean".

## Staffing

| Person | Who | Role taken |
|---|---|---|
| Owner | Parth Mishra (repository owner and tool developer) | owner; runner/evaluator |
| Labmate | name: ______________________ | label curator **and** oracle author (as requested) |
| Faculty reader | name: ______________________ | reads the protocol and this record; no case material |

## r3 roles mapped onto these people

| r3 role (rule) | r3 requires | Filled by | Meets r3? |
|---|---|---|---|
| Label Curator (#6, §15) | independent of the tool developers; seals labels; account or machine separate from the evaluator | Labmate | **yes**, if the labmate never worked on DeploySpecter or Muse and uses their own machine |
| Oracle Author (#7, §15) | writes oracles **without seeing the fix**, its tests or changelog; declares target clauses (#23) | Labmate, the same person as the curator | **no** (gap G1) |
| Oracle Reviewer (#7) | a different person from the author; fix-blind until the oracle is frozen | nobody | **no** (gap G2) |
| Fix-blind Integrator (#29) | writes the workload schema, Muse context, curated RA-DET workloads and eligibility rules; different from **every** other role **and from the developers** | nobody independent | **no** (gap G3) |
| Triage Reviewers A and B (#8) | two people, blind to the arm, recording independently | nobody | **no** (gap G4) |
| Adjudicator (#8) | a third person, blind to the arm | nobody | **no** (gap G5) |
| Owner (#9, §15) | approves last, commits the text beside the frozen protocol, registers its hash with a trusted timestamp | Owner | yes |
| Protocol reader | not an r3 role | Faculty reader | not applicable: adds review, not a role r3 counts |

## Gaps: the closest honest substitute, and what each loses

**G1. Curator and oracle author are one person.**
- **Substitute:** the labmate writes each oracle *before* opening the fix commit or its tests. They work only from the
  application's documentation, its public API and the defective revision. Only after the oracle is hashed and
  committed do they pin the fix pair and seal the labels. The order is logged with timestamps in the case's
  exposure ledger.
- **What is lost:**
  - **Target-clause declaration (#23) is no longer fix-blind:** to choose a candidate defect at all, the curator
    reads at least its issue title or report. So the oracle can be shaped toward that defect.
  - **The two steps can't be checked against each other:** the ordering is self-attested, with no second person to
    detect a leak between curation and oracle writing.

**G2. No oracle reviewer.**
- **Substitute:**
  - The owner runs each oracle on the **corrected** revision under the full budget before the freeze, a check that
    needs no fix knowledge.
  - The faculty reader may review oracles for encoding errors, but only if they agree to see oracle text and nothing
    else.
- **What is lost:** an independent check of the encoding and of the target-clause declaration. If the faculty reader
  reviews oracles, they stop being a pure protocol reader. That is acceptable, but must be recorded.

**G3. No independent integrator.**
- **Substitute:** the owner integrates each case:
  - from the public API, the documentation and the defective revision only;
  - without ever seeing the fix, the labels or the oracle's target clauses before the freeze. The labmate holds the
    reveal files, and the owner's exposure ledger is committed before the freeze.
- **What is lost:** #29's separation from the developers.
  - **Tailoring:** the tool developer can, consciously or not, shape workload schemas and eligibility rules toward
    what the engine handles well.
  - **Muse context:** the person who wrote Muse's context also wrote RA-DET-G's schema.
  - **Leak checks** (`seal_labels.py`) still catch copied label text, but not tailoring.

**G4. No two arm-blind triage reviewers.**
- **Substitute:**
  - The owner relabels arms with a random key, held by the labmate, before triage.
  - The labmate and the faculty reader classify each corrected-version violation independently, blind to the arm.
- **What is lost:**
  - **The labmate isn't fix-blind:** they know the fix and the oracle, so triage categories (b) oracle error and
    (d) new finding are judged by the oracle's own author.
  - **The faculty reader** must see run traces, which goes beyond reading the protocol.

**G5. No adjudicator.**
- **Substitute:** no adjudication. A disagreement is resolved **conservatively** and both verdicts are reported:
  - **Voiding stays:** r3's primary rule already voids a case for an arm on any corrected-version violation, so
    detection needs no triage.
  - **Oracle error needs agreement:** a case is dropped for every arm as an oracle error only if both reviewers say
    (b).
- **What is lost:** a third vote, and with it some cases that an adjudicator would have dropped as oracle errors.

## r3 rules that can't be met with this staffing (also in `DECISIONS_NEEDED.md`)

1. **#7:** an oracle author who hasn't seen the fix (G1).
2. **#7:** an oracle reviewer distinct from the author (G2).
3. **#29:** an integrator distinct from every other role and from the developers (G3).
4. **#8:** two arm-blind triage reviewers and a predeclared adjudicator (G4, G5).
5. **§15's closing rule:** "until every marker in this section is replaced by an accepted ruling, no RA run is
   confirmatory". With G1–G5 the governance markers can't be accepted as written.
6. **approval_r3/:** the forms expect signatures from the curator, integrator, oracle reviewer and triage
   reviewers. With this staffing, the integrator and triage forms can't be signed by people who meet them.

## What signing would and would not mean

- **Would:** the owner commits to r3's design (arms, K = 6 × S = 2 budgets, admission rule, analysis, stopping
  rules) and to the substitutes above, **before** any case is selected. The commitment is registered on OSF with
  this record and r3's hash.
- **Would not:** an evaluation that r3 calls confirmatory. Results under G1–G5 must be labelled **"preregistered,
  partially blind (r3 with declared staffing deviations G1–G5)"**, never "confirmatory under r3".
- **Alternatives,** on the owner's decision list:
  - recruit enough people to meet r3 as written (at least 7, one per role);
  - prepare a new candidate (r4) that states these substitutes as its rules. This is a new text and hash, and needs
    every approval again.

## Signature (blank)

I approve revision candidate r3, sha256 `af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`, with
the staffing and deviations G1–G5 recorded above, and I understand that the results will not be confirmatory under
r3 §15.

Owner: ______________________________   Date (UTC): ____________________

Labmate (curator and oracle author), acknowledging G1 and G4: ______________________________   Date: __________

Faculty reader, acknowledging what they will and won't see: ______________________________   Date: __________

OSF registration DOI or URL: ______________________   Timestamp token (RFC 3161 or OpenTimestamps): ____________
