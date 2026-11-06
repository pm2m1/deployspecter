# ds-p9x/1: real-application state construction through the Phase 9B pipeline (2026-11-06)

**Classification: COMPLETE WITH SUPPORTED-SCOPE LIMITATIONS.**
- Chancy 0.26.0's unmodified worker runs from an initial state that the Phase 9B constructor constructed, that the
  extension verified independently, and that the server log corroborated.
- Procrastinate 3.4.0 is **refused**, with exact reasons.
- This is one application under a NONBLIND, developer-authored registry. It is not broad third-party Phase 9B
  compatibility.

## Architecture: new modules, original Phase 9B sources unmodified

`src/deployspecter/experimental/phase9b_ext/`:

| Module | Role |
|---|---|
| `loader.py` | Executes the three pinned Phase 9B files (sha256 checked; see `state_construction/phase9b_source/SOURCE.json`) under a private package `deployspecter._phase9b_ext_v1`. It makes **one declared substitution**, `seed_feasibility._same` → `loader.same_v1`, the jsonb-aware row attribution. Every non-jsonb comparison is delegated to the original `_same`. |
| `jsonb_v1.py` | Contract `jsonb/1`: strict parsing, PostgreSQL-canonical text, type-exact equality, and cell readback. |
| `catalog.py` | Contract `catalog/1`: exports a live schema as `setup_sql`, fingerprints catalogs, and screens triggers. |
| `execute.py` | The physical plan and its hash; locked, fingerprint-rechecked, typed writes in one transaction; the change must equal the constructor's proven final state; independent readback. |
| `trace.py` | Execution proof, by module name: which constructor instance and functions ran. |
| `pipeline_v1.py` | The end-to-end entry point. Phase 9B is the only constructor here; every refusal is returned, never worked around. |
| `chancy_registry_v1.{json,py}` | The reviewed Chancy 0.26.0 contract, pinned to source hashes. |

The reservations path (`deployspecter._phase9b`, no substitution) is untouched and still passes.

## jsonb/1 coverage

| Value | Outcome |
|---|---|
| Objects, arrays, strings (escapes, non-ASCII), true/false, integers up to ±(2^53−1), empty object and array, JSON null | **Accepted**: canonical text cross-checked with PostgreSQL's `::jsonb::text`, verified on readback |
| SQL NULL vs JSON null | **Distinguished**: `IS NULL` and `jsonb_typeof` on readback. Attribution via `to_jsonb` can't distinguish them, so the database decides |
| Duplicate keys, malformed JSON, `NaN`, `Infinity`, fractions, exponents, integers beyond 2^53−1, `\u0000`, unpaired surrogates, more than 64 KiB, nesting deeper than 32 | **Refused before construction** |
| A non-canonical text, or a jsonb text equal to a non-jsonb value in the same construction | **Refused**: the attribution would be ambiguous |
| `json` (not jsonb) columns | **Refused** (outside jsonb/1) |

Values are always bound parameters cast in SQL, never interpolated.

## Triggers: support and refusal boundaries

| Behaviour | Outcome | Evidence |
|---|---|---|
| A row trigger whose rows the constructor proves (audit insert) | Constructed; the trigger's rows are part of the verified change | `test_trigger_generated_rows_are_part_of_the_verified_state` |
| An order-dependent trigger | `CONSTRUCTED_EQUIVALENT`, physical order `[1, 0]`, plan hash recorded | `test_a_physical_order_change_that_preserves_the_requested_state` |
| A BEFORE trigger that would change the requested value | Refused by the constructor; the requested state is never adjusted | `test_a_trigger_that_would_change_the_requested_value_is_refused_not_obeyed` |
| Dynamic SQL, clock or random calls, `current_setting`, statement-level, constraint or deferrable triggers, non-plpgsql languages | Refused by the screen | `test_unprovable_procedural_triggers_are_refused`, `test_a_statement_level_trigger_is_refused` |
| A trigger writing a table with a `now()`, sequence or identity default | Refused by the screen | `test_a_trigger_writing_a_table_with_a_clock_default_is_refused`; procrastinate |
| A trigger whose effect differs on the target (depends on target data) | Refused at write time, rolled back | `test_an_unexpected_trigger_side_effect_on_the_target_is_caught_and_rolled_back` |
| A trigger-catalog mismatch between target and sandbox (a disabled trigger) | Refused (fingerprint) | `test_a_trigger_catalog_mismatch_between_target_and_sandbox_is_refused` |
| A catalog change between planning and execution | Refused under table locks, rolled back | `test_a_catalog_change_between_planning_and_execution_is_refused` |

**The screen is a static over-approximation of procedural code.** Passing it is necessary, not sufficient: every
state is still compared with the constructor's proven final state, on the database, inside the write transaction.

**Declared catalog allowance.** PostgreSQL's deparse of a partial-index predicate isn't a fixpoint. Chancy's
`state IN (...)` comes back with its casts placed differently. Such differences are accepted only in the expression
text of indexes, CHECK constraints or column defaults of otherwise identical objects, and only when the sandbox's
own re-export rebuilds to the same catalog. This rests on PostgreSQL's documented purpose for the `pg_get_*def`
functions, which is to reconstruct equivalent objects. The first live attempt was refused on exactly this
(`runs_p9x/chancy_1`, exit 1), before the allowance existed.

## Chancy registry (`chancy-0.26.0/registry-v1`)

- **Pinned identities:**

  | What | sha256 |
  |---|---|
  | sdist | `ac98b9c5…` |
  | `chancy/app.py` | `403e4712…` |
  | `chancy/job.py` | `1f6c851d…` |
  | `chancy/queue.py` | `6733cb34…` |
  | `chancy/worker.py` | `0f541cbf…` |
  | migrations v1–v8 | pinned individually in the registry |
  | registry file | `309e15f1…` |

  The adapter file is hashed at run time.
