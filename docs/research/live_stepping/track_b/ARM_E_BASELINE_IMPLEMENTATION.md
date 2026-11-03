# Track B: arm E deterministic baseline, proposed specification and offline prototype

- **Status:** nonnormative research documentation, **PROPOSED, NOT ADOPTED.**
    - The prototype is unfrozen.
    - It is never used in a protocol evaluation.
    - No baseline evidence is changed.
- **Scope:** pure offline code over synthetic registered operations. No PostgreSQL, model or Phase 9B.
- **Labels:**
    - **[inherited]:** taken from frozen v1.2 text (quoted in `workload/ARM_E_BASELINE_SPEC_PROPOSAL.md` §0);
    - **[new]:** a prospective choice;
    - **[comparator]:** a choice that could change the intended comparator, needing explicit approval.

## 1. What the frozen protocol fixes

**[inherited]**

- **Statistical unit:** the eligible held-out defect, paired across arms (§2, §9).
- **Budget:** every arm has B runs and T seconds per defect, as 3 replicates of B/3 and T/3 (§8).
- **Replicates:**
    - a deterministic replicate is "one seeded order of its workloads, with seeds 1, 2 and 3";
    - a Muse replicate is "one independent response".
- **E's content:** "union of A and B, plus pairs of registered entrypoints", with "Phase 9B constructor over
  deterministic goals".
- **E's freeze:** E is frozen before any held-out Muse output, with its effort logged.
- **R:** replays "SQL captured from a plain serial run of E's workloads" from "E's goals".

**E isn't forced into one workload per replicate.** A replicate is a seeded order of all its workloads.

## 2. Proposed resolution of E-1 to E-8

