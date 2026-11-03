# Phase 9B compatibility audit (offline step 5A)

**Recommendation: READY for a pure offline planning interface (step 5B). NOT READY for integrating Phase 9B.**

This audit is read-only. No Phase 9B code was run, imported, copied or changed, no database was used, and nothing
here is committed.

- **Date:** 2026-11-03
- **Contract audited against:** WorkloadSpec v2, frozen at `7bb9bbc4b35499020f303dbb8d57efd3feb91205`, with its
  manifest at `48df818878b7ba433d5120398dc2907b16f7cab0`.

## 0. Freeze boundary and method

- **The frozen contract is unchanged.** `tests/test_workload_v2_frozen.py` passes (6 tests). The 14 manifest files are
  byte-identical to the manifest commit, and nothing in this step edits them.
- **Phase 9B was inspected read-only,** in its uncommitted worktree on branch `trigger-aware-validation`, on top of
  `2dd347b`:
    - git was used only with `--no-optional-locks`, and nothing was executed there, so no bytecode caches were written;
    - afterwards it had the same HEAD, the same 13 uncommitted entries, and no file newer than the start of the
      inspection.
- **Excluded:**
    - two retrospective tools in that worktree, which belong to confidential work, were not opened beyond their
      first lines;
    - the private research archives and the separate experiment worktree weren't accessed;
    - no fixture-specific finding appears here.
- **Tests weren't run,** because they need PostgreSQL. Statements about behaviour below come from reading the code and
  its tests, not from executions in this audit.

**Inspected files, SHA-256 at inspection:**

| SHA-256 | File | State |
|---|---|---|
| `bb3fec6c4efa473315c76eb8d923c00ea17373475f05b84a3bc3dd1efd47c397` | `src/deployspecter/state_constructor.py` | untracked |
| `2684e57fbce8e594ccd82bd02eb3952744613e6eec3e50d25b329d0b6d972152` | `src/deployspecter/seed_feasibility.py` | untracked |
| `de970dd5d313a2ea4b420a90765e2b457695b780c53819603bc9fa038bb46647` | `src/deployspecter/triggers.py` | untracked |
| `7b1026b3dac09f9855930ba9154fbda8a5821c8185f8a1bf607c74057de5c145` | `src/deployspecter/pipeline.py` | modified release file |
| `03b73736062fc1825ad12bd1e2f326401a408c6191ac7d3ced835b583006a0c5` | `src/deployspecter/validation.py` | modified release file |
| `e6457de745a68e6d6e71a53c6f69904aaa1806a5606955c83f3e9c9201d5740e` | `src/deployspecter/models.py` | release, unmodified |
| `e0bea34a415f808f6a4cf8d0c3e20689c8e35b83e8f90d953c53bd96a5f9b788` | `tests/test_state_constructor.py` | untracked |
| `9d169380dbf37de4fbe7b334fe683a61fd0fbaf2aaa40c6ce0e3ee9be241964f` | `tests/test_seed_feasibility.py` | untracked |
| `99ae410b75e1c433a3e796edb96fe4e638eb710d895c7c7d87245a9e81a54c3a` | `tests/test_triggers.py` | untracked |
| `9525af97baa591a85ded29da196975d3143d7507b0c2cab3e000c4baeb511b17` | `docs/TRIGGER_AWARE_EVALUATION_PLAN.md` | untracked, design only |

## 1. Phase 9B: architecture and evidence

### Implemented, uncommitted

| Module | What it does |
|---|---|
| `triggers.py` | Reads user triggers from PostgreSQL catalogs and tokenizes their PL/pgSQL bodies. It never runs a trigger. Each trigger gets targets it *may* write, and an effect of `understood` or `unknown`. Dynamic SQL, calls to other functions and other languages make the effect `unknown`. `fired_closure` follows trigger chains |
| `seed_feasibility.py` | Executes a proposal's seed rows on the disposable schema, row by row, in one rolled-back transaction with savepoints, recording what each insert added and removed. `strict` mode accepts and rejects exactly what `Sandbox.reset` does. `reconcile` mode may skip one kind of row: an explicit row a trigger already produced, identical by PostgreSQL's own comparison, on a table with no INSERT triggers or rules. Each skip is recorded |
| `state_constructor.py` | Keeps the requested rows fixed and searches only physical plans: insertion orders, and leaving out a row that is already present. Replays the best plan from a clean rebuild, repeats it to check determinism, then checks it independently with plain SQL in a separate schema |
| `pipeline.py` (modified) | `compile_all` gains seed modes `strict`, `reconcile`, `construct` and `construct_with_assumptions`. Accepted construct results replace the spec's seed with the effective seed, and an audit record is attached |
| `validation.py` (modified) | `CompileReport` gains `seed_warnings` and `seed_audit` |

