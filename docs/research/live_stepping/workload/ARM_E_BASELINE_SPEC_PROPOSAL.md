# Arm E: proposed deterministic baseline specification

- **Status:** nonnormative proposal. **PROPOSED, NOT ADOPTED.** Nothing here is implemented, and no
  baseline evidence is changed.
- **Labels:**
    - **[inherited]** marks a requirement taken from the frozen protocol (v1.2 with 001 and 002);
    - **[new]** marks a prospective decision needing the owner's approval;
    - **[comparator]** marks a new decision that could change what the frozen protocol intended E to be. It needs
      **explicit** approval.

## 0. Frozen text this rests on

- **§7, arm A:** "Live stepping"; "The application's own test suite, adapted"; "Its fixtures".
- **§7, arm D:** "Live stepping"; "Muse Spark proposals"; "Muse Spark seed goals, resolved by Phase 9B's deterministic
  constructor".
- **§7, arm E:** "Live stepping"; "Strong deterministic baseline: union of A and B, plus pairs of registered
  entrypoints"; "Phase 9B constructor over deterministic goals".
- **§7, arm R:** "Statement replay: SQL captured from a plain serial run of E's workloads, parameters fixed, no Python
  between statements"; "E's workloads"; "E's goals".
- **§7 rules:**
    - "Every arm uses the same oracle, budget and supported paths. The arms can't see requirements, oracles, labels or
      schedules."
    - E "is written and frozen before Muse Spark output is seen on any held-out application, with its effort logged.
      A weak baseline would invalidate the comparison."
- **§8:**
    - "Each arm gets the same total of B runs and T seconds of execution per defect."
    - "3 replicates, each with B/3 runs and T/3 seconds. A Muse replicate is one independent response. A
      deterministic replicate is one seeded order of its workloads, with seeds 1, 2 and 3. This way three chances
      are never compared with one."
- **§9:** "An arm detects a defect if at least one replicate does." Time to detection is counted "with replicates in
  fixed order and earlier replicates' spending counted".
- **§10:** minutes to "write or adapt each workload source (A, B and E)".
- **§11:** "E's deterministic generator runs from each registration at equal budget".

**Proposal counts per replicate.** The frozen text fixes none for E. For D it fixes one response per replicate, and
the owner's N2 proposal is at most one workload document per response.

## 1. Experimental unit

- **[inherited]** The analysis unit is the eligible held-out defect, paired across arms (§2, §9).
- **[inherited]** The execution unit is the (arm, defect, replicate) cell, with B/3 runs and T/3 seconds.
- **[new] A comparable execution opportunity** is that cell's budget: equal runs and seconds, not an equal number
  of workloads.
    - This is how §8 defines equality ("the same total of B runs and T seconds"), and why it says "three chances are
      never compared with one". It equalizes replicates and budget, not workload counts.
- **[new] Within a cell:**
    - a **D cell** holds at most one workload: one response, at most one document;
    - an **E cell** holds E's workload units in its seeded order (section 2).

  The difference in breadth is part of v1.2's design of a "strong deterministic baseline", and isn't removed here.
  **Forcing E into one workload per replicate isn't proposed,** because it would change the intended comparator.

## 2. Number of deterministic candidates per replicate

- **[inherited]** A deterministic replicate is "one seeded order of its workloads".
    - So every replicate has **the same set** of E units.
    - Only the order differs between seeds 1, 2 and 3.
- **[new] N_E** is every unit the generator produces for the application, with no sampling.
    - A hard generation ceiling of 10,000 units exists for safety only.
    - Exceeding it is a generation failure for the application: E and R are blocked there, and nothing is
      truncated.
- **[new] How runs are allocated within a cell:** round-robin over the units in seeded order, one run per unit per
  round.
    - Within a unit, the frozen exploration policy chooses the next schedule, as it does for D's single workload.
    - A unit whose decision tree is exhausted leaves the rotation.
    - The cell stops at B/3 runs or T/3 seconds.
    - Unused budget is reported, never moved.
    - **[comparator]** Sequential allocation, exploring each unit to exhaustion before the next, would favour early
      units. The choice affects S5 (time to detection), so it needs explicit approval.

## 3. Relationship to A-derived tests

- **[inherited]** E includes "the union of A and B". A is "the application's own test suite, adapted", starting from
  "Its fixtures".
