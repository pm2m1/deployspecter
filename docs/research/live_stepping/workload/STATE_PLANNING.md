# State-goal planner (offline step 5B): design and evidence

`src/deployspecter/live/state_planning.py`, tested by `tests/test_state_planning.py`.

- **Status:** research-only, pure, **unfrozen and uncommitted**. Built on the frozen WorkloadSpec v2 contract (manifest
  `48df818`) without changing it, and on the mappings in `PHASE9B_COMPATIBILITY_AUDIT.md`.
- **What it doesn't touch:** no database, network or file. No Phase 9B code is imported, copied or run, and no
  evaluation arm was run.

## 1. Contract boundary (task 1)

- **A `ValidatedWorkload` stays non-executable.** It carries `execution_authorized: False` and
  `correctness_evaluated: False`, and nothing here changes that.
- **State goals carry no oracle and no SQL.** `StateGoal` is `{entity, label, count, constraints}`, and a frozen test
  checks exactly that.
- **The planner builds on the actual frozen entry points:**
    - `check_validated(validated, *, registry, contracts)`;
    - `validated_plan_identity(validated, *, registry, contracts)`;
    - `registry.revalidate_model`, for re-validating trust and context objects;
    - the `StateGoal` and `GoalConstraint` types.

## 2. API

```
plan_state(validated, *, registry, contracts, context, trusted, budget=None) -> PlanningResult
build_context(config, *, registry, trusted) -> PlanningContext            raises ContextError
context_digest(config) -> str                                              order-insensitive, domain-separated
request_digest(request) -> str
```

- `validated` must be a `ValidatedWorkload`, or `TypeError`. It must be genuine `validate_arguments` output for
  `registry` and `contracts`, or **`UntrustedWorkload`** is raised and nothing is planned.
- `context` is a schema context (a mapping or a `SchemaContextConfig`). `trusted` is a harness-built
  `TrustedPlanning(reviewer, pinned_digest)`.
- Neither the context nor anything sent with it can grant trust.

## 3. Trusted schema context

```
SchemaContextConfig  context_version 1, application, registry_digest, schema_digest (of the catalog snapshot),
                     encoding: "UTF8" (required), entities
TrustedEntity        entity, table, columns, primary_key, unique: [UniqueConstraint], foreign_keys, trigger_written,
                     has_insert_triggers, key_assignment: "ascending_int" | none
UniqueConstraint     fields, nulls: "distinct" | "not_distinct" (required, no default)
TrustedColumn        field, column, pg_type, max_length (varchar only), nullable, has_default, generated,
                     filler: {type, value} | none,
                     collation: "deterministic" | "nondeterministic" (required for text and varchar, absent otherwise)
pg_type              smallint | integer | bigint | double precision | boolean | text | varchar | numeric | opaque
```

**Metadata, not SQL.** Identifiers follow fixed patterns, and every string passes the v2 text policy. Unknown fields,
including `trusted`, `digest` or `sql`, are refused. Nothing is inferred: defaults, generated columns and trigger
involvement are only what the harness declares.

**Three semantics are stated, never assumed:**

- **NULL handling in each unique constraint.** PostgreSQL 15 and later allow `NULLS DISTINCT` (the default) or
  `NULLS NOT DISTINCT`. The planner applies whichever the context states.
- **Each text column's collation.** Equality and inequality on a nondeterministic collation are unsupported, because
  `"a" = "A"` may hold there.
- **The database encoding.** Only UTF-8 is accepted, so `varchar` lengths, counted in code points, match PostgreSQL's
  character counts.

**Checked when the context is built.** Each check fails closed, as `InvalidTrustedContext`:

- **Uniqueness:** no repeated entity, table, field or column.
- **Keys:** key and unique fields exist, and primary keys aren't nullable.
- **Key assignment:** only on a single-column, non-generated integer primary key.
- **Foreign keys:** each references an existing single-column key of the same type.
- **Fillers:** exactly typed, finite, in the column's range and length, never on a generated column.
- **Pin and binding:**
    - the digest must equal the harness pin, from outside the context;
    - the context must be bound to this registry's digest and application.

