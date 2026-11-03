# Literal translation and the two readings of arm C (offline step 5C.2)

`src/deployspecter/live/literal_translation.py`, tested by `tests/test_literal_translation.py`.

- **Status:** research-only, pure, **unfrozen and uncommitted**.
- **Unchanged:** protocol v1.2, and `AMENDMENT_003_DRAFT.md`, which stays **DRAFT, NOT ADOPTED**.
- **Not run:** no database, oracle or evaluation arm. Nothing here is a result of protocol arm C.

## 1. Literal translation

`translate_literally(validated, *, registry, contracts, context, trusted, budget=None)` takes the same inputs as
`state_planning.plan_state`, makes the same trust checks in the same order, and returns the planner's result types,
with one difference:

- **The candidate type is `LiteralCandidate`**, whose request is marked `method: "literal_translation"`.
- **Every value carries origin `goal_constraint`.**

`compare_with_planner(...)` runs both methods on one workload and reports how they differ.

**The rules:**

1. **Only `eq` literals and `is_null` supply values.** Exact type tags are kept, so signed 64-bit integers, finite
   binary64 values, booleans and strings stay distinct. An explicit NULL is a value, and a missing column is left
   out.
2. **Every required column must have a literal.** A required column is NOT NULL, with no default, and not generated.
   Otherwise the goal is `key_not_literal` (a primary-key column) or `required_column_unresolved`. That applies to
   `is_null` goals too.
3. **No keys or fillers.** `ascending_int` key assignment and trusted fillers are planner policies, not literal
   sources.
4. **No search.** `ne`, `lt`, `le`, `gt`, `ge` and `not_null` only check a stated literal. Without one, the goal is
   `value_synthesis_required`. Operation counters always show zero expansions.
5. **No inference of database behaviour.** A default or generated column is left out with a `*_not_compared`
   obligation, and trigger effects stay obligations.
6. **No casts.** The planner's type rules apply unchanged: `cast_dependent`, `numeric_coercion`, `opaque_type`, the
   collation rules and `null_comparison`.
7. **No invented identifiers.** `count > 1` on an entity with a primary key or a unique constraint is
   `literal_cardinality`. On a keyless entity, `count` identical literal rows are allowed, since nothing is
   invented.
8. **References and overlap need literals, and nothing is merged:**
    - a foreign key needs a parent row that another goal states literally, with the identical typed value, or the
      goal is `foreign_key_unresolved`;
    - two goals asking for the same key are `overlapping_goal_keys`, never merged;
    - unset columns colliding under NULLS NOT DISTINCT are `unset_nulls_collide`.
9. **Contradictions in stated literals are infeasible, with evidence:** `contradictory_values`,
   `not_null_violated`, `null_with_comparison`, `outside_type_range`, `too_long_for_column` and
   `bounds_exclude_value`.
10. **The planner's general rules apply:**
    - unknown entities and fields, and `count = 0`, are unsupported;
    - infeasibility outranks unsupported, and every goal's status is reported;
    - budget exhaustion is `Indeterminate`, never infeasibility.

## 2. Comparison with the deterministic planner

Fixed synthetic goals and contexts from the test suite. This isn't an evaluation: neither method is run against a
database, and nothing here says which would find more defects.

| Case | Planner | Literal translation | Both accept | Same rows | Planner ops (rows/exp/checks) | Literal ops |
|---|---|---|---|---|---|---|
| 1 Every required value stated | candidate | candidate | yes | yes | 1/1/7 | 1/0/7 |
| 2 Key left to the key policy | candidate | `key_not_literal` | no | — | 1/2/4 | 0/0/1 |
| 2 Value from a comparison | candidate | `value_synthesis_required` | no | — | 1/2/7 | 0/0/4 |
| 2 Required column from a filler | candidate | `required_column_unresolved` | no | — | 2/2/10 | 0/0/5 |
| 3 String order (collation) | `collation_dependent_order` | `collation_dependent_order` | no | — | 0/0/4 | 0/0/4 |
| 3 Float into numeric | `numeric_coercion` | `numeric_coercion` | no | — | 0/0/2 | 0/0/2 |
| 4 Two different `eq` values | infeasible, `contradictory_values` | infeasible, `contradictory_values` | no | — | 0/0/4 | 0/0/4 |
| 5 `count = 3` on a keyed entity | candidate (keys 1 to 3) | `literal_cardinality` | no | — | 3/9/10 | 0/0/0 |
| 5 `count = 3` on a keyless entity | candidate | candidate | yes | yes | 3/3/1 | 3/0/1 |
| 6 int64 minimum and smallest subnormal | candidate | candidate | yes | yes | 1/1/8 | 1/0/8 |
| 7 Unknown entity | `unknown_entity` | `unknown_entity` | no | — | 0/0/0 | 0/0/0 |
| 7 Untrusted context | invalid trusted context | invalid trusted context | no | — | 0/0/0 | 0/0/0 |

**Observations:**