### Proposed only, not implemented

- **The goal-to-row translation:** `OFFLINE_WORKLOAD_PLAN.md` §4, `resolve(goals, schema, registry)`.
- **The trigger-aware evaluation plan:** design only, needing separate authorization.
- **Arms C to G** of protocol v1.2.

### Answers to the ten questions

1. **Input.** `construct_state(sandbox, setup_sql, seed, context, source=None, budget=None)`:
    - `seed` is a list of the release engine's **concrete** `SeedRow(table, values=[ColumnValue(column, value)])`,
      where `value` is `str | int | float | bool | None`.
    - `setup_sql` is the schema as one SQL text.
    - `context` is a `SeedContext` read from the live database: table info, triggers, and tables with rewrite rules.
    - `source` is the release engine's statement-level `SourceModel`, which is optional.

   **There's no logical-goal input.** Phase 9B never turns a goal into rows.
2. **Output.** A `ConstructionReport` with:
    - a classification (see 6);
    - the effective seed and its digest, the plan steps with the state after each, and the final snapshot and
      sequences;
    - dependency and trigger evidence, the latter including each trigger function's SHA-256;
    - assumptions, rejected alternatives, budget used, and the independent verification.

   `construction_audit` wraps it with the raw proposal and its digests.
3. **Schema and trigger dependencies.** Everything comes from the live catalogs: `information_schema.columns`,
   `pg_constraint` (single-column, non-deferrable foreign keys become hard ordering edges), `pg_rewrite`,
   `pg_sequences`, and trigger catalogs plus function bodies. They're read through the release `Sandbox`, in its fixed
   `RUN_SCHEMA`, built from `setup_sql`.
4. **Feasibility checks, all executed in PostgreSQL:**
    - per-row execution;
    - structural failures (SQLSTATE `42…`);
    - order-independent failures: type mismatches, and CHECK or NOT NULL failures on tables without BEFORE triggers;
    - two requested rows sharing one primary key;
    - triggers with unknown effects, or writing outside the schema, and rewrite rules;
    - the requested rows found in the final state, by bipartite matching;
    - a clean-rebuild replay, a repeat replay, and a plain-SQL check in a separate schema.
5. **Deterministic search and insertion order.**
    - It's a depth-first search over `sorted(remaining)` row indices, pruned by state hash and remaining set, under
      operation, plan and rebuild limits.
    - Candidates are ordered by inserted-row count, then by inversions against the original order, then by steps.
    - **One limit is wall-clock time** (`max_seconds = 15`), so whether a search finishes can depend on machine load.
    - The "original order" is the proposal's list order. `reordered` is measured against it.
6. **Rejection and unsupported states:**
    - `ORIGINAL_VALID`, `RECONCILED_EQUIVALENT` and `CONSTRUCTED_EQUIVALENT` (the proven set);
    - `CONSTRUCTED_WITH_ASSUMPTIONS`;
    - `IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS`, which means only that no plan over these rows exists;
    - `UNSUPPORTED_OR_UNKNOWN`, for unknown trigger effects, writes outside the schema, rewrite rules, deferred-only
      production, non-deterministic results, or a failed independent check;
    - `SEARCH_BUDGET_EXHAUSTED`.

   `seed_feasibility` adds about 16 diagnostic categories, with failure stages kept apart: `structural`,
   `seed_execution`, `schedule_execution` and `oracle_violation`.
