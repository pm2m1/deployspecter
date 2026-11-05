# EXPERIMENTAL: real-application stepping MVP

This prototype runs real Python application handlers concurrently on psycopg 3, directly or through the SQLAlchemy 2.x ORM.

- **Scheduling:** every database operation passes through a deterministic scheduler.
- **Oracle:** an independent oracle checks the resulting PostgreSQL state.
- **Corroboration:** each run's driver trace is checked against PostgreSQL's own server log.
- **Initial states:** these can come from the existing WorkloadSpec v2 → planner → catalog verifier → Phase 9B bridge chain, called unmodified.
- **Muse:** it proposes workloads only through deterministic validators.

**It is not certified Gate A2 evidence.**

- It doesn't use the frozen Gate 2A supervisor and has none of its isolation.
- Handlers run in-process, as the same OS user, against a disposable local PostgreSQL only.
- The Gate A2 demonstration stays blocked.
- Gate 2A, Gate A1, WorkloadSpec v2 and the Track A modules are imported, never edited.

## Reproduce in one command

```bash
cd ~/Desktop/deployspecter-real-app-mvp && research_tools/mvp_demo.sh
```

The script runs these steps:

1. **Installs dependencies:** `uv sync --frozen --extra experimental`, which brings in SQLAlchemy 2.0.57.
2. **Starts `deployspecter-mvp-pg`:**
   - pinned PostgreSQL 16 on tmpfs;
   - 1 GB, 2 CPUs, 256 PIDs;
   - `127.0.0.1:55441`, never 55432;
   - csvlog with `log_statement=all`.
3. **Runs the tests:** the offline and live tests.
4. **Re-executes three fixtures on their race schedules, buggy and fixed:**
   - the migration backfill (`reservations_mixed`);
   - the SQLAlchemy ledger (`ledger_orm`);
   - Gate 2A's synthetic `live_app.reservations`, seeded from a WorkloadSpec v2 document through the planning chain.

Each run prints the trace, the scheduler's decisions, the call sites, the oracle's verdict and the server log's verdict, and writes an evidence bundle. Teardown runs on every outcome.

Other commands each need a running instance: run `research_tools/mvp_pg.sh start` first and `stop` afterwards.

- `python -m deployspecter.experimental.demo --out DIR`: the migration race alone.
- `python -m deployspecter.experimental.demo_integrated --out DIR`: the ledger and planned reservations alone.
- `python research_tools/mvp_compare.py ...`: the earlier curated, systematic and Muse comparison on `reservations_mixed`.
- `python research_tools/mvp_compare_apps.py --workloads 6 --schedules 60 [--muse-calls 2] --out DIR`: the same comparison on the ledger and the planner path. It reads only `MODEL_*` from the main repository's `.env`, and `--muse-usd` caps it.

## What is intercepted