## 4. Mappings

**Supported, as candidate witness rows:**

| Feature | Rule |
|---|---|
| Known entity and field names | Through the trusted context only |
| `count ≥ 1` | `count` distinct witness rows, ids `label#0..n-1` |
| `eq` with the exact accepted type | int for integer types, float for `double precision`, bool for `boolean`, str for `text` and `varchar`. Range and length checked |
| Integer comparisons | Interval arithmetic over the column's exact range. Choose the lower bound, or the upper bound if only that is given, or 0, stepping past `ne` values |
| `double precision` comparisons | Same, with exact `nextafter` steps. Never −0.0, never a non-finite value |
| `is_null` | Explicit NULL, on nullable columns |
| `not_null`, and required columns no goal mentions | Only from a trusted filler, or for the key, `ascending_int` |
| Boolean `ne` | The other value |
| String `ne` | Only through a trusted filler that isn't excluded |
| Foreign keys | Only when the child's value comes from a goal, and matches a parent row's goal-given key |
| Trigger-written or trigger-firing entities | Candidate, with verification obligations |

Unconstrained columns are left out of the row, never filled, for three reasons:

- **Database default** (`has_default`): recorded as `database_default_not_compared`.
- **Generated:** `generated_not_compared`.
- **Nullable:** `nullable_unset_not_compared`, and kept distinct from an explicit NULL.

**`UnsupportedGoal` codes:**

| Code | Cause |
|---|---|
| `unknown_entity`, `unknown_field` | Not in the trusted context |
| `absence_claim` | `count = 0` |
| `collation_dependent_order` | Order comparison on a string |
| `boolean_order` | Order comparison on a boolean |
| `null_comparison` | A comparison against a null value; use `is_null` |
| `cast_dependent` | A value of the wrong type for the column |
| `numeric_coercion` | A float into `numeric` |
| `opaque_type` | A column type the planner can't compare |
| `generated_column` | A constraint on a database-generated column |
| `non_finite_value_required` | Only an infinite value would satisfy the goal |
| `no_key_policy`, `no_filler_policy` | A required value with no trusted policy |
| `string_value_required` | String `ne` with no usable filler |
| `foreign_key_unresolved`, `foreign_key_value_not_from_goal` | No goal-given parent, or a reference through a planner-chosen value |
| `overlapping_goal_keys` | Two goals ask for rows with the same key; merging witness rows isn't attempted |
| `planner_choice_collision`, `filler_collides_on_unique` | Values the planner or a filler supplied collide on a unique key |
| `unset_nulls_collide` | Columns left unset are NULL, and collide under `NULLS NOT DISTINCT` |
| `collation_dependent_equality` | String `eq` or `ne` on a nondeterministic collation |

**`InfeasibleUnderAssumptions` codes.** Each is a sound contradiction under the trusted context and the listed
assumptions. Each result carries structured `InfeasibilityEvidence`:

- the goals, entity and fields involved;
- the constraints, in canonical form;
- the trusted facts used, such as type ranges, nullability, collation, encoding, or a unique constraint and its NULL
  handling;
- only the assumptions the contradiction relies on.

`_infeasible` can't be called without evidence.

| Code | Cause |
|---|---|
| `not_null_violated` | `is_null` on a NOT NULL column |
| `null_with_comparison` | `is_null` together with a different constraint on the field. A repeated `is_null` isn't one |
| `contradictory_values` | Two different `eq` values |
| `bounds_exclude_value` | The `eq` value breaks the field's other constraints |
| `outside_type_range` | The value can't be stored in the column's type |
| `too_long_for_column` | Longer than the `varchar` limit |
| `empty_domain` | No value of the type satisfies the constraints |
| `insufficient_distinct_values` | A unique field has fewer satisfying values than `count` |
| `duplicate_key_within_goal` | `count > 1` rows with the same goal-given key, including NULL keys under `NULLS NOT DISTINCT` |

