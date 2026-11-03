# Gate 2 hardening checklist

For the next implementation task. Nothing here is implemented, and no LangGraph or other external code may run until every item is done. Evidence for each problem is in `GATE2_AUDIT.md`. Protocol version 1.2 applies.

Rules for all items:

- Frozen modules (`database.py`, `scheduler.py` and the rest of the release engine) aren't edited. Fixes go in `src/deployspecter/live/` and wrap them.
- A check that can't run counts as a failed check. Missing evidence never becomes `completed`.
- Each item ships with a test that fails when the guard is removed, confirmed by a deliberate-breakage run with an unmodified control.
- New infrastructure outcomes are inconclusive and outrank application outcomes.

## 1. Restricted PostgreSQL login from connection creation

**Problem:** actors log in as admin and then `SET ROLE`, so raw driver access could `RESET ROLE`.

- [ ] Before each run, an admin session creates a run-specific non-superuser `LOGIN` role with a random password, and grants it membership in `deployspecter_runner`.
- [ ] Set `search_path`, statement, lock and idle-in-transaction limits, and `deadlock_timeout` (superuser-only) with `ALTER ROLE ... SET`, so `SET ROLE` is no longer in the session.
- [ ] `open_session` connects directly as that role. Admin credentials and the admin DSN never reach an actor thread. Clear `DATABASE_URL` and `PG*` from what application code can read.
- [ ] Set `CONNECTION LIMIT` to the actor count. **This is defence in depth, not a capture guarantee.** It doesn't see statements on a registered session, connections under any other role, or a bypass that fits within the limit.
- [ ] Revoke, drop and verify the role in teardown, on every path. Keep the password out of traces, logs and evidence.

**Done when:**
- [ ] From a raw `conn.pgconn.exec_`, `RESET ROLE`, `SET ROLE` to admin, and DDL all give no extra privilege.
- [ ] `current_user`, `session_user`, `rolsuper` and all limits are asserted.
- [ ] A connection over the limit is refused, and the run is `unsupported`.
- [ ] Plain-connection controls use the same role setup, so instrumented versus plain stays a fair comparison.

## 2. Detection of unmanaged sessions and statements

**Problem:** measured, an own-connection write changed committed data while the run reported `completed`.

- [ ] Keep a registry of expected backend PIDs for actors and the harness. Before the run, every connection in the target object must be a registered `SteppedConnection`. Refuse `ConnectionPool` and a non-`None` pipe.
- [ ] Require the `pg_stat_database.sessions` change to equal the expected count for the run, or mark it `unsupported`.
- [ ] Check `pg_stat_activity` at each decision and at the end. An unregistered backend on the test database marks the run.
- [ ] Add outcome `unmanaged_activity`, inconclusive. Never score the final state of a marked run. Rebuild before the next.

**Done when:**
- [ ] Canaries are flagged: own connection (read and write), `AsyncConnection`, new thread, subprocess.
- [ ] A soak of at least 1,000 clean runs has zero false flags, with autovacuum and background worker behaviour established rather than assumed.

## 3. Fail-closed server-log reconciliation

**Problem:** session counts miss raw `pgconn` calls and parent-class `wait`. Only the log caught them, in a 9-run probe.

- [ ] Run external work only on a dedicated disposable cluster with `log_connections`, `log_disconnections`, `log_statement=all`, and a prefix carrying timestamp, PID and application name.
- [ ] Turn the audit's reconciler into a real component, with these upgrades:
  - Use a unique per-run marker. A reused container previously produced false flags.
  - Parse multi-line statements. The probe's parser reads single lines only.
  - Compare logged parameter values where the driver path allows, because the probe compares text and count only.
  - Account precisely for psycopg's implicit `BEGIN`, savepoint exit pairs and session setup.
- [ ] Mark `log_unverified` (inconclusive) if the log is missing, truncated, unparsable, doesn't contain both markers, or `logging_collector` or settings aren't as required.
- [ ] Any unknown session, or unexpected or missing statement, makes the run `unmanaged_activity`.
- [ ] Use a preflight canary: a known statement must be found in the log before a run starts.

**Done when:**
- [ ] Each canary from item 2, plus raw `pgconn.exec_` and parent-class `wait`, is flagged.
- [ ] A bypass repeating the exact text of an expected statement is flagged by count.
- [ ] Deleting or truncating the log makes the run inconclusive.
- [ ] Clean multi-actor runs with savepoints, retries, rollbacks, deadlocks and opaque batches have zero false flags.

## 4. Unknown external lock-holder detection

**Problem:** a lock held outside the run is recorded as `external` but doesn't mark it, and could produce a false settle.

- [ ] Any blocker PID that isn't a registered actor makes the run `external_interference`, inconclusive, with the blocker's `backend_type` and application name recorded.
- [ ] Handle autovacuum explicitly: disable it for run tables or the disposable cluster, and treat any remaining autovacuum blocker as interference.
- [ ] Handle PID 0 (prepared transactions) as external, as `pg_blocking_pids` documents it.

**Done when:**
- [ ] Tests cover an outside session holding a row lock, a table lock and an advisory lock, a prepared transaction, and a lock taken during a run. Each is inconclusive, and none is `completed`.