- **Operations:** `setup` (migrate and declare `ds_q`, no job pushed) and `work`.
- **Handler:** `chancy_adapter.ds_job(n: int 0..999)` only.
- **Goal states:** `pending` and `succeeded`, from the documented states pending, running, failed, retrying and
  succeeded (`chancy/job.py`).
- **jsonb fields:** `kwargs` = the typed kwargs; `limits` and `errors` = `[]`; `meta` = `{}`.
- **Preconditions:** the v8 `chancy_jobs` columns, `chancy_schema_version` v8, and the declared queue `ds_q`.
- **Expected database behaviour:** construction on one host session (`ds_p9x_construct`), one transaction; the
  worker on its one stepped connection.
- **Properties:** C1–C4.

The registry holds no defect labels and no oracle answers. A workload names only registered items.

## Live Chancy result (`runs_p9x/chancy_2`, revision `71ff4f0`; rerun at the final revision as `chancy_3`)

| Workload | Classification | Trace | Readback | Worker | Corroboration | Oracle |
|---|---|---|---|---|---|---|
| single (1 pending job) | `ORIGINAL_VALID` | `construct_state` ×1, `same_v1` ×54 | 1 row, 4 jsonb cells, 0 problems | handled [0], bound to the verified digest | 47 statements; 40 params compared, 0 mismatched, 0 unchecked | C1–C4 hold |
| multi (4 pending, 1 succeeded) | `ORIGINAL_VALID` | `construct_state` ×1, `same_v1` ×290 | 5 rows, 20 jsonb cells, 0 problems | handled [3, 0, 2, 1]; job 7 untouched | 191 statements; 121 params compared, 0 mismatched, 0 unchecked | C1–C4 hold |
| unregistered handler | refused by the registry | (no construction) | | | | |

**Comparison with the direct constructor** (same goals, separate run): an equal final state on `id`, `queue`,
`func`, `state`, `attempts`, `kwargs`, `completed` and `has_claim_id`, for all 5 jobs.
- **Not compared:** `started_at` and `completed_at` (Chancy's clock), `taken_by` (the worker's random id) and the
  `claim_id` value (a fresh uuid per claim).
- **Scope:** this shows equal outcomes for these goals only, not general constructor equivalence.

**Why `ORIGINAL_VALID` is not taken on trust:** that classification rests on row attribution alone. Every
constructed state was therefore re-verified on the database:
- inside the write transaction, as a change equal to the proven final state;
- after commit, by the independent readback on a separate session.

## Procrastinate 3.4.0

| Attempt | Outcome |
|---|---|
| `procrastinate_1` | procrastinate's own schema applied (SOUND, corroborated); then **refused_catalog**: `procrastinate_workers.id` is an identity column (catalog/1 did not export identity columns yet) |
| `procrastinate_2` | identity export added; **refused_triggers**: 5 of the 7 `procrastinate_jobs` triggers write `procrastinate_events` or `procrastinate_periodic_defers`, whose `id` defaults to `nextval(...)` and whose `at` defaults to `now()`. Those rows can't be identical between the sandbox and the target, so the state can't be proven |

Two triggers send `pg_notify` notifications, recorded as external effects. No upstream SQL was changed and no
trigger was disabled. Supporting this would need declared semantics for clock and sequence columns written by
triggers, for example comparing them by presence and type rather than value. That is a design decision, not an
implementation detail.

## Remaining limits

- One application, one developer-authored registry, NONBLIND.
- jsonb/1 has no non-integer numbers.
- catalog/1 does not support views, partitions, inheritance, domains, generated columns, rules, policies, or
  function bodies that name their own schema.
- Triggers with non-deterministic effects are refused (procrastinate).
- Phase 9B's own budget is 10 seed rows (`MAX_SEED_ROWS`).
- **The construction session is not server-log corroborated.** It is checked by the change comparison and the
  readback; the worker phase is corroborated.
- Live-catalog fingerprints cover the schema only, not other schemas the application might use.

## Erratum and update (2026-11-06, assurance sprint: ds-p9x/1.1)

**A1 defect in v1.** `same_v1` gave jsonb semantics to *any* string equal to a registered jsonb text. Phase 9B also
compares stored values with each other (`state_constructor.py` lines 481 and 489). A stored text value equal to a
registered jsonb text could therefore be compared by the jsonb rule instead of the original one.
- **Fix (v1.1):** requested jsonb values are `JsonbText` markers, and only marked arguments get jsonb semantics.
  Property-based tests show `same_v1.1` ≡ the original `_same` on every non-jsonb input.
- **Earlier runs:** `chancy_2` and `chancy_3` ran on v1. They are preserved unchanged.
- **Reproduced on v1.1:** `chancy_5_assurance`, with the same outcomes plus the construction-session server-log
  audit (44/44 and 48/48 statements; 38 and 110 parameters checked; 0 unchecked).

**Also in v1.1:**
- **catalog/1.1:** expression differences must be established on PostgreSQL's parsed node trees under two
  documented rules. Text similarity is no longer enough.
- **Construction-session provenance:** reconciled with the csvlog.
- **Injected-failure atomicity:** tested.

Details: `state_construction/assurance/README.md`.

**Correction to "Remaining limits" above:** the construction session is now server-log audited for statements,
parameters, transaction boundaries and session identity. Row effects, the readback session and the sandbox sessions
are still not log-audited; they rest on the database readback.
