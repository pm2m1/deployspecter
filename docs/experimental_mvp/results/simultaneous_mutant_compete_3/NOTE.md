# simultaneous_mutant_compete_3: mutation control, `compete`, seeds 0–9 (NONBLIND; a control, not a Chancy defect)

**Setup.**
- **B (0.26.0):** the declared mutant `no_skip_locked_claim`. `FOR UPDATE OF {jobs} SKIP LOCKED` is removed from the claim query, in the isolated build copy only. Its declared file hash was verified inside the container (`0.26.0+mutant:no_skip_locked_claim`).
- **A (0.25.1):** the unmodified release.

The same revision, seeds and harness were used as for the comparison arm, `simultaneous_explore_compete_2`.

| | Original releases (`explore_compete_2`) | Mutant 0.26.0 + original 0.25.1 |
|---|---|---|
| SOUND | 10 / 10 | 10 / 10 |
| Statements corroborated (mismatched) | 2077 (0) | 2074 (0) |
| Runs violating a property | **0** | **2** (seeds 1 and 4) |
| Violated | none | **P2** `executions_match_attempts`: a job run twice, by both versions, with `max_attempts = 1` (seed 4: twice) |
| Time to first violation | none | seed 1, at 245 grants (15.9 s) |

**The mechanism, seed 1, from the corroborated trace** (`coordinated_summary.py --interleaving`):
- **Grant 47:** the 0.25.1 claim returns 1 row and holds the row lock.
- **Grant 48:** the mutant's unlocked claim selects the same job. Its UPDATE then waits on 0.25.1's lock. Decision 49 records `{"new_worker": ["old_worker"]}`, a lock wait across the two containers, attributed.
- **Grant 49:** the 0.25.1 COMMIT. The mutant's UPDATE re-checks only the join on `id`, so it claims the job as well.

Both workers then execute job 2.

**Reading.**
- The oracle distinguishes the mutant from the released code under identical schedules.
- Detection depends on the schedule: 2 of 10 seeds placed the mutant's claim between 0.25.1's claim and its commit.
- **This is an injected control. It is not a historical or real Chancy defect, and it is not Muse evidence.**
- P3 held in both violating runs: both executions set `attempts = 1` and `succeeded`, so the duplicate is visible only through execution counting (P2).
