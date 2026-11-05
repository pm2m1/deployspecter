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

## Status (2026-11-05)

- **Engineering release candidate `rc1`:** see `RELEASE_CANDIDATE.md`. Its manifest, `RELEASE_CANDIDATE.json`, pins the code at `d9890f8` with per-file hashes, and `python research_tools/rc_manifest.py verify --manifest docs/experimental_mvp/RELEASE_CANDIDATE.json` checks a checkout against it.
- **Real-application pilot:** Chancy 0.25.1 → 0.26.0 as a rolling deploy, NONBLIND. Corroborated and reproduced. **No defect was found.** See `results/real_app_pilot_chancy_mixed_1` and `demo_full_4`.
- **Mutation control:** a declared claim-lock mutant of Chancy 0.26.0 is distinguished by the oracle (`results/mutation_control_chancy_1`).
- **Muse:** in the NONBLIND pilot comparison it matched the deterministic sources and found nothing they missed (`results/real_app_pilot_chancy_compare_1`).
- **Protocol:** Amendment 003's five decisions are PROPOSED only (`protocol/DECISIONS_003.md`). Confirmatory research stays blocked. The independent-evaluation handoff package is in `handoff/`; no reviewer has been engaged.
- **Isolation:** hostile-grade isolation is BLOCKED (`results/isolation_runtime_scan_2`).
- **Simultaneous mixed-version execution: LIVE-TESTED (NONBLIND).** Chancy 0.25.1 and 0.26.0 workers ran at the same time, in two restricted containers, with one coordinator granting every statement. See `simultaneous/RESULTS.md` (`research_tools/mvp_demo.sh OUT simultaneous`).
  - The released code never ran a job twice in 10 seeded schedules; the claim-lock mutant did, in 2 of 10.
  - A cancel was lost in every cell except 0.26.0-cancel with a 0.26.0 worker. That is a mixed-deployment hazard outside the documented upgrade procedure, not a defect.

## Reproduce in one command

```bash
cd ~/Desktop/deployspecter-real-app-mvp && research_tools/mvp_demo.sh            # sync, as before
cd ~/Desktop/deployspecter-real-app-mvp && research_tools/mvp_demo.sh OUT all    # sync + async + isolated runner
cd ~/Desktop/deployspecter-real-app-mvp && research_tools/mvp_demo.sh OUT full   # all + PGQueuer and Chancy
```

The second argument selects the mode:

| Mode | What it adds |
|---|---|
| `sync` | the default |
| `async` | the async engine demos |
| `all` | sync and async, plus the isolated-runner smoke test |
| `thirdparty` | fetches and verifies the pinned sdists (PGQueuer 1.6.0, Chancy 0.26.0 and 0.25.1). In the isolated runner it then runs PGQueuer, the Chancy runtime, both negative controls, concurrent workers, the claim-lock mutant control and the mixed-version pilot |
| `full` | `all`, then `thirdparty` |
| `simultaneous` | fetches the pinned sdists, then runs Chancy 0.25.1 and 0.26.0 workers at the same time under the coordinator, in five scenarios |

The optional third-party tests live in `tests/thirdparty_isolated`, outside the core suite. They run when `DS_THIRDPARTY_SDIST_DIR` points at the fetched sdists.

The script runs these steps:

1. **Installs dependencies:** `uv sync --frozen --extra experimental`, which brings in SQLAlchemy 2.0.57 with asyncio and greenlet.
2. **Starts `deployspecter-mvp-pg`:**
   - pinned PostgreSQL 16 on tmpfs;
   - 1 GB, 2 CPUs, 256 PIDs;
   - `127.0.0.1:55441`, never 55432;
   - csvlog with `log_statement=all`;
   - WAL bounded to 64 MB.
3. **Runs the tests:** the offline and live tests.
4. **Re-executes three fixtures on their race schedules, buggy and fixed:**
   - the migration backfill (`reservations_mixed`);
   - the SQLAlchemy ledger (`ledger_orm`);
   - Gate 2A's synthetic `live_app.reservations`, seeded from a WorkloadSpec v2 document through the planning chain.

5. **`async` adds:** the async migration race, the SQLAlchemy asyncio ledger, and the release path (frozen `fixed` against the experimental `corrected`), all on the async engine.
6. **`all` adds:** the isolated-runner smoke test (`research_tools/isolated/run_isolated.py smoke`).

