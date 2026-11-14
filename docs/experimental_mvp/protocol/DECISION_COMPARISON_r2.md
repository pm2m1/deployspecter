# Decision-by-decision comparison: original candidate, r1 and revision candidate r2 (PROPOSED)

| | File | sha256 | Forms |
|---|---|---|---|
| Original (unchanged) | `AMENDMENT_003_ADOPTION_CANDIDATE.md` | `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0` | `approval/` (unsigned) |
| Revision r1 (PROPOSED, unchanged) | `AMENDMENT_003_ADOPTION_CANDIDATE_r1.md` | `204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727` | `approval_r1/` (unsigned) |
| **Revision r2 (PROPOSED)** | `AMENDMENT_003_ADOPTION_CANDIDATE_r2.md` | `476d5c57013b12abfbe78c70db67a88603e095382dd409068561418869374e46` | `approval_r2/` (unsigned) |

**How r2 is made.** `r2/build_candidate_r2.py` applies 16 exact, asserted text replacements to r1. It refuses unless
r1 and the original are byte-identical to their recorded hashes. `--check` rebuilds r2 in memory and compares it
byte for byte. `diff AMENDMENT_003_ADOPTION_CANDIDATE_r1.md AMENDMENT_003_ADOPTION_CANDIDATE_r2.md` shows every
change.

**What r2 is.** It writes the recommended study design for #23–#30 (plus #24a and a time rule replacing #15's T)
into the candidate's text, so that the text could be signed as recommended. Those recommendations are **not** a
recorded owner approval. Nothing is adopted, signed or registered. The owner takes forward **at most one** of the
three candidates, and signatures never carry from one to another.

## Decisions

| Decision | Original | r1 | **r2** | Where in r2 | Must approve |
|---|---|---|---|---|---|
| #1–#14, #16–#22, #A4, #A5 | as written | unchanged | unchanged | — | as in the original |
| **#15 (budgets)** | B = 12, T = 900 s, 3 replicates | K × S per revision; T = 900 s kept | **K = 6, S = 2; T = 900 s replaced** by uniform per-run limits (45 s stop, 240 s container limit, L = 300 s executor limit) and a 7,500 s per case and arm safety ceiling whose breach excludes the case for every arm | §4 "Time", §19 | owner |
| #23 (target clauses; corrected-version void) | — | new | unchanged from r1 | §6, §15, §19 | owner, oracle author, oracle reviewer, triage reviewers |
| **#24 (allocation)** | — | K × S; values recommended, open | **K = 6, S = 2 fixed** | §4, §19 | owner |
| **#24a (no extra requests; slot accounting)** | — | silent; "no retry except a logged transport failure" | **new:** exactly R requests, scheduled before any run, no additional, replacement or retry request; invalid proposals in the denominator; repetition counted; zero valid means every slot REFUSED; no slot filled from another source | §4, §19 | owner, Integrator |
| #25 (RA-DET-G primary comparator) | — | new; changes the primary outcome | unchanged from r1 | §2, §8, §13, §19 | owner, Integrator |
| #26 (≥ 10 defects, ≥ 5 applications, ≤ 3 per application) | — | new | unchanged from r1 | §3, §8, §19 | owner, curator |
| **#27 (model identity)** | provider model logged; mismatch is a provider failure | adds proposer, context, schema, `max_output_tokens`; provider model *recorded* | **verified:** the exact accepted provider string is recorded at adoption; a mismatch is a provider failure (saved, never used, counted against R, not replaced); client implemented | §12, §19 | owner, Integrator |
| **#28 (time measures; INCOMPLETE/REFUSED)** | — | always nondetections | **adds** prospective eligibility rules: fixed and hashed before the freeze, written without outcome knowledge, applied identically to every arm; rejected proposals invalid | §6, §19 | owner, Integrator |
| **#29 (Integrator)** | — | fix-blind; the curator may serve | **a separate person** from the curator, oracle author and reviewer, triage reviewers, adjudicator and developers; exposure records committed before the freeze | §15, §19 | owner, curator, Integrator |
| **#30 (onboarding study)** | — | option A, B or C open | **option A:** 3 applications, 6 sequences (12 with mirrors), ≥ 18 participants, 24 preferred, sample-size justification | §9, §19 | owner; the study's data owner and ethics or owner reviewer before any session |
| §8 censoring | "censored values at B" | unchanged | "censored values at K × S" | §8 | owner |

## Forms (`approval_r2/`)

| Form | Change from the original's form | Decision rows |
|---|---|---|
| `OWNER_APPROVAL.md` | r2 hash; #15 note; rows #23, #24, #24a, #25–#30 | #1–#30, #24a, #A4, #A5 |
| `CURATOR_ACCEPTANCE.md` | r2 hash; rows #26, #29 | #1, #2, #6, #19, #26, #29 |
| `ORACLE_REVIEWER_ACCEPTANCE.md` | r2 hash; row #23 | #7, #11, #23 |
| `TRIAGE_REVIEWERS_ACCEPTANCE.md` | r2 hash; row #23 | #8, #23 |
| `INTEGRATOR_ACCEPTANCE.md` | **new for r2**: attests the Integrator is a separate person and fix-blind | #24a, #25, #27, #28, #29 |
| `ADOPTION_CHECKLIST.md` | r2 hash; seven added r2 steps (Integrator, constants, provider string, eligibility rules, exposure records, study manifest) | — |

Check: `python docs/experimental_mvp/protocol/r2/build_forms_r2.py --check` confirms that every form names only r2's
hash, nothing is ticked, no name, signature or date is filled in, no form lets the curator serve as Integrator, and
the original and r1 are unchanged.

## Unresolved conflicts and residual choices

1. **Staffing (#29):** a separate Integrator makes seven independent people: curator, Integrator, oracle author,
   oracle reviewer, two triage reviewers and an adjudicator, plus the study's scorers and participants.
2. **#27 vs the provider:** the accepted provider-reported string must be recorded at adoption. If the provider
   reports a dated or aliased name, that exact string must be the one frozen, or every request fails closed.
3. **The time rule's constants (#15):** L = 300 s and the 7,500 s ceiling are derived from the instrument, not from
   results (`r2/TIME_BUDGET_r2.md`). A 1,200 s cap was evaluated and rejected: it covers the observed worst case by
   only 25 s and would truncate arms whenever a run stalls.
4. **#28's eligibility rules:** none is proposed. If the Integrator writes one, it must be frozen before the freeze
   and applies to every arm; otherwise INCOMPLETE runs stay nondetections (the chancy second-job-cancel shape would
   then cost schedules, as it did in ds-nbc/2).
5. **#30's design:** `min18` or `pref24` is fixed in the study manifest before recruitment. A null result at either
   size is not evidence of no effect (`onboarding/participant_study/SAMPLE_SIZE_r2.md`).
6. **The candidate:** the owner takes forward the original, r1 or r2, or asks for another revision.
