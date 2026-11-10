# Recommended r1 decision set (RECOMMENDATIONS ONLY; 2026-11-10)

These are the developer's recommendations, aligned with the owner's proposed directions. **Nothing is approved,
signed, adopted or registered**, and every form in `approval_r1/` stays blank. Where a direction differs from the
text of revision candidate r1 (`AMENDMENT_003_ADOPTION_CANDIDATE_r1.md`, sha256 `20403460…`), r1 can't be signed as
written: the needed text change is listed, and would become a new candidate (r2) with a new hash.

## Decision matrix

| # | Recommended direction | Matches r1 text? | Conflict with existing decisions | Needed amendment | Approvers |
|---|---|---|---|---|---|
| 23 | Strict primary void rule; detection only on target clauses | **yes** (§6, §15, §19 #23) | none. It extends #7 (target-clause declaration) and #8 (triage category c) | none | owner, oracle author, oracle reviewer, triage reviewers |
| 24 | K = 6, S = 2, subject to development-only feasibility checks | **partly**: r1 recommends 6 and 2 but leaves them open | **#15:** T = 900 s per case and arm can truncate an all-INCOMPLETE arm (worst case 1,154 s; `KS_FEASIBILITY.md`) | fix K = 6 and S = 2 in #24, and replace #15's T with 1,200 s per arm and case or a non-truncating safety stop | owner |
| 24a | No additional outcome-dependent Muse requests; invalid proposals and missing slots accounted | **partly**: r1 cycles valid workloads and is silent on extra requests and missing slots | none with #1–#22; consistent with #4's fixed R = 2 | state: no extra request is made; invalid proposals stay in the denominator; with zero valid workloads every slot is a REFUSED, budget-consuming nondetection; slots filled by repetition are counted and reported | owner |
| 25 | RA-DET-G is the primary Muse comparator; curated RA-DET is secondary | **yes** (§2, §8, §13, §19 #25) | **§13 and #4:** the primary outcome changes from RA-MDS vs RA-DET. Allowed only because this is a new candidate, decided before any confirmatory run | none beyond r1 | owner, integrator |
| 26 | At least 10 independent defects from at least 5 applications, at most 3 per application | **yes** (§3, §19 #26) | consistent with #4 (≥ 10 cases) and #5 (commit-level fix pair) | none | owner, curator |
| 27 | Verify model identity and every approved prompt, proposer, context and schema hash before every request | **partly**: r1 *records* the provider-reported model; the direction requires *verification* | none | change r1 #27 to: the provider-reported model must equal the frozen model, or the request is a provider failure (counted against R, its proposals never used). The client side is implemented: `research_tools/confirmatory_prep/verified_request.py`, 9 tests | owner, integrator |
| 28 | INCOMPLETE and REFUSED runs are nondetections, unless excluded under prospective eligibility rules | **partly**: r1 always counts them as nondetections | none | add to #28: an integration may reject workload shapes in `validate` only under a rule fixed before the freeze and applied identically to every arm. Rejected proposals count as invalid | owner, integrator |
| 29 | A separate fix-blind Integrator, keeping existing independence requirements | **no**: r1 lets the curator serve as Integrator | none with #6 and #7 (which stay) | change r1 #29 and §15: the Integrator is a named person distinct from the curator, the oracle author, the oracle reviewer and the developers; `INTEGRATOR_ACCEPTANCE.md` drops "may be the curator" | owner, curator, integrator |
| 30 | A three-application human study, provisionally 18 participants, with crossover allocation and a documented sample-size justification | **partly**: r1 leaves option A, B or C open | **The study package** (`onb-study/1`) is 2 applications with ≥ 12 participants; it must be revised (`onboarding/participant_study/PROTOCOL_r1_3APP.md`). The RA-Q4 claim rule (≥ 3 applications) becomes satisfiable | fix option A in #30, and reference the 3-application protocol and its sample-size justification | owner; the study's data owner and ethics or owner reviewer |

## Unresolved governance conflicts

1. **r1 isn't signable as the recommended set.** Six decisions above (#24, #24a, #27, #28, #29, #30), plus #15's T, need
   text changes. That means a new candidate r2, with its own hash and forms. r1 and the original stay unchanged.
   r2 hasn't been generated, because these directions are recommendations awaiting the owner. Once confirmed, it is
   built the same way as r1 (`r1/build_candidate_r1.py` plus the edits listed here).
2. **#15's time cap vs #24's equal budgets.** T must not truncate schedules.
3. **#29 vs staffing.** A separate Integrator adds a sixth independent person: a curator, an integrator, an oracle
   author, an oracle reviewer, two triage reviewers and an adjudicator.
4. **#30 vs the existing study package.** The package's analysis code (`MIN_PARTICIPANTS = 12`, 4 sequences) must be
   updated to the 3-application design before its manifest is frozen. Not done here; it is development work after
   the owner's choice.
5. **#27 vs provider behaviour.** If the provider reports a dated or aliased model name, every request would fail
   closed. The exact accepted string must be recorded at adoption.
