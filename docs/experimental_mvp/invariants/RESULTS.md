# Mutation-validated invariants (Workstream C): results

**Status:** EXPERIMENTAL, NONBLIND. Design is in `README.md`; the source is `../muse_contrib/analysis_1_a1/invariants.json`.

**The validator classified every candidate.** Neither Muse nor the developer decided any class.

**No candidate is claimed as a genuine new correctness property.** None was even a `new_property_candidate`: no candidate was violated where the oracle judged a run and found nothing.

**The mutants are controlled experiments, not upstream defects.**

## Corpus (sound observations only)

| Domain | Observations | Sound | Reference | Defective | Mutant |
|---|---|---|---|---|---|
| reservations (trusted fixture, server-log corroborated) | 3505 | 3505 | 747 | 1734 | 1024 |
| chancy_jobs (restricted containers, SOUND runs) | 86 | 56 | 15 | 15 | 26 |

## Chancy (real application; reference = fix commit 2895c4a, defective = parent 80f570f)

- **The frozen oracle (H0–H2):**
  - kills `abl_no_fence`, `abl_cancel_keeps_claim` and `abl_no_claim_assignment`;
  - gives NO_VERDICT on every run of `mc_claim_takes_failed` and `mc_cancel_keeps_state`;
  - misses the other three mutants.

| Candidate | Source | Class | Mutants killed | Notes |
|---|---|---|---|---|
| D-C1 cancelled ⇒ failed | deterministic | duplicates_oracle | abl_no_fence, abl_cancel_keeps_claim, abl_no_claim_assignment, mc_claim_takes_failed | H1 restated; it also flags 4 observations where the oracle gave no verdict; parent violated 11 times |
| D-C2 cancelled ⇒ no claim | deterministic | mutant_sensitive | abl_cancel_keeps_claim, mc_claim_takes_failed | |
| D-C5 cancelled ⇒ ≤ 1 execution | deterministic | mutant_sensitive | mc_claim_takes_failed | |
| D-C3, D-C4, D-C6, D-C7 | deterministic | valid | – | |
| mc_inv_chancy#1 each job succeeds ≤ 1 | Muse | mutant_sensitive | mc_claim_takes_failed | |
| mc_inv_chancy#2 executions ≤ max_attempts | Muse | mutant_sensitive | mc_claim_takes_failed | |
| mc_inv_chancy#0, #3, #7 | Muse | valid | – | |
| mc_inv_chancy#5 pending ⇒ never executed | Muse | valid_vacuous | – | constrained no row |
| mc_inv_chancy#4 failed ⇒ never succeeded | Muse | **invalid** | – | false on the fix commit: a cancelled job's handler can still return |
| mc_inv_chancy#6 cancelled ⇒ never succeeded | Muse | **invalid** | – | same false positive |
| X-C-taut / X-C-false | control | valid_vacuous / invalid | – | the controls behaved as required |

**Mutation score (Chancy):**
- **Deterministic candidates:** 4 of 8 mutants (the three ablations and `mc_claim_takes_failed`).
- **Muse candidates:** 1 of 8 (`mc_claim_takes_failed`).

**Missed by every valid candidate:**
- `mc_cancel_keeps_state`. **Observability gap:** the run's `cancelled` flag is read from the cancel statement's text, which this mutant changes. No cancel is observed, and the oracle's H0 also gives no verdict.
- `mc_completion_drops_attempts` and `mc_claim_ignores_budget`. They're unexercised: their effect needs failing or retried handlers, and the case's handler always returns.
- `no_skip_locked_claim`. Its only two-worker workload was mostly INCOMPLETE (see `../muse_contrib/RESULTS.md`).

## Reservations (trusted fixture; reference = `corrected`, defective = `buggy`, `fixed`)

- **The frozen oracle (R1, R2, R5):** kills `no_event_lock` and `off_by_one`; misses `no_guard` and `no_reservation_lock`.

| Candidate | Source | Class | Mutants killed | Defective-build violations |
|---|---|---|---|---|
| D-R1 reserved = Σ qty | deterministic | mutant_sensitive | no_event_lock, off_by_one | buggy 356, fixed 228 |
| D-R2 Σ qty ≤ capacity | deterministic | mutant_sensitive | – | buggy 144 |
| D-R5 count change = reserved − released calls | deterministic | mutant_sensitive | – | buggy 228, fixed 228 |
| D-R3, D-R4, D-R6, D-R7 | deterministic | valid | – | – |
| mc_inv_res#2 reserved = Σ qty | Muse | mutant_sensitive | no_event_lock, off_by_one | buggy 356, fixed 228 (equivalent to D-R1) |
| mc_inv_res#6 Σ reserved = Σ qty | Muse | mutant_sensitive | no_event_lock, off_by_one | buggy 356, fixed 228 |
| mc_inv_res#1 reserved ≥ 0 | Muse | mutant_sensitive | – | buggy 18, fixed 114 |
| mc_inv_res#0, #3, #4, #5, #7 | Muse | valid | – | – |
| X-R-taut / X-R-false | control | valid_vacuous / invalid | – | the controls behaved as required |

- **Mutation score (reservations):** deterministic 2 of 4, Muse 2 of 4, the same two. Both sets miss `no_guard` and `no_reservation_lock`.
- **"Mutant-sensitive" here means within the oracle's flags.** Every candidate that kills does so inside what R1/R2/R5 already flag.

## Answer to Q3

**Matched on both domains.** Muse proposed admissible, mutant-sensitive invariants that restate deterministic ones. It found no mutant that the deterministic candidates missed. On Chancy it killed fewer mutants (1 of 8 vs 4 of 8) and produced two invalid candidates.

That's a nonblind, small-sample observation, not evidence about Muse in general.
