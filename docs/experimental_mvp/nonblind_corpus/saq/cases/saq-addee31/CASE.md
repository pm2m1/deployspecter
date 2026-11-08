# Case `saq-addee31`: a session-scoped init lock makes a second queue skip its migrations (NONBLIND development case)

**Frozen before the case's first live run.** Later corrections go in `ERRATA.md`.

## The upstream defect

| | |
|---|---|
| Repository | https://github.com/tobymao/saq (MIT) |
| Fix | `addee31a697c42d292c5fd05aa107c157b0ee8e0`: "fix: make init_db advisory lock transaction-scoped". "init_db acquired a session-level advisory lock on a pooled connection and never released it, so the lock lived as long as that connection. Any other queue running init_db meanwhile silently skipped migrations and then failed with UndefinedTable". One statement in saq/ (`pg_try_advisory_lock` to `pg_try_advisory_xact_lock`), plus a test-helper change. No regression test. First release with it: v0.26.5 |

| Slot | Commit | Tree | Archive sha256 (normalized `git archive` of `saq/`) |
|---|---|---|---|
| R1 (buggy, the fix's first parent) | `f827b529d48557e1a156f0207121aaf638a1035d` | `3c5a2543e935089ca8e301283a262142f6b30ef8` | `32212a41573c1521610f1efa0d5de89ff275a2811053bd4a190384d1821e06e8` |
| R2 (fixed, the fix commit) | `addee31a697c42d292c5fd05aa107c157b0ee8e0` | `a3716f1d42d1716ab09a3ccb802a4fc14a4fb029` | `d6ac6adacf68999abe26b32fe00f82e2e25b0114fca8a8f29563c9c943c08c41` |

Reproduce the archives: `python -I research_tools/nbc/pin_saq.py --repo <mirror of https://github.com/tobymao/saq> --out <dir>` (verifies commit, tree and sha256).

## Contract and property

Single-version: one revision at a time, on a fresh schema. The deployment is two queues of that revision in one database, the second configured with the other table set through `PostgresQueue`'s documented `jobs_table`/`stats_table`/`versions_table` arguments ("versions_table: name of the Postgres table SAQ will use to maintain migrations"). Both use saq's default `saq_lock_keyspace`. Property (`oracle.py`, Q1-Q2): the second queue's `connect` and `enqueue` calls succeed and its jobs are in its table. This is the weakest-sourced saq case: the docstring defines the tables but no doc or test states the init behaviour; the commit message is the maintainers' statement of the defect.

## Workloads (RA-DET, frozen in `saq_two_queues_addee31.py`)

Shape `two_queues`: the first queue connects, enqueues its jobs and runs a worker; the second queue starts once the first worker has polled (gate), enqueues jobs, and returns; then the first worker is stopped.
- W1: first `default` (1 job), second `alt` (1 job), poll 0.5 s.
- W2: first `alt` (0 jobs), second `default` (2 jobs), poll 0.5 s.
- W3: first `default` (2 jobs), second `alt` (3 jobs), poll 1.0 s.

## Predictions (written before running)

- R1: the first queue's session still holds the init lock, so the second queue's `init_db` gets `false`, skips its migrations, and its first `enqueue` fails with `UndefinedTable` (Q1). Expected in every workload and schedule: the order is fixed by the gate, not by the explorer.
- R2: the lock ended with the first `init_db`'s transaction; the second queue migrates its tables; Q1 and Q2 hold.

## Competing explanations, recorded before running

1. The gate is what makes the second `init_db` run after the first committed. Without it, the second could hit the first's still-open transaction and skip in **both** revisions. That's a separate behaviour, which this case doesn't test.
2. saq's index names are fixed (`saq_jobs_*_idx`), so the second table set's migration reuses or skips some index names. This is expected in R2 and doesn't affect Q1-Q2.

## How it runs

`run_nbc.py run --arms RA-DET` under `pg_lock.py`, with the plan's §5 budgets (B = 12 per workload: 3 replicates × 4 schedules, one workload per replicate; T = 900 s per case and arm; never retried). Each schedule is one restricted container (CPython 3.12.16 runner image, saq tree copied in, hash-pinned dependencies, no network) plus the coordinator, on a fresh schema `saq_nbc` migrated by that revision's own `connect()`; every statement is granted by the coordinator under the seeded explorer (`real_executor.seed_for`); the merged trace is corroborated against the server log; the oracle decides only a SOUND run. Integration machinery: `research_tools/nbc/integrations/saq_common.py`, `saq_shapes.py`; container side `src/deployspecter/experimental/thirdparty/saq_coord_adapter.py` (a declared variant of the pilot adapter, with no code deviation in saq) and the `saq_coordinated` mode of `smoke_inside.py`.

**Label: NONBLIND development.** The case was chosen after reading the fix. The oracle is developer-written from the documented contract. A REPRODUCED result reproduces a documented upstream defect; it is never a new finding.

## Exposure (who read what, before which step)

All by the Track A agent on 2026-11-08 (UTC), recorded in `case_spec.json` `exposure`: the 12 screened candidates' messages and stats (04:49Z); this fix's diff and the neighbouring fixes' diffs (04:51-04:54Z); saq's docs, the Worker docstring and the maintainers' burst tests (04:54Z); the full parent `postgres.py`/`worker.py` sources and the diffs between the pinned revisions (05:00-09:40Z); bring-up runs on v0.26.5 (`../../bringup/`). All of it before the integration, workloads and oracle were written, and before any live run of R1 or R2. Training-data exposure of the public repository can't be excluded.
