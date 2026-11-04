# Track B: O6 fixed bounded planning-budget proposal

- **Status:** nonnormative, development-only. **PROPOSED, NOT ADOPTED**, and not part of protocol v1.2.
- **Unchanged:** the corpus v1 and v2 records, their declarations and their failed confirmations. No calibration was
  rerun. The numbers below are read from the existing result files.

## Proposed policy

`max_expansions = 65,536`, `max_constraint_checks = 65,536` and `max_rows = 64` (the frozen v2 limit).

**These values were selected retrospectively from development-only data, corpora v1 and v2, after their results were
seen. They are not proven worst-case bounds, and they weren't confirmed on an independent corpus.** Corpus v1's failed
stability rule and corpus v2's failed confirmation stand as recorded. No further corpus search is proposed.

## Why these values

They are chosen from development evidence only, disclosed as such:

| Corpus | Peak expansions | Peak constraint checks | Margin to 65,536 (expansions) | Margin to 65,536 (checks) |
|---|---|---|---|---|
| v1 (49 cases) | 35,424 | 17,568 | 1.85× | 3.73× |
| v2 (41 cases) | 33,344 | 33,344 | 1.97× | 1.97× |

- **No recorded case in either corpus exceeds 65,536 on either counter.**
- **The checks value is new.** It was raised from v1's 32,768 candidate after v2's failure. That is post hoc, and it
  is stated here rather than presented as confirmed.

## The policy's rules

| Topic | Rule |
|---|---|
| Maximum work per proposal | ≤ 65,536 expansions, ≤ 65,536 checks, ≤ 64 rows. The planner is pure, so these bound its work. There is no interrupting clock; wall time is recorded only |
| Exhaustion | The attempt is counted. Its category is "construction blocked by constructor limit": feasibility indeterminate and no detection within budget. It consumes no execution runs and is never replaced |
| D and E comparability | Identical limits for every arm. Exhaustion counts are reported per arm, defect and replicate. E's synthetic units used at most 2 expansions and 10 checks, so limits are most likely to bind on large model proposals. That effect is part of D's operational definition and must be reported |
| Freeze | Frozen with B and T before any held-out arm. Under §14, no held-out result may change the values; any later change is exploratory and can't alter a primary analysis |
| Further calibration | None, to avoid searching for a favourable corpus. A future change needs a new, separately declared amendment |

## Not shown

- That the values are sufficient for every valid v2 document.
- Anything about real or held-out proposals.
- Anything about O5.
