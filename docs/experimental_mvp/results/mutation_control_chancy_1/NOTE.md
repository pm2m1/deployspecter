# mutation_control_chancy_1

This is a controlled mutation check on pinned public code (Chancy 0.26.0). It is NONBLIND. It is **not a historical defect** and not a detection claim.

**Setup.** Two workers run as two actors, each with its own Chancy instance and its own stepped connection, draining four jobs.

**The mutant (`no_skip_locked_claim`).** It removes `FOR UPDATE OF {jobs} SKIP LOCKED` from the job-claim statement in `chancy/worker.py`. The change was made in the isolated build copy only, and the adapter accepts it only through its declared hash.

**Oracle.** K1 and K2, written from the documented job states:
- every pushed job succeeded exactly once;
- across all workers, each job's handler ran exactly once.

**Result.** The unmodified release holds. The mutant is distinguished: the same jobs were executed by both workers, and the remaining jobs were left pending. The server log corroborates both runs completely.

**What this shows.** The independent oracle discriminates this semantically meaningful fault under real concurrent execution. It says nothing about Muse.
