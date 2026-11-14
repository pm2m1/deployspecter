# Reviewer package for revision candidate r2 (PROPOSED; not sent; no reviewer named)

**Read first.**
- The candidate under review is `docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r2.md`, sha256
  `476d5c57013b12abfbe78c70db67a88603e095382dd409068561418869374e46`. Verify every file in this package with
  `shasum -a 256 -c docs/experimental_mvp/handoff/send_r2/SHA256SUMS_r2` before reading on.
- The original candidate (`ad5ea331…`) and r1 (`20403460…`) are separate, unchanged candidates. The owner takes
  forward at most one.
- Changes: `protocol/DECISION_COMPARISON_r2.md`. Time budget: `protocol/r2/TIME_BUDGET_r2.md`.
- r2 states a recommended study design as proposed text. It is not an approval by anyone.

**What you will not receive.** Any confirmatory case, its fix, labels or results. None exists yet. You also won't
receive the developers' NONBLIND results as an input to your work. They are public (`docs/experimental_mvp/`), but
your role doesn't need them, and the curator's and Integrator's work should not use them.

**Every role** first declares conflicts of interest with `handoff/templates/participation_declaration.md`. No person
holds two roles for the same case.

## Curator (`approval_r2/CURATOR_ACCEPTANCE.md`: #1, #2, #6, #19, #26, #29)

1. Select candidate cases under the frozen rules, **excluding** every development-exposed application (chancy,
   pgqueuer, procrastinate, saq, taskiq-postgresql, pgtq, postgres-tq, InvenTree, paperless-ngx,
   langgraph-checkpoint-postgres), anything GitLab-derived, and the frozen protocol's held-out applications.
2. At least 10 cases, one per upstream defect, at least 5 applications, at most 3 per application (#26).
3. Pin each fix pair. Write the labels (`handoff/templates/labels.json`), seal them with
   `research_tools/seal_labels.py` and submit through `research_tools/confirmatory/intake_v2.py`, on an account or
   machine separate from the evaluator.
4. Under r2 the curator is **never** the Integrator (#29).

## Integrator (`approval_r2/INTEGRATOR_ACCEPTANCE.md`: #24a, #25, #27, #28, #29)

1. Attest that you hold no other role and have no exposure to any confirmatory fix, its tests, its changelog entry,
   its labels or any outcome.
2. For each case, from the public API, the documentation and the **defective** revision's pinned tree only, write or
   approve: the workload schema (strict JSON schema), the Muse context (operation registry and schema DDL, with no
   fix, test or oracle hint), and the curated RA-DET workloads.
3. **Eligibility rules (#28), optional:** a rule that rejects a workload shape is written before the freeze, without
   outcome knowledge, hashed into the freeze, and applied by the one validator every arm uses
   (`prep_r2.UniformValidator`). No rule is added or changed after the freeze.
4. Record your reads in the case's exposure record before the freeze (`prep_r2.exposure_record`), and run
   `seal_labels.py leak-check` over every input.
5. With the owner, record the exact provider-reported model string that is accepted (#27).
6. The RA-DET-G generator, the K × S allocation, the request plan and the time rule are code
   (`research_tools/confirmatory_prep/prep.py`, `prep_r2.py`). You supply only the schema, context, curated
   workloads and any eligibility rule.

## Oracle author and oracle reviewer (`approval_r2/ORACLE_REVIEWER_ACCEPTANCE.md`: #7, #11, #23; two people)

1. **Author:** write each case's oracle from documentation and the public API only, never from the fix. List its
   clauses, and declare which **target** clauses encode the defect (#23). The oracle reports violated clause ids
   through `check_clauses(final_state, raw_results)`.
2. **Reviewer:** check the encoding, confirm the target-clause declaration, and validate that the oracle holds on the
   corrected revision under the full budget before it is approved and hashed.

## Triage reviewers (two, blind to arm) and adjudicator (`approval_r2/TRIAGE_REVIEWERS_ACCEPTANCE.md`: #8, #23)

Classify every corrected-revision violation as (a) a false flag, (b) an oracle error (the case drops for every arm),
(c) a pre-existing violation of a non-target clause (reported; the arm's case stays void), or (d) a new finding. The
adjudicator resolves disagreements, and κ is reported.

## Onboarding study roles (#30; not covered by these forms)

The study's data owner, an independent coordinator (who selects the three applications by a written rule), an
ethics or owner reviewer, two blind scorers and an adjudicating scorer, and 18–24 participants. Protocol:
`onboarding/participant_study/PROTOCOL_r2_3APP.md`; sample size: `SAMPLE_SIZE_r2.md`.

## Owner (`approval_r2/OWNER_APPROVAL.md`: #1–#30, #24a, #A4, #A5)

Review `DECISION_COMPARISON_r2.md` and `r2/TIME_BUDGET_r2.md`. Choose a candidate (the original, r1 or r2), record the
accepted provider model string and the #30 design (`min18` or `pref24`), sign last, and register the chosen
candidate's hash before any run (`approval_r2/ADOPTION_CHECKLIST.md`).
