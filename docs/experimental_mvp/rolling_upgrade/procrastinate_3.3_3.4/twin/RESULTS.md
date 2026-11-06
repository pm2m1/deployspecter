# Supported digital twin of procrastinate 3.3.0 → 3.4.0 (EXPLORATORY DEVELOPMENT CASE, NONBLIND, 2026-11-06)

**Classification: COMPLETE WITH SUPPORTED-SCOPE LIMITATIONS.**

The documented blue-green contract was executed four times as seven modeled phases (11 steps). Every phase's entry and exit conditions were checked by the host. Every step was SOUND in all four runs, and no application property was violated.
- **Backfill:** NOT SUPPORTED, because this upgrade pair has none.
- **Scope:** this is a supported experimental twin for one application pair. It is not a production deployment controller, and nothing in it is blind or confirmatory.

## What it is
- **Model:** `src/deployspecter/experimental/twin/procrastinate_twin.py`, host side only, not in the runner image.
- **Runner:** `research_tools/run_twin_procrastinate.py`. It reuses, unchanged, the rolling-upgrade case's phase specs and app hooks (`research_tools/isolated/run_rolling_upgrade.py`) and the coordinated isolated runner.
- **Frozen material:** `CASE.md` and the frozen checker are untouched, and their sha256 is recorded in every report.
- **First failure ends the run:** the first step that isn't SOUND ends it, as REFUSED_ENTRY, REFUSED_RUN or REFUSED_EXIT, with no retry.

| Phase | Steps | Versions | Entry condition (checked) | Exit condition (checked) |
|---|---|---|---|---|
| T1 old on old | `0a_old_schema`, `0b_old_on_old_schema` | 3.3.0 | `public` empty | 3.3.0 catalog (`retry_job_v1`, no `v2`); 0b leaves the catalog unchanged; every job terminal; no registered worker |
| T2 pre-migration (expand) | `1_pre_migration`, `2_old_on_pre_schema` | 3.4.0 operator, then 3.3.0 | catalog equals T1's exit | `pre` is **purely additive** (catalog diff has additions only: the `retried` enum label and `retry_job_v2`); **job rows unchanged** by it; old code still runs on it; catalog unchanged by step 2 |
| T3 simultaneous | `3_overlap` | 3.3.0 + 3.4.0 | catalog equals T2's exit | both versions executed, **interleaved in one global grant order** (version switches ≥ 1); catalog unchanged; no `doing` job; no registered worker |
| T4 background-job interaction | `T4_background_retry` | 3.3.0 + 3.4.0 | catalog unchanged; at least one `failed` job (n=4 is reserved for phase 5); targets chosen deterministically | catalog unchanged; no `doing` job; no registered worker |
| T5 old-version quiescence | `T5_quiescence` (host only) | none | T4 sound | no 3.3.0 container; no client session; no row in `procrastinate_workers`; no `doing` job; no foreign lock; no prepared transaction |
| T6 post-migration, then new only | `4_post_migration`, `5_new_only` | 3.4.0 | **T5 quiesced**; catalog equals T4's exit | `post` changed **only** the status-events trigger function; job rows unchanged by it; phase 5 used only 3.4.0; every job terminal |
| T7 final-state verification | `T7_final_verification` (host), `R_fresh_3.4.0_schema` | 3.4.0 | T6 sound | frozen checker, with E1/E2 split per `ERRATA.md`; contract order; upgraded catalog compared with a **fresh 3.4.0 install** |

**Recorded for every container step:**
- application version identity: package version, sdist sha256, and the image's pinned-source hashes;
- entry and exit catalog digests and state digests;
- the catalog diff;
- permitted operations: DDL only in schema or migration steps, only the phase's versions, no `retry_job_v2` from 3.3.0;
- session and transaction provenance per actor, cross-checked against the server log's session attribution;
- the global interleaving;
- server-log corroboration (typed parameters, exact);
- isolation and teardown.

**Backfill: NOT SUPPORTED.** Neither pinned migration contains a row-modifying statement outside a function body:
- `pre` (`6883b733…`): `ALTER TYPE ... ADD VALUE` and `CREATE FUNCTION`;
- `post` (`ac4021eb…`): a trigger and its function are replaced.

The detector (`data_only_statements`) has offline tests that find a real `UPDATE`/`COPY` backfill and ignore function bodies, comments, and trigger or foreign-key event clauses.

## Runs (all evidence kept; nothing retried)

