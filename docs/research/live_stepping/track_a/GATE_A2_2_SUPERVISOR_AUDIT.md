# Gate A2.2: auditing Gate 2A's supervisor without bypassing it

**Status: offline only, PARTIALLY_COMPLETE. Gate A2 is not executable.** `execution_manifest_v4.json` keeps all eight blockers open, and `GATE2A_SECTION_IMPLEMENTED` is still `False`. Nothing here connects to a database, starts a container or calls a model.

The supervisor isn't replaced, patched or bypassed. Gate A2 adds no session inside its protected window. Everything below is either read from PostgreSQL's log after the supervisor has returned control, or reported as unobservable.

## 1. One supervised run, from the frozen code

`SupervisedRunner._execute` and `_run_child` (`supervisor.py:622-690`) run on the Sandbox's admin session (`database.py:67`) unless the row says otherwise. "Logged" means `log_statement=all` writes the statement text and its parameters. Query results are never logged.

| Step | Safety guarantee | What the log shows |
|---|---|---|
| preflight: 10 × `current_setting($1, true)`, the override count | logging and disposable-cluster settings hold before any reset | statements and names logged; **values unlogged** (the supervisor judges them) |
| hardening, once per runner: `pg_database`, then `REVOKE ALL ON DATABASE … FROM PUBLIC` for each | only the run role, through its own grant, can connect | logged, one row per database |
| stale-role sweep (`ALTER ROLE … NOLOGIN` for each) | no earlier run's role can log in | query logged; any `ALTER ROLE` logged |
| outside-session sweep (`_ACTIVITY_SQL`, then `pg_terminate_backend` for each, then repeat) | **no client session but the supervisor's own exists at the reset** | the poll and the excluded pid set logged; every termination logged with its pid |
| prepared transactions (`pg_prepared_xacts`, `ROLLBACK PREPARED`) | none survive into the run | logged |
| reset limits (`SET lock_timeout`, `SET statement_timeout`) | the reset can't hang | logged with values |
| `Sandbox.reset`: DROP and CREATE SCHEMA, `search_path`, BEGIN, setup SQL, seed INSERTs, COMMIT, 3 GRANTs | the schema is rebuilt from the pinned setup and the bridged seed only | **every statement and every seed parameter logged**; an error would add an ERROR row |
| limits restored | — | logged |
| initial snapshot (`information_schema.tables`, then `SELECT * FROM <table> ORDER BY 1`) | the state after the reset is read before any run session exists | statements logged; **values unlogged** |
| log position, `SELECT 'ds_mark_start_<run>'`, session counter | Gate 2A's own reconciliation window begins | logged, with the run id |
| role creation: `SET log_statement='none'`, `CREATE ROLE … PASSWORD …`, `RESET log_statement` | a non-superuser login for this run only; the verifier stays out of the log | **only the SET is logged** (B2) |
| role configuration: `GRANT CONNECT`, 5 × `ALTER ROLE … SET` | the role gets exactly the reviewed settings | logged, with role name and values |
| child process (`_spawn`) | a separate process with a clean environment and only this login | the child's sessions are logged (role, application name `ds:<run>:<name>`) |
| supervision: activity polls, one registration check, escalation (cancel, terminate, kill) | only registered sessions; deadlines enforced | polls, registration pids and every cancel or terminate logged |
| teardown: `ALTER ROLE NOLOGIN`, kill, sweep, prepared transactions, quiescence poll | nothing of the run can commit afterwards | logged; terminated pids logged |
| final snapshot | the state after teardown | statements logged; **values unlogged** |
| `DROP OWNED`, `DROP ROLE`, `pg_roles` count, counter | the role is gone | logged; the count's **value unlogged** |
| `SELECT 'ds_mark_end_<run>'`, log reads, `serverlog.reconcile` | the run's own sessions reconciled | logged; Gate 2A skips its own sessions' rows by design |

Construction (`Sandbox.__init__`) does the following:

1. `ensure_test_database` opens a session on the initial database. It runs `SELECT 1 FROM pg_database WHERE datname = $1`, then `CREATE DATABASE` if that finds nothing, then a `DO` block that creates `deployspecter_runner` if it is missing.
2. It opens the admin session: `SET lock_timeout`, `pg_advisory_lock(7420001)`, `SET lock_timeout = 0`.
3. It opens the monitor session.

## 2. B1: a strategy that doesn't bypass the supervisor (feasible; implemented offline)