7. **Access to correctness oracles.**
    - The constructor gets none, and a test enforces its parameter list and that it doesn't import `oracle`,
      `explorer`, `meta_client`, `analyzer`, `scenarios` or `documented`.
    - **However, `seed_feasibility` imports `scenarios.load_case`** for its command-line entry point. A `Case` carries
      `requirements`, the oracles, and `hand_spec`. So the constructor reaches them transitively, though no
      construction code path uses them. The guard checks direct imports only.
    - `stage_of_verdict` maps verdict labels to stages without reading an oracle.
    - In `compile_all`, the full `TestProposal`, invariants included, is dumped into the audit record, but it isn't
      used for construction.
8. **A known number of operations or actors.**
    - The constructor ignores actors entirely.
    - It is limited to `MAX_SEED_ROWS = 10` rows, from the release validator, and the search grows factorially in
      rows.
    - **The `compile_all` path also runs the release validator, which requires 2 to 3 actors.** Version 2 allows 4.
9. **Mutable global state and private fixtures.**
    - No module-level state is mutated.
    - It does depend on the release `Sandbox` and settings, including `test_dsn` from the environment, on the fixed
      schema names `RUN_SCHEMA` and `ds_construct_verify`, which it drops and recreates, so concurrent runs would
      collide, and on wall-clock time.
    - The core modules carry no fixture data, and a test checks the constructor names nothing fixture-specific. The
      tests use fixtures written in the test file.
10. **The boundary between candidate construction and database verification.** There isn't a pure stage.
    - **Construction is itself a database search:** it executes inserts.
    - **Verification** is the clean-rebuild replay plus `verify_independently`, plain psycopg in its own schema, on a
      database whose name must end in `_test`.

    The pure, offline part, logical goals to candidate rows, doesn't exist in Phase 9B.

## 2. Compatibility matrix: WorkloadSpec v2 state goals to Phase 9B

WorkloadSpec v2 represents goals as `ValidatedWorkload.authorized.state_goals`, a tuple of
`StateGoal(entity, label, count 0–20, constraints)`. Each constraint is a
`GoalConstraint(field, op ∈ {eq, ne, lt, le, gt, ge, is_null, not_null}, value: typed or none)`. There are at most 12
goals and 64 rows in total, sorted by label. Goals are logical, so nothing in v2 names a table or column.

Key to the last three columns: **O** is *mappable without an oracle*; **A** is *a new research-only adapter suffices*;
**U** is *must stay unsupported*, at first.

