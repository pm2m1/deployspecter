# Incident 1: an unlocked test-suite run on the shared database (2026-11-08, about 04:53–04:56Z)

**What happened.**
- The Track A procrastinate agent ran the core experimental suite (`pytest tests/experimental`) without
  `research_tools/pg_lock.py`.
- Its brief's test command had omitted the wrapper: a coordinator error in the brief, not a harness defect. That
  suite runs live tests whenever the disposable database's password file exists.
- It reached the disposable database on 127.0.0.1:55441 while onboarding sessions held the lock. The protected
  55432 instance was never involved.

**Effect.**
- `postgres_tq_D_r1` attempts 01 (04:54:40Z) and 02 (04:55:26Z) failed S2. Their server-log corroboration found
  sessions that weren't theirs, 292 and 193 problems respectively: reservations-fixture migration statements and
  `pg_blocking_pids` monitors from the unlocked suite.
- The operator's own statements in both attempts were clean: 49 statements matched, 0 parameters mismatched, and
  each key was handled once.
- Attempt 03, at 05:02:10Z with the same adapter, reached S2 and S3.
- In the same window, `pgtq_D_r1`, `pgtq_M_r1` and `postgres_tq_M_r1` ran, and each passed corroboration.
- The procrastinate agent's own unlocked run also had 2 corroboration failures (it saw a session from the
  onboarding harness). Its locked rerun is the valid result.

**Instrument.** The corroborator worked as designed: it refused runs that had foreign sessions, and it accepted
nothing it couldn't explain. The lock itself is shared and was honoured by every locked caller.

**Analysis.**
- **Primary analysis (predeclared, unchanged):** the two attempts count as failed attempts, and the lost time stays
  in `postgres_tq_D_r1`'s elapsed times.
- **POST-HOC note:** this disadvantages D in that one pair. A counterfactual time can't be computed, so it is
  reported as a threat to validity, not adjusted.

**Correction.** The other agents were told at once to run the suite only through the lock.