`gate2a_audit.AdminRun` holds the admin session's rows for one call to a **declared template**:

- exact statement text;
- exact parameters, from the manifest, the prepared scenario and the run id;
- exact order.

Variable parts are allowed only within strict rules:

- **polls** carry exactly the supervisor's own pids and stay under a bound computed from the manifest's limits;
- **registration** carries exactly the trace's registered pids, in order;
- **escalation** is exactly the steps the trace records, one statement per actor pid;
- **teardown terminations** are of the run's own pids only;
- **log reads** all start from one offset.

Any extra, missing, reordered or misparameterized statement is REFUTED, and the phases after it become INDETERMINATE. So is a prepared name or a non-LOG row.

The template is checked in two ways:

- **independently:** the frozen constants (`_ACTIVITY_SQL`, the `REQUIRED_SETTINGS` order, the runner-role `DO` block in `database.py`) are compared with the template's own text;
- **end to end:** the real `Sandbox.__init__` and `SupervisedRunner.run` run against a fake server, and all 127 admin statements of two runs match the template phase by phase, as do the 3 of the bootstrap. Hardening appears only in the first run.

**Not established:** that a real PostgreSQL 16 log of a supervised run agrees. That claim is NOT_TESTED.

## 3. Per-run initial-state assurance: what can and can't be shown

The invariant is that the state verified for run R is the state Gate 2A's reset produced, as it stood before the worker ran. The log and the trace together support these claims, each reported separately for every run:

| Claim | Status | Basis |
|---|---|---|
| the reset's statements, parameters and COMMIT give exactly the manifest's reviewed `initial_rows` | VERIFIED | logged seed parameters, compared with the manifest rather than with the scenario that produced them |
| no other session wrote between the reset and the snapshot | VERIFIED | every session in the gap is attributed |
| the snapshot was taken after the reset and before the run's first session | VERIFIED | log order |
| the snapshot's **values** | **UNOBSERVABLE** | results aren't logged, and no session may open in the window to re-read them |
| the supervisor's snapshot equals the construction batch's independently verified digest | VERIFIED, as a **trusted-control-plane** read | an in-process read by the supervisor, compared with a reconciled digest |
| the state is unchanged between the snapshot and the worker's first statement | **UNOBSERVABLE** | other sessions are excluded, but the admin session's unlogged role-creation interval lies between them (B2) |

**Conclusion.** Post-reset initial-state assurance is **not proven independently**. The log shows exactly what the reset wrote, and shows that nothing else wrote. The values come from the supervisor's own read. The B2 interval lies between that read and the worker.

## 4. B2: the unlogged role creation

**What the code does** (`_create_role`, `supervisor.py:248-260`):

- `SET log_statement = 'none'` is an ordinary session-level SET, on **the admin session only**. It isn't global, `ALTER SYSTEM` or `SET LOCAL`. The SET itself is logged, because logging is decided before a statement runs.
- **Interval:** from just after that SET until `RESET log_statement` completes.
- **Unlogged statements:** exactly two, `CREATE ROLE "ds_run_<run>" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '<SCRAM verifier>' CONNECTION LIMIT n IN ROLE deployspecter_runner` and the `RESET`.

**Paths:**

- If `CREATE ROLE` fails, the `finally` still runs the `RESET`. The failure is still logged as an ERROR row, because error logging doesn't depend on `log_statement`.
- If the `RESET` fails, or a bounded call times out after the SET, logging stays off on the admin session. The template then refutes the run, because the next logged admin row isn't this run's `GRANT CONNECT`. A fake-server test exercises the failing `RESET`.
- `_create_role` and `_configure_role` run **before** the `try` that guarantees teardown (`supervisor.py:667-678`). An exception or interrupt there skips `_tear_down` and `_drop_role`, which can leave a login role behind. The next runner's stale-role sweep would disable it. Gate A2 stops the batch either way.

**Other sessions.** They keep logging, and connection records don't depend on `log_statement`. Any other session, and any SET it issues, is therefore visible. Only the admin session's own statements in the interval are unseen. In the pinned source that is one statement, sent through the supervisor's single-threaded executor.

**Catalog corroboration:**

- **During the run:**
  - the supervisor's registration query (logged; its result unlogged);
  - the child's privilege check, which is untrusted, checked by the supervisor, logged as text, with its values unlogged.
- **After the run:** `DROP ROLE` is logged, and `role_dropped` is in the trusted trace.
- **Missing:** no independent before-and-after read of the role catalog exists yet.

