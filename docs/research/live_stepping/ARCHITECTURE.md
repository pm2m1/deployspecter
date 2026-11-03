# Driver-level application stepping: architecture

Research prototype, separate from the frozen hackathon release. It is an opt-in layer over psycopg 3 that runs real, synchronous Python functions under controlled PostgreSQL interleavings. No frozen module was edited, and nothing in the release imports this code.

## Why the existing engine isn't enough

`ScheduleRunner` executes an `ExecutableSpec`, which is a fixed list of SQL steps whose parameters are literals or simple row references. Python between statements is modelled, not run. That has two consequences this prototype removes:

- A function that branches on what it reads runs a different number of statements in different interleavings. `enumerate_schedules` needs step counts in advance, so it can't describe that.
- Any computation the step format can't express (a retry, a calculation, a loop) isn't exercised at all.

## What is reused, and what needed an adapter

| Existing component | Decision | Why |
|---|---|---|
| `database.Sandbox` | Reused as is | Test-database check, advisory lock, schema rebuild, `snapshot`, `blockers`, monitor connection |
| `Sandbox.actor_connection` | Same setup, new function `open_session` | It hard-codes the plain connection class and autocommit. The order of the session settings is copied, and a test asserts the resulting role and limits |
| `scheduler.ScheduleRunner` | Not reused | It pulls steps from a plan; here actors push requests at run time. The settle idea (wait until everything has finished or is blocked according to `pg_blocking_pids`) is carried over |
| `scheduler.enumerate_schedules`, `preemptions` | Not reused | They need step counts. `live/explore.py` discovers the tree by running, keeping fewest-preemptions-first |
| `explorer.explore` | Not reused | Same reason. Budget and reporting concepts are kept |
| `replay` | Not reused | Replay is now a strict list of decisions, compared by fingerprint |
| `models.ExecutionTrace`, `StepRecord` | Not reused; `live/trace.py` is separate | The oracle reads `Step` semantics that no longer exist. `SeedRow` is reused |
| `oracle` | Not reused yet | Its reference models read planned parameters. This slice's requirements are plain functions over committed state. An adapter is needed before existing scenarios can run live |
| `validation.compile_proposal` | Not reused | It checks statement ids against extracted source. `live/workload.py` plays the equivalent trust-boundary role for entrypoints |
| Phase 9A/9B (`state_constructor`) | Not merged | Interface only: unresolved `SeedGoal` values would become its input |

## Components

| File | Role |
|---|---|
| `src/deployspecter/live/driver.py` | `SteppedConnection`, `SteppedCursor`, `SteppedTransaction`, `ActorChannel`, statement classification |
| `src/deployspecter/live/runner.py` | Controller, `LiveRunner`, `ExplicitSchedule`, `open_session`, uninstrumented serial control |
| `src/deployspecter/live/trace.py` | `LiveTrace`, `OpRecord`, `Decision`, outcomes, fingerprint |
| `src/deployspecter/live/explore.py` | Systematic exploration of the discovered decision tree |
| `src/deployspecter/live/workload.py` | Typed workload proposal and its validator. No model call |
| `tests/live_app/` | The sample application and its independently stated requirements |

## Event and control protocol

Each actor is a daemon thread with its own connection. It calls `entrypoint(conn, **kwargs)` with an injected `SteppedConnection`.

```
actor thread                                   controller (one thread)
------------                                   -----------------------
Python runs                                    state: python
reaches a database call
  OpRequested(kind, sql, params, provenance) →  state: gate
  waits for its grant                          waits for quiescence, then decides
                                             ← grant (or abort)
  real driver call                             state: in_db
                                               if unfinished: asks pg_blocking_pids (monitor session)
  OpCompleted(result or exception)           →  state: python
  query result returns to the application
... function returns or raises
  ActorFinished                              →  state: finished
```

**Quiescence** is when every actor is at its gate, finished, or has a statement PostgreSQL reports as blocked. Only then does the controller choose one actor at the gate to admit, and it records a `Decision` (who could run, who was blocked and by whom, who was chosen).

**Lock waits.** A blocked statement stays in flight while others are admitted. If nothing is at a gate and statements are still blocked, the controller waits for PostgreSQL to end it: a commit, its deadlock detector or a timeout. The controller never concludes that a deadlock exists.

**What is not serialized.** Transactions overlap freely, and a blocked statement runs alongside others. What the controller refuses is starting two statements at the same instant, because their order would then be irreproducible. Consequence: slow statements hold up the next decision, and races inside PostgreSQL between two statements that are both actively running are only reached through lock-wait orderings.

**Result fidelity.** `execute` buffers the whole result client-side, so recording it takes a copy and restores the cursor position and row factory. The application then reads the real result in the usual way.

**Transactions.** After each operation the driver's own transaction status decides what happened. This is how `COMMIT` of a failed transaction is correctly recorded as a rollback. Each write is marked `committed`, `autocommitted`, `rolled_back` or `savepoint_rolled_back`. A function that returns with a transaction open gets a scheduled `implicit_rollback`, because releasing its locks is visible to others.

**Abort.** `RunAborted` derives from `BaseException`, so `except Exception` can't absorb it. A stopped run never commits, even if the application swallows the abort inside a transaction block. Statements in flight are cancelled. Sessions are always closed and their disappearance is confirmed through `pg_stat_activity`. A thread that won't stop is reported and makes the runner refuse further runs.

## Outcomes

`completed`, `python_exception`, `execution_error`, `deadlock` and `serialization_failure` are results about the application.

