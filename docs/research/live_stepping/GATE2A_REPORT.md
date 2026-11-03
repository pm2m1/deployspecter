# Gate 2A: fail-closed execution hardening

Uncommitted research work on `research/live-stepping`. Protocol version 1.2 is unchanged. No external application ran, and no model request was made. Evidence is in `evidence/gate2a_soak.json`, `gate2a_mutation_check.txt` and `gate2a_restart.json`.

## The cluster these checks need

A supervised run refuses to start unless PostgreSQL declares itself disposable and logs what reconciliation depends on:

```sh
docker run -d --rm --name deployspecter-live-pg -p 127.0.0.1:55433:5432 \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=deployspecter -e POSTGRES_DB=deployspecter -e POSTGRES_PASSWORD=CHANGE_ME postgres:16 \
  -c cluster_name=ds_live_disposable -c logging_collector=on -c log_destination=csvlog -c log_directory=log \
  -c log_filename=ds.log -c log_rotation_age=0 -c log_rotation_size=0 -c log_connections=on \
  -c log_disconnections=on -c log_statement=all -c log_parameter_max_length=-1 -c autovacuum=off \
  -c max_prepared_transactions=5
```

On any other cluster, supervised tests skip with the reason, and one fail-closed test still runs. A green suite there says nothing about Gate 2A.

## Status

**Gate 2A passed for the supported synthetic execution model only:** synchronous psycopg 3 application code, registered
entrypoints, one disposable local PostgreSQL 16 cluster with the logging below, and synthetic data. No external
application has run under it. Two deviations were accepted by the owner:

1. **Restart:** a backend crash and PostgreSQL's crash recovery are tested. A full postmaster or container restart is not.
2. **Recovery:** an infrastructure failure stops the batch. Automated cluster recreation is not implemented.

## Reproduction environment

- Image `postgres:16`, digest `sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c`
  (PostgreSQL 16.15, Debian 16.15-1.pgdg13+2, aarch64)
- Python 3.12.14 through uv; psycopg 3.3.6 with libpq 18.6; macOS 26.6
- The cluster command below, a random password, and a git-ignored `.env` pointing at it. `MODEL_API_KEY` empty
- `uv run pytest -m "not live"`: the paid test stays deselected by the project's own marker

## Trust model

`LiveTrace.scoreable` is true only if the run was supervised, has no evidence flag, isn't inconclusive, reached verified quiescence, dropped its role, left no thread running and left no session open. A successful Python return contributes nothing to that. `LiveRunner` remains the fast in-process engine for tests, and its traces are never scoreable.

| Component | Role |
|---|---|
| `live/supervisor.py` | Parent. Holds admin credentials. Preflight, bounded reset, run role, child, escalation, teardown, final state, reconciliation |
| `live/worker.py` | Fresh child process. Holds only the run's login. Runs the controller and actor threads |
| `live/serverlog.py` | Reads the CSV log and reconciles every session and statement against the trace |
| `live/runner.py`, `driver.py`, `trace.py`, `explore.py` | Engine changes: external blockers, `effect_unknown`, opaque batches, new outcomes |

## Trust-boundary review before the freeze

| Question | Finding | Change |
|---|---|---|
| Are unchecked parameter types visible? | They weren't reported | Reconciliation now reports parameters compared, not compared, and statements whose parameters weren't checked (all of an opaque batch) |
| Can unverified operations still be scoreable? | A `ready` message missing a session's privilege check was accepted, and registered PIDs weren't checked to belong to the run role | Both are now required, or the run is flagged |
| Does an opaque batch report incomplete coverage? | Yes: `statement_level_coverage` is false, and exploration's `complete_schedule_coverage` is false | None |
| Do errors, timeouts and `effect_unknown` prevent scoring? | **No, a timeout the application caught could be scored** | Lock, statement and idle-in-transaction timeouts now make the run inconclusive even when handled, like connection loss. Deadlocks and serialization failures stay scoreable application outcomes |
| Is every actor session quiescent before results are trusted? | Yes: final state is read only after the parent verifies no client session remains and no prepared transaction exists | None |
| Can the child reach private paths, credentials or secrets? | Its environment held none, but **as the same OS user it could read `.env` and any file under home** | On macOS the child runs under `sandbox-exec`, with no reads or writes under home except its code and Python, and no outbound network except localhost. Its import path holds only code directories. Elsewhere confinement is `none`, and the evidence says so |
| Do server logs hold credentials or private data? | **The SCRAM verifier for each run role was logged** by the admin's `CREATE ROLE` | That one statement now runs with logging off. Logs hold synthetic statements and parameters, and stay on the disposable cluster's tmpfs |

