# Gate A2.3: one real characterization of the frozen supervisor, prepared but not run

**Status: offline only.** The package (`gate_a2_supervisor_characterization_manifest.json`, driver `research_tools/gate_a2_supervisor_characterize.py`) is **READY_FOR_SEPARATE_USER_AUTHORIZATION**. It runs nothing without a new approval file, signed by the owner, that names the manifest's SHA-256 and the phase `gate_a2_supervisor_characterization`.

Gate A2's buggy/fixed demonstration stays blocked: `execution_manifest_v5.json` keeps B1 to B8 open, and `GATE2A_SECTION_IMPLEMENTED` is `False`.

The characterization's only purpose is to observe the existing supervisor once, on a disposable synthetic cluster. It is:

- not the buggy/fixed demonstration;
- not an evaluation;
- not a detection;
- not held-out evidence.

Nothing is scored, and nothing is retried.

## 1. B2: option A, for characterization only

**What the log can bound.**

- **Start:** the interval begins after the admin session's logged `SET log_statement = 'none'`.
- **End:** it ends before that session's next logged row, which must be this run's `GRANT CONNECT ON DATABASE "deployspecter_test" TO "ds_run_<run id>"`. The two rows must be on consecutive session lines. A gap is REFUTED, and a missing bound is INDETERMINATE.
- **Session:** the Sandbox admin session, identified by the backend pid psycopg reports.
- **Role:** `ds_run_` plus the run id Gate 2A chose, as reported in `trace.evidence.run_id`.
- **Unlogged by source:**
  - `CREATE ROLE … LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '<verifier>' CONNECTION LIMIT <actors + 1> IN ROLE deployspecter_runner`;
  - `RESET log_statement`.

**The catalog checks (`gate2a_role_catalog`).**

- **Where and how:** reconciled, read-only parent reads, run in the reader segments only:
  - before the sandbox bootstrap;
  - after it, which is also before the run;
  - after the run.
- **Why the `op0p` tag:** Gate A1's reconciler accepts only its ten tags. The reads therefore run under `op0p`, with their own pinned statement set, `op0p_role_catalog`.
- **What they read:**
  - `pg_roles` attributes (super, inherit, createrole, createdb, canlogin, replication, bypassrls, connlimit);
  - `pg_auth_members`;
  - `pg_db_role_setting`;
  - `pg_database.datacl`.

**Permitted before/after state:**

- no `ds_run_*` role, membership, setting or database privilege;
- roles, memberships and settings identical before and after;
- the admin role still a superuser;
- `deployspecter_runner` exactly as its `DO` block declares;
- after the first run (hardening), PUBLIC has no CONNECT on any expected database;
- across a call that doesn't harden, database privileges unchanged.

**The only permitted claim** is "role catalog meets the before/after conditions". These stay UNOBSERVABLE: the role-creation SQL itself, and the run role's attributes and privileges inside the interval. A clean final catalog is not proof of correct intermediate execution, and a change made and undone inside the interval is undetectable.

**Failure paths, each tested under fakes:**

- CREATE ROLE failing;
- `RESET` failing (logging left off);
- a partial reset;
- a worker failure;
- a timeout;
- a lingering session;
- an operator interrupt.

Each stops the batch after one read-only "after" observation from the new segment. An interrupt during role setup leaves a LOGIN role, because `_create_role` and `_configure_role` run before teardown's `try`. That observation shows the role, and the claim is REFUTED. A missing observation is INDETERMINATE.

Option B, a reviewed supervisor revision, stays a separate proposal (GATE_A2_2_SUPERVISOR_AUDIT.md, section 4). **B2 is not closed.**

## 2. B5: option 1, feasible in the real code path

The following is shown from the real implementation, offline, in `tests/research_tracks/test_gate_a2_3.py::TestB5FromTheRealCode`:

- **psycopg's property.** `psycopg.Connection.prepare_threshold` is a documented public property. Its setter is the single assignment `self._prepared.prepare_threshold = value`: no call, nothing sent to the server.
- **`Sandbox.__init__`.** It assigns `self.admin` and `self.monitor` once each. It sends exactly three distinct statements, once each, all on `admin`: `SET lock_timeout = <n>`, `SELECT pg_advisory_lock(%s)` and `SET lock_timeout = 0`. It sends nothing on `monitor`. At psycopg's default threshold (prepare after the 5th execution of a key), none can have been prepared. That holds with psycopg 3.3.6's own `PrepareManager` too.
- **Nothing reassigns `admin` or `monitor`,** in either `supervisor.py` or `database.py`.
- **`SupervisedRunner.__init__` sends nothing,** and the supervisor reaches the sessions as `self.box.admin` at every call.
- **With the threshold set to None,** the real `PrepareManager` never names a statement or sends `DEALLOCATE`.

`SegmentedRun.bootstrap` applies it in the production research path, right after `Sandbox(...)` returns and before the runner exists:

- it requires a real property with a setter on each connection's class;
- it sets the threshold and reads it back as `None`;
- it checks each session's host, address, port, database and user against the manifest;
- it requires distinct admin and monitor backends, neither of them a reader's.

No other connection is touched:

- the readers and parent sessions connect unprepared;
- the child sessions are already unprepared in `worker.py`;
- the ensure session is closed before the policy applies.

