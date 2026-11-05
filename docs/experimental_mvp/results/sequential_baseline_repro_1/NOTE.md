# sequential_baseline_repro_1: reading note

This reproduces the sequential Chancy 0.25.1 → 0.26.0 rolling-deploy pilot at the release-candidate code (`afd9ffd` = rc1 `d9890f8` plus docs). It was run before any change for the simultaneous-execution work, and **exited 0**.

- **Versions.** Both releases were verified inside their containers by source hashes: 0.25.1 for phases 1 and 3, 0.26.0 for phases 2 and 4.
- **Corroboration.** All four phases were corroborated, with 0 parameters mismatched and 0 unchecked:

  | Phase | Statements | Values compared |
  |---|---|---|
  | 1 | 45 | 64 |
  | 2 | 11 | 7 |
  | 3 | 65 | 80 |
  | 4 | 371 | 212 |

  These are the same counts as in `demo_full_4`.
- **Oracle.** M1–M4 holds.
- **Quiescence.** The only aborted operations are `cancelled_while_held`, in phase 3, where 0.25.1's stop cancelled a held heartbeat at the gate. None is `cancelled` mid-statement.
- **Evidence unchanged.** `demo_full_3`, `demo_full_4` and `real_app_pilot_chancy_mixed_1` were checked by digest before this run, and they match the release-candidate report.