- **Literal acceptance implies identical planner rows.** If literal translation accepts a workload, every required
  column has a literal, and the planner uses exactly those literals and leaves out the same columns. So the rows,
  dependencies, obligations and assumptions are identical. A property test checks this over generated goals.
- **The planner accepts strictly more.** Every difference comes from one of three capabilities literal translation
  lacks: key assignment, trusted fillers, and choosing values from comparisons. That includes inventing identifiers
  for `count > 1`.
- **Rejections and contradictions agree** wherever both methods examine the same constraint.
- **Literal translation never expands**, which shows it does no search.

## 3. The two readings of arm C

Protocol v1.2, section 7: arm C is "Muse Spark seed goals, resolved literally, with no search. An infeasible goal
fails and is counted". Hypothesis S3 says "D detects more than C (the constructor's contribution, given model
goals)".

| | A. Literal translation | B. Shared planner, then strict PostgreSQL construction |
|---|---|---|
| Values | Goal literals only | Literals, plus key assignment, trusted fillers and comparison choices |
| Search over insertion orders | None | None (Phase 9B strict mode) |
| Goals it can represent | Fully stated rows only | Also unstated keys, filler-covered required columns, comparison-only constraints and multi-row keyed goals |
| C against D measures | The planner's value synthesis **and** the constructor's order search, together | The constructor's order search **alone** |

**What B adds over A:**

- **Key synthesis:** `ascending_int` identifiers, including for `count > 1`.
- **Fillers:** trusted values for required columns no goal mentions.
- **Comparison choices:** deterministic values for `gt`, `ge`, `lt`, `le` and `ne`, by exact integer and binary64
  stepping.
- **Pigeonhole checks:** for unique fields, which add more `InfeasibleUnderAssumptions` findings.

None of these is order search. All of them are value synthesis, which the word "literally" suggests C doesn't do.

**Does adopting B change the preregistered comparator?** Yes, materially:

- **Under the wording**, "resolved literally" most naturally means A.
- **Under S3**, "the constructor's contribution" means Phase 9B's search, which only B isolates. Under A, C against D
  conflates value synthesis with search.
- **The protocol never defined how logical goals become rows.** The goal-to-row translation didn't exist when it was
  written, so neither reading can be called the unambiguous preregistered one. Choosing B would give arm C
  capabilities its wording doesn't describe. Choosing A would make S3 measure more than "the constructor".

## 4. Recommendation on Amendment 003

**Keep it DRAFT, NOT ADOPTED, and revise its item 6 before any adoption.** The draft currently proposes reading B.
A more faithful amendment would:

1. **Define arm C as reading A**, matching the preregistered wording, with infeasible and unsupported goals counted
   as the protocol requires.
2. **Add a separately labelled ablation C′ as reading B,** with no model calls, since it reuses the same responses.
3. **Restate S3 as two comparisons:** C′ against D is the order search alone, and C against C′ is the planner's
   value synthesis alone.
4. **Report all three with equal budgets** under the rest of the amendment, and say plainly that C′ wasn't
   preregistered.

That needs your decision. Nothing here adopts it, and protocol v1.2 is unchanged.

## 5. Evidence

| Check | Result |
|---|---|
| Ruff | Clean |
| Literal translation tests (unit and property-based) | **63 passed** |
| All contract, planner, verifier and translator suites, root environment, `-m "not live"` | **1,041 passed**, 1 skipped (the LangGraph adapter file; `langgraph` isn't installed there) |
| Same, isolated environment | **1,089 passed** |
| v1 offline tests | 37 passed |
| Frozen-contract integrity | Passed; committed steps 5B and 5C.1 unchanged |
| Live tests | 0 collected |

`MODEL_API_KEY` was unset throughout.

**Deliberate breakage: 37 of 39 caught.** The mutants cover:

- value invention, in four ways;
- type coercion, in three;
- NULL and missing confusion, in four;
- untrusted input, in three;
- unreported problems, in five;
- silent merging, in three;
- foreign keys, in two;
- contradictions, in four;
- provenance, in four;
- search, ordering, budgets and the comparator.

The two survivors were assessed as equivalent on every reachable input:

- **Removing the infeasibility-downgrade guard.** The translator returns as soon as any goal is infeasible, before
  any later step could relabel it.
- **Not sorting goals.** The frozen `check_authorized` guarantees label order.

**Two earlier survivors were test gaps, now closed:**

- **`is_null` on a NOT NULL column** wasn't tested.
- **"Rows always called equal"** survived because literal acceptance implies identical planner rows. The comparator's
  difference logic is now exercised against a monkeypatched planner result.

## 6. Limitations

- **Nothing here reaches PostgreSQL.** Both readings produce candidates pending PostgreSQL validation, so neither
  establishes that a state can be built.
- **Literal translation shares the planner's trust, type and NULL rules, and its limitations:** the context is
  trusted relative to its pinned snapshot, and CHECK constraints, triggers and data are not evaluated.
- **The comparison shows capability, not effectiveness.** It says nothing about defect detection.
