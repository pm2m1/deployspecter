# simultaneous_diagnostics_1: diagnostic `new_to_old` runs made while fixing the stop path (kept as a record)

These seven runs were made while diagnosing an intermittent refusal of the optional test `test_old_and_new_workers_run_at_the_same_time_under_one_coordinator`. That test's own refused run was in a pytest temporary directory and is gone. Its summary line, as printed by the test, was REFUSED with corroboration ok.

| Runs | Code state | Verdicts | What they showed |
|---|---|---|---|
| `a_hold_stop_new_to_old` | `637334f`: the stop held new work (`quiesced`), `shutdown_timeout = 5` | SOUND, but both workers' `stop()` timed out (`worker_stopped` False), so P6 was not evaluated | 0.26.0's graceful drain waits for queue loops whose next statement the hold blocks |
| `b_hold_stop_shutdown30_1..3` | uncommitted: the same, with `shutdown_timeout = 30` | **REFUSED**, 3 of 3 (`corroborated`) | While B drained for up to 30 s, the cross-process lockstep stalled A's open claim transaction until the server ended it (idle in transaction) |
| `c_deferral_1..3` | uncommitted, then committed as `4e94b94`: cancellation deferred to the statement boundary, no hold | SOUND, 3 of 3; both workers stopped cleanly; P6 evaluated; no violation | the fix |
