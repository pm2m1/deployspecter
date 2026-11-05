# Bring-up of the procrastinate rolling-upgrade runner (all five attempts; made at uncommitted states before `run_rolling_upgrade.py` was committed)

| Attempt | Outcome | Cause (harness) |
|---|---|---|
| 1 | crashed before any container ran | the generic state digest expected table-shaped state |
| 2 | crashed after phase 0a's run, while writing its report | the phase directory didn't exist |
| 3 | phase 0a REFUSED (fail-closed) | procrastinate's `__init__` reads its distribution metadata, and the build copied only the package |
| 4 (`attempt4_refused_metadata`) | phase 0a REFUSED (fail-closed) | the same: the `.dist-info` directory was misnamed |
| 5 (`attempt5_sound`) | **all 7 phases SOUND** | the sdist's own `PKG-INFO`, installed as `procrastinate-<v>.dist-info/METADATA` |

**Attempt 5 in detail.** Every phase was corroborated, with 0 mismatched values.
- **Unchecked parameters, counted:** composite-array and float parameters (`list` 15, `float` 5 across phases).
- **R1 and R3–R6:** clean.
- **R2:** entries on all 15 jobs. That is erratum E1 (`../ERRATA.md`), an oracle error in the harness assumption. The source-defined relation, attempts = executions + admin retries, held for 15/15, and so did the documented bound.

Attempts 1–3 left no evidence worth keeping beyond this note.
