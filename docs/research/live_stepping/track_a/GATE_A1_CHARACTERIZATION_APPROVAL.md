# Gate A1: minimal PostgreSQL characterization, approval package

**Status: READY_FOR_NEW_USER_AUTHORIZATION. This is not an authorization.** Version 4 hasn't run. It needs a new
approval file and a **new signature** covering the digest below; no earlier approval or signature can be reused.

- **Manifest:** `gate_a1_manifest.json`, **version 4**, SHA-256
  `1590d23ea9dec1e9e7ca903daf2b52b15c0404e3b496c4e5d7d714bcb2b02fb5`.
- **Versions 1** (`04f1d751…`), **2** (`d92ea590…`) **and 3** (`768cce7e…`) each ran once and are superseded
  (section 0). All three are recorded in the manifest's `supersedes`, and the driver refuses them.
- **Superseded drafts,** never run and never approved, kept in the manifest's `draft_history`:
    - `3b98c82e…`, the milestone 4 draft;
    - `c58d0d10…`, after the final review's driver fixes;
    - `1c8d1a55…`, before parse errors were kept out of records.
- **Driver:** `research_tools/gate_a1_characterize.py`.
- **Pure logic:** `src/deployspecter/research/track_a/characterization.py`.
- **Tests:** `tests/research_tracks/test_gate_a1.py`, and the real-log tests `test_gate_a1_observed_log.py` (run 1),
  `test_gate_a1_v2_observed_log.py` (run 2) and `test_gate_a1_v3_observed_log.py` (run 3), all offline.

## 0. Run history

| Version | Manifest | Run | Result |
|---|---|---|---|
| 1 | `04f1d7518685d0555a9925ccd5d4d7bd1260ac3431f34e2cb7e4c1a8d3f17520` | Once, on 2026-11-04, under the owner's signed approval; PostgreSQL 16.15 on the pinned image | **`refused_fail_closed` at op0p**. Evidence: `evidence/gate_a1/20261104T113256Z-d0b21a60/`, kept locally and uncommitted. No catalog, state or binding operation ran; the container was torn down |
| 2 | `d92ea5900eb22c163da9faac2f00e67ec1c38395cc8cfe170bf856a85f1ba0c9` | Once, on 2026-11-04, under a new signed approval; PostgreSQL 16.15 on the pinned image | **`refused_fail_closed` at the seeded op0r reset** (the sixth operation). Evidence: `evidence/gate_a1/20261104T132842Z-1c432509/`, kept locally and uncommitted. Preflight passed; both exports agreed; the `information_schema` re-read agreed; `verify_context` reached MATCH. The state checks, bindings, cleanup and batch audit didn't run |
| 3 | `768cce7e04de126a79c35a7040c79cd7d20edd25515429fabca128450872e044` | Once, on 2026-11-04, under a new signed approval, with automatic preparation off | **`refused_fail_closed` at op7b**. Evidence: `evidence/gate_a1/20261104T140952Z-58f6e18e/`, kept locally and uncommitted. op0p to op6f reconciled in full, and both typed state checks passed. The binding read-back was never judged; cleanup and the batch audit didn't run |

**The two causes,** both confirmed from the observed rows:

1. **F1, a PostgreSQL 16 behaviour.** A TCP password login logs `connection authenticated: identity="deployspecter"
   method=scram-sha-256 (/var/lib/postgresql/data/pg_hba.conf:128)` as session line 2, between "connection received"
   and "connection authorized". The version 1 reconciler knew only received, authorized and disconnection rows.
2. **F2, a driver defect.** The driver's `wait_gone` poll runs on the log-reader session after the parent session ends
   and before the end marker, so it is logged inside the operation window. The version 1 per-operation reconciler
   counted it as an unattributed session. The offline fake didn't log the poll, so no test could see this.