**Unsupported never becomes infeasible.** When a goal has an unsupported constraint, no infeasibility is claimed for
that goal. Values the planner or a filler chose, and columns left unset, never support an infeasibility: collisions
among them are unsupported. Budget exhaustion is `Indeterminate`.

## 5. Determinism and budgets

- **Everything runs in canonical order:** goals by label, constraints by field, operator and encoded value, and
  columns by field. Value choices are fixed walks from one end of the domain.
- **The output is byte-identical across runs** and independent of goal or constraint order, which is tested.
- **`PlanningBudget` counts operations only; there's no time limit:**

  | Limit | Default | Counts |
  |---|---|---|
  | `max_rows` | 64, never above v2's 64 | candidate rows |
  | `max_expansions` | 4,096 | rows built and value-search steps |
  | `max_constraint_checks` | 65,536 | constraint evaluations, key comparisons, foreign-key lookups, overlap checks |

- **The consumed counters are in every result.** Running out gives `Indeterminate` (`row_budget`,
  `expansions_budget` or `constraint_checks_budget`), never infeasibility.
- **Static contradictions are found before any search,** so they need no expansion budget, which is tested.
- **Phase 9B's 10-row limit isn't adopted.** A plan with more rows is still a `CandidatePlan`, with
  `phase9b_constructor_compatible: false` and a note. Nothing is truncated.

## 6. Representations

- **`StatePlanningRequest`** holds:
    - the request, planner and rules versions, and the fixed
      `feasibility: "candidate_pending_postgresql_validation"`;
    - the `vplan1` identity, and the registry, contract, context and schema digests;
    - the application, rows, dependencies, obligations and assumptions;
    - the Phase 9B compatibility flag and notes;
    - `request_digest`, a domain-separated SHA-256 with exact typed encoding, pinned in a test.
- **`CandidateRow`** holds the row id, goal label, ordinal, entity and table, typed `values`, `omitted` columns with
  their reasons, `obligations`, the context digest and the plan identity.
- **`CandidateValue(field, column, type, value, origin)`** refuses coercion. Its origin is one of `goal_constraint`,
  `key_assignment`, `comparison_choice` or `filler`.
- **Every result also carries:**
    - a `GoalStatus` per goal (`planned`, `unsupported`, `infeasible` or `not_examined`), with its code and reason;
    - `also_matching_rows` and `undetermined_rows`: other goals' rows that satisfy this goal offline, or whose match
      depends on database values;
    - the counters.
- **Precedence:** `InfeasibleUnderAssumptions` outranks `UnsupportedGoal`, because a sound contradiction holds
  whatever else is unsupported. A finding is never downgraded.

**Obligations:**

- `postgresql_validation_required`, on every row;
- `exact_count_unverified`: count is a number of witness rows, and an exact final count is PostgreSQL's to check;
- `trigger_written_entity` and `insert_triggers_unverified`;
- the `*_not_compared` codes for omitted columns;
- `unique_with_database_values`, and `foreign_key_order`.

## 7. Evidence

