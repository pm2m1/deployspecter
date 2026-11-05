# The `4e94b94` sets (superseded; kept as a record)

This note covers the sets run at `4e94b94`, the first revision with cancellation deferral:
- `simultaneous_scenarios_3`;
- `simultaneous_explore_compete_3`;
- `simultaneous_mutant_compete_4`;
- `simultaneous_repeat_compete_2`;
- `simultaneous_repeat_cancel_new_old_2`;
- `sequential_baseline_repro_3`.

**Results.**
- The sequential baseline: exit 0, all four phases corroborated, oracle clean.
- Of 35 simultaneous runs, **5 were REFUSED**, from two harness faults that both failed closed. None of the refused runs has a property verdict.

| Run | Refused because |
|---|---|
| `scenarios_3/old_to_new`, `scenarios_3/compete` | **Coordinator race.** A grant arrived for a statement that 0.25.1's `Worker.stop` had just cancelled at its gate, so the process aborted with `protocol_error` |
| `scenarios_3/retry_handoff`, `explore_compete_3/01_seeded1`, `mutant_compete_4/08_seeded8` | **Open transaction.** A COMMIT cancelled before its grant left psycopg's transaction open. The pool's later `rollback()` was refused client-side by psycopg, so it never reached the server, and the server log refuted the trace |

**Fixes, at the next commit:**
- **The open transaction.** A COMMIT (or RELEASE) cancelled before its grant now exits the transaction with an ungranted, recorded ROLLBACK. The regression test is `TestCommitCancelledAtTheGate`, which fails without the fix: the session is left `INTRANS`.
- **The client-side refusal.** A driver refusal before any I/O is classified `client_side`, and is not expected in the server log.
- **The coordinator race.** A stale grant is declined, recorded, and decided again. It is tested offline.

The SOUND runs of these sets agree with the final sets: the same properties, outcomes and P7 cells, and the mutant again detected in 2 of the 9 sound runs. They are superseded only because the engine changed. The final evidence is `simultaneous_scenarios_4` and the other `_final` sets.