| Call path | How |
|---|---|
| `Connection.execute` | creates its cursor through `cursor_factory`, which is `SteppedCursor` |
| `Cursor.execute`, `Cursor.executemany` | gated; rows are prefetched for the trace and rewound with `scroll` |
| `Connection.commit`, `Connection.rollback` | gated (psycopg sends these through `_exec_command`, bypassing cursors) |
| `Connection.transaction()` and savepoints | gated at entry and exit, including the exit on an exception |
| Implicit BEGIN in non-autocommit mode | part of the gated execute; recorded from the transaction status before and after |
| SQLAlchemy ORM (`Session.get`, flush, `scalars(select(...))`, `begin()`, commit, rollback, the unit of work's batched UPDATE and INSERT) | through the same driver gates. The engine is `create_engine("postgresql+psycopg://", creator=..., poolclass=StaticPool, use_native_hstore=False)`, whose creator hands over the actor's stepped connection once. Nothing is monkeypatched |
| SQLAlchemy's first-connect queries (`version()`, `current_schema()`, two `show`s) | gated, recorded and corroborated, but granted without a decision point (`setup`). These, and a COMMIT or ROLLBACK that sends nothing, commute with every other operation |
| Refused, never executed | named cursors (including SQLAlchemy `stream_results`), `copy`, `stream`, `pipeline`, use from another thread, any statement outside the handler's allowlist, a second DBAPI connection for an actor |
| **Not interceptable; caught by the server log** | raw `pgconn` calls, connections a handler opens itself (including its own SQLAlchemy engine), and a plain `psycopg.Cursor(conn)` built directly. psycopg's `TypeInfo.fetch` does that, which is why native hstore is off |
| **Unsupported** | async connections and pools, so neither pinned third-party candidate can be stepped yet |

## Pooling

| Mode | Result |
|---|---|
| `StaticPool`, one dedicated connection per actor (the supported mode) | checkout and return across several Sessions work; corroborated |
| `QueuePool` per actor | works for one connection; a second concurrent Session needs a second connection and fails closed |
| `NullPool` (no pooling) | one Session per invocation works; a second Session fails closed, because NullPool closed the actor's connection |
| One engine shared across actors (Static or Queue) | the borrowing actor is refused: its thread uses another actor's connection, or the creator is called from another thread |

## Server-log corroboration

Two markers on the admin session bracket each run. Inside that window, every csvlog row must be explained.

- **Actor sessions:** each actor backend's statements must match the trace's granted operations, in order. That includes psycopg's own BEGIN, COMMIT, ROLLBACK, SAVEPOINT and RELEASE.
- **Bound values:** integers, NULL and booleans are compared exactly, and strings by digest. Positional and named (`%(x)s`) placeholders are mapped to `$n` as psycopg does.
- **Errors and identity:** ERROR rows must match the trace's errors and SQLSTATEs. Each session must connect and disconnect once, as the expected user, database and application.
- **Order:** the server's order must agree with the grant order.
- **Admin session:** it may send only its allowlisted polls.

Anything else refuses the run.

## Planning-chain integration (`planning_adapter.py`)

- **Stages:** `plan(document)` runs the unmodified stages in Track A's order. It returns one explicit outcome, with the stage and the stage's own code: `bridged`, `invalid`, `unsupported`, `infeasible`, `indeterminate` or `refused`. Nothing is repaired.
- **Typed rows:** the MVP takes the bridge's typed representation, not the legacy `SeedRow` projection, and inserts each value with its exact Python type.
- **Refusals:** an unknown tag, a bool posing as an int, a non-finite float, or a table or column outside the trusted context is refused. A bridge refusal stops the chain, even when its typed representation exists.
- **Handlers:** these are only the registry's entrypoints in `TRUSTED_OPERATIONS`, loaded from SHA-256-pinned sources. The buggy and fixed builds are Track A's harness-owned `lock_row` switch.
- **Initial state:** before running, it's read independently and compared with the requested rows by exact type and count. The run is then bound to that state's digest.
- **Catalog check:** against Track A's hand-written dry-run snapshot only. **No live catalog export.**

## Measured smoke test (`results/apps_compare_2`)

Budgets: K = 6 valid workloads per source, S = 60 schedules per workload per build, 2 Muse calls per target, 6 proposals per call.

| Target | Source | Valid | Executed | Workloads with a verified violation | Fixed-build violations | Schedules | Handlers reached |
|---|---|---|---|---|---|---|---|
| SQLAlchemy ledger | curated | 4/4 | 4 | 3 | 0 | 216 | all 3 |
| | systematic | 6/6 | 6 | 6 | 0 | 192 | all 3 |
| | Muse | 12/12 | 6 | 6 | 0 | 280 | all 3 |
| Planner path (WorkloadSpec v2) | curated | 1/1 bridged | 1 | 1 | 0 | 96 | `reserve_seats` |
| | systematic | 6/6 bridged | 6 | 5 | 0 | 564 | `reserve_seats` |
| | Muse | 6/12 bridged; 6 `unsupported` (`no_filler_policy`) | 6 | 5 | 1 (a real defect, below) | 618 | `reserve_seats`, `release_seats` |

- **Coverage:** all three ledger sources reached the same 7 application statements. Neither fixture has migration-touching statements; that measure applies only to `reservations_mixed`, which the earlier `mvp_compare.py` covered.
- **Muse's invariant hypotheses on the ledger:** 18 on the executed workloads, evaluated deterministically on every final state.
  - 7 detected the buggy build only: all 6 balance identities, plus one conservation hypothesis on a 3-account transfer ring.
  - 5 were already false on the initial state.
  - 6 were never violated.
  - None was violated on a fixed build.
- **A second defect:** Muse's planner document `double_release_same_holder` exposed a check-then-act defect in Gate 2A's synthetic `release_seats` that the "fixed" `lock_row` build doesn't fix. The server log corroborates it, and it's pinned by a test. That fixture is developer-written, so this is not a blind detection.
- **Cost:** about $0.149 estimated for this run (see `LEDGER.md`).

## Limitations

- **Trusted synthetic fixtures only.** All three were written with their defects known, so every comparison here is a smoke test, not a benchmark.
- **Exploration scope.** "Exhausted" means the decision frontier emptied within the budget, assuming each prefix re-executes to the same decision points. It isn't a completeness proof.
- **Statement allowlists.** These are reviewed captures (`ledger_orm.py` and `planning_adapter.ALLOWED_SQL`). A new ORM query shape needs a new reviewed entry.
- **Isolation.** No third-party code has been run. The two pinned candidates are async and blocked (`CORPUS_SHORTLIST.json`).
