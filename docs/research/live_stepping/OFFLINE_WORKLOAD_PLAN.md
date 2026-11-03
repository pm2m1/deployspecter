# Offline research plan: typed workload proposals, validation and ablations

A plan only. Nothing here is implemented, and nothing here calls a model, runs external application code, or uses the
held-out corpus or private research. Everything below uses committed project code and synthetic examples. It stays
within evaluation protocol v1.2; any change to that protocol's arms, budgets or scoring needs a numbered amendment
first.

## Starting point

Already committed:

- **`live/workload.py`.** A typed `WorkloadProposal` with no field that can hold SQL or code: registered entrypoints,
  scalar arguments, version labels, request or background-job roles, `SeedGoal` logical state goals, and
  `SourceConstraint` line references. `AppRegistry` declares what may be used, `validate_workload` lists every
  violation, and `compile_workload` returns registered functions with unresolved goals.
- **The synthetic reservations application**, its independently written requirements, and the live stepping engine.
  The engine needs PostgreSQL to run actors; only its data types and validation can be tested without a database.
- **Baseline B** in the frozen release engine, the schema-derived non-LLM harness, which is reused, not modified.

Phase 9B's deterministic state constructor lives uncommitted in a separate worktree. This plan only defines an interface
to it. Bringing its code in needs separate approval.

## 1. Typed workload specifications

- **Version the format** (`schema_version`) and publish its JSON Schema as a frozen artifact, hashed with the protocol's
  tool freeze.
- **Add the remaining declarative fields:** an optional per-actor schedule hint (a preference, never a requirement), a
  per-proposal budget request that the harness caps, and a `purpose` label from a fixed list (contention, ordering,
  rollout overlap, failure recovery), which is used only for analysis.
- **Reject unknown fields everywhere.** Strings come only from registered patterns or choices, as today.
- **Fixtures, not model output.** Development uses hand-written proposals, plus deliberately malformed ones. They're
  labelled as hand-written, never as Muse Spark output.

## 2. Registered entrypoint selection

- An `AppRegistry` entry per application, written by a person, records the following. Onboarding time and lines are
  measured, per protocol section 10:
    - the function's import path
    - argument specifications with types, ranges, patterns and choices
    - version labels
    - request or background-job role
    - any required pairing (for example "release needs a prior reservation goal")
- **Selection rules a proposal must satisfy:**
    - at least two actors
    - only registered entrypoints for the declared versions
    - background jobs only where registered as such
    - at most the registered number of instances of any entrypoint
- **Compiling a proposal resolves names to callables** in the harness, never in model text.

## 3. Deterministic argument validation

- **Validation is a pure function of the proposal and registry.** It gives the same verdict every time, lists every
  reason, and has no database access.
- **Property-based tests** with Hypothesis, already a dev dependency:
    - every value the registry accepts compiles
    - every out-of-range, wrong-type, unpatterned or unknown value is rejected with a reason naming it
    - no accepted proposal yields a string that wasn't registered
- **A rejection taxonomy** (unknown entrypoint, version, argument, type, range, pattern, choice, role, seed entity,
  field or source line) gives counts that feed protocol section 9's "proposal rejection reasons and rates".
- **Canonical form.** A deterministic hash of each accepted proposal, so equal proposals are recognised as duplicates
  across arms.

## 4. Integration with Phase 9B state construction

- **Interface only, defined offline:** `resolve(goals, schema, registry) -> ConstructionReport`, where each logical
  `SeedGoal` becomes candidate seed rows by deterministic rules. Then the existing constructor's classes report what
  was achieved: `ORIGINAL_VALID`, `CONSTRUCTED_EQUIVALENT`, `IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS`, and so on.
- **The translation from goals to rows is ours and deterministic.** Field operators (`eq`, `lt`, `is_null`…) map to
  chosen values by fixed rules: the smallest satisfying value, a seeded choice from a registered domain, or refusal.
  It never consults requirements, oracles or failing schedules.
- **Arm separation:** arm C resolves goals literally, with no search, and arms D to G use the constructor, matching
  the protocol's 2×2. Infeasible goals are counted, never dropped.
- **Offline tests** use the synthetic reservations schema: feasible goals, conflicting goals, goals needing order, and
  goals the supported operations can't reach.

## 5. Baseline B versus Muse workload ablations

Arm definitions come from protocol v1.2, section 7. This plan prepares their offline machinery only.

- **Arm B:** the frozen Baseline B generator, wrapped so its output is expressed as `WorkloadProposal`s. The same
  validator and budget then apply to every arm.
- **The Muse arms**, when approved later: C, D and G use Muse workloads, and F uses Muse state goals. They need a frozen prompt, frozen model settings, request and
  cost ceilings, and three responses per defect. Until then their slot in the pipeline is filled by labelled
  hand-written fixtures, so the harness can be tested end to end without any model request.
- **Equal budgets:** three replicates per arm at B/3 runs and T/3 seconds each. Deterministic replicates use seeded
  orders 1, 2 and 3.
- **The comparison table is produced by one script**, from frozen inputs, with no manual steps.
- **No result from development fixtures is reported as a comparison of arms.** Development uses the synthetic
  application only.

## 6. Independent coverage and scoring metrics

All defined in protocol section 9, implemented here as offline functions over traces:

- Registered entrypoints reached, and distinct normalized statements executed.
- Line and branch coverage of registered modules, through `coverage.py` in the child process.
- Conflicting statement pairs on the same row or table, and runs with an observed lock wait.
- Distinct behaviours per run, and requirements exercised (non-vacuous).
- Proposal rejection reasons and rates, and seed goals feasible by constructor class.
- Detection, using the protocol's conditional replay rules (amendment 002) and independent oracle validation. Only
  `LiveTrace.scoreable` runs count.

Each metric gets a unit test with a hand-computed expected value on a fixed synthetic trace, so a metric bug can't
silently change an arm comparison.

## Order of work, each step separately reviewable

1. Format versioning, the JSON Schema artifact and the rejection taxonomy, with property tests.
2. Registry selection rules and canonical hashing.
3. The goal-to-row translation interface and its tests, with the constructor interface stubbed.
4. Arm B wrapped as proposals, with the equal-budget harness on the synthetic application.
5. The metric functions and their fixed-trace tests.
6. A dry run of the full offline pipeline with hand-written fixtures in the model slots, clearly labelled.

## Explicitly out of scope until approved

- Model requests, and any Muse Spark output.
- Bringing Phase 9B code into this branch.
- External or held-out applications.
- Database-backed LangGraph work, and any change to the protocol.
