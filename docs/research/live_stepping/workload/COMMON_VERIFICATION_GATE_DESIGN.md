# Common verification gate: design for O3-b

- **Status:** nonnormative design. **PROPOSED, NOT ADOPTED,** and not authorized for implementation.
- **Not done:** no code was written. Phase 9B was read only, and wasn't imported, executed, copied or modified.
- **Phase 9B references** are to `deployspecter-trigger-aware-dev` at `2dd347b`:
    - `state_constructor.py`, SHA-256 `bb3fec6c…`;
    - `seed_feasibility.py`, SHA-256 `2684e57f…`.
- **Version label:** `common-verification/1`.

**O3-b proposes one independent verification standard** for every constructed state admitted into defect
scoring, whatever produced it.

**This is not historical Phase 9B behaviour.**

- Phase 9B gives its strict and reconciled acceptances one committed reset (`seed_feasibility.py:466–480`).
- Only search-path states get the clean rebuild, the repeat and the independent check (`state_constructor.py:618–665`).
- Adopting O3-b changes D's and E's operational acceptance. It may affect P1, P2 and S3.

## 1. What the gate is and isn't

- **One state, one verdict.**
    - It takes one proposed state and returns `VERIFIED` or a structured failure.
    - It is called once per unit, after construction.
    - Its verdict is final: on failure, the constructor is never re-invoked or asked for an alternative.

  That's what keeps it from becoming a state selector.
- **No oracle input.** It reads only:
    - the trusted schema setup;
    - the pinned context and catalog snapshot;
    - the typed rows from the bridge;
    - the effective seed;
    - the workload's own trusted state goals.

  It never sees requirements, oracles, labels, schedules, fixes, test actors or results. Its module would carry an
  import guard for this, checked transitively.
- **No search internals.** It uses only a constructor's output:
    - the effective seed, the rows actually inserted, in order;
    - the claim of which requested rows were left out as "already present".

  For Phase 9B, those are `ConstructionReport.effective_seed` and `plan` (`state_constructor.py:80–104`), both public
  output fields.

## 2. Inputs

| Input | Source | Trust |
|---|---|---|
| `setup_sql` and its digest | Harness-written synthetic or application schema setup | Pinned |
| Context and catalog snapshot | Steps 5C.1 and 5C.3, with a `MATCH` | Pinned; verification recomputed |
| Bridged representation | `bridge_to_phase9b`, recomputed | Typed rows, `seed_order`, digests |
| Effective seed | The constructor: C and C′ give all bridged rows in bridge order; D and E give Phase 9B's `effective_seed`; R gives E's verified effective seed | Untrusted claim. Checked in step 1 |
| Trusted state goals | The validated workload | Recomputed by `check_validated` |

## 3. Acceptance standard: every step must pass, and missing evidence fails

