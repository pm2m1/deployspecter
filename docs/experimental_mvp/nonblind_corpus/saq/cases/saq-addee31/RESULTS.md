# Case `saq-addee31`: results (NONBLIND development case, 2026-11-08)

**Classification (rule §5): REPRODUCED in attempt 3, with a disclosed post-hoc re-encoding.** Attempt 3's
integration v2 and `oracle_v2.py` were written **after** attempt 2 had shown R1's behaviour in this shape
(ERRATA E2). That makes this the weakest of the saq results: a corroborated reproduction of a documented upstream
defect, not a pre-registered test of it. Nothing here is a new finding, blind, confirmatory or independent
evidence.

## Attempts

| Attempt | Integration / oracle | Outcome | Why |
|---|---|---|---|
| `reproduction_1` (09:46-09:53Z) | v1 / `oracle.py` | 3 runs REFUSED, then interrupted | The image held a stale `saq/__init__.py` (ERRATA E1). The in-container pin check refused before any saq code ran |
| `reproduction_2` (09:55-09:57Z) | v1 / `oracle.py`, E1 build fix | R2 HOLDS 12/12; **R1 REFUSED 3/3** (no R1 verdict) | R1's second queue skipped its migrations and its INSERT failed at parse (`UndefinedTable`). The corroborator can't pair a parse-time ERROR (ERRATA E2) |
| `reproduction_3` (10:04-10:05Z) | v2 (`0926d8c9…`) / `oracle_v2.py` (`1da3266e…`), spec `case_spec_v2.json` (`7d55198e…`) | **REPRODUCED** | below |

## Attempt `reproduction_3`

Code revision `7e867b8` (clean). Pinned trees as in the spec.

| Replicate (workload) | R1 `f827b52` | R2 `addee31` |
|---|---|---|
| 0 (W1: first `default` 1 job, second `alt`) | **VIOLATED at schedule 1**: `saq_alt_*` tables absent after the second queue's `connect()` returned | HOLDS 4/4: all six tables present |
| 1 (W2: first `alt`, second `default`) | **VIOLATED at schedule 1**: `saq_jobs`, `saq_stats`, `saq_versions` absent | HOLDS 4/4 |
| 2 (W3: first `default` 2 jobs, second `alt`, poll 1 s) | **VIOLATED at schedule 1** | HOLDS 4/4 |

- Schedules to the first violation: **1** (2.7 s). R1 used 3 schedules; the fixed control used 12, all HOLD.
- All 15 runs SOUND. Corroboration totals: **480 statements matched, 376 typed parameters compared, 0 mismatched,
  0 unchecked**. 47.7 s in all, not censored by T.

**What R1 did** (coordinator decisions): the second queue's whole `init_db` was `BEGIN`,
`SELECT pg_try_advisory_lock(%(key1)s, 0)` (false: the first queue's session still held it), `COMMIT`. It returned
without creating its tables, and `connect()` reported success. On R2, `pg_try_advisory_xact_lock` succeeded, because
the first queue's lock had ended with its own init transaction, and the second queue migrated its table set.

**Also seen, not part of the property:** in both trees the first queue's worker, stopped by the run right after the
second queue's actor returned, leaves the job it had just claimed `active`. That is saq's non-burst stop, before
the sweep runs. It is outside this case and isn't asserted.

**Determinism.** The gate fixes the order that matters (the second queue starts after the first worker's poll), so
seeds don't change the outcome.

## What this does not show

- Pre-registration of the v2 property (see the classification note).
- Independent discovery or oracle authorship; anything about saq's LISTEN path; any statistic (one case).
