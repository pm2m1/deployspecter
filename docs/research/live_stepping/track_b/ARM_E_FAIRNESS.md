# Track B: arm E fairness record (milestone 3)

- **Status:** nonnormative. **PROPOSED, NOT ADOPTED.** E isn't frozen.
- **Evidence:** synthetic development registry only (`ARM_E_COVERAGE_AUDIT.md`, and
  `tests/research_tracks/test_track_b_*.py`).
- **Labels:** **[comparator]** marks a choice that could change what the frozen protocol intended E to be.

| Topic | Proposed rule | Evidence on the synthetic registry | Kind |
|---|---|---|---|
| E-1 allocation | Round-robin over E's units in seeded order within each (defect, replicate) cell of B/3 runs and T/3 seconds. Unused budget is reported, never moved | `round_robin` tests | **[comparator]**: affects S5 |
| E-2 inexpressible A tests | Listed by name, counted as blocked for E, and still run in arm A. E is narrower than "union of A and B" by exactly that list | 1 of 2 A fixtures not expressible | **[comparator]** |
| Entrypoint pairs | Unordered, with self-pairs only when `max_instances` ≥ 2, and three assignments | v1: 1 of 1. v2: 2 of 3, with `sweep+sweep` impossible under `max_instances = 1` | Proposed |
| Boundary values | Canonical tuple plus one argument at a time | Default: v1 22 of 24, v2 `sweep` 4 of 6. With both options: complete | Proposed |
| Contract companions (`repair_requires`) | **Off by default.** When on, it generates the combination a trusted `requires` predicate demands, before validation. It never edits a rejected unit. Such units are labelled `+companion:<arg>`, with different unit ids | v1: +2 valid units | **[comparator]** |
| Single-instance partners (`pair_with_partner`) | **Off by default.** When on, the second actor is the first other entrypoint's canonical call. It respects `max_instances`. Such units are labelled `+partner:<entrypoint>` | v2: +5 valid units, every one within `max_instances` (tested) | **[comparator]** |
| Goal-policy ownership | Written by the onboarding person from the schema and registration only, pinned by SHA-256, and frozen before any held-out Muse output. It never sees oracles, requirements or results | `GoalPolicy` refuses another registry's digest | Proposed |
| Valid and rejected counts | Every candidate is kept, with its stage and code. Nothing is removed, and duplicates are reported only | v1 25 / 23 / 2; v2 34 / 29 / 5 (defaults) | Inherited (§12) |
| Ordering | Ascending `sha256("deployspecter-live-protocol-v1:E-order:<owner>/<repo>:<seed>:<unit_id>")`, seeds 1 to 3 | Same set across seeds, independent of input order | Proposed |
| Equal B and T opportunity | Equality is runs and seconds per cell, as §8 defines it. E's breadth (many units against D's single workload) is reported, not equalized | — | Inherited |
| Exact R state reuse | R starts only from E's verified state digest. Otherwise R is blocked for that unit | `r_state_requirements` tests | Proposed |
| Planning budget | The same limits as D (`O6_FIXED_BUDGET_PROPOSAL.md`). Exhaustion is counted per arm | E's units used at most 2 expansions and 10 checks | Proposed |
| Oracle access | None. The generator imports no oracle, scenario, property or Phase 9B module (AST test) | — | Inherited (§7) |

## Owner decisions needed

1. **E-1 and E-2.**
2. **Whether to enable either option.** Either one makes E stronger, which raises the bar for P1.
3. **The goal-policy format.**
4. **When to freeze the generator.**
