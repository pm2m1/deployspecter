# Decision comparison: revision candidate r3 (PROPOSED review candidate) against r2

| | File | sha256 | Forms |
|---|---|---|---|
| Original (unchanged) | `AMENDMENT_003_ADOPTION_CANDIDATE.md` | `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0` | `approval/` |
| r1 (unchanged) | `AMENDMENT_003_ADOPTION_CANDIDATE_r1.md` | `204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727` | `approval_r1/` |
| r2 (unchanged) | `AMENDMENT_003_ADOPTION_CANDIDATE_r2.md` | `476d5c57013b12abfbe78c70db67a88603e095382dd409068561418869374e46` | `approval_r2/` |
| **r3 (PROPOSED)** | `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md` | `af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c` | `approval_r3/` |

**What r3 is.** r2 with one clarification: the proposal-admission rule (#24a). `r3/build_candidate_r3.py` makes 5
asserted edits (the header, two in §4, one in §5, one in §19) and refuses unless the original, r1 and r2 match
their hashes. Every other decision is r2's, word for word (`DECISION_COMPARISON_r2.md`). All forms are blank. The
owner's recommendations are written as proposed text, not as approval. **r3 is intended as the stable review
candidate:** further revisions only if a reviewer identifies a substantive problem.

## The gap r3 closes

With R = 2 requests and P = 6 proposals there can be up to 12 proposals for K = 6 slots. r2 said only "Admission
takes valid proposals in model order up to K". It didn't define the order across requests, the fate and reporting
of valid proposals beyond K, or duplicates.

## The rule (r3 §4, "Proposal admission")

| # | Recommended decision | r3 text |
|---|---|---|
| 1 | Exactly the predeclared requests, before any workload runs | step 1: R requests by index 0 … R − 1, all recorded before any run; failures count against R, never retried or replaced |
| 2 | Request-index order, proposal order within each reply | step 2 |
| 3 | Every generated proposal in the validity denominator | step 3; §5: validity rate = valid ÷ generated, failed requests reported beside it |
| 4 | Deduplicate valid workloads by canonical representation | step 5: sha256 of canonical JSON, across the case's requests |
| 5 | Admit the first K = 6 distinct valid workloads | step 6 |
| 6 | Valid proposals beyond K counted, never executed | step 6 ("overflow") |
| 7 | Duplicates count toward validity, not distinct admission | step 5 |
| 8 | Unfilled slots consume their schedule budget as nondetections | step 7 (see C1) |
| 9 | No extra requests, replacements, early stopping or outcome-dependent reordering | step 8; admitted list journaled before the first run |
| 10 | Report generated, valid, distinct, admitted, executed, duplicate and overflow counts separately | step 9 and §5 |

## Consistency with the K × S budgets and r2's scoring

- **Budgets:** unchanged. Every arm still runs K × S schedules per revision. Admission only decides which
  workloads fill the slots (tested: 0, 3 and 6 admitted workloads all give 12 schedules per revision).
- **Scoring:** unchanged. Detection, the corrected-version void rule, target clauses, censoring at K × S and the
  three time measures (#23, #28) don't depend on admission. INCOMPLETE and REFUSED runs stay nondetections.
- **RA-DET-G:** unchanged. It already supplies K distinct workloads or reports a shortfall. The new
  deduplication makes the Muse arm's "distinct" count comparable to it.

## Contradictions found, and how r3 resolves them (reviewers should confirm)

- **C1. Rule 8 against #24's cycling.** Read literally, "unfilled slots consume their budget as nondetections"
  could mean that a Muse arm with 3 distinct valid workloads leaves slots 3–5 empty (REFUSED), while r2's #24 fills
  them by repetition with new seeds, as every other arm with fewer than K workloads does. Those two readings can't
  both hold.
  - **r3 keeps #24's cycling.** A slot is "unfilled" only when no workload is admitted, and then every slot is a
    REFUSED, schedule-consuming nondetection. This is the smallest change; it keeps one slot rule for every arm and
    matches the tested code.
  - **The alternative** (no repetition for Muse arms) would give a low-diversity Muse arm fewer executed schedules
    than RA-DET-G. That changes the estimand from equal executed budgets to equal distinct workloads, and would need
    #24 itself amended. It is not adopted here.
- **C2. Proposals in a failed reply.** Rule 3 counts every generated proposal, and #27 says a provider-failure
  reply is never used. r3: such proposals count as generated and as **not valid** (reason "provider failure"). A
  request that returned nothing contributes no proposals; it is reported as a failed request instead.
- **C3. "Exactly R requests" against a request refused before sending.** The freeze check (#27) can refuse a
  request without sending it. r3: each request index is attempted exactly once, and a refused one is a failed
  request that counts against R, never replaced.
- **C4. "Schema-valid" against the full validator.** r3 deduplicates workloads that are valid under the whole
  shared validator (shape, schema, integration validator and frozen eligibility rules, #28), so an ineligible
  workload never takes a distinct place.
- **No other contradiction found.** One consequence to note: the second request is always made, even when the first
  already gives K distinct valid workloads. Its proposals are then all duplicates or overflow: counted, never
  executed, and their cost is spent. That follows from rule 1 and keeps the request schedule outcome-independent.

## Forms (`approval_r3/`)

Generated by `r3/build_forms_r3.py` from the unchanged `approval/` forms, as for r2: the r3 hash only, the same
decision rows as r2, the #24a row annotated "clarified in r3", the separate-Integrator form, and the r2 checklist
steps plus **r3-h** (commit each Muse arm's admission record and journal hash before its first run). `--check`
confirms that nothing is ticked, named, signed or dated, and that the original, r1 and r2 are unchanged.

## Implementation (development-only)

`research_tools/confirmatory_prep/prep_r3.py`: `admit_replies` and `reply_record`, and `executed_counts`, which
refuses if the executed workloads differ from the admitted ones. Tests: `tests/experimental/
test_confirmatory_prep_r3_admission_offline.py` (12). `prep.py`, `prep_r2.py` and the real-mode gates are unchanged.
