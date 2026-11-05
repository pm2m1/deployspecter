# simultaneous_repeat_compete_1: repeatability of `compete` under `alternate` (3 runs from verified identical initial states)

Every run started from the same verified state:
- a fresh schema;
- the two pre-phases, corroborated;
- `chancy_schema_version = 8` and no jobs, checked by the host.

| Run | Verdict | Grants | Transaction order | Canonical final state |
|---|---|---|---|---|
| 00 | SOUND | 297 | `9d3f090f…` | `7b016bcd…`: 0.25.1 ran jobs 0 and 1, 0.26.0 ran 2 and 3 |
| 01 | SOUND | 296 | `fd1fd7b6…` | `7b016bcd…` |
| 02 | SOUND | 297 | `9d3f090f…` | `7b016bcd…` |

**Outcome: 3 of 3 identical.** Statements corroborated: 268 per run, 0 mismatched.

**Trace: 2 of 3 identical.** Run 01 differs by one statement. The policy repeats its *choices*, but which of a worker's tasks asks next, and when, is not controlled (`DESIGN.md`).
