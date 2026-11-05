# Third-party and framework capability matrix (EXPERIMENTAL, 2026-11-05; release candidate rc1)

These are tested statuses, not claims of general support. Every row marked SUPPORTED has a live run in which the server log explains every row, with typed parameters compared exactly. Every REFUSED or UNSUPPORTED row has a test showing the refusal.

| Path | Status | Evidence | Boundary |
|---|---|---|---|
| PGQueuer 1.6.0: install, enqueue, drain-mode worker (Driver-port adapter) | SUPPORTED, LIVE-TESTED | `third_party_pgqueuer_lifecycle_2`: 37 statements; 72 values compared, 0 mismatched, 0 unchecked | Adapter pinned to 1.6.0 and its source hashes; one actor per phase |
| PGQueuer 1.6.0: `PgQueuer.from_psycopg_connection` / `Queries.from_psycopg_connection` | REFUSED (bypass) | `third_party_pgqueuer_public_factory_refuted_1` | Its driver builds `AsyncRawCursor` directly, outside the gate |
| PGQueuer asyncpg drivers | NOT SUPPORTED | none | A separate driver question; not built |
| Chancy 0.26.0: migrate, declare, push, worker, job, stop (one-connection stepped pool) | SUPPORTED, LIVE-TESTED | `third_party_chancy_runtime_3`: 86 statements; 79 compared, 0 mismatched, 0 unchecked | `notifications=False`, `no_default_plugins=True`, `AsyncExecutor` |
| Chancy 0.26.0: two workers as two actors, each its own instance and connection, draining 4 jobs concurrently | SUPPORTED, LIVE-TESTED | `third_party_chancy_concurrent_workers_1`: 3 sessions, 136 statements, 174 compared, 0 mismatched; each job ran exactly once (w1 [0, 2], w2 [1, 3]) | Concurrency across workers. Tasks within one worker share its connection, as its one-connection pool makes them |
| Chancy 0.25.1 → 0.26.0 rolling deploy: old setup, new migration, old producer and worker on the new schema, new producer and worker (four isolated phases) | SUPPORTED, LIVE-TESTED (NONBLIND) | `real_app_pilot_chancy_mixed_1`; `demo_full_4`: 45/11/65/371 statements, 0 mismatched, 0 unchecked; reproduced in 5 of 5 repeated runs at `d42e369` | One worker per phase, so workers of different versions are never concurrently schedulable. 0.25.1 is pinned by source hashes like 0.26.0 |
| Stopping a Chancy worker (either release) | SUPPORTED: Chancy's own `stop()`, with cancellation deferred to statement boundaries | `TestQuiesced`, `TestCommitCancelledAtTheGate`; `demo_full_3` and `simultaneous_diagnostics_1` (the failures these fix) | A statement in flight finishes before its task's cancellation is raised. A COMMIT cancelled before its grant rolls back. The earlier `quiesced` hold is kept, but isn't used for stops |
| **Chancy 0.25.1 and 0.26.0 workers running AT THE SAME TIME** (two restricted containers, one coordinator granting every statement, `ds-coord/1`) | **SUPPORTED, LIVE-TESTED (NONBLIND)** | `simultaneous/RESULTS.md`; the final sets at `d3630b5`: 35 of 35 SOUND, 7428 statements corroborated, 0 mismatched; cross-container lock waits attributed | Grants are controlled; in-process task timing is not. `notifications=False`, `AsyncExecutor`, runc only. Undocumented deployment ("Stop your workers") |
| Simultaneous scenarios: old→new, new→old, distinct queues, competing on one queue, retry hand-off, cancel cells (canceller version × worker version) | LIVE-TESTED | `simultaneous_scenarios_4` | Producers wait until both workers are polling (the window gate). Gates only order statements |
| Mutation control under simultaneous execution | LIVE-TESTED | `simultaneous_mutant_compete_5` vs `simultaneous_explore_compete_4`: P2 in 2/10 vs 0/10 | An injected control, not a defect |
| Declared mutants of pinned public code (`--mutant`), applied to the isolated build copy only | SUPPORTED (one declared mutant: `no_skip_locked_claim`) | `mutation_control_chancy_1`; `demo_full_4` | The exact single-substring change and its resulting file hash are declared and verified. A mutant is a control, not a defect |
| Chancy LISTEN/NOTIFY listener (`notifications=True`) | REFUSED (unseamed connection) | `third_party_chancy_notifications_refuted_1` | `Worker._maintain_notifications` calls `AsyncConnection.connect(dsn)` directly. No supported seam: injecting would mean patching `chancy.worker` or overriding a private method |
| Chancy sync APIs (`sync_pool`) | NOT SUPPORTED | none (they get a DSN that points nowhere and fail loudly) | |
| Chancy process executor | NOT SUPPORTED | none | Runs jobs in child processes outside the scheduler |
| Django 5.2 ORM, server-side binding | SUPPORTED, LIVE-TESTED (trusted fixture) | `test_mvp_django_live.py`: migrations, get, filter, save, create, `update(F)`, `select_for_update`, `atomic`, nested savepoints; buggy 24/22, fixed 8/0 | One dedicated connection per actor thread |
| Django 5.2 ORM, client-side binding (default) | SUPPORTED, LIVE-TESTED (trusted fixture) | The same tests; the merged statement text is compared by digest | |
| Django `QuerySet.iterator()` (server-side cursor) | REFUSED; supported with `DISABLE_SERVER_SIDE_CURSORS` | `test_mvp_django_live.py` | Django builds `ServerSideCursor` directly in client mode; the backend refuses named cursors |
| Django reopened connection, `CONN_MAX_AGE=0` across requests | REFUSED (second connection) | same | Persistent connections (`CONN_MAX_AGE=None`) on one session: supported |
| Django `OPTIONS["pool"]`, wrong target, non-default isolation level | REFUSED | same | |
| Raw `pgconn` calls, connections a handler opens itself, direct cursors | CAUGHT by the server log (refuted) | sync, async and Django tests | |
| A str parameter the server coerced to uuid (Chancy 0.25.1 sends `uuid.hex`) | COMPARED by canonical or 32-hex form | `test_mvp_corroborate_offline.py` (57 vectors in all) | Any other str-to-uuid difference: UNVERIFIED (`str_coerced_uuid`), counted, never accepted |
| Typed parameters: int, NULL, bool, str, bytea, 1-D arrays, json/jsonb, timestamptz (by instant), timestamp/date/time/timetz (by wall clock and offset), interval (by component), uuid (by digest) | COMPARED EXACTLY | `test_mvp_corroborate_offline.py` (52 vectors at increment 5); live in two session time zones | `infinity`, BC dates, intervals with months and other IntervalStyles: UNVERIFIED, counted, never accepted |

## Isolation

| Mode | Status |
|---|---|
| Restricted container: runc, internal network, uid 65534, read-only root, all capabilities dropped, PID and memory limits | SUPPORTED for trusted code and pinned, reviewed third-party code |
| Hostile-grade isolation (gVisor, microVM) | BLOCKED: only runc is available (`isolation_runtime_scan_2`). No unreviewed application code is run |
