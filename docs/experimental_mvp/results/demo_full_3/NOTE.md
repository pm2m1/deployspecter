# demo_full_3: reading note (a FAILED run, kept as evidence)

This is the full one-command run at revision `5b146bf`. It **exited 15**. Every step passed (277 core experimental tests, the sync/async/integrated demos, the isolated smoke test, PGQueuer, the Chancy runtime and both negative controls, concurrent workers, the claim-lock mutant) up to the last one: the Chancy 0.25.1/0.26.0 mixed-version pilot.

**What failed.** In phase 3 (the 0.25.1 worker on the new schema), the server log refuted the trace: `unexplained session pid 4184: connection received`, `old_worker: 4 traced operation(s) never reached the log`. Phase 4 never ran. The `M2`/`M3` oracle entries in `chancy_mixed_version_pilot/report.json` describe an aborted run, not an application defect: jobs 3 to 5 stayed pending because the new worker never started.

**Cause.**
- 0.25.1's `Worker.stop` cancels its maintenance tasks. Here it cancelled the heartbeat while the heartbeat's `INSERT INTO chancy_workers` was on the wire. The trace records it truthfully as `aborted/cancelled`.
- psycopg then sent a cancel request on a new connection: that is pid 4184, which only ever logs `connection received`.
- The connection was left `ACTIVE`, so the ungranted transaction exits that followed never reached the server.

The corroborator did its job. The harness was at fault: it let a framework stop interrupt a statement. The first recorded pilot (`real_app_pilot_chancy_mixed_1`) passed because the stop happened not to land during a heartbeat.

**Fix** (next commit):
- `AsyncRun.quiesced`: while a framework is being stopped, its earlier tasks' new operations are held before the gate. They are recorded as `cancelled_while_held` when cancelled there, and the statement in flight finishes first. The stopping task, and tasks it creates, are exempt.
- `chancy_adapter.stop_worker` waits until no job update is queued and the pool's connection is free, then stops inside the hold.
- Regression test: `TestQuiesced` in `tests/experimental/test_mvp_async_live.py`. With the hold disabled, it recorded a mid-statement `cancelled` op in 5 of 5 runs.
- After the fix, 5 of 5 repeated mixed-version runs were corroborated in every phase, with the same version split as the first pilot: 0.25.1 handled jobs [0, 1, 2] and 0.26.0 handled [3–7].