`lock_timeout`, `statement_timeout`, `idle_in_transaction_timeout`, `connection_lost`, `unsupported`, `schedule_divergence`, `python_stall`, `statement_watchdog` and `run_timeout` are **inconclusive**: no conclusion about the application follows from them. Harness conditions outrank application errors in the same run. An error the application catches and handles stays in the trace but doesn't change `completed`.

## Supported driver paths

| Path | Scheduling |
|---|---|
| `conn.execute`, `cursor.execute` (client-side), including `Composable` SQL | One step |
| `fetchone`, `fetchmany`, `fetchall`, iteration, `scroll`, row factories | No step; the data is already buffered |
| `conn.commit`, `conn.rollback` | One step |
| `with conn.transaction()`, including `force_rollback` and `psycopg.Rollback` | One step on entry and one on exit |
| Nested `conn.transaction()` (savepoints) | One step each. `ROLLBACK TO` and `RELEASE` on exit are one step |
| `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`, `ROLLBACK TO` as text | One step, classified |
| Implicit `BEGIN` on a non-autocommit connection | Part of the first statement's step |
| `SET LOCAL`, `SET TRANSACTION`, `SET CONSTRAINTS` | One step |
| Isolation level and autocommit fixed when the actor is declared | Applied before the run |

## Opaque batches

`executemany` runs as one `opaque_batch` step using psycopg's own code. Nothing inside it is individually scheduled, so a run containing one can't claim statement-level or complete schedule coverage. See `GATE2A_REPORT.md`.

## Unsupported driver paths

Each is refused with a reason, and the run becomes `unsupported`. It's never silently run outside the schedule.

| Path | Why |
|---|---|
| `cursor.stream` | Keeps a result open across scheduling points |
| `cursor.copy`, `COPY` text | Not modelled |
| Pipeline mode | Batches statements below the scheduling boundary |
| Server-side cursors, `DECLARE`/`FETCH`/`MOVE`/`CLOSE` | State held across scheduling points |
| Several statements in one `execute` | Would be one atomic scheduling step |
| `LISTEN`, `NOTIFY`, `conn.notifies` | Cross-session signalling isn't modelled |
| Two-phase commit | Not modelled |
| `SET` of session settings, `RESET`, `DISCARD`, `SET ROLE`, `SET SESSION AUTHORIZATION` | Would change the sandbox's limits or role |
| Changing autocommit or isolation during a run | Goes through `Connection.wait` with no grant |
| `conn.close`, `conn.cancel`, `conn.cancel_safe` from application code | The harness owns the connection's lifetime |
| A connection shared between threads | One actor owns one connection |
| Directly built cursors (`ClientCursor(conn)`, `RawCursor(conn)`) | Caught by the traffic guard in `Connection.wait` |
| A database call made from inside another (a dumper that queries) | Would deadlock the protocol |

**Not detected, and therefore not safe to assume:**

- `AsyncConnection` is simply a different class, so async application code can't take part.
- `psycopg_pool` isn't integrated. A pool hands out connections the harness didn't inject.
- A function that opens its own connection escapes instrumentation with no error, because nothing is patched globally. Measured: such a write changed committed data while the run reported `completed`. `SupervisedRunner` now flags this. `LiveRunner`, the in-process engine, still can't see it, and its traces are never scoreable.
- Raw `conn.pgconn` calls and `psycopg.Connection.wait(conn, ...)` also escape the traffic guard.
- Other processes (workers, Celery) are out of reach.
- On macOS a supervised child is confined by `sandbox-exec`; see `GATE2A_REPORT.md`. Elsewhere it isn't.
- Python-level blocking (a lock, queue or sleep) isn't scheduled. If it stops an actor reaching a gate, the run ends as `python_stall`.

## Reproducibility bounds

A replay of the same decisions reproduces the same fingerprint, measured in `evidence/slice_results.json`, unless one of these applies:

- **Deadlock victim.** PostgreSQL's detector chooses it, with or without this tool. See `GATE2_AUDIT.md`, section 3. Over 200 strict replays of one deadlock, the outcome class reproduced every time, and the trace and final state reproduced only when the victim matched.
- **Several waiters on one lock.** PostgreSQL chooses who wakes. Three actors matched in 100 of 100 replays in one run and 99 of 100 in the post-commit evidence run. Four actors varied in 16% to 29% of runs, and in plain psycopg too. Exact replay of multi-waiter wakeups isn't guaranteed.
- **Application nondeterminism.** Random values, wall-clock logic and unordered iteration aren't controlled.
- **Timestamps** are masked by `normalize`. **Sequence** values are reproducible only because each run rebuilds the schema.
- **Operations that finish under the same decision have no recorded order.** An earlier version recorded their arrival order and failed replay about one time in five; that was a bug in the trace, now removed. Commit numbers within such a window put the just-admitted operation first, then the woken ones by admission order. That ordering is inferred, not observed.

A strict replay that can't follow its decisions reports `schedule_divergence` instead of continuing. A strict replay that completes isn't evidence of reproduction, because decisions can stay legal when the server chooses differently. Only fingerprint and state comparisons show it.

## Safety

Application functions run as the sandbox's `deployspecter_runner` role (a non-superuser, DML only) in `ds_run`, on a database whose name must end in `_test`. Statement, lock, deadlock and idle-in-transaction limits are set per session, and there is an overall run limit and a limit on Python between statements.

This isn't a sandbox for hostile code. Functions run in-process with the permissions of the DeploySpecter process, and the refusal of `SET ROLE` is a guard against accidents, not a security boundary. Only code a person has chosen to register may run here, and a real target's parameters and rows would appear in traces, so nothing from one should be published unreviewed.