| v2 feature | Phase 9B capability | Trusted context needed | Ambiguity | O | A | U |
|---|---|---|---|---|---|---|
| `entity` | none: Phase 9B takes table names | a harness-written map from entity to table | v2 entity names aren't table names; an unknown entity mustn't be guessed | yes | yes | — |
| `field` | none: Phase 9B takes column names | a map from field to column, per entity | same | yes | yes | — |
| `label` | none (row indices) | none | none: carried for traceability, and identity-significant in `vplan1` | yes | yes | — |
| `count = n ≥ 1` | n explicit rows, if the adapter writes them | primary key and unique columns, their types, identity and default facts | needs n distinct keys; a deterministic rule (smallest unused value in canonical order) is a choice to record | yes | yes | — |
| `count = 0` | none: Phase 9B asserts presence only | — | "no such rows" is an absence claim, which only a database check can confirm, and triggers can create rows | yes | no | **yes** |
| `eq` with a typed literal | yes: becomes a `ColumnValue` | the column type, and which v2 types each column type accepts | `str` into `timestamp`, `uuid` or an enum relies on a PostgreSQL cast; `float` into `numeric` may round | yes | yes, with an explicit allow-list and no coercion | other casts |
| `ne`, `lt`, `le`, `gt`, `ge` on `int` | none: values need choosing | the type's range; CHECK constraints are opaque text | the choice (smallest satisfying value) is a policy; CHECK expressions can't be evaluated offline | yes | yes, as a candidate; the database step decides CHECK | — |
| the same on `float` | none | as above | finding a neighbour with `nextafter` is exact for `double precision` only, not `numeric` or `real` | yes | `double precision` only | other types |
| the same on `str` | none | collation | ordering depends on collation | — | no | **yes** |
| the same on `bool` | none | — | no order | — | no | **yes** |
| comparisons against `null` | none | — | undefined | — | no | **yes** (the v2 parser already requires a value for these operators) |
| `is_null` | yes: value `None` | nullability | NOT NULL makes it infeasible offline; a BEFORE trigger could still change the row | yes | yes | — |
| `not_null` | none: needs a value | a harness-written filler policy, per column type | the filler chosen could matter to a defect, so it's recorded as an assumption | yes | yes, if the policy is frozen before held-out runs | if there's no policy |
| several constraints on one field | none | as above | numeric intersections are checkable offline; contradictions are infeasible | yes | yes | for `str` |
| unmentioned NOT NULL columns without a default | none: Phase 9B never fills | filler policy | as for `not_null` | yes | yes, with policy | if there's no policy |
| a row referencing another goal's row (foreign key) | ordering edges from foreign keys | foreign-key metadata | v2 has no cross-goal references, only literal values that happen to match | yes | literal matches only | implicit parents |
| a foreign-key value with no matching goal | none: Phase 9B adds no rows | — | needs a parent row nobody asked for | — | no | **yes**: infeasible or unsupported, never an invented parent |
| a goal on a table a trigger writes | yes: "already present" plans, and reconcile skips | trigger metadata, flagging the table | whether a trigger or an explicit insert produced the row is decided only by the database search | yes | candidate only; database step | — |
| goal order | Phase 9B measures "original order" and `reordered` against list order | — | v2 goals are a set, sorted by label | yes | yes: the canonical order (label, then row index) is the "literal" order, and recorded | — |
| typed equality when matching | `_same` compares through `float()`, so `"1"` equals `1` and integers beyond 2^53 merge | — | **a conflict with v2's exact typing** | — | the adapter must refuse type mismatches first; the database step compares with PostgreSQL casts | — |
| more than 10 rows | `MAX_SEED_ROWS = 10` gives `SEARCH_BUDGET_EXHAUSTED` | — | v2 allows 64 rows | yes | planner reports `indeterminate` | until a constructor cap is agreed |

**Nothing is silently reinterpreted.** Every row marked U produces an explicit `UnsupportedGoal` result.

## 3. Proposed interface (step 5B, a new unfrozen module)

The module would be `src/deployspecter/live/state_planning.py`, research-only and pure. It wouldn't import Phase 9B,
the release engine, psycopg or any oracle module.

```
TrustedSchemaContext          harness-built, pinned by digest like the registry and contracts
  context_version: 1
  application, registry_digest            bound to one registry
  schema_digest                           digest of the catalog snapshot this was written from
  entities: {entity -> TrustedEntity}
TrustedEntity
  table; columns: {field -> TrustedColumn}; primary_key: [field]; unique: [[field]]
  foreign_keys: [(field, entity, field)]  single-column only
  trigger_written: bool; has_insert_triggers: bool; check_constraints: [sha256]  (opaque, never evaluated)
TrustedColumn
  column; pg_type ∈ closed list {smallint, integer, bigint, double precision, boolean, text, varchar(n)}
  accepts: v2 types allowed without a cast; nullable; has_default; identity; generated
  filler: optional, from a closed policy {smallest_int, empty_text, false, none}
TrustedPlanning(reviewer, pinned_digest)  built by harness code

plan_state(validated, *, registry, contracts, context, trusted, budget) -> PlanningResult
  1. check_validated(validated, registry=registry, contracts=contracts) must pass
  2. the context must verify against `trusted`, and be bound to registry.digest and the application
  3. goals → candidate rows by fixed rules, in canonical order (label, then 0..count-1)

PlanningResult, exactly one of:
  CandidatePlan        request: StatePlanningRequest
  UnsupportedGoal      goal_label, feature, reason            (from the U column)
  InvalidTrustedContext reasons                                (pin, binding or unknown type)
  InfeasibleUnderAssumptions goal_label, reason, assumptions   (contradiction, NOT NULL, key space, range)
  Indeterminate        reason, budget_used                     (row cap, or value search exhausted)

StatePlanningRequest
  plan_identity (vplan1), context_digest, planner_version, rules_version
  rows: [CandidateRow(goal_label, ordinal, entity, table, values: [(column, v2 type tag, exact value)],
                      origins: {column -> goal_constraint | key_assignment | filler})]
  dependencies: [(child row, parent row, foreign key)], assumptions: [str], budget_used
  feasibility: "candidate"                                     never "constructed" or "verified"
```