The matcher still allowlists no prepared name. A connection that reports `None` but keeps preparing is refused by the matcher.

**Proof boundary.** Accessibility and timing are established from the real code. The real log showing no prepared harness statement is what the characterization will observe. Prepared-name prediction (`gate2a_prepare_model`) remains a separately reviewable fallback and is not used.

## 3. B3: the segmented-reader boundary

For the one call, `SegmentedRun.gate2a` follows this timeline:

1. "Before" role-catalog observation.
2. The segment's end marker; Gate A1's audit of the segment; the reader disconnects.
3. Gate 2A runs, with no Gate A2 session open:
   - its preflight;
   - its outside-session sweep (the old reader's logged disconnection must precede the first Gate 2A row; a reader still present is terminated by the sweep and REFUTED);
   - reset, snapshot, role, child, teardown, final snapshot, role removal;
   - its own end marker and reconciliation.
4. The call returns or raises.
5. A new reader connects (its connection records must follow Gate 2A's last row). It reads from the batch's one recorded offset until its own start marker is in a complete log, with a bounded wait.
6. The gap is audited.
7. The "after" observation is judged.

**Markers.** Only real ones are used: Gate A1's batch markers bound the segments, and Gate 2A's `ds_mark_start_<run>`, `ds_mark_end_<run>` and `ds_run_<run>` bind the rows to the trace.

**Outcomes.** Each call ends as one of:

- **completed:** a scoreable trace and a verified gap;
- **flagged:** a trace Gate 2A flagged;
- **failed:** the call raised;
- **interrupted_unknown_effects:** an operator interrupt.

Only "completed" continues. A missing end marker is never a successful segment:

- Gate A1's segment close waits for its end marker and refuses without it;
- a gap with Gate 2A's end marker missing is INDETERMINATE;
- a missing batch offset, or a rotated or truncated log, is refused.

## 4. B1: initial-state assurance stays blocked

| Claim | Status |
|---|---|
| the logged reset and seed parameters give the manifest's reviewed `initial_rows` | VERIFIED |
| no other session wrote between the reset and the snapshot | VERIFIED |
| the snapshot came after the reset and before the run's first session | VERIFIED |
| the supervisor's in-process snapshot equals the digest the construction batch verified independently | VERIFIED, as a trusted-control-plane read |
| the snapshot's values, independently | **UNOBSERVABLE** |
| the state between the snapshot and the worker | **UNOBSERVABLE** (the B2 interval) |

**The narrower qualified claim the package can support:** the reset was exactly the reviewed one and nothing else wrote, and the supervisor's own read agrees with an independently verified digest. That is not independent post-reset verification.

**A future interface, proposed, not implemented.** A reviewed Gate 2A revision could have the admin session run, right after the reset, a fixed `DO $$ … RAISE LOG 'ds_state <run> %', (SELECT md5(…)) … $$`. The server itself would then write a state digest into the log, with no client report in between.

## 5. B4, B6 and plain_serial

- **B4: the real-log readiness of the template.** The template's fixed text is checked against the frozen constants, and the real supervisor's statements match it under fakes. Agreement with a real log, and the `'{a,b}'` form for integer arrays, are NOT_TESTED until the run.
- **B6: bootstrap visibility.**
  - What Sandbox construction sends is matched exactly.
  - The runner role's attributes are now observed afterwards in the catalog. What the `DO` block executed stays UNOBSERVABLE.
  - So does everything before the batch's first log position: container start, initdb and the operator's `CREATE DATABASE`.
- **plain_serial.** Its result carries no run id, so it stays refused. No run id is invented or inferred.

  Amendment 002's manual uninstrumented reproduction is a separate obligation. This characterization does not perform it.

## 6. The planned run

| | |
|---|---|
| Target | a fresh `postgres:16@sha256:1beaa308…` container `deployspecter-live-pg`, on `127.0.0.1:55433`, tmpfs; database `deployspecter_test`, user `deployspecter`, scram-sha-256. `deployspecter-pg` (55432) is forbidden |
| Workload | Gate 2A's own synthetic `live_app.reservations`, variant `fixed`, schedule `a, b`, the bridged one-row seed. Scenario digest pinned; nothing scored |
| Sessions | the Gate A2 readers (one per segment); tagged parent sessions; the ensure session; the harness admin and monitor; the child's monitor and actors `a`, `b`. Nothing else |
| Budget | parent batch 600 s; Gate 2A `run_s` 30, `handshake_s` 20, `quiescence_s` 5, `log_wait_s` 5; one call |
| Expected classification | `OBSERVABLE_SQL_VERIFIED_WITH_DISCLOSED_GAPS`; never `ALL_SQL_VERIFIED` |

What would count under each status is listed in the manifest's `expected_results`.

**Two separate reports.** Log-template agreement (what the admin session sent) and database-state verification (what the reconciled parent reads saw) are reported separately; neither stands in for the other.

## 7. What a successful run would and wouldn't establish

**It would establish**, on one synthetic PostgreSQL 16 instance:

- that the template and the real supervisor's log agree;
- the integer-array parameter form;
- that the harness sends no prepared statement under option 1;
- that the reader boundary holds for one call;
- that the role catalog meets its conditions.

**It would not establish:**

- the statements inside the B2 interval;
- independent post-reset state;
- plain_serial attribution;
- Gate A2's demonstration;
- any result beyond this one run.
