# Mutants as a predeclared second source (prepared 2026-11-15; NOT RUN)

Mutants are a **second, separately reported** source of cases (r3 §6 "Mutants", ruling #5). Historical cases and
mutants are **reported separately and never pooled**: no table, test or headline combines them, and a mutant is
never called a defect.

## Operators (fixed here, before any curator application is known)

| ID | Operator | Applied to | Frozen protocol §4.2 equivalent |
|---|---|---|---|
| RM1 | Remove `FOR UPDATE` / `FOR SHARE`, or remove `SKIP LOCKED` (including Django `select_for_update(skip_locked=True)`) | a locking read | M1, extended to `SKIP LOCKED` |
| RM2 | Remove an idempotency guard (an existence check, `ON CONFLICT`, a unique-key test or a status check before a write) | the guarded write | M5 / M6 |
| RM3 | Widen a backfill predicate (drop one conjunct of its `WHERE`, such as `IS NULL` or a version or batch bound) | a batched `UPDATE` or `INSERT … SELECT` in a migration or backfill | M6 |
| RM4 | Reorder the two writes of a dual write | the application function | M10 (reordered rather than removed) |
| RM5 | Drop one branch of a trigger function (`INSERT`, `UPDATE` or `DELETE`) | a trigger defined in a migration | new |
| RM6 | Make a conditional `UPDATE` unconditional (drop its compare-and-set condition) | an `UPDATE … WHERE <state condition>` | M6 |

**Sampling** follows the frozen protocol's §4.2:
- a script enumerates every site per operator;
- sites are sorted by `sha256("deployspecter-ra-r3:mutation:<owner>/<repo>:<operator>:<path>:<line>")`;
- the first 5 per operator per application are taken.

## When the generator may run

- **Never on a curator's application before the freeze.** The mutant generator doesn't run on any application in
  the curator's sealed set until r3's adoption commit is registered, every oracle is hashed, and the curator's
  labels are sealed.
- **Why:** running it earlier would expose the owner to that application's code paths, the same exposure the sealing
  prevents.
- **Development applications:** before the freeze, the generator may run only on applications already exposed
  (chancy, pgqueuer, saq, procrastinate) to test it. Those runs are labelled NONBLIND development.

## Equivalence and reachability

- **Invalid mutant:** it doesn't import or migrate, or it fails the application's own serial tests in an unrelated
  way. It is excluded and replaced by the next site in hash order, and the count is reported.
- **Unreached:** no workload of any arm executed the mutated statement (from the corroborated trace). It is reported
  as unreached, never as a miss.
- **Possibly equivalent:** reached, and it survives every arm at the full K × S budget. Two reviewers classify it
  without seeing arm labels. A disagreement stays "undetermined". Scores are reported with and without undetermined
  mutants.
- **Killed:** a SOUND, corroborated violation of any oracle clause on the mutant, with the unmutated corrected
  revision holding under the same schedules.

## Reporting

- Per operator and per application: sites found, sampled, invalid, unreached, killed, survived, equivalent and
  undetermined.
- For each arm, a mutation score with exact counts, beside the historical results and never added to them.
- A McNemar test on mutants, if one is run, is separate and secondary, and named as such.