Each run prints the trace, the scheduler's decisions, the call sites, the oracle's verdict and the server log's verdict. It also writes an evidence bundle:
- code revision;
- workload hash;
- the executed functions' source files and hashes;
- initial and final digests;
- trace hash;
- the server-log reconciliation;
- a classification.

Teardown runs on every outcome.

Other commands each need a running instance: run `research_tools/mvp_pg.sh start` first and `stop` afterwards.

- `python -m deployspecter.experimental.demo --out DIR`: the migration race alone.
- `python -m deployspecter.experimental.demo_integrated --out DIR`: the ledger and planned reservations alone.
- `python research_tools/mvp_compare.py ...`: the earlier curated, systematic and Muse comparison on `reservations_mixed`.
- `python research_tools/mvp_compare_apps.py --workloads 6 --schedules 60 [--muse-calls 2] --out DIR`: the same comparison on the ledger and the planner path. It reads only `MODEL_*` from the main repository's `.env`, and `--muse-usd` caps it.
- `python research_tools/mvp_compare.py --engine async ...`: the `reservations_mixed` comparison on the async engine.
- `python research_tools/mvp_compare_release.py --workloads 6 --schedules 60 [--muse-calls 2] --out DIR`: the release path. It runs the frozen builds, the corrected reference and four mutants, with Muse hypotheses gated by the reference.
- `python research_tools/mvp_release_evidence.py --out DIR`: the release_seats evidence (see `RELEASE_PATH.md`).
- `python research_tools/isolated/run_isolated.py smoke|chancy --out DIR`: the isolated runner. `chancy` also needs `--chancy-sdist` pointing at the pinned `chancy-0.26.0.tar.gz`; its SHA-256 is re-verified.

## Async engine (`stepping_async.py`)

- **Driver classes:** `AsyncSteppedConnection` and `AsyncSteppedCursor` subclass psycopg's `AsyncConnection` and `AsyncCursor`. They're installed only through `cursor_factory` and `connect()`, and nothing is patched.
- **Gated:** `execute`, `executemany`, `commit`, `rollback`, and `transaction()` with savepoints. Each is gated on an `asyncio.Condition`, with one task per actor.
- **Refused:** named cursors (including SQLAlchemy `stream`), COPY, `stream`, pipelines, any statement outside the allowlist, and a second operation on one connection while one is in flight.
- **Task identity is causal.** A task the actor's own code spawns, or that a framework spawns on its behalf (SQLAlchemy closes an `AsyncSession` in a shielded child task), inherits the actor's context variable. Its operations record `on_behalf_of`. Another actor's task is refused.
- **Lock waits** are seen on a separate async monitor connection.
- **A halt:** it cancels blocked queries on the server, cancels any task still running after a grace period, records the cleanup ROLLBACK, and closes every connection. Tests check that no backend or lock remains.
- **Equivalence:** the async engine reproduces the sync engine step for step. On the migration race it explores the same schedule sets: buggy 61 schedules with 46 violating, fixed 30 with 0.
- **Scope:** exploring decision points isn't exploring every asyncio interleaving. CPU-bound handler code blocks the loop.

## Isolated runner (`research_tools/isolated/`)

- **Image:** a digest-pinned base, dependencies installed `--require-hashes --no-deps` from `uv.lock`, and the payload copied in, never mounted.
- **Network:** a fresh `--internal` network shared only with the disposable PostgreSQL.
- **Container:** uid 65534, read-only root, a 64 MB noexec tmpfs, all capabilities dropped, `no-new-privileges`, 64 PIDs, 512 MB memory and no swap, 1 CPU, and a wall-clock kill. Its environment holds only the disposable credentials.
- **Probes:** the container reports what it observes (identity, filesystem, secrets, the host and 55432, the internet, DNS and cgroup limits), and the host refuses the run unless every probe matches. Corroboration and the oracle run on the host. Teardown removes the container, the network and the work directory, and checks the protected container is unchanged.
- **Not a hostile-code sandbox:** it shares the Docker Desktop VM's kernel and has no gVisor or Kata.
- **Third-party run (`results/third_party_chancy_capture_1`):** chancy 0.26.0's own migration applier ran in it through its public API, `Migrator.migrate(conn)`, on a stepped async connection. That's a capture, with no allowlist, no defect hypothesis and no oracle. The server log corroborates all 36 statements, and six tables were created.