- **[new] Proposed: an A test enters E as a v2 unit** when the adapted test can be expressed as one v2 document:
    - two to four actors calling registered entrypoints with literal typed arguments;
    - its fixtures written as literal `eq` and `is_null` goals.

  The translation is mechanical, frozen with E, and its minutes are logged under §10.
- **[new] An A test that can't be expressed** stays in arm A unchanged. In E's accounting it is "blocked by an
  unsupported path (not expressible as v2)", listed by name. It is never silently dropped.
- **[comparator]** This can make E weaker than "the union of A and B" literally means, because some A tests may not be
  expressible. **It needs explicit approval.** The alternative is to run inexpressible A tests in E from their own
  fixtures. That would give E two state pipelines, unlike D, and would also need approval.

## 4. Registered operation and version selection

- **[inherited]** Only registered entrypoints, versions and roles. Registration is written under §3.5.
- **[new] The version under test:** for each defect, the units use the registered `application_version` values valid
  for that defect's pre-fix and fix versions. The same document runs on both, as §5 and amendment 002 require.
  Entrypoints not registered for a version produce no units for it.
- **[new] Instance and role limits** come from the registry: `max_instances` and `roles`, enforced by the frozen
  authorize stage.

**B's units** [new]:

- **Workloads:** for each entrypoint and role, two actors with identical arguments (v2 needs at least two).
- **Arguments:** one canonical tuple, plus one-at-a-time variations over each argument's boundary values from its
  frozen contract:

  | Type | Boundary values |
  |---|---|
  | Integer | min, min+1, max−1, max, and 0 if in range |
  | Float | min, max, and 0.0 if in range |
  | String | the shortest and longest allowed strings in the declared charset, written as a fixed deterministic fill |
  | Boolean | each allowed value |
  | Enumerated | each allowed value |
  | Nullable | null, if allowed |

- **Predicates:** a tuple that violates a contract predicate is generated, rejected at stage 3, and counted (section
  10).

**Pair units** [new]:

- **Which pairs:** every unordered pair {eᵢ, eⱼ} with i ≤ j. A self-pair is allowed only when `max_instances` ≥ 2.
- **Two actors only,** because the protocol says "pairs".
- **Three argument assignments per pair:**
    1. both actors on their canonical tuples;
    2. arguments chosen to collide, using the same values for every argument name the two signatures share;
    3. arguments chosen to differ, using each actor's first non-canonical boundary value.

## 5. Goal generation

- **[inherited]** E's state is "Phase 9B constructor over deterministic goals", and R's state is "E's goals".
- **[new] A deterministic goal policy** per application:
    - written by the onboarding person from the schema and registration only;
    - pinned by digest like the context;
    - frozen before any held-out Muse output.

  It maps each argument name that refers to a stored key to an (entity, field) pair. Its minutes are logged under
  §10.
- **[new] Reference goals:** for each unit, every mapped argument value becomes one goal, `count 1`, `eq` on that
  entity's key, with labels derived deterministically.
    - A-derived units take their goals from their fixtures (section 3).
    - No other values come from the generator. Keys and fillers come only from the shared planner and its trusted
      context, exactly as for D.
- **[new] No background population** beyond reference goals. Adding populated rows would be a new comparator choice,
  and isn't proposed.
- **The frozen v2 registry has no argument-to-entity mapping,** so this policy is a new, separately pinned artifact.
  It isn't a change to frozen files.

## 6. Candidate selection and ordering

- **[new] No selection.** Every generated unit that passes stages 1 to 3 is in every replicate.
- **[new] Order for seed s:** ascending
  `sha256("deployspecter-live-protocol-v1:E-order:<owner>/<repo>:<s>:<unit_id>")`.
    - This follows the protocol's own convention of SHA-256 orderings (§3.4, §4.1, §4.2).
    - `unit_id` = SHA-256 of the generator version, the unit's source (A, B or pair) and its canonical inputs, so it
      doesn't depend on the seed.

## 7. Randomness

- **[inherited]** Seeds 1, 2 and 3.
- **[new] No pseudo-random generator.** The only randomness is the keyed SHA-256 ordering in section 6, which is
  reproducible from the seed and the unit set alone.

## 8. Workload-generation budget

