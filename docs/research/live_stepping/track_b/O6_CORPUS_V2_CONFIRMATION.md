# Track B: O6 confirmation on development corpus v2

- **Status:** development-only, nonnormative. Not an evaluation arm.
- **Result: NOT CONFIRMED** under the rule declared before the run.
- **Candidate values:** 65,536 expansions and 32,768 constraint checks stay **unresolved**, and aren't changed.

**Inputs, fixed before any planner run:**

| Item | Value |
|---|---|
| Corpus | `workload/o6_calibration/corpus_v2.json`, SHA-256 `5441f978a1bf21a314b82280738622e2fd29b81846186ba3215635acbfc2603e`. 41 new hand-written synthetic cases over a new eight-entity context |
| Declaration | `workload/o6_calibration/DECLARATION_V2.md`, SHA-256 `5d345f4f736685afac267418f626648ab01cbafb72421e002e23ce52ca1e68b7` |
| Runner | `research_tools/o6_confirm_v2.py`, SHA-256 `bfe29831…`. It refuses unless the declaration names the corpus digest |
| Planner | Unchanged |
| Output | `workload/o6_calibration/results_v2.json`, SHA-256 `9b732671ce71a922e8313b13d77376e877f42c26eb0756cffd92501545a0db65` |

**The declaration discloses that the candidates were chosen on corpus v1, after its results were seen,** and that
this corpus wasn't blind to them.

## Results

| Budget | Completed | Rejected | Indeterminate |
|---|---|---|---|
| **Candidate (65,536 and 32,768)** | **17** | **23** | **1** |
| Half (32,768 and 16,384) | 15 | 23 | 3 |
| Double (131,072 and 65,536) | 17 | 24 | 0 |
| Probe (1,000,000 and 10,000,000) | 17 | 24 | 0 |

**Against the declared criteria:**

- deterministic: **yes**;
- no probe reached: **yes**;
- every stratum has a completed case: **yes**;
- **no case exhausted at the candidate budgets: no.**

**The case that exhausted:** `stress_nullkey_r64`, 64 rows on an entity with 16 single-column unique constraints
declared `NULLS NOT DISTINCT`, all left unset.

- Duplicate-key comparisons grow with the square of the rows in each key set.
- It needs **33,344** constraint checks, 576 more than the candidate.
- At the probe it is `UnsupportedGoal` (`unset_nulls_collide`). At the candidate it becomes `Indeterminate`
  (`constraint_checks_budget`), which O8 classes as feasibility-indeterminate, with no detection within budget.

**Other heavy cases, all completed at the candidate budgets:**

| Case | Expansions | Constraint checks | At half |
|---|---|---|---|
| `stress_grid_claims_r64` | 33,344 | — | `Indeterminate` |
| `stress_link_scan_r64` | — | 18,126 | `Indeterminate` |
| `stress_cardinality_r64` | — | 11,584 | completed |

**Descriptive only, no conclusion:** no v2 case exceeded the expansion candidate, and its peak was 33,344. The
constraint-check candidate was exceeded once. The declared rule judges the pair, so neither value is confirmed.

## Interpretation

- **Repeated pattern.** As with v1, a new adversarial family set the maximum. Bounded limits chosen from one corpus
  didn't survive the next.
- **No detection was lost.** The exhausted case is a document the planner refuses anyway, so its outcome changes
  from "blocked by an unsupported path" to "construction blocked by constructor limit". The categorization still
  changes, and the declared rule counts it.
- **This strengthens Option A** (`O6_BOUNDED_BUDGET_REVIEW.md`): limits at or above a proved worst case for valid v2
  documents, so planning budgets never bind.
- **Not done:** no value is re-chosen from these results. Any new candidate would need a new, separately declared
  corpus. **O6 stays unresolved.**
