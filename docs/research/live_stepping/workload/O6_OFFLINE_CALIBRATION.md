# O6: offline planner-budget calibration (development only)

- **Status:** a nonnormative development exercise. **It is not an evaluation arm,** and it adopts no
  protocol value.
- **Not covered:** O5 (Phase 9B construction), which still needs supervised PostgreSQL characterization and the
  approved constructor integration.
- **Not used:** a database, network access (sockets are refused in the runner), a model, Phase 9B, or any
  application or held-out material.

## 1. Inputs, fixed before the run

| Item | Value |
|---|---|
| Corpus | `o6_calibration/corpus_v1.json`, SHA-256 `22be1066c169d70fdbbf59e84a4bd907231f152da66f4372b4d1b138a8880a95` |
| Declaration | `o6_calibration/DECLARATION.md`, SHA-256 `2ef1f769e2782fe3974df95933daeda4965b7f52afaaf9257cc66fd4d6bddddb`. It has the full inventory, probes, selection rule and stability criterion, all written before any planner run |
| Runner | `research_tools/o6_offline_calibration.py`. It refuses to run unless the declaration names the corpus digest |
| Planner | `live/state_planning.py`, unchanged (`state-planning/1`, rules 1, SHA-256 `c9f657f527c23003…`) |
| Results | `o6_calibration/results_v1.json`, SHA-256 `d5827ba670a92c6e0cc29fe8f35a7828211a28b44eeebbff2e1eb07a3c1eda62` |
| Environment | Python 3.12.14, macOS, uv |

**The corpus:**

- **49 hand-written synthetic cases,** over one invented nine-entity context.
- **Categories:**
    - 9 simple;
    - 12 conflicting;
    - 9 foreign-key;
    - 14 unsupported;
    - 5 stress, which are adversarial but valid v2 documents.
- **Strata, in logical rows:** 0, 1, 2, 4, 8, 10, 11, 16, 32 and 64.
- **Review before the run:**
    - no application, held-out or confidential name or content;
    - every case passes stages 1 to 3 of the frozen v2 pipeline, checked by the runner's `validate` mode, which never
      calls the planner.

**Probes:** `max_rows` = 64 (fixed), `max_expansions` = 1,000,000 and `max_constraint_checks` = 10,000,000, the
fields' own upper bounds.

## 2. Results

**Every case ran twice at the probe, with identical outcomes, codes, counters and request digests.** No case came
within 3.6% of either probe.

| Category | Cases | Outcomes | Peak expansions | Peak constraint checks |
|---|---|---|---|---|
| simple | 9 | all `CandidatePlan` (1 to 64 rows) | 1,408 (64 rows) | 212 |
| conflicting | 12 | all `InfeasibleUnderAssumptions`, 9 distinct codes | 2,144 (64 rows) | 612 |
| foreign_key | 9 | 7 `CandidatePlan`, 2 `UnsupportedGoal` | 2,017 (64 rows) | 406 |
| unsupported | 14 | all `UnsupportedGoal`, 14 distinct codes | 2,144 (64 rows) | 612 |
| stress | 5 | all `CandidatePlan` | **35,424** (64 rows over 16 unique columns) | **17,568** (64 rows, 16 foreign keys, a late parent) |

**Usage grows:**

- **Expansions** grow roughly with the square of the rows on each unique column. The ascending walk restarts at the
  low end for every row: 1,408 for 64 plain rows, and 35,424 when every row needs 16 more unique values.
- **Constraint checks** grow with rows × foreign keys × parent rows scanned, and with goals × rows × constraints
  (cardinality).

**Planner wall time,** descriptive only: 13 to 59 ms per case. Most of it is the frozen trust checks that run
before planning.

**At the planner's current defaults** (4,096 expansions and 65,536 checks):

- **Three valid stress documents become `Indeterminate`,** with code `expansions_budget`: `stress_wide_r32`,
  `stress_wide_r64` and `stress_wide_12goals_r64`.
- **So the defaults can bind on valid v2 documents.** Under O8 such a unit would be "construction blocked by
  constructor limit".

## 3. The declared rule, applied

| Limit | Full corpus | Without stress | Without the 64-row stratum | Other leave-one-out subsets | Stable? |
|---|---|---|---|---|---|
| `max_expansions` | 131,072 (peak 35,424) | 8,192 | 32,768 | 131,072 | **No** |
| `max_constraint_checks` | 65,536 (peak 17,568) | 2,048 | 4,096 | 65,536 | **No** |

**Both values fail the declared stability criterion, so both stay UNRESOLVED.**

- **A single category moves the result 16 to 32 times:** the adversarial stress cases, all at 32 to 64 rows.
- **The "twice the largest observed usage" rule is therefore governed by synthetic outliers.**
    - With them, it gives limits well above every non-stress case.
    - Without them, it gives limits that the stress documents, which are valid v2 inputs, would exceed.
- **No limit chosen from this corpus is defensible** as a description of what real proposals need.

## 4. Interpretation and options for the owner

None of these is adopted.

1. **The rule is the wrong tool for this planner.**
    - Planner usage is a deterministic function of the document.
    - It is bounded by v2's own limits: 12 goals, 16 constraints, counts up to 20, 64 rows, 64 columns, and 16
      unique constraints and foreign keys per entity.
    - The observed adversarial peaks are lower bounds on that worst case.
2. **Option A, budget-neutral planning.**
    - Set both limits at or above a proven worst-case bound for valid v2 documents, so a planner budget can never
      bind and O6 stops affecting the estimand. For example, the field maxima: 1,000,000 and 10,000,000.
    - Planning cost here is milliseconds, so this costs little.
    - **It needs the worst-case bound proved for the frozen planner,** which hasn't been done. The 35,424 and 17,568
      peaks show the current defaults are below that bound.
3. **Option B, a representative corpus.**
    - Rerun the declared procedure as a new, separately declared corpus version.
    - The cases would be drawn from development-application proposals: protocol development applications only,
      separately approved, with any model request needing a cost ceiling.
    - **This is still sensitive** to what "representative" means, and it lets budgets decide outcomes.
4. **Option C, keep the defaults.** Not supported: they demonstrably bind on valid documents.

**Recommendation for review:** Option A, after a separate bounded-worst-case analysis of the frozen planner. Until
then, **O6 stays unresolved.**

## 5. What this calibration does not show

- **Anything about real application proposals or Muse output.**
- **Anything about O5,** Phase 9B's operation, candidate or rebuild limits, or its clock limits.
- **That the planner's outcomes are correct.** This measured budget use only. The planner's own tests cover
  correctness.

## 6. Tool location (added 2026-11-04, after the run)

**The two O6 tools were first written under `tools/`.** That is a path the Gate 2A frozen-files guard
(`tests/test_live_langgraph_adapter.py::TestNothingFrozenChanged`) forbids new files in. **That guard therefore
failed** from Step 6C until the move below. It wasn't run in Step 6C, and that step's report didn't mention it.

**The tools were moved byte for byte to `research_tools/`:**

| Tool | SHA-256, unchanged |
|---|---|
| `o6_offline_calibration.py` | `242ac620…` |
| `o6_budget_sensitivity.py` | `6b5e7b43…` |

**Effects of the move:**

- **Recorded results are unchanged.** Rerunning both tools from the new location reproduced `results_v1.json` and
  `sensitivity_v1.json` exactly, apart from wall time and platform text. The reruns were written to scratch, not over
  the records.
- **The declarations are unchanged.** `DECLARATION.md` and `SENSITIVITY_DECLARATION.md` still name the old
  `tools/` paths. Their hashes are recorded in the results, so their text isn't edited.