- **[new]** Generation is exhaustive and pure, with no model and no database, so it needs no deciding budget.
- **[new]** The only limit is the 10,000-unit safety ceiling (section 2). Reaching it blocks E and R for the
  application, with no truncation.
- **[new]** Generation time and unit counts by source are reported per application.

## 9. Planning and construction budget

- **[inherited, as proposed for Amendment 003]** The same planner, bridge, constructor and verification gate (O3-b),
  with the same operation-counted limits as D:
    - O6 is unresolved (`O6_OFFLINE_CALIBRATION.md`);
    - O5 is blocked.
- **[new]** Construction runs once per unit and is reused across seeds and by R. Seeds change only the order.
  Construction doesn't consume B or T (N1).

## 10. Failure handling and accounting

| Event | Outcome |
|---|---|
| A generated unit fails stages 1 to 3 | "generation rejected", kept with its stage and code, never replaced |
| Planner, bridge, constructor or gate outcome | Mapped exactly as for D (O8 and O9) |
| A unit that can't be constructed or verified | Consumes no runs, stays in the record, and the rotation continues |
| An application where every unit fails, or generation fails | E and R are blocked for every defect of that application. The defects stay in the denominator as misses |
| Infrastructure failure | No automatic replacement or retry. The batch stops under Gate 2A |

**Reported per application and replicate:**

- units generated, by source;
- rejected units;
- planned units;
- constructed units;
- verified units;
- runs used per unit;
- unused budget.

## 11. Exact reuse of E's state by R

- **[inherited]** R's workloads are E's, and its starting state is "E's goals".
- **[new] Same state:** for each unit, R starts from **E's verified state**. That means E's effective seed is rebuilt,
  and its gate snapshot digest (O3-b) must equal E's before any R run. A mismatch is a tool bug under §12, and the
  affected runs are repeated for every arm.
- **[new] Capture:** one plain serial run per verified E unit, under Gate 2A's existing `plain_serial` mode. Actors run
  in the document's actor order, which is E-7. It records the SQL and parameters R replays.
- **[new] Budget:** capture doesn't consume R's B or T, which is E-6. That matches amendment 002's treatment of replay
  and N1. Capture is reported separately.
- **[new] Order:** R uses E's seeded order, the same seeds, and the same round-robin allocation.
- **[new] A capture that fails** means R is blocked for that unit, recorded, and the unit is never replaced.

## 12. Fairness relative to D

**The same for D and E:**

- registration;
- trusted context, with a `MATCH`;
- planner, bridge and constructor versions;
- the common verification gate (O3-b);
- operation limits;
- execution budget and replicate shape;
- exploration policy within a workload;
- oracle.

**Neither D nor E sees** requirements, oracles, labels, schedules or fixes.

**E's freeze:**

- the generator, B rules, pair rules, A translations and goal policy are hashed before any held-out Muse output;
- the effort is logged.

**Known asymmetries, all to be reported:**

- **Breadth.** An E cell has many units, and a D cell has at most one.
- **Starting state.** D's goals come from Muse, and E's from the goal policy.
- **Failure.** An unconstructible D replicate executes nothing, while an E replicate continues to its other units.

These follow from v1.2's definitions, and aren't introduced here.

## 13. Status of E-1 to E-8

| # | Decision | Proposal | Remaining |
|---|---|---|---|
| E-1 | Units per replicate | All units in each replicate. Equality is by runs and seconds (sections 1 and 2) | Approval. **[comparator]** round-robin allocation |
| E-2 | A tests not expressible as v2 | Blocked in E, listed (section 3) | **[comparator]**: explicit approval |
| E-3 | Pairing | Unordered pairs, self-pairs by `max_instances`, three argument assignments (section 4) | Approval |
| E-4 | Goals | Pinned goal policy, with reference goals only (section 5) | Approval. The policy format must be specified and reviewed |
| E-5 | Generation budget and randomness | Exhaustive generation, a 10,000-unit safety ceiling, SHA-256 keyed order (sections 6 to 8) | Approval |
| E-6 | R's capture budget | Doesn't consume B or T (section 11) | Approval |
| E-7 | R's capture order | The document's actor order (section 11) | Approval |
| E-8 | The generator | Not implemented | Needs implementation, review and a freeze after approval |

**O10 can't be adopted until the following are approved and frozen:**

- E-1 to E-7;
- the goal-policy format;
- E-8's generator.
