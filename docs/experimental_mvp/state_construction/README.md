# State construction: results (EXPERIMENTAL, 2026-11-06)

**Classification: COMPLETE WITH SUPPORTED-SCOPE LIMITATIONS.**

Phase 9B's deterministic constructor now runs inside the full WorkloadSpec v2 pipeline, and a real application (Chancy
0.26.0) consumes a constructed state. The constructor's json/jsonb and NaN gaps, and the reservations-only reach of
the planning chain, are BLOCKED as described below. None of this is Gate 2A/A2 evidence, and none of it is blind.

## What runs end to end

**Gate 2A's synthetic reservations application** (`pipeline.run`):
1. A WorkloadSpec v2 document goes through parse, authorize, validate and plan identity, then becomes state goals and
   deterministic candidate rows.
2. The catalog is verified (MATCH only), and the typed bridge produces a typed representation plus a legacy
   projection.
3. Phase 9B's `construct_state` runs, unmodified, in its own sandbox. Only `PROVEN` classifications continue.
4. The run database gets an exact typed construction, in the constructor's order.
5. Three independent checks follow:
   - an exact typed re-read on its own session (one-to-one, with exact counts);
   - full `to_jsonb` equality with the constructor's independently verified state;
   - Track A's exact-type initial-state check.
6. The run uses the real handlers on the stepping engine, bound to the verified digest, then server-log corroboration,
   then the oracle (R1/R2/R5).

**Chancy 0.26.0** (narrow path, `chancy.py`):
1. A job-list workload becomes goals and deterministic `chancy_jobs` rows (uuid, text, varchar, jsonb, integer,
   timestamptz, NULL).
2. Live-catalog preconditions are checked: the pinned columns and types, and no triggers, rules, policies, foreign
   keys or deferrable constraints on the table.
3. One transaction of exact typed inserts, then an exact independent re-read.
4. Chancy's **unmodified worker**, in the isolated runner, runs bound to the verified state's digest.
5. Server-log corroboration, then an independent oracle (C1–C4).

## Final evidence (`runs/`, all from a clean tree)

| Run | Code | Exit | SUMMARY sha256 | Directory digest* |
|---|---|---|---|---|
| `reservations` | `6803398` | 0 | `43788d6241e5aea9…` | `b25566d2260a2c08` |
| `fixtures` | `6803398` | 0 | `233db170dc3c649c…` | `0c525886b7a6d5d2` |
| `chancy` | `79719f0` | 0 | `f99793335c29774a…` | `985da2fe12fc5cfb` |
| `reservations_attempt_1_inconsistent_document` | `79719f0` | **1** | `11730d0875e74dae…` | `f89ba080a637e408` |
| `fixtures_attempt_1_foreign_schema_counted` | `79719f0` | **1** | `a9488718b26e8c11…` | `612cfe0c31a3f43e` |

\*The digest is the sha256 of the sorted `shasum -a 256` lines of every file, computed from the repository root
(those lines include paths).

### Reservations
| Document | Build | Catalog | Phase 9B | Outcome | Oracle | Parameters compared / mismatched / unchecked |
|---|---|---|---|---|---|---|
| Track A document | buggy | dry run | ORIGINAL_VALID | ran | R1, R2 violated (the frozen scenario's known lost update) | 12 / 0 / 0 |
| Track A document | fixed | dry run | ORIGINAL_VALID | ran | holds | 10 / 0 / 0 |
| plus a held reservation (serial id) | buggy | dry run | ORIGINAL_VALID | ran | R1, R2 violated | 12 / 0 / 0 |
| plus a held reservation | fixed | dry run | ORIGINAL_VALID | ran | holds | 10 / 0 / 0 |
| Track A document | fixed | **live** | – | **refused_planning** (indeterminate, `trigger_effect_unknown`) | – | – |
| unknown field | fixed | dry run | – | refused_planning (`plan_state`) | – | – |
| infeasible goal | fixed | dry run | – | refused_planning | – | – |

Every run that reached the oracle had empty typed problems, an equal constructor snapshot, and corroboration ok.

**Why the live catalog mode refused.** The live export lists triggers for the whole database. `mvp_app.events`
carries `events_sync_reserved_v2`, left by the core suite's mixed-version app (recorded in
`reservations_database_triggers_probe.json`), and a trigger anywhere could write into the schema. So planning is
fail-closed `indeterminate`, a correct refusal. The dry-run mode verifies against Track A's pinned snapshot, as
`planning_adapter.run_planned` does. Everything after that check is live in both modes.

### Trusted fixtures (17, all as expected)
- **Constructed and exact-verified:**
  - int8 extremes, `-0.0`, 5e-324, `'NULL'` vs NULL, int4 max;
  - a trigger-dependent reordering (`CONSTRUCTED_EQUIVALENT`, order [1, 0]);
  - a dependency order (`CONSTRUCTED_EQUIVALENT`);
  - uuid + timestamptz without json (`ORIGINAL_VALID`).