| Run | Policy | Revision | Exit | All 11 steps | Statements corroborated | Params compared / mismatched / unchecked | Overlap switches | T4 retried (n → state at read) | T4 executed by | Genuine violations | E1 | E2 | Upgraded = fresh 3.4.0 catalog |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `alternate_1` | alternate | `c23e259` | 0 | SOUND | 360 | 298 / 0 / 0 | 84 | 1, 2, 8 → todo | 3.3.0 worker only | 0 | 15 | 0 | yes (`554bf337…`) |
| `seeded_1` | seeded 1 | `c23e259` | 0 | SOUND | 387 | 316 / 0 / 0 | 46 | 1, 2 → todo; 7 → doing | 3.4.0 worker only | 0 | 15 | 1 | yes |
| `seeded_2` | seeded 2 | `c23e259` | **1** | SOUND | 411 | 332 / 0 / 0 | 47 | 1 → todo; 2 → doing; 6 → todo | both | **1 as recorded, 0 reclassified** (see below) | 15 | 1 → 2 | yes |
| `seeded_3` | seeded 3 | `24112b4` | 0 | SOUND | 387 | 319 / 0 / 0 | 43 | 1, 2, 7 → todo | both | 0 | 15 | 0 | yes |

**What the four runs show:**
- **Quiescence (T5)** was verified in 4/4 runs, with nothing in flight and no `todo` job left. `post` ran only after it (contract order 4/4).
- **Pre is expand-only.** In 4/4 runs it was additive and left job rows unchanged. **Post changed only** `procrastinate_trigger_function_status_events_update_v1`, again leaving job rows unchanged, in 4/4.
- **Schema identity.** The schema reached by 3.3.0 + `pre` + `post` is byte-identical, by the catalog model, to a fresh 3.4.0 install in 4/4 runs (digest `554bf337006d…` every time).
- **T4, executed.** Jobs that 3.3.0 had failed were retried by 3.4.0's admin feature. They were then executed by the 3.3.0 worker (`alternate_1`), by the 3.4.0 worker (`seeded_1`), or by both (`seeded_2`, `seeded_3`), with no property violated.

**`seeded_2`'s recorded "genuine violation" was a classifier defect (a harness error, not an application finding).**
- **What happened.** Job n=1 is admin-retried twice: in the overlap, and again in T4. The frozen checker's R5 entry names the overlap retry: before `failed`/2, after `doing`/3, which is exactly erratum E2's pattern.
- **The defect.** The twin's classifier keyed retries by `n`, so it matched the entry to the T4 retry.
- **The fix.** It is in `24112b4`, with a regression test that failed on the old code. The run's `report.json` and its exit status 1 are preserved unchanged. `RECLASSIFICATION.json` beside each run re-applies the fixed classifier to the saved checker output: `seeded_2` changes to 0 genuine and 2 E2, and the other runs are unchanged.
- **Confirmation.** `seeded_3` ran on the fixed revision.

**A failed invocation is preserved.** `_failed_invocations/seeded_3_attempt0` exited 127 because zsh didn't word-split an environment prefix. Nothing executed.

## Limits
- **One supported pair** (procrastinate 3.3.0 → 3.4.0) and **one contract.** It is a development application under the frozen protocol, and its migration area is NEEDS_PROTOCOL_RULING (#21, proposed). The run is NONBLIND.
- **Background interaction** is limited to what this pair supports (3.4.0's admin retry of failed jobs while both versions' workers run). No periodic jobs, no LISTEN/NOTIFY (disabled, as in the case), no backfill.
- **The catalog model** covers columns, column order, constraints, indexes, functions (exact and whitespace-normalized body hashes), triggers, enums, composites, sequences and views in `public`. It does not cover privileges, comments or extensions.
- **Quiescence is verified at one point** (before `post`) from the host's view: containers, sessions, the worker registry, jobs, locks and prepared transactions. It is not a proof that no old code can ever run again.
- **Scheduling is statement-level only,** under the existing ds-coord/1 coordinator.

## Reproduce
```
python research_tools/pg_lock.py -- env -u MODEL_API_KEY -u DATABASE_URL \
  python research_tools/run_twin_procrastinate.py --old-sdist SDISTS/procrastinate-3.3.0.tar.gz \
  --new-sdist SDISTS/procrastinate-3.4.0.tar.gz --policy '{"kind":"seeded","seed":3}' --out NEW_DIR
python research_tools/twin_reclassify.py RUN_DIR
pytest tests/experimental/test_twin_offline.py
```