**A third finding, from reviewing the evidence.** The driver wrote `approval.json` re-serialized with sorted keys
(SHA-256 `445342d9…`), not the signed bytes (`ec9a4f86…`). The content was identical, but the evidence copy couldn't
be checked against the signature. Version 2 copies the file byte for byte and records its provenance.

**The version 2 cause, confirmed from the observed rows.**
- psycopg 3 prepares a query once a connection has run it `prepare_threshold` times (default 5), and the server then
  logs it as `execute _pg3_N:`.
- The log reader is one long-lived connection. Its sixth poll logged as `execute _pg3_2:`, with the approved text, the
  correct tag parameter, the correct session and a consecutive line. Its `pg_current_logfile` and `pg_stat_file` queries
  had become `_pg3_0` and `_pg3_1` outside the windows.
- All 17 prepared executions were the reader's, and were of exactly those three approved texts. No other session
  appeared.
- Version 2 required the exact `execute <unnamed>:` form for the poll, so it refused.
- This is client-side behaviour, not a PostgreSQL defect.

**The version 3 cause, confirmed from all 32 op7b statements in the real log.**
- Exactly one statement differed: statement 15, the binding case `(14, -0.0)`. PostgreSQL logged `$2 = '-0'`; the
  reconciler expected `'-0.0'`.
- psycopg 3.3.6 sends Python floats in binary, as float8 (`FloatBinaryDumper`, OID 701). PostgreSQL logs a binary
  parameter with the type's text output, and float8 output writes negative zero as `-0`.
- The other 31 statements matched in text, parameters, order and session. That includes NaN, ±Infinity, 5e-324,
  ±max, 0.1 and −2.5, and the integer, boolean, NULL and string cases.
- It is only a formatting difference. Nothing was prepared, and no session or statement was unexplained.

## 0.00 What version 4 changes

- **Float parameters are matched against float8's logged text, in established forms only:**
    - NaN and ±Infinity, as before;
    - `-0` for negative zero, as observed;
    - Python's repr for non-integral finite values below 1e15 or in exponent form, which agrees with the observed
      cases.
- **Everything else is refused, not guessed.** That covers positive zero, integral-valued floats, values from 1e15 to
  1e16, and any type other than None, booleans, integers, floats and strings.
    - The driver checks every binding parameter before any SQL runs.
    - The reconciler reports such a parameter as a problem.
- **`extra_float_digits=1`** is pinned in the session options (`safety_ceilings.session_options`), and the driver
  refuses a manifest without it.
    - Both float8's logged text and the bit-exact float read-back depend on shortest-exact float output.
    - A client startup option takes precedence over any role or database setting.
    - It equals PostgreSQL's default, which the third run's `5e-324` shows was in effect.
- **The values sent to PostgreSQL don't change.** Strings stay text, so the string `"-0.0"` is never read as a float.
  Negative and positive zero stay distinct. The typed state encoding and its digests are untouched.
- **Unchanged:** the SQL statement sets, the parameter order and count checks, session identity, the reader limits,
  the disabled-preparation policy, the target, the image and the operator commands.

## 0.0 What version 3 changed

- **psycopg's automatic preparation is disabled on Gate A1's own connections.**
    - `prepare_threshold=None` is passed at both connection sites, the log reader and every tagged parent session, both
      in `RealEffects`.
    - Each connection is checked before use, and one that could still prepare is refused.
    - The manifest must declare the same policy (`client_connection_policy`), or the driver refuses it.
    - Nothing in Gate A1 asks for preparation explicitly: there is no `prepare=`, no `executemany` and no cursor.
- **Prepared forms now fail rather than pass.**
    - Tagged sessions may log only `statement:` or `execute <unnamed>:`.
    - The batch audit holds the reader to the same rule.
    - The reconciler is not widened to accept any prepared-statement name.
- **Unchanged:** the SQL statement sets, the parameter checks, session identity, the reader limits, the target, the
  image and the operator commands. Gate 2A, Gate A2 and Phase 9B connections are not touched. Gate A2's own reader
  would need its own preparation and logging review.

### What the version 3 evidence will and won't establish