**The confinement isn't a sandbox for hostile code.** The child can still read files outside home, run processes,
signal processes of the same user, and reach localhost services.

## Residual ways application code can escape

**This is detection, not a security boundary.** The child runs as the same OS user as the supervisor.

1. **Admin credentials on disk.** The child starts in a neutral directory with a clean environment. On macOS it can't read the repository's `.env` or anything else under home outside its code. Elsewhere it could. The connection is logged and flagged. With superuser access, though, code could then alter or rewrite the log before reconciliation, or change settings, and that could defeat the checks. Hostile code needs a separate OS user or container.
2. **Attacking the supervisor.** Same OS user, so it could signal the parent or its files.
3. **Detached process that scrubs its environment.** It isn't found by the process scan. If it holds no database session, nothing sees it. It can't log in again (tested), and its session, if any, is ended (tested). It could still act outside this database.
4. **Other servers and services.** The log covers this PostgreSQL cluster only.
5. **In-process forgery.** Code in the child can rewrite the controller's memory or the results it sends. Statements, order and sessions are checked against the server's log. **Returned Python values and decisions not visible to the server can't be verified from outside.**
6. **Parameter values.** Compared only for plain integers, strings and nulls. Floats, booleans, bytes, named parameters and everything inside an opaque batch are compared by statement text or count only.
7. **Scheduling inside an opaque batch.** Not controlled. The real torn state `[0, 1]` on plain psycopg isn't reachable, and a test records that.
8. **Harness-allowed exceptions on stopped runs.** A bare `ROLLBACK` or a connection that goes no further is accepted only on a run that is already inconclusive.
9. **Confinement depends on the platform.** It's macOS-only and outside-home reads remain possible. On Linux it is `none` until a container or separate OS user is added.
10. **False-flag evidence is limited to these scenarios.** 1,200 clean runs across 42 synthetic scenarios. Real applications may generate statements the reconciler's model doesn't anticipate, such as `DEALLOCATE` or type-lookup queries. Those would be flagged, so they'd cost false inconclusives, not false trust.

## Evidence artifacts

| File | Produced by | Notes |
|---|---|---|
| `evidence/gate2a_soak.json` | `tools/live_stepping_soak.py`, after the commits | 1,200 clean runs |
| `evidence/gate2a_mutation_check.txt` | `tools/live_stepping_mutants.py`, after the commits | 66 deliberate breaks with controls |
| `evidence/gate2a_restart.json` | `tools/live_stepping_restart_check.py`, after the commits | Crash and recovery |
| `evidence/slice_results.json` | `tools/live_stepping_evidence.py`, after the commits | In-process engine on the synthetic application, with a `psql` cross-check |
| `evidence/gate2_audit.json` | `tools/live_stepping_gate2_audit.py`, before Gate 2A | Needs a cluster that logs to stderr, not this one; kept as the audit's record |
| `evidence/superseded/` | Earlier tool versions | Kept as history, not current evidence |

None contains credentials or log rows beyond synthetic statements.

## Deviations from the checklist

- **Item 6, server restart:** the check kills one backend with SIGKILL, which makes the postmaster reset every session and run crash recovery. A full postmaster restart wasn't possible, because in this container it would stop the container and lose its tmpfs data. It runs as a tool outside pytest, because it ends every session.
- **Item 7, fallback:** recreating the disposable cluster isn't automated. A breach poisons the runner and stops the batch, and recreating the cluster is a manual step.
- **Deliberate-breakage runs** use the test or tests that target each guard (a `-k` filter), not the full suite, with a control run first and last.
- **Four engine tests changed on purpose:** `executemany` is no longer refused, so three tests now use `stream` as their refused path, and the outside-lock test now expects `external_interference`.
