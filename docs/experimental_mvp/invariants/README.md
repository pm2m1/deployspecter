# Mutation-validated application invariants (Workstream C): design

**Status: EXPERIMENTAL, NONBLIND, exploratory.** Results are in `RESULTS.md`.

## What decides
Proposers only propose: Muse (one frozen call per domain), the developer (a deterministic candidate list), and two controls per domain. A **deterministic validator** (`src/deployspecter/experimental/invariants/validator.py`) decides admissibility and class. No hypothesis is ever executed as code.

## The language: `ds-invariant/1` (`invariants/language.py`)
- **Row hypotheses:** for every row of a table satisfying `where`, `require` holds. Terms are a column, a typed constant, NULL, or a child sum/count over another table by a foreign key.
- **Aggregate hypotheses:** signed sums of `sum`/`count` (optionally filtered by one string column) over the initial or the final state, compared.
- **Admissibility, checked before evaluation:** a known domain, tables and columns; type-compatible comparisons; no ordering against NULL or booleans; no self-comparison; at most 4 conditions or terms a side.
- **NULL semantics, fixed:** `eq`/`ne` treat NULL as a value; an ordering comparison with NULL is false.
- **Domains:**
  - `reservations`: `events`, `reservations`, `rejections`, plus `calls`, the status each handler call returned;
  - `chancy_jobs`: `jobs`, read back from `chancy_jobs` and joined with what the actors observed: `cancelled`, `executions`, `successes`.

## The eight checks
1. **Corrected reference:** the reservations `corrected` build, or the Chancy fix commit `2895c4a`.
2. **Valid initial states:** every constructed initial state, with initial = final.
3. **Serial reference behaviour.**
   - Reservations: each actor's statements form one block.
   - Chancy: no cancel is granted while a claimed job awaits its completion flush.
4. **Concurrent reference behaviour.**
5. **Mutants:** application mutants only.
   - Reservations: the four `release_seats` mutants in `apps/reservations_mutants.py`.
   - Chancy: eight single-substring mutants of the fix tree.
     - Four are existing: `abl_no_fence`, `abl_cancel_keeps_claim`, `abl_no_claim_assignment`, `no_skip_locked_claim`.
     - Four are new: `mc_claim_takes_failed`, `mc_cancel_keeps_state`, `mc_completion_drops_attempts`, `mc_claim_ignores_budget`.
     - They are registered in-process by `research_tools/muse_contrib/chancy_runner.py`; `run_isolated.py` isn't edited. Each is applied to the isolated build copy, and the adapter verifies the declared hash.
   - Known-defective builds are reported apart: reservations `buggy`/`fixed`, and the Chancy fix parent `80f570f`.
   - **The mutants are controlled experiments, not upstream defects.**
6. **Vacuity:** a row hypothesis that constrained no row anywhere, or one that holds on all 300 seeded random states of the domain (a tautology).
7. **False positives:** any violation on the reference or on an initial state.
8. **Kills and missed mutants.**

## Classes
- `invalid`: inadmissible, false at start, or a reference false positive.
- `not_assessable`: no sound reference observation.
- `valid_vacuous`, `valid`.
- `duplicates_oracle`: among observations the oracle judged, violated exactly where it is. Violations where the oracle gave no verdict, such as Chancy's H0 sanity failure, are counted beside.
- `mutant_sensitive`: kills something, within what the oracle already flags.
- `new_property_candidate`: violated where the oracle judged and found nothing. Whether that is a **genuine new correctness property** needs a documentary basis and a reviewer; the validator never decides it.

Only sound observations count:
- reservations: the run completed and the server log corroborated it;
- Chancy: the run is SOUND (isolated, corroborated, quiescent).

Infrastructure mutation tests (`mutation-testing-harness`) are not counted here.