- **It characterizes one client configuration:** psycopg 3.3.6 with automatic preparation **disabled**, against the
  pinned PostgreSQL 16 image.
- **It establishes nothing about default, auto-prepared psycopg sessions.** The version 2 run remains the only
  evidence of that behaviour: preparation after five executions, and `_pg3_N` names in the log.
- **Future Gate A2 connections need their own preparation and logging policy review.** No result here applies to them
  automatically.
- **No scientific or production claim may generalize silently beyond the tested configuration.**

## 0.1 What version 2 changed

- **Parent-session records:**
    - The record sequence is strict: received, then exactly one authenticated record (identity = admin user,
      method = the manifest's `authentication_method`, `scram-sha-256`), then exactly one authorized record
      (user, database, tag), then the approved statements, then exactly one disconnection.
    - The disconnection must match the connection's user, database, host and port.
    - Session lines must be consecutive from 1.
    - Every record is matched exactly. Anything else, including an unknown connection-state record, a duplicate or an
      out-of-order record, fails.
- **The log reader:**
    - It is identified from the rows that wrote each operation's markers, and must be the same session and process
      for the whole batch.
    - Inside an operation window it may issue only the exact poll for that operation's tag, after the session's last
      statement, at most `backend_exit_wait_s / 0.05 + 2` (102) times.
    - Its rows must be on consecutive session lines between the two markers, so none can be missing.
    - In the batch audit, its rows must be consecutive between the batch markers, and a second session calling itself
      the log reader fails.
- **The approval** is copied byte for byte as `approval.json`. `approval_provenance.json` records the source path, the
  SHA-256 and the signature file's SHA-256. The driver still doesn't verify the signature. A file that changes while
  being read is refused.
- **The readiness check** used during the version 1 run is now part of the code-built operator commands.
- **The SQL statement sets are unchanged,** and so are their hashes. No new statement, permission, role or target was
  added.

## 1. Scope

**Gate A1 covers only:**

- the parent catalog export, and its independent `information_schema` re-read;
- a reset on a reconciled session;
- independent typed state reads of the one bridged row;
- psycopg 3 value binding and decoding (bridge assumptions D1 to D3);
- reconciliation of every parent session, and attribution of every logged session.

**It is separate from Gate A2** (`track_a_run.py`), which stays EXECUTION_BLOCKED. A1 runs:

- no Gate 2A supervisor;
- no release `Sandbox`;
- no child process;
- no application code;
- no model;
- no command.

Its outputs are **synthetic characterization evidence**. They are never an evaluation, a detection or held-out evidence.

## 2. Steps, statements and expected effects

All on tagged sessions, in this order. Statement sets are pinned in the manifest's `statement_sets_sha256`.

| Step | Statements | Expected effect |
|---|---|---|
| Batch start marker | Log reader | — |
| op0p preflight | 3 | Gate 2A's required settings and A1's four duration and sampling settings; no per-role or per-database overrides; `deployspecter_test`; user `deployspecter`; superuser; PostgreSQL 16 |
| op0r reset, no seed | 6 | `ds_run` holds `SETUP_SQL`'s three tables |
| op2a, op2b exports | 12 each | Read only |
| op2c re-read | 7 | Read only |
| `verify_context` against the export | none | Recorded only. **Not a pin:** A1 creates no reviewer attestation |
| op0r seeded reset, then op6i typed check and op6f typed read, **twice** | 7 + 9 + 5, twice | `ds_run.events` holds exactly `(1, 3, 0)` |
| op7b bindings | 32 | `ds_char` is created, holds 27 rows, and is dropped |
| op9c cleanup | 2 | `ds_char` and `ds_run` are dropped |
| Batch end marker, then the whole-batch audit | — | Every logged session is attributed |

**After each operation,** the driver:

1. waits up to `backend_exit_wait_s` (5 s) for the tagged backend to leave `pg_stat_activity`, polling every 0.05 s on
   the log-reader session;
2. writes the end marker;
3. waits up to `log_wait_s` (5 s) for the collector to flush it;
4. identifies the log reader from the marker rows;
5. reconciles the tagged session and the reader's polls (section 0.1).

Any failure stops the batch.

**No other effect is expected:**

- no role, database (beyond the operator's one), file or process is created;
- evidence is written only to a new `docs/research/live_stepping/evidence/gate_a1/<run id>/`.

## 3. PostgreSQL 16 logging the reconciler relies on (Part B, reviewed without connecting)

| Topic | What the code requires or assumes | Established offline? |
|---|---|---|
| Statement logging | `log_statement=all`, `log_parameter_max_length=-1`, and `log_duration`, `log_min_duration_statement`, `log_min_duration_sample` and `log_transaction_sample_rate` off. op0p checks all of them, and that no role or database setting overrides them | Checked at run time by op0p |
| `log_line_prefix` | **Not used.** csvlog has fixed columns. The reconciler reads user, database, pid, session id, severity, message, detail, application name and backend type | Yes, from the csvlog format |
| Session identity | csvlog `session_id` plus `pid`. A tagged session must show one session id and one pid, its tag in `application_name`, and type `client backend` | Assumption; **needs the real server** |
| Transaction boundaries | Reset statements are sent as separate statements: `BEGIN`, the setup SQL, the INSERTs, then `COMMIT`. Each must log once, in order | Assumption about psycopg; **needs the real server** |
| Connection and disconnection | `log_connections` and `log_disconnections` on. Received, authenticated, authorized and disconnection records, in that order | **Observed once** in the version 1 run (op0p and the reader). Other sessions are still unobserved |
| Flush and completeness | The collector writes asynchronously, so the driver waits, within a bound, for the end marker. Polling `pg_stat_activity` first makes the disconnection row precede the marker | The ordering **needs the real server** |
| Parameterised statements | Extended protocol logs as `execute <unnamed>: …` with a `parameters: $1 = '…'` detail line. Parameterless and multi-statement texts log as `statement: …`. Both forms are accepted only for the exact expected text and parameters | psycopg's literal formats for floats, booleans, NaN and ±Infinity are assumed; **needs the real server** |
| Log reader | Only five statement forms are allowed. Two bracketing reads, the first position query and the final read, can't audit themselves | Known limit |
| Other backends | Checkpointer `LOG` rows are allowed. Any other session fails the batch audit | Logic tested offline |

**Needs the real database:**

- whether psycopg sends anything on connect (none was seen in the one observed session);
- whether the connection and disconnection forms seen in op0p hold for every operation;
- flush timing, and the order of the disconnection row and the end marker (one observation so far);
- whether the reader ever needs more than one poll;
- `indclass[s.k]` subscripting in the export;
- `information_schema` details for serial columns;
- whether the tmpfs size and memory limits suffice;
- whether the image digest is a manifest-list digest or a platform digest.

**Nothing above is verified from mocked logs.**

## 4. Privilege and isolation (Part C)

| Property | State |
|---|---|
| Image | Pinned by digest, `postgres:16@sha256:1beaa308…`. Whether that is a manifest-list digest is checked at the first pull |
| Network | Published only on `127.0.0.1:55433`. The dev container `deployspecter-pg` on `127.0.0.1:55432` is a different target, and the driver refuses it |
| Storage | Data on tmpfs (512 MB), with no volumes and no host mounts. `--rm` plus `docker stop` discards everything |
| Data | Synthetic only: `SETUP_SQL`, one bridged row and the binding cases |
| Target | **Fixed by the manifest before connecting.** The driver takes host, address, port, database and user from the manifest. `DATABASE_URL` must agree and may only supply the password. libpq variables such as `PGHOSTADDR` and `PGSERVICE` can't redirect the connection, because every routing keyword is passed explicitly. After connecting, the session's host, port, database and user are checked again |
| Secrets | **`MODEL_API_KEY` refused**, even when `.env` supplies it. Error records quote no DSN text, because libpq's parse errors can quote the password. `Settings.test_dsn` is excluded from repr. **Accepted risk:** `-e POSTGRES_PASSWORD="$DS_PG_PASSWORD"` puts the password in the `docker run` arguments and in `docker inspect` on this single-user host |
| Resources | `--memory 1g`, `--cpus 2`, `--pids-limit 256`; session timeouts of 10 s per statement, 3 s per lock and 15 s idle in transaction; 600 s per batch |
| Lifecycle | The operator starts and stops the container. The driver runs no command |
| **Privilege** | **The parent is a superuser on the disposable cluster.** Read-only transactions and log reconciliation detect deviation; **they are not a least-privilege or hostile-code boundary.** A1 runs no untrusted code |
| Database creation | The operator's `CREATE DATABASE` is the one statement outside reconciliation |

## 5. Operator checklist (Part E)

Every command below is printed by the driver's offline `plan` command, from the manifest, or is the documented driver
invocation. The signature step is the owner's own procedure. **The driver doesn't check it.**

### 5.1 Preconditions (stop if any fails)

1. You are in the worktree `deployspecter-live-stepping` at the Gate A1 commit, with `git status` clean apart from the
   uncommitted Amendment 003 drafts.
2. `shasum -a 256 docs/research/live_stepping/track_a/gate_a1_manifest.json` prints the digest at the top of this file.
3. There's no container named `deployspecter-live-pg`, and port 55433 is free:
   `docker ps -a --filter name=deployspecter-live-pg`.
4. Generate a password in your shell, for example `export DS_PG_PASSWORD="$(openssl rand -hex 24)"`, and never commit
   it.
5. The worktree's git-ignored `.env` contains exactly
   `DATABASE_URL=postgresql://deployspecter:<the same password>@127.0.0.1:55433/deployspecter` and **no
   `MODEL_API_KEY`**. The driver refuses any other host, port, user or extra keyword before connecting.

### 5.2 Approval (owner only)

1. Review this package and the manifest.
2. Write `approval.json` yourself, **outside** the evidence tree:

   ```json
   {"manifest_sha256": "1590d23ea9dec1e9e7ca903daf2b52b15c0404e3b496c4e5d7d714bcb2b02fb5", "phase": "gate_a1", "approver": "<you>", "approved_on": "<date>"}
   ```
3. Sign it, for example `ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n deployspecter-gate-a1 approval.json`.
4. Verify the signature before running:
   `ssh-keygen -Y verify -f <allowed_signers> -I <you> -n deployspecter-gate-a1 -s approval.json.sig < approval.json`.

**The driver checks only the digest and the phase.** No approval file has been created by this work.

### 5.3 Start (operator)

```sh
docker run -d --rm --name deployspecter-live-pg -p 127.0.0.1:55433:5432 --tmpfs /var/lib/postgresql/data:rw,size=512m --memory 1g --cpus 2 --pids-limit 256 -e POSTGRES_USER=deployspecter -e POSTGRES_DB=deployspecter -e POSTGRES_PASSWORD="$DS_PG_PASSWORD" postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c -c cluster_name=ds_live_disposable -c logging_collector=on -c log_destination=csvlog -c log_directory=log -c log_filename=ds.log -c log_rotation_age=0 -c log_rotation_size=0 -c log_connections=on -c log_disconnections=on -c log_statement=all -c log_parameter_max_length=-1 -c autovacuum=off -c max_prepared_transactions=5
docker exec deployspecter-live-pg psql -U deployspecter -d deployspecter -c 'CREATE DATABASE deployspecter_test'
```

Before creating the database, wait until this prints `accepting connections` (it runs no SQL):

```sh
docker exec deployspecter-live-pg pg_isready -h 127.0.0.1 -p 5432 -U deployspecter -d deployspecter
```

If the `CREATE DATABASE` fails, stop the container and start again.

### 5.4 Run (operator)

```sh
env -u MODEL_API_KEY uv run python research_tools/gate_a1_characterize.py characterize \
  --manifest docs/research/live_stepping/track_a/gate_a1_manifest.json \
  --manifest-sha256 1590d23ea9dec1e9e7ca903daf2b52b15c0404e3b496c4e5d7d714bcb2b02fb5 \
  --approval <path to your approval.json>
```

### 5.5 Evidence

The run writes a new directory, `docs/research/live_stepping/evidence/gate_a1/<UTC time>-<8 hex>/`, and never
overwrites one.

| Artifact | What it records |
|---|---|
| `approval.json` | The signed approval file, byte for byte |
| `approval_provenance.json` | The approval's source path, SHA-256 and signature-file SHA-256. The signature itself isn't checked by the driver |
| `preflight.json` | The settings rows and any problem. A problem stops the batch |
| `catalog.json` | Two export digests, the structural digest, cross-check problems, the `verify_context` outcome, time-typed columns, the snapshot |
| `states.json` | For each repeat: independent-check problems, server digests, typed-read problems, read digest |
| `bindings.json` | Plain-read failures (D2, D3), refuted D1 cases the bridge relies on, and the recorded non-relied cases (−0.0, NaN, ±∞) |
| `parent_reconciliation.json` | Every tagged session's reconciliation |
| `batch_audit.json` | Each logged session's classification, and any problem |
| `summary.json` | The outcome and findings |
| `failure.json` | On any stop: `refused_fail_closed`, `interrupted_stop` or `unknown_effect_stop`, with `retried: false` |

### 5.6 Interpreting the result

| Result | Meaning |
|---|---|
| Exit 0, `CHARACTERIZED` | Every check held on this cluster. **Synthetic characterization evidence only** |
| Exit 1, `CHARACTERIZED_WITH_FINDINGS` | The run completed and recorded a refuted driver assumption or an unattributed session. **It is a finding to review, not a pass,** and Gate A2 can't rely on the refuted assumption |
| Exception plus `failure.json` | The batch stopped. `refused_fail_closed` means a check refused; `interrupted_stop` means the operator interrupted it; `unknown_effect_stop` means an unexpected error, and the cluster state is unknown. **Nothing is retried.** A new run needs a fresh container and a new review |

### 5.7 Mandatory cleanup, on every outcome

```sh
docker stop deployspecter-live-pg
```

`--rm` removes the container, and the data lived only on tmpfs. Then confirm with
`docker ps -a --filter name=deployspecter-live-pg`, which should list nothing. Remove `DS_PG_PASSWORD` from your shell,
and the password from `.env`.

### 5.8 Stop immediately, run `docker stop`, and report if:

- any precondition, digest or signature check fails;
- the driver refuses the target, or reports `MODEL_API_KEY`;
- `docker run` reports a different image digest or a port conflict;
- anything prompts for a password or tries to reach a host other than `127.0.0.1:55433`;
- the run exceeds 600 s, or hangs without output;
- `failure.json` appears;
- a session appears in the audit that you can't explain;
- any file outside the new evidence directory changes.

## 6. Approval-file integrity

- **The approval file is a record, not an authentication.** Anyone with access to this worktree can write one,
  including this assistant. Its digest proves only that it hasn't changed since it was written.
- **Authorization is the owner's own decision and action:** reviewing, signing, verifying and running in person
  (section 5).

## 7. Verification record (offline only, version 4)

- **Real-log regression:** all three runs' logs are pinned, verbatim fixtures with provenance files. On the third
  run's log, under the version 4 rules:
    - the original failure reproduces, on statement 15 only;
    - **all 12 operation windows reconcile, including all 32 op7b statements;**
    - the reader's activity up to the stop passes the batch audit;
    - nothing was prepared.
- **Negative tests on the real binding rows refuse each of these:** a lost sign; the old `'-0.0'` form (both variants
  are never accepted); a missing or extra parameter; swapped parameter order; changed SQL text; a prepared name;
  swapped statements; a foreign or unknown session; an extra statement.
- **Model tests:** negative zero is `'-0'`; positive zero, integral floats, the 1e15 to 1e16 range and unsupported
  types are refused; the string `"-0.0"` stays text. A test checks offline that the installed psycopg sends ints,
  booleans and floats in binary (float8, OID 701) and strings as text.
- **Simulated full batch:**
    - The fake effects log op7b's parameters with the text PostgreSQL actually logged in the third run, not with the
      reconciler's own formatter.
    - The batch runs through op7b, op9c, the batch end marker, the whole-batch audit and every evidence file, and
      reaches `CHARACTERIZED`.
    - Separately, the old `-0.0` expectation, a missing cleanup statement and an extra reader statement are each
      caught.
    - None of this is a live observation.
- **Mutation testing** (scratchpad harness, run in isolated copies):
    - each mutant runs in a fresh copy of the tree;
    - the copy's manifests are re-pinned, so a digest check can't kill a mutant trivially;
    - tests run in their own process group, under a fixed 30 s watchdog and a batch budget;
    - the worktree is hashed before and after every mutant.

  | Suite | Mutants | Killed by a test assertion | Killed by the watchdog | Equivalent | Survived | Errors |
  |---|---|---|---|---|---|---|
  | Gate A1 (14 new for version 4: negative zero, the zero sign, unestablished forms, types, missing and reordered parameters, the pre-execution check, the `extra_float_digits` pin, cleanup, reader statements) | 81 | 81 | 0 | 0 | 0 | 0 |
  | Gate A2 (offline; Gate 2A code untouched) | 22 | 21 | 0 | 1 | 0 | 0 |

    - **The equivalent mutant** removes the "failed reconciliation is an unknown effect" term. It is unreachable,
      because `parent_operation` raises before returning a failed record.
    - **The watchdog self-check still holds.**

## 8. Limits that remain

- The parent is a superuser (section 4).
- **Three partial real runs aren't a characterization.** op0p to op6f have run and reconciled on the real server, as
  have op7b's 32 statements in the version 4 replay. **Still not tested live:**
    - the binding judgement: plain read-back, the `to_jsonb` decoding, and psycopg's decoding of `-0`, NaN and
      Infinity;
    - op9c cleanup;
    - the batch end marker and final read;
    - the whole-batch audit over a complete batch;
    - the final evidence files.

  The simulated batch, and the audit of the third run's real rows up to its stop, pass. Neither is a live observation.
- **Predicted, not observed:** `to_jsonb` will turn negative zero into `0` and NaN and ±Infinity into strings. These
  are recorded cases the bridge doesn't rely on, so they would be listed as findings, not count as failures.
- **The tested client configuration is psycopg with automatic preparation off** (section 0.0).
- Two bracketing log-reader reads can't audit themselves: the reader's connection and first position query before the
  batch start marker, and its final read after the batch end marker.
- The batch audit allows the reader's poll form anywhere in the batch. Its tag parameter is constrained only inside
  operation windows, where every poll is issued.
- A1 says nothing about Gate 2A's per-run reset. That is Gate A2's blocker
  (`STATEMENT_COVERAGE_AND_PER_RUN_ASSURANCE.md`).
- Gate A2's own `RealEffects` (`track_a_run.py`) has neither a fixed-target check nor a `MODEL_API_KEY` refusal: it
  connects to whatever `DATABASE_URL` names. A2 is EXECUTION_BLOCKED, and this is one more item to fix before it can
  be unblocked. It wasn't changed here.

## 9. Readiness

**READY_FOR_NEW_USER_AUTHORIZATION.** The version 4 package is complete, the statements and sources are pinned, and
every effect and command is listed.

**Not authorized:**
- a new run needs a **new** approval file naming the version 4 digest, a **new** signature from the owner, and a fresh
  container;
- the version 1, 2 and 3 approvals and signatures are never valid for version 4;
- nothing is retried automatically.
