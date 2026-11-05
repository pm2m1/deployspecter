# Paired detection evaluation: pipeline and results on existing development data (NONBLIND, 2026-11-06)

`research_tools/paired_eval.py` implements the predeclared paired analysis. Its docstring is the specification.

- **Unit and budgets:** the unit is the case, and comparisons need identical budgets. A budget mismatch is refused.
- **Censoring:** schedules and seconds to first verified detection, with nondetections right-censored at the budget.
- **Tests:** exact McNemar on detection, and an exact sign test on paired Gehan scores for censored
  schedules-to-detection.
- **Descriptive:** Kaplan–Meier medians; fixed-version false positives, invalid and refused counts, tokens and
  estimated dollars.
- **Minimum sample:** below 10 paired defect cases the verdict is always **INSUFFICIENT SAMPLE**.

There are 22 offline tests on synthetic data (`tests/experimental/test_paired_eval_offline.py`): known p-values,
censoring and ties, budget refusal, the insufficient-sample rule, no-known-defect cases, and exit codes.

## Existing data

`existing_1/runs.jsonl` holds 47 run records converted from committed evidence by
`paired_eval.py --from-existing`. Nothing was rerun. Reproduce with `docs/experimental_mvp/paired_eval/run_existing.sh`.

| Case | Kind | Arms (runs) | Source |
|---|---|---|---|
| `chancy-2895c4a` | historical defect (known, NONBLIND) | `seeded_explore` (10), `forced_schedule_fix_informed` (3) | `historical/chancy_2895c4a/runs/final_*` |
| `chancy-mutant-no_skip_locked_claim` | injected mutant (not historical) | `seeded_explore` (10) | `results/simultaneous_mutant_compete_5` vs `_explore_compete_4` |
| `procrastinate-3.3-3.4-blue-green` | no known defect | curated, systematic, muse (2 each) | `rolling_upgrade/…/compare_1` |
| `chancy-0.25.1-0.26.0-mixed` | no known defect | curated, systematic, muse (2 each) | `results/real_app_pilot_chancy_compare_1` |
| `pgqueuer-1.6.0-lifecycle` | no known defect | curated, systematic, muse (4 each) | `results/pgqueuer_compare_1` |

The conversion reproduces the recorded outcomes:
- the historical parent is violated in 9/10 seeded runs and 3/3 forced runs, and the fix in 0/10;
- the mutant is detected at seeds 1 and 4, and the original in 0/10;
- there are 0 refused runs and 0 fixed-version violations;
- the Muse cost is $0.0373 in total (three ledgers).

| Comparison | Exit | Paired (defect) cases | Result |
|---|---|---|---|
| `seeded_explore` vs `forced_schedule_fix_informed`, unequal budgets (10 vs 3) | 2 | — | **REFUSED**: identical budgets required |
| the same pair, `--budget 3` | 0 | 1 (1) | both detect at schedule 1 (Gehan 0). **INSUFFICIENT SAMPLE** |
| curated vs muse, `--budget 2` | 0 | 3 (0) | no defect cases, so no detection comparison. 0 vs 0 false positives, 0 vs 0 invalid, 0 vs 0 refused. Muse $0.0373 (1406 in / 8363 out tokens). **INSUFFICIENT SAMPLE** |
| systematic vs muse, `--budget 2` | 0 | 3 (0) | the same. **INSUFFICIENT SAMPLE** |
| curated vs systematic, `--budget 2` | 0 | 3 (0) | 0 vs 0 everywhere. **INSUFFICIENT SAMPLE** |

`--budget 2` truncates the PGQueuer arms from 4 to 2 workloads, so that every paired case has the same budget.

**Reading.**
- The existing development data supports **no** comparative claim.
- There is at most one paired defect case, and that case's forced schedule was designed from the fix.
- No pair of workload arms shares a defect case, so Muse's detection contribution is untested by these data.

New exploratory outcomes, such as Agent B's arms, feed in through the same CLI:

    python research_tools/paired_eval.py --runs FILE... --arms X Y --out DIR [--budget N]

**Known simplifications.**
- `seconds_to_detection_upper_bound` counts the detecting run's whole wall time. It is null whenever a run's time
  wasn't recorded, which is the case for the procrastinate, Chancy-mixed and PGQueuer rows.
- `detecting_runs` counts every detecting run that starts inside the budget.