| # | Decision | Proposal | Kind |
|---|---|---|---|
| E-1 | Units per replicate, and allocation | Every valid unit is in every replicate; only the order differs. Runs are allocated **round-robin** in seeded order, one run per unit per round. A unit leaves when its decision tree is exhausted. The cell stops at B/3 runs or T/3 seconds. Unused budget is reported, never moved | Inclusion [inherited]. **Allocation [comparator]**: it affects S5, and the alternatives (sequential, equal share) change which units are reached first |
| E-2 | A-derived workloads | An adapted A test enters E only if it can be written as one v2 document: registered entrypoints, literal typed arguments, fixtures as literal `eq` and `is_null` goals. Otherwise it is "not expressible" for E, listed by name, and still runs in arm A | **[comparator]**: may make E narrower than "union of A and B" |
| E-3 | Entrypoint pairs | Unordered pairs {eᵢ, eⱼ} with i ≤ j. Self-pairs only when `max_instances` ≥ 2. Two actors. Three assignments: **canonical** (each side's canonical tuple), **collide** (shared argument names take the first actor's value), **differ** (the second actor takes each required argument's first non-canonical boundary value) | [new] |
| E-4 | Goal-policy inputs | A pinned `GoalPolicy`: application, registry digest, and (entrypoint, argument) → (entity, field) references. It is written by the onboarding person from the schema and registration only. Each mapped argument value becomes one `count 1` `eq` goal, with equal references merged. Keys and fillers come only from the shared planner | [new]. The policy format is a new pinned artifact |
| E-5 | Ordering, randomness, generation limit | Ascending `sha256("deployspecter-live-protocol-v1:E-order:<owner>/<repo>:<seed>:<unit_id>")`. No pseudo-random generator. Generation is exhaustive, with a 10,000-unit safety ceiling: over it, E and R are blocked for the application and nothing is truncated | [new] |
| E-6 | R's capture budget | One plain serial capture per verified E unit. It doesn't consume R's B or T | [new], matching amendment 002's treatment of replay |
| E-7 | R's capture order | The document's actor order | [new] |
| E-8 | The generator | **Prototype implemented** (section 3). Not frozen | Needs review, then freezing before any held-out Muse output |

**B-derived units** [new]:

- **Workloads:** per entrypoint, two actors with identical arguments.
- **Arguments:** the canonical tuple, plus one argument at a time over each boundary value.

  | Type | Boundary values |
  |---|---|
  | Integer | min, min+1, max−1, max, and 0 if in range; or an allowed list |
  | Float | min, max, and 0.0 if in range |
  | String | the shortest and longest allowed strings, filled with a fixed character for the charset; or an allowed list |
  | Boolean | each allowed value |
  | Nullable | null |

- **Optional arguments** are absent from the canonical tuple, and are varied one at a time.

**Prototype finding:** an entrypoint with `max_instances = 1` can never form a B unit, because two identical actors
exceed its limit. Every such unit is generated, rejected at authorization, and counted. **[comparator]**: such an
entrypoint is reached only through pairs with other entrypoints. One alternative is a B unit of that entrypoint paired
with a fixed second actor, which would be a new rule.

**E-to-R exact-state reuse** [new]:

- R starts from E's verified state for the same unit;
- the rebuilt state's digest must equal E's `verified_state_digest`;
- otherwise the unit is blocked for R, as `r_state_requirements` does.

**Failure accounting:**

| Event | Outcome |
|---|---|
| A unit failing stages 1 to 3 | "generation rejected", kept with its stage and code |
| Planner, bridge, construction or verification outcome | Mapped as for D |
| An unconstructible or unverifiable unit | Consumes no runs, and is never replaced |
| Generation over the ceiling | E and R blocked for the application, with its defects kept in the denominator |
| Infrastructure failure | No automatic retry or replacement |

## 3. The offline prototype

- **Code:** `src/deployspecter/research/track_b/arm_e_generator.py`, `arm-e-generator/0-prototype`. Not frozen. It
  reads only a trusted v2 Registry and Contracts, a `GoalPolicy` and `ATranslation` fixtures. It imports no oracle,
  scenario, explorer, property, Phase 9B or Track A module, which a test checks.
- **Attribution:** each unit's source (A, B or pair) is recorded by the generator, never from `provenance`.
- **Tests:** `tests/research_tracks/test_track_b_arm_e.py`, with the connection and socket constructors replaced to
  refuse any connection. Synthetic inputs only:
    - the hand-written `ticketing` registry and contracts from `tests/test_argument_validation.py`;
    - the planning context from `tests/test_state_planning.py`;
    - one expressible and one inexpressible A translation, both hand-written.

**Generation, v1:**

| Source | Generated | Valid | Rejected |
|---|---|---|---|
| A | 1 | 1 | 0 |
| A, not expressible | 1 blocked (listed) | — | — |
| B | 22 | 20 | 2 (`cross_field_violated`: coupon without price) |
| pair | 3 | 3 | 0 |
| **Total** | 26 candidates | **24 valid** | 2 rejected |

**Generation, v2:** 28 B units (5 rejected, all `instance_limit_exceeded` for the single-instance `sweep`) and
6 pair units (no `sweep+sweep` self-pair).

**Determinism:**

- identical unit ids and documents across runs;
- unit ids don't depend on the seed;
- seeds 1, 2 and 3 give three different orders of the same set, independent of input order;
- seed 4 is refused.

**The ceiling:** `max_units = 10` with 26 candidates blocks the whole application with no unit kept, rather than
truncating.

**Round-robin:**

- trees of 1, 3 and 0 runs with a budget of 10 give `x y y y`, with 6 runs unused and not moved;
- the unconstructible unit gets none.

**Planning budget use** (shared planner, current defaults): all 24 valid v1 units give `candidate_plan`, with at most
2 expansions and 10 constraint checks.

## 4. Equal-opportunity accounting

**The comparable execution opportunity** is one (arm, defect, replicate) cell, with B/3 runs and T/3 seconds.

**Reported per cell:**

- units in the cell: E all of them, D at most one;
- valid, planned, constructed and verified units;
- runs used per unit;
- unused runs;
- seconds used;
- detection.

**Known asymmetries, all from v1.2's definitions and reported, not corrected:**

- **Breadth.** An E cell has many units, and a D cell at most one.
- **Failure.** An unconstructible D response leaves its cell empty, while E continues to its other units.
- **Goal source.** D's goals come from Muse, and E's from the goal policy.

## 5. Comparator choices still needing explicit approval

1. **E-1:** round-robin allocation within a cell.
2. **E-2:** A tests that can't be expressed as v2 are blocked in E, not run another way.
3. **B units for single-instance entrypoints** can't exist under the two-identical-actor rule.
4. **The boundary-value table,** and the three pair assignments (E-3).
5. **The goal-policy format** (E-4). Reference goals only, with no background population.

## 6. Not shown by this prototype

- Anything about detection, or about real or held-out applications.
- That E is a *strong* baseline in the protocol's sense. That depends on the A translations and goal policy written
  for each application, which don't exist yet.
- Construction, verification or execution, which all need the authorized execution path.
