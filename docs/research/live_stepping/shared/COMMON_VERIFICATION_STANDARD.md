# Common verification standard, version 1 (proposed)

- **Status:** nonnormative, **PROPOSED, NOT ADOPTED,** shared by Track A and Track B.
    - It refines `workload/COMMON_VERIFICATION_GATE_DESIGN.md`, which is unchanged.
    - It resolves that design's two missing semantics for the **initial synthetic gate only**.
- **No database-backed gate is implemented,** and none may be before separate approval.
- **This is a prospective change** affecting D and E: it makes their admission stricter than Phase 9B's historical
  behaviour. **It is not historical Phase 9B behaviour.** Historical Phase 9B results aren't rescored.

## 1. Count semantics: exact

**E1, row-exactness.**

- For every table the trusted context describes, the verified initial state holds **exactly** the requested rows.
- That means a one-to-one match between requested and final rows, by exact typed equality on each requested row's
  listed columns.
- No other row may be present, whether written by a trigger, a default, setup SQL or anything else.

**E2, goal-exactness.**

- For every trusted state goal, the number of final rows satisfying all its constraints, with SQL NULL semantics, is
  **exactly** its `count`.
- If the planner reports `also_matching_rows` for a goal, the unit is predicted to fail E2. It is still checked in
  PostgreSQL.

**Status of this choice.**

- It is a prospective definition, because the frozen v2 contract doesn't say whether `count` means at least or
  exactly. **No frozen file is changed.**
- It applies identically to every arm.

## 2. No masking in the initial synthetic gate

- **No value is masked.** Timestamps, random values and anything else are compared as they are.
- **A value that differs between the rebuild and the repeat** fails as `nondeterministic_state`.
- **Sequence-generated values** in fresh per-run schemas are deterministic, so they compare equal.
- **A masking rule** may be added later only by a separate, reviewed revision. It would never apply to requested
  columns.

## 3. Acceptance steps

All are required, and missing evidence fails:

1. **Input binding:** every inserted row is an exact typed match of a distinct requested row.
2. **Clean rebuild:** a fresh schema, then the setup SQL, then the effective rows in order. Any error fails.
3. **Exact typed snapshot** of every table and sequence, with no row bound.
4. **Repeat:** a second fresh rebuild whose snapshot is identical, sequences included.
5. **Independent typed check:**
    - a separate connection and code path;
    - typed predicates using catalog types from the pinned snapshot;
    - a server-side per-table digest equal to the rebuild's.
6. **E1 and E2** hold.
7. **Unknown effects fail closed:**
    - an enabled non-internal trigger on a context table without a reviewed write analysis;
    - a rewrite rule;
    - any sign of writes outside the run schema;
    - a column whose type the gate can't compare exactly.
8. **Evidence complete:** every step produced its evidence.

## 4. Construction verification and the oracle stay separate

- **The gate checks only the starting state** against the request and the trusted goals. It never runs, imports or
  sees an oracle, requirement, label, schedule or fix.
- **The oracle checks only post-execution state and results,** for example Track A's `tests/live_app/properties.py`.
  It never decides admission.
- **The two results are separate fields** of the proposed evidence record, and an oracle result requires a verified
  starting state.

## 5. Which arms use it

| Arm | Gate |
|---|---|
| C, C′ | The full gate. Step 2 is their single strict insertion |
| D, E | The full gate, after Phase 9B returns a proven class, once that integration is approved |
| R | Steps 1 to 4, against E's verified digest |

Track A's demonstration uses the C rule, with no Phase 9B.

**Track A's mapping, with Gate 2A unchanged:**

- Gate 2A's bounded reset is step 2.
- Its parent-side `initial_state` read is step 3.
- Comparing every run's initial-state digest is step 4.
- `pipeline.check_initial_state` is E1 for the requested tables.
- Step 5 is OP-6, which is **waiting for approval.**

## 6. Feasibility

| Part | Classification |
|---|---|
| Steps 1 and 6, E1's comparison over a read state, and evidence completeness | Pure. **Implemented for Track A's scenario** (`pipeline.check_initial_state`). A general version is READY_FOR_SEPARATE_IMPLEMENTATION_REVIEW |
| Steps 2 to 5 and 7 | BLOCKED_BY_EXECUTION_BOUNDARY |
| Count and masking semantics | **Proposed here.** If the owner approves them, the missing-semantics block is removed for the initial synthetic gate |