**Rules:**

- **No SQL, code or names from proposals.** Tables and columns come only from the trusted context, and values only
  from goal literals, the fixed assignment rules and the frozen filler policy. A request holds identifiers and typed
  values, never SQL text.
- **No oracle, requirement, schedule, defect label or fix diff, ever.** Trusted schema metadata is allowed.
- **A candidate isn't a construction.** Only the later database step can classify it with Phase 9B's classes, and the
  two classifications are reported separately.

### What a future executed-scenario digest must carry, beyond what `WORKLOAD_IDENTITY.md` already lists

- The `StatePlanningRequest` digest: plan identity, context digest, planner and rules versions, and the candidate rows.
- The Phase 9B report's:
    - classification, effective seed digest and plan order;
    - final snapshot and sequence values;
    - assumptions, and trigger function SHA-256 values;
    - setup or schema digest, budget used and the reason it stopped.
- The source hashes of `state_constructor`, `seed_feasibility` and `triggers`.
- The database server version and settings.

## 4. Supported, unsupported and ambiguous cases

- **Supported as candidates:**
    - known entities and fields;
    - `count` of 1 or more, with keys assigned;
    - `eq` with an allowed type;
    - integer comparisons and intersections;
    - `double precision` comparisons;
    - `is_null` on nullable columns;
    - `not_null` and unmentioned NOT NULL columns, given a frozen filler policy;
    - foreign keys between goals by literal match;
    - goals on trigger-written tables, flagged for the database step.
- **Infeasible offline:**
    - contradictory bounds;
    - `is_null` on NOT NULL;
    - a value outside the type's range;
    - more rows than the key space allows.
- **Unsupported:**
    - unknown entity or field;
    - `count = 0`;
    - comparisons on `str` or `bool`;
    - casts outside the allow-list, such as `str` into `timestamp`;
    - `float` into `numeric`;
    - a foreign-key value without a goal that supplies it;
    - `not_null` without a filler policy.
- **Ambiguous, recorded as assumptions rather than resolved silently:**
    - key assignment values;
    - filler values;
    - the canonical order used as the "literal" order.

## 5. Trusted schema and oracle boundaries; protocol v1.2

- **Trusted context is harness-written, pinned and bound,** like the registry and contracts. Writing it is manual
  setup work, logged under §10 of the protocol. The filler policy and planning rules are frozen before any held-out
  arm, as baseline E must be.
- **No oracle-informed seed selection.** The planner can't import oracle, requirement, scenario or analyzer modules,
  and step 5B should test this transitively, not only direct imports. Phase 9B's transitive reach to `Case`
  requirements, through `seed_feasibility`, has to be removed or isolated before integration.
- **Equal budgets.** Protocol v1.2 freezes execution budgets (runs and seconds) but no planning or construction budget.
  Phase 9B's search runs inserts and is capped by wall-clock time. Before arms C to G run, a numbered amendment should:
    - fix identical planning and construction budgets for every arm;
    - use operation counts rather than time as the deciding limit;
    - say whether construction time counts against T.
- **Distinct accounting.** These are reported per arm and never merged or dropped:
    - planning results (`UnsupportedGoal`, `InfeasibleUnderAssumptions`, `Indeterminate`);
    - Phase 9B classes;
    - executable construction;
    - verified defect detection.

  This matches §9 ("seed goals feasible, by constructor class") and §12 ("seed goal infeasible").
- **The planner is shared by every arm.** Arm C ("resolved literally, with no search") uses the same planner with
  Phase 9B's strict check on canonical order. D to G add the constructor search. So C against D isolates the search,
  not the planner.
- **Deterministic and model-proposed goals are told apart by the harness,** never by `provenance.origin`.
- **No retrospective selection.** Every planned unit stays in the denominator, including unsupported and infeasible
  goals.

No protocol arm was run.

## 6. Dependencies an implementation would need

- **Step 5B, pure:** only the frozen v2 modules (`check_validated`, `StateGoal`, typed values), the new module, and
  hand-written synthetic trusted contexts. No database, Phase 9B or release modules.