- **Refused by Phase 9B, and nothing inserted:** a CHECK violation (IMPOSSIBLE), NaN, json and jsonb.
- **Refused by type, before anything:** bool for bigint, int4 overflow, an int tag holding a float, a non-canonical
  uuid, a naive timestamp, varchar overflow, NULL in a NOT NULL column.
- **Recorded as Phase 9B semantics:**
  - A dynamic-SQL trigger and an identical trigger-made audit row both come out `ORIGINAL_VALID`. The constructor
    accepts any state that contains every requested row, so a trigger's extra rows are allowed.
  - The `to_jsonb` equality check pins down the whole resulting state.

### Chancy (real application)
- **Form calibration:** a job Chancy pushed itself vs the translator's row for it showed 0 differences, apart from
  id and the two clock columns.
- **Constructed run:** jobs n=0, 1, 2 pending, and n=7 constructed as already succeeded.
  - The worker handled exactly {0, 1, 2}, each once, and never 7.
  - The oracle held, and the run was bound to the verified digest.
  - Corroboration: 131 statements, 90 parameters compared, 0 mismatched, 0 unchecked.
- **Negative control:** one row's func was corrupted, and the oracle caught it with C2, C3 and C4. The run itself was
  sound.

## Supported scope
- Phase 9B path: tables whose requested values are int, float (finite, not NaN), bool, text/varchar, uuid,
  timestamptz or NULL, at most 10 rows, with the constructor's trigger, rule and order handling.
- WorkloadSpec v2 path: only the registered Gate 2A reservations entities. The registry and contracts are
  Track A's.
- Direct path: chosen by a fixed rule, used only when a value is json or jsonb, and only for tables that meet
  the live-catalog preconditions (no triggers, rules, policies, FKs or deferrable constraints). That makes insertion
  order irrelevant.

## BLOCKED (with reasons)
1. **Phase 9B on json/jsonb columns.** Its matcher compares the stored `to_jsonb` object with the requested string,
   and reports IMPOSSIBLE. It is not worked around: those tables go through the direct path only when its
   preconditions hold, and are refused otherwise. Fixing this needs a change to the constructor, which is never
   edited here.
2. **Phase 9B on NaN.** NaN ≠ NaN in its float comparison gives IMPOSSIBLE. Refused.
3. **WorkloadSpec v2 for third-party applications.** The registry, contracts and context cover only the synthetic
   reservations entities. No Chancy or procrastinate registry has been reviewed, so Chancy's goals come from the
   narrow job-list translator, not WorkloadSpec v2.
4. **The live catalog mode in a shared database.** Any user trigger in another schema of `ds_mvp` makes it
   fail-closed. A dedicated database is needed, and `harness.Target` pins `ds_mvp`.
5. **Procrastinate.** Not attempted: its schema relies on triggers and functions, so it is outside the direct path.
   The Phase 9B path for it would need its DDL as setup SQL and a reviewed registry.

## Provenance of the constructor
- `phase9b_source/` holds byte-identical copies of `seed_feasibility.py`, `state_constructor.py` and `triggers.py`.
- They come from the uncommitted working tree of `~/Desktop/deployspecter-trigger-aware-dev` (branch
  `trigger-aware-validation`, base `2dd347b`). The sha256 values and mtimes are in `SOURCE.json`.
- They are loaded only when the sha256 matches, under a private package (`deployspecter._phase9b`).
- The origin's tests and tools were not copied.
- **Owner check needed:** an earlier plan (`docs/research/live_stepping/OFFLINE_WORKLOAD_PLAN.md`) said bringing this
  code in "needs separate approval". This sprint's brief asked for the constructor's integration. The lead or owner
  should confirm before merging.

## Reproduce (disposable PostgreSQL running; `DS_SDISTS` = the pinned sdist directory)
```
L="python research_tools/pg_lock.py --"
$L env -u MODEL_API_KEY -u DATABASE_URL PYTHONPATH=src python research_tools/run_state_construction.py reservations --out NEW_DIR
$L env -u MODEL_API_KEY -u DATABASE_URL PYTHONPATH=src python research_tools/run_state_construction.py fixtures --out NEW_DIR
$L env -u MODEL_API_KEY -u DATABASE_URL PYTHONPATH=src python research_tools/run_state_construction.py chancy --out NEW_DIR --sdists $DS_SDISTS
$L env -u MODEL_API_KEY -u DATABASE_URL PYTHONPATH=src python -m pytest tests/experimental/test_state_construction_live.py tests/experimental/test_state_construction_offline.py
```
Development attempts, including the failed ones, are in `attempts/` (`NOTE.md`).
