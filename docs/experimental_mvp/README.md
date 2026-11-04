# EXPERIMENTAL: real-application stepping MVP

This prototype runs real Python application handlers concurrently on psycopg 3 and gates every database operation through a deterministic scheduler. It checks the resulting PostgreSQL state with an independent oracle, and it accepts workloads from Meta Muse only through a deterministic validator.

**It is not certified Gate A2 evidence.** It doesn't use the frozen Gate 2A supervisor and has none of its isolation. Handlers run in-process, as the same OS user, against a disposable local PostgreSQL only. The Gate A2 demonstration stays blocked.

## Reproduce in one command

```bash
cd ~/Desktop/deployspecter-real-app-mvp && research_tools/mvp_demo.sh
```

This starts `deployspecter-mvp-pg`: pinned PostgreSQL 16, tmpfs, 1 GB, 2 CPUs, 256 PIDs, `127.0.0.1:55441`, never 55432. It runs the offline and live tests, then re-executes the buggy and fixed builds on the race schedule. It prints the handlers, the driver-level trace, the scheduler's decisions, the rows each read returned, the verified initial digest, the final state and the oracle's verdict. Teardown runs on every outcome.

Other commands, each needing a running instance (`research_tools/mvp_pg.sh start`, then `stop` afterwards):

- `python -m deployspecter.experimental.demo --out DIR`: the race demo alone.
- `python research_tools/mvp_compare.py --workloads 5 --schedules 60 --out DIR`: the curated and systematic sources. Add `--muse-calls 2` to include Muse; it reads only `MODEL_*` from the main repository's `.env` and is capped by `--muse-usd`.

## What is intercepted

| Call path | How |
|---|---|
| `Connection.execute` | it creates its cursor through `cursor_factory`, which is `SteppedCursor` |
| `Cursor.execute`, `Cursor.executemany` | gated; the rows are prefetched for the trace and rewound with `scroll`, so the handler reads them itself |
| `Connection.commit`, `Connection.rollback` | gated (psycopg sends these through `_exec_command`, which bypasses cursors) |
| `Connection.transaction()` and savepoints | gated at entry (BEGIN or SAVEPOINT) and exit (COMMIT, RELEASE or ROLLBACK), including the exit on an exception |
| Implicit BEGIN in non-autocommit mode | part of the gated execute; recorded from the transaction status before and after |
| Refused, never executed | named cursors, `copy`, `stream`, `pipeline`, use from another thread, any statement outside the handler's allowlist |
| **Not interceptable** | raw `pgconn` calls, connections a handler opens itself, connection pools, async connections. Only the PostgreSQL log would show these |

## Provenance per operation

Each operation records:

- the app version, actor, handler and invocation id;
- the migration phase;
- the logical operation index;
- the session (backend pid) and thread;
- the application call site (file:line:function);
- the actor-local transaction id;
- the statement id (a hash of the normalized text);
- the sanitized parameters and rows;
- the grant order and the decision it was granted under;
- the outcome and error class;
- the transaction status before and after.

Sanitizing keeps numbers, booleans and NULL exactly, because they drive the handlers' arithmetic. Strings become a length and a short digest.

## Limitations

- **One trusted synthetic fixture.** Every comparison here is a smoke test, not a benchmark.
- **Exploration scope.** "Exhausted" means the decision frontier emptied within the budget, assuming each prefix re-executes to the same decision points. It isn't a completeness proof.
- **The fixture's lock discipline.** The race needs the fixture's own lock discipline. Lock waits are detected with `pg_blocking_pids` from the harness's admin connection.
- **Integration not done.** The existing WorkloadSpec v2, planner and Phase 9B bridge aren't integrated. The prototype has its own validator and seed constructor.