## 5. Guaranteed shutdown or isolation of surviving actor threads

**Problem:** Python threads can't be killed. Today a stuck thread is reported, and its runner refuses further runs, but it stays inside the process.

- [ ] Run each scenario in a child process that holds the controller and actor threads. A parent process holds the watchdog and evaluates results.
- [ ] Escalate on abort, with bounded steps: abort flags, then `cancel_safe`, then `pg_terminate_backend` from the admin session, then join, then `SIGKILL` of the child.
- [ ] Stop closing a connection from the controller while an actor thread may be inside `wait`, whose behaviour isn't established. Terminate the server session first and let the thread fail.
- [ ] Confirm from the parent that all run backends are gone and the role is dropped, before any state is read as final.
- [ ] Make `sessions_left_open > 0` inconclusive. It's only reported today.
- [ ] Results cross the process boundary as validated data. A killed child produces no result except `harness_killed`.

**Done when:**
- [ ] A Python infinite loop, a C-level blocking call, a stuck statement and a thread spawned by the application all end within the bound, leave no session behind, and the next run is clean.
- [ ] A worker spawned by the application that survives is detected as unmanaged. If it holds a connection, the run is inconclusive.

## 6. Lost-cancel and unexpected-disconnection testing

**Problem:** cancellation is sent once, and could be lost. Disconnections are classified only at the surface.

- [ ] Add a test-only fault hook to lose or delay a cancel, including just after a grant and before the statement reaches the server.
- [ ] Inject `pg_terminate_backend` and a dropped TCP connection (through a local test proxy) at: a gate, in flight, blocked on a lock, idle in transaction, and **during `COMMIT`**.
- [ ] Add `effect_unknown`. A connection lost during `COMMIT` can't tell whether it committed. Record that, make the run inconclusive, and never infer the effect from transaction status.
- [ ] Verify that application `except` clauses can't turn a connection loss into a scored `completed` run.
- [ ] Add a server restart mid-run: the outcome is inconclusive, and the cluster is rebuilt or the batch is stopped.

**Done when:**
- [ ] Every injection point gives an inconclusive outcome within the bound. Committed state read afterwards agrees with what the trace claims, or the trace says `effect_unknown`.

## 7. Bounded database reset and teardown

**Problem:** `Sandbox.reset` has no `lock_timeout` on its admin session, so `DROP SCHEMA` can hang behind an outside lock. That file is frozen.

- [ ] Wrap reset, snapshot and teardown in a live-only layer that applies statement and lock limits to the admin session and restores them. Before reset, it terminates leftover sessions on the test database.
- [ ] Add a limit for connecting and for the whole reset. A breach is `infrastructure_failure`, which stops the batch rather than scoring a run.
- [ ] Verify the database name, schema and role before each destructive step, as the sandbox does for the test-database suffix.
- [ ] Offer one fallback: recreate the disposable cluster. Never widen any destructive step beyond it.
- [ ] Make `Sandbox` advisory-lock acquisition failure a clear stop.

**Done when:**
- [ ] An outside lock on a run table, a session idle in transaction, and an unresponsive server each end reset within the bound, with a named outcome.
- [ ] Frozen files are unchanged, and the public reproduction still gives 63 of 63.

## 8. Opaque `executemany` checkpoint semantics

**Problem:** the prototype refuses `executemany`. Repeated `execute` is wrong under autocommit, and an opaque batch hides real interleavings.

- [ ] Implement only Option A, as kind `opaque_batch`, with a per-run opaque count. Option B stays refused, and `conn.pipeline()` blocks stay `unsupported`.
- [ ] Keep psycopg's own call, with no reimplementation. Count parameter sets by wrapping the iterator, without consuming it early, so laziness is preserved.
- [ ] Record effects by case:
  - Explicit transaction: pending, then resolved by the commit or rollback.
  - Autocommit with a server error: nothing committed.
  - **Autocommit with a client-side error: `effect_unknown`, inconclusive.** Row 1 was measured as committed in that case.
- [ ] Handle `returning=True` without disturbing `nextset`. If that can't be proved, refuse it.
- [ ] Any run with an opaque step reports it, and can't claim complete schedule coverage, per protocol section 6.
- [ ] Teach the reconciler that one opaque step corresponds to exactly the counted number of logged statements.

**Done when:**
- [ ] Instrumented and plain agree on committed state, exception, row count and results across the audit's 44 cells.
- [ ] A blocked batch lets the lock holder finish.
- [ ] A test records that the torn state `[0, 1]`, which is real on plain psycopg, is **not** reachable, so the limit stays visible.

## Exit gate, before any LangGraph work

- [ ] All 8 items are complete, with their tests.
- [ ] A deliberate-breakage run catches every guard's removal, with an unmodified control passing.
- [ ] The soak has zero false flags.
- [ ] The full offline suite, Ruff and public reproduction pass.
- [ ] Results are reported, including anything that failed. The next step gets its own approval.

## Not in this work

LangGraph integration, any external application, Phase 9B integration, Muse requests, held-out access, publication, and any change to the frozen protocol other than a numbered amendment.
