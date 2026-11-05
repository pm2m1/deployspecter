# Rolling-upgrade case procrastinate 3.3.0 → 3.4.0: results (EXPLORATORY DEVELOPMENT CASE, NONBLIND, 2026-11-06)

**Result: a real, contract-following blue-green upgrade was executed. Old and new application code ran at the same time on the documented compatible schema, and no application property was violated (negative result).**

All 5 evidence runs are SOUND in all 7 phases: isolated, coordinated to completion, and corroborated against PostgreSQL's server log, with 0 values mismatched. The only frozen-checker entries are two harness oracle errors, E1 and E2 (`ERRATA.md`), and they are reported as such.

This is not a blind or confirmatory case. procrastinate is a frozen-protocol development application, and its migration area is NEEDS_PROTOCOL_RULING under the frozen rule (#21, proposed).

## Provenance

| | |
|---|---|
| Code revision of every evidence run | `5e45cc1` (clean) |
| Frozen before any run (`dc64cc2`) | `CASE.md` `775685f1…`, checker `procrastinate_properties.py` `9c9a59d3…`; both hashes recorded in every report |
| Old: 3.3.0 | sdist `5e03de13…`, tag `cbaa7566…`, image `ds-mvp-iso:c1cb43a112468b9f` (`sha256:d552e047…`) |
| New: 3.4.0 | sdist `9cd3af41…`, tag `41aec8f7…`, image `ds-mvp-iso:ccd1c3bc627bed2f` (`sha256:191b6a75…`) |
| Dependencies | `research_tools/isolated/requirements.procrastinate.txt`: the closure pinned at 3.4.0's release date (2025-07-18), by wheel sha256. psycopg 3.3.6 and psycopg-pool 3.3.3 come from the image |
| Version identity | verified inside each container: `queries.sql` and `schema.sql` sha256. The two releases' Python sources are byte-identical; their only query difference is `retry_job` (v1 → v2) |
| Migrations | `03.04.00_01_pre…` `6883b733…` and `03.04.00_50_post…` `ac4021eb…`, executed verbatim from the package, sha256-checked |

## What ran, in contract order (each phase's precondition checked by the host before it started)

| Phase | Precondition (host, from the catalog) | Statements corroborated (alternate run 1) |
|---|---|---|
| 0a: 3.3.0 applies its own schema | empty `public` schema | 3 |
| 0b: old code on the old schema (defer 3 jobs, drain) | 3.3.0 schema, no `retry_job_v2`, 0 jobs | 51 |
| 1: the `pre` migration (operator step) | `v1` present, `v2` absent, the trigger without `retried` | 3 |
| 2: old code on the `pre`-migrated schema (defer 2, drain) | `v2` present, the trigger without `retried` | 36 |
| **3: overlap.** 3.3.0 producer and worker **and** 3.4.0 producer, worker and admin, at the same time | `v2` present, the trigger without `retried` | **129** |
| 4: the `post` migration, after every 3.3.0 process had exited | the same | 3 |
| 5: new code alone (defer 2, admin retry, drain) | the trigger records `retried` | 57 |

In the overlap, the window gate held every producer and admin statement until both workers had claimed. There were 5 distinct sessions:
- 3.3.0: `old_defer`, `old_worker`;
- 3.4.0: `new_defer`, `new_worker`, `new_admin`.

| Run | Policy | Overlap grants (3.3.0 / 3.4.0) | Version switches | v1 retries (3.3.0) | v2 retries (3.4.0 worker) | v2 admin retries | Jobs run by the other release | Jobs run by both | Statements, all phases | Unchecked values |
|---|---|---|---|---|---|---|---|---|---|---|
| `final_alternate_1` | alternate | 35 / 34 | 68 | 2 | 2 | 1 | 6 | 2 | 282 | 20 |
| `final_alternate_2` | alternate | 35 / 34 | 68 | 2 | 2 | 1 | 6 | 2 | 282 | 20 |
| `final_seeded_1` | seeded 1 | 27 / 40 | 39 | 2 | 2 | 1 | 8 | 3 | 282 | 20 |
| `final_seeded_2` | seeded 2 | 39 / 49 | 37 | 2 | 2 | 1 | 5 | 4 | 315 | 20 |
| `final_seeded_3` | seeded 3 | 30 / 40 | 35 | 3 | 2 | 1 | 9 | 3 | 291 | 20 |

**Unchecked values.** These are parameters the server-log reconciler counts but can't compare value by value: the composite-array argument of `procrastinate_defer_jobs_v1`, and float intervals. They are **not** accepted as matching. That is a stated limitation of the corroboration for this application.

## Properties

| Property | Result in 5 runs |
|---|---|
| R1 terminal (after a quiescent completion) | holds 5/5 |
| R2 documented bound (at most `max_attempts + 1` executions per retry cycle) | holds 15/15 jobs × 5 |
| R2 equality as frozen | **oracle error E1**: `attempts` counts finished executions plus admin retries (`finish_job_v1` increments it), not retries alone. The source-defined relation holds 15/15 × 5 |
| R3 outcome matches the last execution | holds 5/5 |
| R4 cross-version execution | holds 5/5: every job ran, including jobs deferred by one release and run by the other |
| R5 admin retry (3.4.0's new feature, during the overlap and after the cutover) | holds 5/5 by its documented effect: before `failed`, `attempts + 1`, the job ran again. In `final_seeded_2` the frozen "after" read saw `doing`, which is **oracle error E2**: a worker had claimed the job between the retry and the read |
| R6 events | holds 5/5: an overlap admin retry (before `post`) leaves **no** `retried` event; the post-cutover retry leaves one. Both are consistent with `post` being applied "after upgrading the code" |
| R7 contract order | holds: `post` ran only after the overlap's 3.3.0 container exited |

**Notable, executed.** In the overlap, a job that 3.3.0 had failed was retried by 3.4.0's new admin feature through `retry_job_v2`, and then **executed again by a 3.3.0 worker**. Jobs failed and retried by one release ran next on the other. Nothing in the final state contradicted the contract.

**Repeatability.** The two `alternate` runs gave identical statement counts and identical coverage. The seeded runs differ, as intended.

## Evidence (sha256 over all files, sorted)

| Directory | Digest |
|---|---|
| `runs/bringup` | `e2562740ed0ab34c` |
| `runs/final_alternate_1` | `ba1713c4e0ae11e1` |
| `runs/final_alternate_2` | `a60133f45e44715a` |
| `runs/final_seeded_1` | `696ebb421159c178` |
| `runs/final_seeded_2` | `81355632ca8d1294` |
| `runs/final_seeded_3` | `d9c28ce10d538ae6` |

## Reproduce (disposable PostgreSQL running)

```bash
python research_tools/isolated/run_rolling_upgrade.py --old-sdist SDISTS/procrastinate-3.3.0.tar.gz \
  --new-sdist SDISTS/procrastinate-3.4.0.tar.gz --policy '{"kind": "alternate"}' --out OUT/run
```

The sdists are fetched by hash from PyPI (the URLs and sha256 are in `run_isolated.PROCRASTINATE_SDISTS`).

## Limits

- **NONBLIND:** the oracles and workloads were developer-encoded.
- **Execution route:** LISTEN/NOTIFY disabled (polling); restricted runc, not hostile-grade isolation.
- **Exploration:** 5 schedules is not exhaustive.
- **Unchecked values:** as stated above.
