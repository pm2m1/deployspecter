# Track B: arm E coverage and fairness audit, and generator policy review

- **Status:** nonnormative, **PROPOSED, NOT ADOPTED.** E isn't frozen. No evaluation input is used.
- **Inputs:**
    - the synthetic development `ticketing` registry and contracts from `tests/test_argument_validation.py`;
    - the planning context from `tests/test_state_planning.py`;
    - the goal policy `book.event_id → event.id`.
- **Code:**
    - `research/track_b/arm_e_audit.py`, new and pure;
    - `research/track_b/arm_e_generator.py`, which gains **default-off** `GeneratorOptions`. With defaults, unit ids are
      identical to the committed prototype (checked against `7dbd96e`).
- **Tests:** `tests/research_tracks/test_track_b_milestone2.py`.

## 1. Against the frozen definition

E is "union of A and B, plus pairs of registered entrypoints", with "Phase 9B constructor over deterministic goals".

| Part | What the generator does | Audit result on the synthetic registry |
|---|---|---|
| A | Expressible A tests become units. Inexpressible ones are listed and stay in arm A | Measured separately with A fixtures: 1 expressible, 1 listed as not expressible. There is no real A suite, so A coverage of a real application **is unmeasured** |
| B | Per entrypoint, the canonical tuple plus one argument at a time over its boundary values | **v1: 22 of 24 boundary values reached** (coupon's two strings missing). **v2: book 25 of 25; sweep 4 of 6** (batch 99 and 100 missing) |
| Pairs | Unordered pairs, with self-pairs by `max_instances`, three assignments | v1: 1 of 1 possible. v2: 2 of 3, with `sweep+sweep` excluded by `max_instances = 1` |

**Other measures:**

| Measure | v1 | v2 |
|---|---|---|
| Candidates generated | 25 | 34 |
| Valid | 23 | 29 |
| Rejected | 2 (stage 3, `cross_field_violated`) | 5 (stage 2, `instance_limit_exceeded`) |
| Rejections from `max_instances` | 0 | 5 |
| Units with unsupported state goals (shared planner) | 0 | 0 |
| Planner use | at most 2 expansions and 10 checks | at most 2 expansions and 10 checks |
| Distinct `vplan1` identities | 21 | 26 |
| Duplicate units (reporting only, never removed) | 2 | 3 |
| Generation ceiling | 10,000, not reached | 10,000, not reached |

## 2. Do the exclusions weaken E?

**On this synthetic registry, measurably but narrowly.** Two mechanisms drop boundary values:

1. **`requires` predicates.** v1's `coupon` requires `price`. A one-at-a-time variation that sets `coupon` without
   `price` is rejected at stage 3, so coupon's string values are never exercised.
2. **Single-instance entrypoints.** Two identical actors exceed `max_instances = 1`. So `sweep`'s B units are all
   rejected, and its boundary values reach E only through pairs, which use only the canonical value and one other.

**For real applications this may be material.** Background jobs (`role: background_job`, often single-instance) are
central to the migration and backfill defects the protocol targets. That is a judgement about likely relevance, not a
measurement. It is flagged for decision, and **E isn't redefined.**

## 3. Policy options evaluated (pure, default-off, PROPOSED, NOT ADOPTED)

| Option | Effect, v1 | Effect, v2 | Kind |
|---|---|---|---|
| `repair_requires`: add the required argument at its canonical value | 25 valid, 0 rejected; boundary 24 of 24 | No change (no `requires` in v2) | **[comparator]**: changes what B units are |
| `single_instance_b = pair_with_partner`: the second actor is the first other entrypoint's canonical call | No change | 34 valid, 0 rejected; sweep 6 of 6 | **[comparator]**: makes such B units pairs |
| Both | 25 valid, 24 of 24 | 34 valid, full boundary coverage | **[comparator]** |

Units generated under any non-default option get different unit ids, so their results can never be mixed with
default-policy units.

## 4. Generator policy, finalized for review

Every row is **PROPOSED, NOT ADOPTED.**

| Policy | Proposal |
|---|---|
| E-1, allocation | Round-robin over units in seeded order within each (defect, replicate) cell, with B/3 runs and T/3 seconds. **[comparator]**, because it affects S5 |
| E-2, inexpressible A tests | Listed by name and counted. They run in arm A only. **[comparator]** |
| Goal policy format | `GoalPolicy(policy_version 1, application, registry_digest, references)`, pinned by its SHA-256 and written from the schema and registration only |
| Ordering | Ascending `sha256("deployspecter-live-protocol-v1:E-order:<owner>/<repo>:<seed>:<unit_id>")`, seeds 1 to 3 |
| Ceiling | 10,000 candidates. Over it, E and R are blocked for the application, with no truncation |
| E to R | R needs E's verified state digest. Otherwise it is blocked |
| Equal opportunity | Equal B and T per cell. E's breadth is reported, not equalized |
| Recommended for owner decision | Adopt `repair_requires` and `pair_with_partner`, both **[comparator]**, because they remove exclusions that come from argument rules rather than from E's definition |

**Disclosure of size and diversity,** reported per application:

- candidates, valid and rejected units by source, stage and code;
- inexpressible A tests by name;
- boundary values reached against available, per entrypoint and argument;
- pairs possible, generated and valid;
- `max_instances` exclusions;
- distinct plan identities and duplicates;
- planner outcomes and counters;
- the policy options in force.

## 5. Fairness notes

- **E's breadth.** E has many units per cell and D at most one. That is v1.2's design, and it is reported.
- **Policy options apply to E only,** because D's proposals come from the model. They make E stronger, which raises
  the bar for P1, and they need explicit approval.
- **Unmeasured:** real-application A coverage, the goal policy's adequacy, and E's strength against D on any real or
  held-out application.
