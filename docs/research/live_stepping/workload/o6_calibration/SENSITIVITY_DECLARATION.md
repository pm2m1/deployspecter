# O6 budget sensitivity: declaration, written before this analysis ran

- **Status:** development-only, **exploratory and post hoc.** Corpus v1's results (`results_v1.json`) and their
  declared failure were already seen when this was written. Nothing here replaces them or chooses a final value.
- **Inputs:**
    - corpus `corpus_v1.json`, SHA-256 `22be1066c169d70fdbbf59e84a4bd907231f152da66f4372b4d1b138a8880a95`, unchanged;
    - the unchanged pure planner;
    - the tool `tools/o6_budget_sensitivity.py`.

  There is no database, network, model or Phase 9B.

## Declared grid

| Limit varied | Values | Other limit held at |
|---|---|---|
| `max_expansions` | 2,048; 4,096; 8,192; 16,384; 32,768; 65,536; 131,072 | its probe, 10,000,000 |
| `max_constraint_checks` | 1,024; 2,048; 4,096; 8,192; 16,384; 32,768; 65,536 | its probe, 1,000,000 |
| Both | the current defaults (4,096 and 65,536) | — |

`max_rows` is 64 throughout.

## Reported for each value

- the number of cases whose outcome changes to `Indeterminate`, by category and stratum;
- their ids;
- whether any non-`Indeterminate` outcome changes (it must not: budgets can only stop planning early).

## Not done

- **No value is selected by this analysis.** The comparison of methods, and any recommendation, are for the owner to
  decide.
- **A value chosen from it would need confirming on a new, separately declared corpus version** before any held-out
  arm.
