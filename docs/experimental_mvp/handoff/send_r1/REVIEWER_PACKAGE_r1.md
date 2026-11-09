# Reviewer package for revision candidate r1 (PROPOSED; not sent; no reviewer named)

**Read first.**
- The candidate under review is `docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r1.md`, sha256
  `204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727`. Verify it with `shasum -a 256` before
  reading on.
- The original candidate (`ad5ea331…`) is a separate, unchanged candidate. The owner takes forward at most one.
- Changes: `protocol/DECISION_COMPARISON_r1.md`. Reasons: `protocol/AMENDMENT_003_FINAL_RECOMMENDATIONS_r1.md`.

**What you will not receive.** Any confirmatory case, its fix, labels or results. None exists yet. You also won't
receive the developers' NONBLIND results as an input to your work. They are public (`docs/experimental_mvp/`), but
your role does not need them, and the curator's and integrator's work should not use them.

## Curator (`approval_r1/CURATOR_ACCEPTANCE.md`: #1, #2, #6, #19, #26, #29)

1. Declare conflicts of interest (`handoff/templates/`).
2. Select candidate cases under the frozen rules, **excluding** every development-exposed application:
   - chancy, pgqueuer, procrastinate, saq, taskiq-postgresql, pgtq, postgres-tq;
   - InvenTree, paperless-ngx, langgraph-checkpoint-postgres;
   - anything GitLab-derived;
   - the frozen protocol's held-out applications.

   Take one case per upstream defect, at most 3 per application and at least 5 applications (#26).
3. Pin each fix pair. Write the labels (`handoff/templates/labels.json`), seal them with `research_tools/seal_labels.py`
   and submit through `research_tools/confirmatory/intake_v2.py`, on an account or machine separate from the
   evaluator.
4. If acting as the Integrator (#29), also follow the Integrator section and sign `INTEGRATOR_ACCEPTANCE.md`.

## Integrator (`approval_r1/INTEGRATOR_ACCEPTANCE.md`: #25, #27, #29)

1. Attest no exposure to any confirmatory fix, its tests, its changelog entry or its labels.
2. For each case, from the public API, the documentation and the **defective** revision's pinned tree only, write or
   approve:
   - the workload schema (strict JSON schema);
   - the Muse context (operation registry and schema DDL, with no fix, test or oracle hint);
   - the curated RA-DET workloads.
3. Run `seal_labels.py leak-check` over every input, and record your reads in the case's exposure ledger.
4. The RA-DET-G generator and the K × S allocation are code (`research_tools/confirmatory_prep/`). You supply only
   the schema.

## Oracle author and oracle reviewer (`approval_r1/ORACLE_REVIEWER_ACCEPTANCE.md`: #7, #11, #23; two different people)

1. **Author:** write each case's oracle from documentation and the public API only, never from the fix. List its
   clauses, and declare which **target** clauses encode the defect (#23). The oracle reports violated clause ids
   through `check_clauses(final_state, raw_results)`.
2. **Reviewer:** check the encoding, confirm the target-clause declaration, and validate that the oracle holds on the
   corrected revision under the full budget before it is approved and hashed.

## Triage reviewers (two, blind to arm) and adjudicator (`approval_r1/TRIAGE_REVIEWERS_ACCEPTANCE.md`: #8, #23)

Classify every corrected-revision violation into one category:
- (a) a false flag;
- (b) an oracle error (the case drops for every arm);
- (c) a pre-existing violation of a non-target clause (reported; the arm's case stays void);
- (d) a new finding.

The adjudicator resolves disagreements, and κ is reported.

## Owner (`approval_r1/OWNER_APPROVAL.md`: #1–#30)

Decide the choices listed in `DECISION_COMPARISON_r1.md`, including the #30 option. Choose a candidate, sign last,
and register its hash before any run.
