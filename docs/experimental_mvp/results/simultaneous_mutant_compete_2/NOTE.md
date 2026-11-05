# simultaneous_mutant_compete_2: mutation control, all 10 runs REFUSED (harness marker flaw); no verdict

**Setup.** Scenario `compete`, seeds 0–9.
- **B (0.26.0):** the declared `no_skip_locked_claim` mutant. The `FOR UPDATE OF {jobs} SKIP LOCKED` row lock is removed from the claim query, in the isolated build copy only. Its declared file hash was verified by the adapter (`0.26.0+mutant:no_skip_locked_claim`).
- **A (0.25.1):** the original release.

**Outcome.** All 10 runs were refused with `stall`. Every statement was corroborated, but nothing was concluded.

**Cause.** The window gate holds the producers until each worker has completed a claim query, and the harness's `fetch`/`claim` markers recognized a claim query by the text `SKIP LOCKED`. That is the very text the mutant removes. So the mutant worker never counted as ready, the producers stayed gated, and the coordinator stalled. This is a harness fault, and it failed closed.

**Fix.** The markers now recognize a claim query by its CTE, `WITH selected_jobs AS`. That text occurs once in each release (in the claim query only), and the mutation doesn't touch it.

**Next.** The control was re-run as `simultaneous_mutant_compete_3`. The original releases' exploration was run again with the corrected markers as `simultaneous_explore_compete_2`, so that the comparison is like for like.
