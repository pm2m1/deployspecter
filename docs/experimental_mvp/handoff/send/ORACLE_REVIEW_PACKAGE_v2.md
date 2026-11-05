# Package B: independent oracle author and reviewer (v2, ready to send; NOT sent; no recipient named)

**Your role:** write the correctness properties that DeploySpecter checks after running an application's upgrade, from documentation alone, so that a detection can't have been shaped by knowing the fix. A second person reviews each encoding. Estimated effort: 2–4 hours per case, plus 2–3 hours for the process check in §5. You may stop at any time.

**Before you start:** sign `protocol/approval/ORACLE_REVIEWER_ACCEPTANCE.md` after checking the candidate's sha256. You may not be the curator for the same cases.

## 1. What you receive and what you must not read

- **You receive:** the application's name and its pinned release (version, sdist sha256 or archive sha256), the template `handoff/templates/oracle_spec.md`, and the upgrade contract's citation.
- **You may read:** user documentation, API references, upgrade and migration guides, and the public API.
- **You must not read:** fix diffs, commit messages about fixes, issue discussions of defects, the project's tests, changelog "Fixes" entries, curator material, and any DeploySpecter or Muse output.

## 2. Deliverable format: `oracle_spec.md`, one per case

For each property:
- **id and statement:** exact, over the final database state and the application's public results;
- **class:** G (documented guarantee; quote and cite it), H (an assumption you state) or X (exploratory);
- **scope:** for example, "after a quiescent completion";
- **competing explanations:** legitimate behaviour that could look like a violation;
- **observation:** which values are read, and whether any read is non-atomic with the action it checks.

The last item exists because of this project's own erratum E2: a "status right after a retry" check was racy.

End the spec with "Sources read" and your signature. The owner records its sha256, which the curator puts in the submission.

## 3. Corrected-version validation (before any evaluation arm is scored)

1. The developers encode your spec as a checker. **The reviewer** checks the encoding against your text, line by line, and signs "encoding matches" or lists the differences.
2. The curator runs the case's fixed revision only, with the deterministic baseline under the full budget, and reports "holds" or "doesn't hold". No one else learns which revision is fixed.
3. **Doesn't hold** means an oracle error: the case is dropped for every arm, and recorded. It is never re-encoded after results are seen.

## 4. Disagreements

| Between | About | Resolution | Recorded in |
|---|---|---|---|
| author and reviewer | whether the encoding matches the spec | the developers re-encode until the reviewer signs; the author's text governs intent | the review record |
| author and reviewer | whether a property is justified by the documentation | the owner decides **before the freeze**; otherwise the property is dropped | the review record |
| the two triage reviewers (ruling #8) | how to classify a corrected-version violation | the adjudicator decides; both verdicts and κ are reported | `templates/triage_record.json` |

## 5. Process check on development cases (not blind; tests this procedure, not the cases)

Review the developer-encoded oracles:
- `historical/chancy_2895c4a/CASE.md` (H0–H2);
- `rolling_upgrade/procrastinate_3.3_3.4/CASE.md` (R1–R7), with `ERRATA.md` (E1, E2);
- `simultaneous/PROPERTIES.md` (P1–P8).

For each, return agree, disagree or needs change, with your reason.

## 6. Acceptance checklist (the owner ticks it on receipt)

- [ ] `ORACLE_REVIEWER_ACCEPTANCE.md` signed (author and reviewer), with the sha256 matching `CANDIDATE_SHA256`
- [ ] each case has an `oracle_spec.md` with classes, scope, competing explanations, observation notes, "Sources read" and a signature; its sha256 recorded
- [ ] each encoding has a signed review stating that it matches
- [ ] the curator reported each case's corrected-version validation result; failures were dropped and recorded
- [ ] the §5 process-check verdicts were returned
- [ ] nothing in "Sources read" includes forbidden material