## Third-party runtimes in the isolated runner (`experimental/thirdparty/`)

Both adapters are pinned to their library version and source hashes, accept only a stepped connection, and patch nothing.

| Library | Seam | Result | Negative control (must be refuted) |
|---|---|---|---|
| PGQueuer 1.6.0 | Its public `Driver` port. `PsycopgDriver`'s `fetch`/`execute` build `AsyncSteppedRawCursor` instead of a plain `AsyncRawCursor`. LISTEN, `pg_notify` and the `notifies()` watcher are PGQueuer's own | Install, enqueue, then a drain-mode worker: claim, trusted handler, finish, shutdown. The server log corroborates 37 statements (62 values compared, 10 unchecked); oracle Q1–Q3 holds (`results/third_party_pgqueuer_lifecycle_1`) | `Queries.from_psycopg_connection(conn)`: its direct `AsyncRawCursor` bypasses the gate, and the server log refutes the run (`…_public_factory_refuted_1`) |
| Chancy 0.26.0 | A `SteppedChancy` subclass overrides only `pool`: a one-connection `AsyncConnectionPool` whose `connection_class` hands over the actor's stepped connection once. Options: `notifications=False`, `no_default_plugins=True`, `AsyncExecutor` | Migrate, declare, push, worker start, the job claimed and run, `succeeded`, worker stop, pool close. The server log corroborates 86 statements (63 values compared, 16 unchecked); oracle C1–C3 holds (`results/third_party_chancy_runtime_1`) | `notifications=True`: the worker's own LISTEN connection is an unexplained session (`…_notifications_refuted_1`) |

The async engine supports these with three opt-in features:
- gated raw (`$n`) cursors;
- recorded NOTIFY deliveries;
- `Handler.serialize_tasks`, which lets a framework's tasks take turns on one connection.

The server-log check compares one-dimensional arrays and `bytea` exactly.

**Unsupported paths:**
- Chancy's sync APIs (`sync_pool`), and its LISTEN listener;
- PGQueuer's asyncpg drivers;
- any library code that builds its own connection or cursor outside the seam. The server log catches these.

**Hostile-grade isolation is BLOCKED:** only `runc` is available (`results/isolation_runtime_scan_1`, `_2`).

**Stopping a framework.** `AsyncRun.quiesced(actor)` holds new operations from the actor's earlier tasks before the gate, and lets the statement in flight finish. Inside it, a framework's `stop()` cancels those tasks at the gate, and each is recorded as `cancelled_while_held`. Without the hold, Chancy's `Worker.stop` can cancel a statement on the wire. The server log then refutes the run, which is what happened in `results/demo_full_3`.

## Django (`experimental/django_backend`)

A trusted backend subclasses Django 5.2's PostgreSQL `DatabaseWrapper`.
- **Connections:** it hands each actor thread its stepped connection, and refuses reopening, pooling, wrong targets and server-side cursors.
- **Binding modes:** both of Django's psycopg 3 modes are gated. In client-side binding, the merged statement text is compared with the server log by digest.
- **Fixture:** `apps/django_fixture.py`. The schema comes from the app's own migration, applied through the gate.

See `CAPABILITY_MATRIX.md` for every supported, refused and unsupported path.

## Live catalog verification (`catalog_export.py`)

`planning_adapter.run_planned_live` creates the trusted schema fresh and exports its catalog read-only. Then it plans with that snapshot, under its own trust labels, and refuses unless the unmodified `verify_context` says MATCH. After the planned reset it re-exports, and refuses if the structure changed. On a database holding only the planned schema, the live export is structurally identical to Track A's dry-run fixture.

Refusals, all tested:

| Live schema | Outcome |
|---|---|
| A user trigger elsewhere, writes unknown | `indeterminate` |
| A CHECK constraint | `unsupported` |
| A renamed column | `invalid` |
| A changed type | `invalid` |
| A change between export and run | refused |

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
- **Third-party scope.** PGQueuer 1.6.0's lifecycle and Chancy 0.26.0's job runtime run through version-pinned adapters, in a restricted container that is not hostile-grade. Neither is a blind evaluation case. Candidates and open rulings are in `CORPUS_MANIFEST.json` and `protocol/`.
- **Release path.** See `RELEASE_PATH.md`. The frozen `fixed` build is not a valid negative control for `release_seats`.
