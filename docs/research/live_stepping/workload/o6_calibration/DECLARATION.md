# O6 offline calibration: declaration, written before any planner run

- **Status:** development-only. This is not an evaluation arm, and it adopts no protocol value.
- **Corpus:** `corpus_v1.json`
    - SHA-256 `22be1066c169d70fdbbf59e84a4bd907231f152da66f4372b4d1b138a8880a95`
    - 49 cases, hand-written, synthetic only
    - the inventory is below
- **Review before any run:**
    - no application, held-out or confidential name or content; a search for every protocol application name and
      "gitlab" found nothing;
    - every case passes stages 1 to 3 of the frozen v2 pipeline, checked with the runner's `validate` mode, which
      never calls the planner.
- **What runs:** the existing pure planner `live/state_planning.py`, unchanged, through `tools/o6_offline_calibration.py`.
  There is no database, network, model or Phase 9B.

## Declared before results

1. **Probe ceilings:** `max_rows` = 64, the v2 maximum and fixed; `max_expansions` = 1,000,000; `max_constraint_checks`
   = 10,000,000. These are the planner fields' own upper bounds.
2. **Repeat runs:** each case runs twice at the probe. Counters, outcome, code and request digest must be identical,
   or the evidence is inadequate.
3. **Selection rule,** for each of `max_expansions` and `max_constraint_checks`:
    - **if more than 5% of cases reach the probe, no value;**
    - **otherwise:**

      ```text
      L = min(P, 2^ceil(log2(2 × max observed usage)))
      ```

      over the cases that didn't reach the probe, with a floor of 1.
    - Usage is the counter at termination, whatever the outcome.
4. **Stability criterion:**
    - L is computed for the full corpus and for each leave-one-category-out subset.
    - The value is **stable** only if every subset gives the same L as the full corpus.
    - Otherwise it is **sensitive**, and it is left **unresolved**. The spread is reported.
5. **Reported, but not part of the rule:**
    - leave-one-stratum-out and per-category values;
    - a rerun of every case at the planner's current defaults (4,096 and 65,536), to show whether the defaults bind;
    - wall time, as descriptive only and never used to choose a value.
6. **After the run:**
    - **The corpus, the probes and these rules don't change.**
    - **A second corpus would be a new version,** declared the same way.
    - **Any value found is a development-only proposal** that needs the owner's approval.

## Strata

| Logical rows | Cases |
|---|---|
| 0 | 1 |
| 1 | 19 |
| 2 | 5 |
| 4 | 4 |
| 8 | 2 |
| 10 | 3 |
| 11 | 2 |
| 16 | 3 |
| 32 | 3 |
| 64 | 7 |

## Categories

| Category | Cases |
|---|---|
| conflicting | 12 |
| foreign_key | 9 |
| simple | 9 |
| stress | 5 |
| unsupported | 14 |

## Inventory

| Case | Category | Logical rows | Goals |
|---|---|---|---|
| `simple_r1` | simple | 1 | 1 |
| `simple_r2` | simple | 2 | 1 |
| `simple_r4` | simple | 4 | 1 |
| `simple_r8` | simple | 8 | 1 |
| `simple_r10` | simple | 10 | 1 |
| `simple_r11` | simple | 11 | 1 |
| `simple_r16` | simple | 16 | 1 |
| `simple_r32` | simple | 32 | 2 |
| `simple_r64` | simple | 64 | 4 |
| `conflict_contradictory_r1` | conflicting | 1 | 1 |
| `conflict_null_not_null_r1` | conflicting | 1 | 1 |
| `conflict_bounds_r1` | conflicting | 1 | 1 |
| `conflict_empty_int_r1` | conflicting | 1 | 1 |
| `conflict_too_long_r1` | conflicting | 1 | 1 |
| `conflict_outside_range_r1` | conflicting | 1 | 1 |
| `conflict_null_with_comparison_r1` | conflicting | 1 | 1 |
| `conflict_duplicate_key_r2` | conflicting | 2 | 1 |
| `conflict_insufficient_r4` | conflicting | 4 | 1 |
| `conflict_duplicate_key_r8` | conflicting | 8 | 1 |
| `conflict_duplicate_key_r16` | conflicting | 16 | 1 |
| `conflict_duplicate_key_r64` | conflicting | 64 | 4 |
| `fk_r2` | foreign_key | 2 | 2 |
| `fk_r4` | foreign_key | 4 | 2 |
| `fk_chain_r4` | foreign_key | 4 | 3 |
| `fk_r10` | foreign_key | 10 | 4 |
| `fk_chain_r11` | foreign_key | 11 | 4 |
| `fk_r32` | foreign_key | 32 | 4 |
| `fk_r64` | foreign_key | 64 | 6 |
| `fk_unresolved_r1` | foreign_key | 1 | 1 |
| `fk_value_chosen_r1` | foreign_key | 1 | 1 |
| `unsupported_unknown_field_r1` | unsupported | 1 | 1 |
| `unsupported_unknown_field_r16` | unsupported | 16 | 1 |
| `unsupported_generated_r1` | unsupported | 1 | 1 |
| `unsupported_numeric_r1` | unsupported | 1 | 1 |
| `unsupported_opaque_r1` | unsupported | 1 | 1 |
| `unsupported_string_order_r1` | unsupported | 1 | 1 |
| `unsupported_boolean_order_r1` | unsupported | 1 | 1 |
| `unsupported_null_comparison_r1` | unsupported | 1 | 1 |
| `unsupported_absence_r0` | unsupported | 0 | 1 |
| `unsupported_unknown_entity_r1` | unsupported | 1 | 1 |
| `unsupported_no_filler_r1` | unsupported | 1 | 1 |
| `unsupported_unset_nulls_r2` | unsupported | 2 | 1 |
| `unsupported_overlap_r2` | unsupported | 2 | 2 |
| `unsupported_unset_nulls_r64` | unsupported | 64 | 4 |
| `stress_wide_r10` | stress | 10 | 1 |
| `stress_wide_r32` | stress | 32 | 2 |
| `stress_wide_r64` | stress | 64 | 4 |
| `stress_wide_12goals_r64` | stress | 64 | 12 |
| `stress_fk_scan_r64` | stress | 64 | 5 |