- **A later integration, needing separate approval:**
    - Phase 9B's `state_constructor`, `seed_feasibility` and `triggers`;
    - release `database.Sandbox`, `models.SeedRow` and `TestProposal`, `analyzer_support.read_schema`, `config`,
      `validation.MAX_SEED_ROWS`, `source_model`, and transitively `scenarios`;
    - psycopg, and PostgreSQL.
- **Unresolved for live applications:**
    - Phase 9B needs `setup_sql`, but live applications build schemas by their own migrations;
    - its `Sandbox` connections sit outside Gate 2A's supervised role, session accounting and log reconciliation;
    - `source` (the release `SourceModel`) doesn't exist for live applications, so the check for reads without
      ORDER BY would be skipped silently.

## 7. Risks to the frozen contract

- **No frozen file needs changing for step 5B.** The planner consumes public, frozen types, and `check_validated` is
  its entry check.
- **Pressure for a version 3.** These would be schema changes, never edits:
    - entity-to-table mapping, cross-goal references, absence semantics and ordering are absent from v2 by design;
    - wanting them later needs a new schema version;
    - the trusted context supplies the mapping instead.
- **Limit mismatch.** Version 2 allows 64 goal rows and 4 actors, but Phase 9B handles 10 rows, and its
  `compile_all` path allows 3 actors. The planner must report `Indeterminate` above an agreed cap, and integration
  should call the constructor directly, not through `compile_all`.
- **Type fidelity.** Phase 9B's `float()`-based `_same` conflicts with v2's exact typing. The adapter must refuse type
  mismatches before rows reach Phase 9B. Its database comparisons use PostgreSQL casts, which is acceptable only
  after that refusal.

## 8. Synthetic fixtures recommended for step 5B

These are hand-written trusted contexts and validated workloads only, with no database.

1. One entity, `eq` on all fields, `count = 1`: a candidate.
2. `count = 3` with an integer primary key: a candidate with keys 1 to 3, recorded as key assignment.
3. `is_null` on a nullable column (candidate) and on a NOT NULL column (infeasible).
4. `ge 5` with `le 3`: infeasible.
5. `lt` the int64 minimum: infeasible (range).
6. An unknown entity, and an unknown field: unsupported, never guessed.
7. `lt` on a `str` field, and on a `bool` field: unsupported.
8. A `str` literal for an `integer` column, and a `float` for `numeric`: unsupported (no coercion).
9. `count = 0`: unsupported.
10. A NOT NULL column with no default, without and then with a filler policy: unsupported, then a candidate with an
    assumption.
11. A child goal whose foreign-key literal matches a parent goal (candidate with a dependency), and one that matches
    nothing (unsupported).
12. 11 or more total rows: indeterminate, at a 10-row cap.
13. A goal on a trigger-written table: a candidate flagged for the database step.
14. Invalid trusted context: wrong pin, a different registry, an unknown `pg_type`, a duplicate column mapping.
15. A forged or stale `ValidatedWorkload`: refused through `check_validated`.
16. Determinism: identical requests and digests across runs, and goal order irrelevant.

## 9. Recommendation

**READY for step 5B: a pure offline planning interface, with tests and breakage checks, no database and no Phase 9B
code.**

**NOT READY for integrating Phase 9B.** The blockers, each needing a separate decision or approval:

- **Constructor budget:** a numbered protocol amendment fixing equal, operation-counted construction budgets.
- **Oracle reach:** isolating Phase 9B's transitive import of `scenarios`, and with it the oracle-bearing `Case`.
- **Live schema setup:** how a live application's schema becomes Phase 9B's `setup_sql`, or a replacement for it.
- **Supervision:** running construction inside, or accounted for by, Gate 2A's supervised boundary.
- **Row cap:** an agreed limit between version 2's 64 rows and Phase 9B's 10.
- **Shared schema names:** concurrency-safe names in place of `ds_construct_verify`.
- **Code transfer:** bringing Phase 9B into this branch at all, which `OFFLINE_WORKLOAD_PLAN.md` and protocol §7
  reserve for a separately approved step.
