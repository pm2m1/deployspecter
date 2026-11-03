# O6 corpus v2: confirmation declaration, written before any planner run

- **Status:** development-only. Not an evaluation arm. Adopts nothing.
- **Purpose:** confirm or refute the **candidate planning budgets**:
    - **65,536** `max_expansions`;
    - **32,768** `max_constraint_checks`;
    - `max_rows` 64.

## Where the candidate values came from, and what they aren't

- **They were chosen on corpus v1, after its results were seen:** they are the smallest values in v1's declared
  sensitivity grid at which no v1 case stopped. They aren't independently established limits.
- **This corpus wasn't written blind to them.**
    - The author knew v1's peak usage (35,424 expansions and 17,568 checks).
    - The author knew the planner's growth terms: walks on unique columns grow with the square of the rows; foreign-key
      scans grow with children × keys × parents; duplicate-key comparisons under NULLS NOT DISTINCT grow with the square
      of the rows per key set.
    - New adversarial families were written from that knowledge, neither to pass nor to fail. That is disclosed here,
      before running.

## Corpus

- **File:** `corpus_v2.json`, SHA-256 `5441f978a1bf21a314b82280738622e2fd29b81846186ba3215635acbfc2603e`.
- **Size:** 41 hand-written synthetic cases over a new invented context of eight entities. Nothing is
  copied from v1.
- **Review:** no application, held-out or confidential name or content; a search for every protocol application name and
  "gitlab" found nothing.
- **Validity:** every case passes stages 1 to 3, checked with the calibration runner's `validate` mode, which never calls
  the planner.
- **Runner:** `research_tools/o6_confirm_v2.py`, SHA-256 `bfe29831e8cf8b233b4da66f0033502d6f3e0f3c01f9472562367b3787183582`.
- **Planner:** `live/state_planning.py`, unchanged.

## Declared before results

1. **Budgets run for each case:**
    - **candidate:** 65,536 and 32,768;
    - **half:** 32,768 and 16,384;
    - **double:** 131,072 and 65,536;
    - **probe:** 1,000,000 and 10,000,000.

   `max_rows` is 64 throughout. The candidate budget runs twice, and its results must be identical.
2. **Outcome classes:**
    - `CandidatePlan` is **completed**;
    - `UnsupportedGoal` and `InfeasibleUnderAssumptions` are **rejected**;
    - `Indeterminate` is **indeterminate**.
3. **The candidate budgets are CONFIRMED on corpus v2 only if all of these hold:**
    - no case is `Indeterminate` at the candidate budgets;
    - no case reaches a probe;
    - results are deterministic;
    - every stratum of 1, 2, 4, 8, 10, 11, 16, 32 and 64 rows has at least one completed case at the candidate budgets.

   **Otherwise NOT CONFIRMED.**
4. **Handling exhaustion:** a case exhausted at the candidate budgets is recorded as feasibility-indeterminate, no
   detection within budget (O8).
    - **The candidate values are then left unresolved.**
    - **They aren't raised, lowered or re-chosen from these results.**
    - A different value would need a new, separately declared corpus.
5. **Reported only:** half and double results, and probe usage. They select nothing.
6. **Unchanged afterwards:** this corpus and declaration.

## Strata and categories

| Logical rows | Cases |
|---|---|
| 0 | 1 |
| 1 | 16 |
| 2 | 3 |
| 4 | 2 |
| 5 | 1 |
| 8 | 2 |
| 10 | 1 |
| 11 | 2 |
| 16 | 3 |
| 20 | 1 |
| 32 | 2 |
| 64 | 7 |

| Category | Cases |
|---|---|
| conflicting | 10 |
| foreign_key | 6 |
| simple | 9 |
| stress | 5 |
| unsupported | 11 |

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
| `conflict_eq_ne_r1` | conflicting | 1 | 1 |
| `conflict_too_long_r1` | conflicting | 1 | 1 |
| `conflict_empty_r1` | conflicting | 1 | 1 |
| `conflict_not_null_r1` | conflicting | 1 | 1 |
| `conflict_outside_r1` | conflicting | 1 | 1 |
| `conflict_duplicate_r4` | conflicting | 4 | 1 |
| `conflict_insufficient_r5` | conflicting | 5 | 1 |
| `conflict_contradictory_r11` | conflicting | 11 | 1 |
| `conflict_duplicate_r32` | conflicting | 32 | 2 |
| `conflict_duplicate_r64` | conflicting | 64 | 4 |
| `fk_r2` | foreign_key | 2 | 2 |
| `fk_r8` | foreign_key | 8 | 3 |
| `fk_r16` | foreign_key | 16 | 2 |
| `fk_r64` | foreign_key | 64 | 7 |
| `fk_unresolved_r1` | foreign_key | 1 | 1 |
| `fk_chosen_r1` | foreign_key | 1 | 1 |
| `unsupported_unknown_field_r1` | unsupported | 1 | 1 |
| `unsupported_unknown_entity_r1` | unsupported | 1 | 1 |
| `unsupported_numeric_r1` | unsupported | 1 | 1 |
| `unsupported_opaque_r1` | unsupported | 1 | 1 |
| `unsupported_string_order_r1` | unsupported | 1 | 1 |
| `unsupported_boolean_order_r1` | unsupported | 1 | 1 |
| `unsupported_null_comparison_r1` | unsupported | 1 | 1 |
| `unsupported_absence_r0` | unsupported | 0 | 1 |
| `unsupported_no_filler_r1` | unsupported | 1 | 1 |
| `unsupported_overlap_r2` | unsupported | 2 | 2 |
| `unsupported_unset_nulls_r16` | unsupported | 16 | 1 |
| `stress_grid_claims_r64` | stress | 64 | 7 |
| `stress_grid_r20` | stress | 20 | 1 |
| `stress_link_scan_r64` | stress | 64 | 6 |
| `stress_cardinality_r64` | stress | 64 | 12 |
| `stress_nullkey_r64` | stress | 64 | 4 |