| Check | Result |
|---|---|
| Ruff | Clean |
| Planner tests | **131 passed** |
| Contract suites plus planner, root environment, `-m "not live"` | **886 passed**, 1 skipped (the LangGraph adapter file; `langgraph` isn't installed there) |
| Same, isolated environment | **934 passed** |
| v1 offline tests | 37 passed, both environments |
| Live tests in the planner suite | 0 |
| Frozen-contract integrity test | 6 passed; the 14 manifest files are byte-identical |

`MODEL_API_KEY` was unset throughout.

**Deliberate breakage: 65 of 66 caught.** One survivor, assessed as equivalent on the public interface:

- **Not sorting goals in the planner.** `plan_state` plans only workloads that pass `check_validated`, which requires
  the authorized workload to equal what the frozen `authorize` produces, and that sorts goals by label. The planner
  class is private. A test shows that reordered goals in a forged object are refused.

**Corrected from the first Step 5B report.** Two survivors there were wrongly called equivalent:

- **Removing the planner's own `check_validated` call.** It's observable. A forged workload with an invalid context or
  budget would return `InvalidTrustedContext`, or raise `TypeError`, instead of being refused. The refusal message
  also differed. It's now tested, and caught.
- **Removing the application check in `build_context`.** It's observable. `build_context` is public, and a context
  with several problems would report only some of them. It's now tested, and caught.

**Defects found and fixed:**

- **In development:**
    - a within-goal duplicate key was missed behind another goal's key;
    - a float walk to infinity was called infeasible;
    - an infeasibility could be downgraded;
    - static infeasibility and −0.0 handling were untested.
- **In the final review:**
    - a repeated `is_null` was called a contradiction;
    - NULL handling in unique constraints was assumed, not stated;
    - string contradictions assumed a deterministic collation;
    - `varchar` lengths assumed UTF-8;
    - infeasibility reasons weren't structured.

## 8. Soundness limitations

- **"Infeasible" is relative to the trusted context.** A wrong context gives wrong outcomes. The context is written
  from catalogs, never from defects.
- **Not evaluated offline:** CHECK constraints, trigger effects, defaults, generated values, deferrable constraints,
  exclusion constraints, rules, row-level security and existing setup rows. All are obligations for PostgreSQL. An
  assigned key could collide with a row the schema setup already inserted.
- **Witness rows are per goal.** Goals whose rows could be merged into one are reported as overlapping, never merged.
  `count` means distinct witness rows.
- **Text:** equality is planned only on deterministic collations, and lengths only in UTF-8 databases. Both are
  context facts.
- **Unique NULL handling, collation and encoding are as the context states.** A wrong statement there gives wrong
  outcomes, like any other context error.
- **Only single-column foreign keys** are modelled, by literal match.
- **Not proof against hostile code in the same process.**

## 9. Compatibility with Phase 9B's 10-row constructor

A request maps onto Phase 9B's `SeedRow(table, values=[ColumnValue(column, value)])` without inventing anything.
That conversion isn't written; it belongs to a separately approved integration.

- **Exact types:** every value already has the column's exact type, so Phase 9B's loose `float()`-based matching
  can't merge values that differ.
- **Omitted columns** become columns left out of the seed row.
- **Order:** canonical order is the "original order" Phase 9B measures reordering against.
- **More than 10 rows** is flagged incompatible, not truncated. Raising the constructor's limit is an integration
  decision.

## 10. Future evaluation counts (design only; nothing was run)

Counted per arm, defect and replicate. No category is merged with another, and none is dropped:

| Count | Source |
|---|---|
| Logical goals proposed | The workload |
| Goals accepted by the planner (`planned`) | `GoalStatus` |
| Goals unsupported, by code | `GoalStatus` |
| Goals infeasible under assumptions, by code | `GoalStatus` |
| Plans indeterminate, by budget | `Indeterminate.code` |
| Candidate rows constructed, and requests above Phase 9B's limit | `StatePlanningRequest` |
| Requests by Phase 9B class (`ORIGINAL_VALID` … `SEARCH_BUDGET_EXHAUSTED`) | The future database step |
| Rows actually constructed in PostgreSQL (the effective seed, after Phase 9B's independent check) | The future database step |
| Verified application-level defects | The protocol's oracle and replay rules, unchanged |

**Deterministic and model-proposed goals are counted apart,** by the arm the harness assigned, never by
`provenance.origin`. Planner budgets and counters are reported per arm.

## 11. Draft Amendment 003

A proposal for equal, operation-counted planning and construction budgets, and an explicit interpretation of arm C,
is in `AMENDMENT_003_DRAFT.md`. It's **DRAFT, NOT ADOPTED**, kept uncommitted, and changes nothing in protocol v1.2.

## 12. Recommendation

**Ready as the pure front end of a future, separately approved integration.** The open items from the compatibility
audit remain prerequisites for that integration:

- the construction budget (Amendment 003);
- isolating Phase 9B's indirect reach to oracles;
- schema setup for live applications;
- Gate 2A supervision of construction;
- the row cap;
- concurrency-safe schema names.

The planner itself needs none of these to stay correct.