| # | Step | Behaviour | Failure code |
|---|---|---|---|
| 1 | **Input binding** | Every effective row must equal, exactly and by type, a distinct bridged row. Booleans are never integers, integers are never floats, and text is compared by code point. Bridged rows absent from the effective seed are the "left-out" set | `effective_row_not_requested`, `binding_mismatch` |
| 2 | **Clean rebuild (A)** | A fresh per-run schema. The setup runs, then the effective rows are inserted one by one, in order, with typed binding, and committed. Any error fails the gate. For C and C′ this step *is* the strict check: a failure there is "seed goal infeasible" (O8) | `rebuild_failed` |
| 3 | **Exact typed snapshot (A)** | Every table in the run schema is read with catalog-typed decoding. Integers, doubles (as `float.hex`), booleans, text and NULL are compared exactly. Any other column type is read as server text and marked `compared_as_text`. Sequences record `last_value` and `is_called`. The read has no row bound: a table larger than the declared maximum fails rather than truncating | `read_bound_exceeded`, `unsupported_type_in_requested_column` |
| 4 | **Repeat (B)** | A second fresh schema repeats steps 2 and 3. Snapshots A and B must be identical, sequences included | `nondeterministic_state` |
| 5 | **Independent check (C)** | A separate connection and a separate code path. It rebuilds in a third fresh schema, then checks each requested row with typed SQL predicates (`col IS NOT DISTINCT FROM $n::<catalog type>`, types from the pinned snapshot, not `information_schema`). It computes a server-side canonical digest of each table (`string_agg(t::text ORDER BY t::text)`), which must equal the same digest computed on rebuild A | `independent_check_failed`, `independent_digest_mismatch` |
| 6 | **Requested rows present** | Bipartite matching of every requested row (inserted and left-out) to a distinct final row, by exact typed equality on its listed columns. Omitted columns aren't compared, and are listed | `requested_row_absent` |
| 7 | **Trusted goals hold** | Each goal's constraints are evaluated in SQL on the final state, with SQL NULL semantics. **[BLOCKED_BY_MISSING_SEMANTICS]** whether `count` means at least or exactly that many rows (section 5) | `goal_not_satisfied` |
| 8 | **Trigger-created and default-created rows** | Every row not matched in step 6 is listed by table, with whether a trigger could have written it (from the snapshot's trigger catalog). Every omitted column's final value is recorded. Such rows and values pass only if steps 4, 5 and 7 hold, and the gate never removes or rewrites them | `unknown_effect` (see step 9) |
| 9 | **Determinism and unknown effects** | Fails closed if any of these appear: an enabled trigger whose writes the reviewed analysis can't resolve; a rewrite rule on a seeded table; any sign of writes outside the run schema; a clock- or random-dependent value in a compared column. The last is **[BLOCKED_BY_MISSING_SEMANTICS]** for masking (section 5) | `unknown_effect`, `outside_schema_effect`, `clock_dependent_value` |
| 10 | **Evidence complete** | The verdict is `VERIFIED` only if every step produced its evidence. Any missing, partial or errored step is a failure (`evidence_missing`), never a pass | `evidence_missing` |

**Output:**

- **`VerificationResult`,** carrying:
    - the version;
    - every input digest;
    - snapshot digests A, B and C;
    - the matched rows, left-out rows, unrequested rows and omitted values;
    - per-step status;
    - one verdict.
- **`verified_state_digest`,** equal to snapshot A's digest, which R uses.
- **No execution authorization:** a `VERIFIED` result is never permission to execute. Scheduling remains the
  harness's decision.

## 4. Applying it to every arm

| Arm | Effective seed | Gate | Notes |
|---|---|---|---|
| C | All literal bridged rows, bridge order | Same | Step 2 is C's single strict insertion, so **C and C′ no longer need Phase 9B** |
| C′ | All planned bridged rows, bridge order | Same | As C |
| D | Phase 9B's `effective_seed`, from a proven class only (O9) | Same | Admitted only if Phase 9B's class is proven **and** the gate verifies it |
| E | As D | Same | As D |
| R | E's verified effective seed | Steps 1 to 4 only, checked against E's `verified_state_digest` | R reuses E's verification and never re-verifies independently. A digest mismatch is a tool bug |

**What a gate failure means:**

- the unit isn't admitted;
- it is recorded under O8's mapping, either "blocked by an unsupported path" or "seed goal infeasible" for C and C′'s
  step 2;
- it is never replaced.

## 5. Where the gate needs something it doesn't have

| Need | Kind | Detail |
|---|---|---|
| **Goal `count` semantics** | **Missing semantics** | The frozen v2 contract says "count 0 to 20". The planner treats count as "a number of witness rows", and calls an exact final count "PostgreSQL's to check" (`exact_count_unverified`). **It isn't defined whether a goal holds with more than `count` matching rows.** An owner decision is needed: at least `count`, or exactly `count`. Exactly would make trigger-created matching rows fail |
| **Masking of clock and random values** | **Missing semantics** | Protocol §5 and 002 mention "masked timestamps" for replay without defining which columns are masked. The gate needs a declared masking rule, for omitted columns of time types whose value comes from a default, or determinism fails on every such state. Requested columns are never masked |
| **Trigger write analysis** | **Missing input** | Until a reviewed analysis exists, any enabled trigger on a seeded table makes `writes` unknown, so step 9 fails closed. That's safe, but rejects many real states |
| **Database execution** | **Execution boundary** | Steps 2 to 5 create schemas, run setup SQL and insert rows. They need the parent's admin privileges, which Gate 2A's reset already uses, inside a supervised disposable cluster. **They need no privilege beyond Gate 2A's parent,** but they are new harness statements that Gate 2A doesn't reconcile today. They need fixed statement templates reconciled from the log, and per-run schema names instead of Phase 9B's fixed `ds_run` and `ds_construct_verify`. None of this is authorized |
| **Oracle information** | Not needed | — |
| **Search-specific internals** | Not needed | — |

## 6. Can O3-b be implemented without changing historical Phase 9B code?

**Yes,** as a separate module that runs after Phase 9B returns.

- **Phase 9B's code, results and internal gates stay as they are.**
- **D's and E's admission becomes stricter:** Phase 9B's proven class **and** this gate.
- **Calling Phase 9B at all still needs the separately approved integration step,** with its own blockers:
    - the module-level import of `scenarios`;
    - the fixed schema names;
    - the admin-only connections.
- **C and C′ need only the gate,** not Phase 9B.

## 7. Feasibility

| Part | Classification |
|---|---|
| Steps 1, 6 and 10, and the typed comparison and matching logic (pure, over snapshots) | READY_FOR_SEPARATE_IMPLEMENTATION_REVIEW |
| Steps 2 to 5, and step 9's database reads | BLOCKED_BY_EXECUTION_BOUNDARY |
| Step 7, and step 9's clock and random handling | BLOCKED_BY_MISSING_SEMANTICS |

**Overall: BLOCKED_BY_MISSING_SEMANTICS.** The count and masking semantics must be decided before an implementation
review could judge correctness. After that, the execution boundary still needs separate authorization and
verification.

## 8. Effect on the hypotheses (prospective)

- **P1 (D against E) and P2 (E against R):**
    - D and E are admitted under the same stricter standard;
    - R inherits E's verified state exactly;
    - the rule is identical, but its effect may differ between arms, and must be reported as accepted-unit counts per
      arm.
- **S3 (D against C):** C and D are held to the same standard. C becomes stricter than "resolved literally, with no
  search" alone, so the report must say so.
- **Historical Phase 9B results are not rescored** under this gate.