**What can be pinned:** the source, by digest. Every parameter except the verifier, which is random and must stay secret, is derivable: the role name from the run id, the connection limit as actors + 1, the fixed attributes and `IN ROLE`.

**The two options. The audit doesn't close B2.** It reports the interval as UNOBSERVABLE, and a batch with any UNOBSERVABLE claim is never classified as "all SQL verified".

| | Option A: a disclosed trusted-control-plane exception | Option B: a reviewed supervisor change |
|---|---|---|
| What | Accept the interval as unobservable, within the pinned source. Add reconciled before-and-after reads of the role catalog (`pg_roles` attributes, `pg_auth_members`, `pg_db_role_setting`) in the segments around each run, and require no lasting change | A new, separately reviewed Gate 2A version. `CREATE ROLE … NOLOGIN` with every attribute, logged. Then only the password set (`ALTER ROLE … LOGIN PASSWORD`) inside `BEGIN; SET LOCAL log_statement='none'; …; COMMIT`, which resets itself even if the client never sends `RESET`. Then a logged attribute check |
| Safety boundary | unchanged | unchanged: the role is still non-superuser and the verifier still unlogged. A frozen-code change needs its own review and freeze |
| Evidence | "role creation unobserved; no lasting role or privilege change detected" — a narrower claim. A change made and undone inside the interval stays undetectable | attributes observed; the unlogged part shrinks to one password statement. The "logging left off" failure mode is removed |
| Cost | a new reconciled operation tag (Gate A1's reconciler accepts only its ten operations) and reviewed statements | a Gate 2A change, re-review, and new characterization |
| Status | proposed; NOT_TESTED | proposed; not implemented (frozen code isn't modified) |

## 5. B3: the segmented reader (implemented and tested with fakes; not characterized)

`SegmentedRun` (in `gate_a2_run.py`, not reachable from the command line) works like this:

1. Each segment is an ordinary Gate A1 batch: Gate A1's own batch markers, one reader, Gate A1's audit.
2. Before each Gate 2A call, the segment closes and its reader disconnects.
3. After the call returns or raises, a **new** reader connects and starts the next segment.
4. It reads the log from the batch's first offset, carried forward explicitly as `ServerLog.path` and `offset`. It waits, boundedly, until its own start marker is in a complete log, then audits the gap.

No new kind of marker is invented. The gaps are bounded by Gate A1's markers. Inside a gap, Gate 2A's own `ds_mark_start_<run>`, `ds_mark_end_<run>` and `ds_run_<run>` role name bind the rows to the trace's run id.

**In every gap:**

- **The old reader.** It may only read the log file, then disconnect. The disconnection must be logged before Gate 2A's first row. If the reader was terminated by the sweep, that is REFUTED. If its disconnection is missing, that is INDETERMINATE.
- **The new reader.** Its connection records must follow Gate 2A's last row.
- **The admin session** matches the template.
- **The monitor** is silent.
- **The children** are inside their run's window, with registered pids and names.
- **Anything else** is unattributed and REFUTED.
- **Afterwards:** every session's line numbers across the batch must be consecutive. A truncated final row or a missing marker refuses.

**Why log order is the evidence.** PostgreSQL writes the disconnection record from an exit callback that runs after the backend has left shared memory. This comes from reading PostgreSQL's source and isn't characterized. So a disconnection logged before the supervisor's sweep means the reader couldn't be seen by that sweep. Independently, the sweep's own logged `pg_terminate_backend` names any session it found.

**Disconnecting doesn't prove completeness.** Completeness rests on the next segment's start marker being in a complete log, on consecutive line numbers, and on the batch-end check.

**Results:**

- the clean case passes;
- a worker timeout and escalation are attributed exactly, then the batch stops;
- a reader left connected is refuted, through the sweep's termination;
- a reader that can't reconnect stops the batch with no retry;
- an incomplete flush is refused after a bounded wait;
- a failed reset, an incomplete teardown, a dropped role that remains, and an interrupt all stop the batch. An interrupt stays an interrupt once the evidence is read.

**Limitation:** `plain_serial` returns no run id and no registered sessions, so SegmentedRun refuses it. A plain call's gap could only be bound to a run id read from the log being audited.

## 6. B4: the template matcher and the coverage report

`gate2a_audit.Coverage` gives every claim one of VERIFIED, UNOBSERVABLE, REFUTED, INDETERMINATE or NOT_TESTED, and classifies the batch:

- REFUTED, or INDETERMINATE: both fail closed;
- `OBSERVABLE_SQL_VERIFIED_WITH_DISCLOSED_GAPS`;
- `ALL_SQL_VERIFIED`: reachable only with no UNOBSERVABLE and no NOT_TESTED claim, so never for a batch with Gate 2A runs.

A clean simulated batch of two runs gives:

- **48 VERIFIED**;
- **8 UNOBSERVABLE:**
  - in each of the two runs, role creation, the snapshot values, and the interval between the snapshot and the worker;
  - at bootstrap, the runner role's attributes, and everything before the batch;
- **3 NOT_TESTED:**
  - agreement with a real log;
  - the log form of integer-array parameters;
  - Option A's catalog check.

## 7. B5: the preparation policy

`Sandbox` opens its admin and monitor sessions with `psycopg.connect(test_dsn, autocommit=True, connect_timeout=5)`. That uses psycopg's default preparation (prepare after 5 executions), and the threshold isn't a libpq parameter, so the DSN can't set it. Under fakes, the frozen supervisor's very first run logs `execute _pg3_0` at its 6th settings read, and the matcher refuses it. No prepared name is ever allowlisted.

There are three ways to control it without changing frozen code:

1. **Set the public `prepare_threshold` attribute** on the two sessions after construction (`gate_a2_controls.apply_harness_policy`). It changes how statements are sent, not what they do, and the clean fake batch uses it. It is not adopted: it alters frozen sessions at run time and needs review and a separately approved characterization.
2. **Predict the names.** `gate2a_prepare_model` drives psycopg 3.3.6's own `PrepareManager` and query conversion. It reproduces all 51 statements of the real version 2 log reader exactly, including the switch to `_pg3_0`, `_pg3_1` and `_pg3_2`.
   - The admin session also sends DROP and ALTER, which clear psycopg's cache and log `DEALLOCATE ALL`. That logic is taken from psycopg's source but not validated against a real log.
   - The keys depend on the adapter-chosen parameter types: an integer's size changes the key.
   - This is feasible for a future reviewed audit, but more fragile than option 1.
3. **A process-wide default** (`PrepareManager.prepare_threshold`), which would patch the library for every connection. Rejected.

## 8. B6: bootstrap and hardening

`audit_bootstrap_gap` holds Sandbox construction, made in a gap of its own, to the template:

- **The ensure session:** the existence check with the test database's name, and the `DO` block. `CREATE DATABASE` is refuted, because the manifest requires the database to exist already.
- **The admin session:** its advisory lock.
- **The monitor session:** nothing.

Hardening is matched in the first run's gap against the manifest's database list.

**Unobservable:**

- the runner role's attributes after the `DO` block, which logs as a single statement;
- the container start, initdb and the operator's `CREATE DATABASE`, which come before the batch's first log position.

## 9. Tests and mutation testing

| Suite | Result |
|---|---|
| `test_gate_a2_2.py` | **68 tests** under fakes, positive and negative |
| `test_gate_a2_1.py` | 104 tests, now against manifest version 4 |
| Isolated mutation testing, Gate A2.2 | **48 of 49 mutants killed by test failures**, plus 1 equivalent with its justification recorded. A first pass left 5 survivors, each a missing test that was then added |
| Gate A2.1's 40 mutants, re-run | 40 killed |

The mutation runs had no timeouts, and the worktree was unchanged.

## 10. Remaining blockers

They are listed in `execution_manifest_v4.json`.

| Blocker | Status |
|---|---|
| B1 | Open: implemented offline, not characterized; snapshot values unobservable |
| B2 | Open: needs an owner decision between options A and B |
| B3 | Open: implemented offline, not characterized |
| B4 | Open: implemented offline, not characterized |
| B5 | Open: needs a decision and a separately approved characterization |
| B6 | Open: pre-batch operations unobservable |
| B7 | Open: Amendment 002's manual confirmations |
| B8 | Open: a signed approval |

## 11. The smallest next evidence-producing step

The owner decides B2 (option A or B) and B5 (option 1 or 2).

Then comes a separately approved, Gate A1-style characterization on the disposable cluster. It would:

- construct the Sandbox and make **one** supervised run of a trivial scenario, inside SegmentedRun;
- record the real log;
- check whether the template matches;
- check the integer-array parameter form;
- check whether the disconnection-before-sweep ordering holds.

That single run turns the NOT_TESTED claims into observed ones. It still doesn't make Gate A2 executable.
