# Track B: O6 bounded planning-budget review

- **Status:** nonnormative, development-only, **exploratory and post hoc.** No value is adopted.
- **Unchanged:**
    - `workload/O6_OFFLINE_CALIBRATION.md` and `o6_calibration/results_v1.json` (`d5827ba6…`) stand as recorded;
    - the declared stability rule **failed**, and that result isn't revised.
- **O5,** Phase 9B construction limits, stays blocked pending authorized execution.

**Inputs:**

- **Corpus:** v1, 49 synthetic cases, `22be1066…`, unchanged.
- **Declaration:** `o6_calibration/SENSITIVITY_DECLARATION.md`, `41355cc3…`, written before this analysis ran, and
  after v1's results were seen.
- **Tool:** `research_tools/o6_budget_sensitivity.py`.
- **Output:** `o6_calibration/sensitivity_v1.json`, `da8a3923…`.

## 1. Sensitivity across the declared grid

Each limit was varied with the other held at its probe. A smaller budget only ever turned an outcome into
`Indeterminate`. No other outcome or request digest changed.

| `max_expansions` | Cases stopped | Which |
|---|---|---|
| 2,048 | 5 | 3 stress, plus the 64-row duplicate-key and the 64-row unset-null cases (both 2,144) |
| 4,096 | 3 | stress (32 and 64 rows) |
| 8,192 | 3 | stress |
| 16,384 | 2 | stress (64 rows) |
| 32,768 | 2 | stress (64 rows) |
| 65,536 | 0 | — |
| 131,072 | 0 | — |

| `max_constraint_checks` | Cases stopped |
|---|---|
| 1,024 | 4 (stress) |
| 2,048 | 3 (stress) |
| 4,096 | 3 (stress) |
| 8,192 | 2 (stress) |
| 16,384 | 1 (stress, the foreign-key scan) |
| 32,768 | 0 |
| 65,536 | 0 |

At the current defaults (4,096 and 65,536), 3 stress cases stop.

## 2. The methods compared

| Method | On corpus v1 | Strength | Weakness |
|---|---|---|---|
| **Maximum-based rule:** L = 2^⌈log₂(2 × peak)⌉ | 131,072 and 65,536, but **unstable** (8,192 and 2,048 without stress) | Mechanical | Governed by whichever outliers the corpus contains. Failed its declared criterion |
| **Fixed bounded limits, exhaustion permitted and recorded** | For example, 65,536 and 32,768: the smallest declared grid values with no stopped case in v1 | A stated cap, machine-independent, with exhaustion an explicit, counted outcome: "construction blocked by constructor limit", feasibility indeterminate, no detection within budget | **Chosen after seeing v1.** Adversarial documents written later could exceed it |
| **Budget-neutral (Option A):** at or above a proved worst case for valid v2 documents | No case stops at the field maxima | Planning budget can't affect the estimand | Needs a worst-case proof for the frozen planner, which isn't done |
| **Declared sensitivity values** | Report detection under the chosen L, with exhaustion counts at ½L and 2L | Shows how much the choice matters | A detection effect of planning limits can be measured only by executing the units that differ. That would be an extra, separately budgeted exploratory run |

## 3. Proposal for review (PROPOSED, NOT ADOPTED)

1. **Adopt fixed bounded limits as the method,** with exhaustion permitted and recorded. Report the exhaustion count
   per arm, defect and replicate beside every comparison.
2. **Candidate values:** 65,536 expansions and 32,768 constraint checks.
    - They're 1.85 times the largest adversarial usage seen (35,424 and 17,568).
    - Planning at these limits takes milliseconds.
3. **Before adoption, confirm the values on a new corpus,** v2: separately declared, development-only, with new
   adversarial cases.
    - If any v2 case stops, the values are left unresolved. They aren't raised after the fact.
    - The same author wrote v1, so v2 can't be blind to v1's values, and the report must say so.
4. **Pre-declare the sensitivity reporting:** exhaustion counts at ½L and 2L. No re-execution is part of the
   preregistered analysis.
5. **No tuning on held-out applications,** and no change to B, T or statistical rules.

**Status:** O6 stays **unresolved** until the owner chooses a method and a v2 confirmation passes.
