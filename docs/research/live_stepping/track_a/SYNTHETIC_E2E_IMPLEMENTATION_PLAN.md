# Track A: synthetic end-to-end demonstration plan

- **Status:** nonnormative research plan, committed as documentation.
    - Offline wiring is implemented and tested.
    - **No PostgreSQL has run.**
    - Every database-backed step below is **WAITING_FOR_EXECUTION_APPROVAL.**
- **No condition in section 6 is claimed to pass.**
- **This is a synthetic demonstration.** It is never an evaluation arm, never held-out evidence, and never enters a
  primary analysis.

## 1. The scenario

| Property | Choice |
|---|---|
| Application | Gate 2A's existing synthetic `tests/live_app/reservations.py` (unchanged), already run under supervision by `tests/test_live_supervised.py` |
| Defect | Lost update. `reserve_seats` reads `reserved`, computes in Python and writes back. Without `FOR UPDATE`, two concurrent reservations of 2 seats each on a capacity-3 event can both succeed |
| Buggy and fixed versions | `lock_row=False` and `lock_row=True`: a harness-owned code switch injected after validation. It isn't a registered argument, so no workload can choose its version (`VARIANTS` in `research/track_a/scenario.py`) |
| Registered operations | `reserve_seats(event_id, holder, qty)` and `release_seats(holder)`, both `live_app.reservations:*` |
| Workload (v2) | Two actors, each `reserve_seats(event_id=1, holder=alice/bob, qty=2)` |
| Initial-state goal | `event_one`: `events` with `id = 1`, `capacity = 3`, `reserved = 0`, count 1 |
| Schema | The existing `SETUP_SQL`: `events`, `reservations` and `rejections`. Its SHA-256 `3bb29d21…` is checked before any scenario is built |
| Independent oracle | `tests/live_app/properties.py`, unchanged. It imports nothing from the tool or the application. **R1:** `reserved` equals the reserved seats. **R2:** reserved seats ≤ capacity. **R3:** results agree with rows. **R4:** serializable |
| Expected | **Buggy:** some interleaving (both read before either writes) violates R1 and R2. **Fixed:** no interleaving violates them; the second reader blocks on `FOR UPDATE` and is rejected |

No GitLab, external or held-out application is involved.

## 2. The pipeline, stage by stage

| Stage | Component | Status |
|---|---|---|
| WorkloadSpec v2 | `workload_spec.parse_workload` (frozen) | **Runs offline**, accepted |
| Trusted registry | `registry.build_registry` and `authorize` (frozen) | **Runs offline**, accepted |
| Argument validation | `argument_validation.validate_arguments` and `check_validated` (frozen) | **Runs offline**, accepted and rechecked |
| Plan identity | `workload_identity.validated_plan_identity` (frozen) | **Runs offline**, `vplan1:048a3a0c…` |
| State planning | `state_planning.plan_state` | **Runs offline**: one candidate row, 1 expansion and 4 constraint checks |
| Literal translation | `literal_translation.translate_literally` and `compare_with_planner` | **Runs offline**: both accept, same rows. So the demonstration is arm C's construction rule |
| Catalog verification | `catalog_verification.verify_context` | **Runs offline: MATCH against a hand-written dry-run snapshot only.** This is not evidence. A pinned snapshot needs operation OP-2 |
| Typed bridge | `phase9b_bridge.bridge_to_phase9b` | **Runs offline**: bridged, 1 equivalent row, `execution_authorized: False` |
| Construction (**documented alternative to Phase 9B**) | The bridge's legacy projection becomes the release `SeedRow` list, which Gate 2A's own `_bounded_reset` inserts in one strict insertion, in bridge order, with no reconciliation and no search. **Phase 9B isn't imported** | Wired offline (`pipeline.live_scenario`). **Executes only in OP-3 to OP-5** |
| Initial-state check | Gate 2A's parent reads `trace.initial_state` after each reset (`supervisor.py:654`, unchanged). `pipeline.check_initial_state` compares it exactly, by type and count. Every run's initial state must have the same digest, which is the repeat check | Pure comparator **implemented and tested**. The read happens in OP-3 to OP-5 |
| Independent state check | A second, parent-side read on a separate connection with typed predicates and a server-side digest (common standard, step 5) | **Designed, not implemented:** OP-6 |
| Live stepping | `SupervisedRunner` with `explore_live`, both unchanged | **Executes only in OP-3 and OP-4** |
| Independent oracle | `properties.check_invariants` and `check_results` on the parent-read final state | Exists. Runs in OP-3 to OP-5 |
| Semantic replay | `SupervisedRunner.run` with `ExplicitSchedule(strict=True)` (unchanged), classified by `research/track_a/replay_policy.py` (amendment 002 as pure code, a candidate needing review) | Policy **implemented and tested**. Attempts are OP-5 |
| Acceptance | `research/track_a/acceptance.py` | **Implemented and tested.** Today it judges conditions 2 to 7 and 9 as `not_run` |

## 3. Missing connections, and the minimal changes made

**Code added,** all new research files. No frozen file was edited:

| File | Purpose |
|---|---|
| `src/deployspecter/research/track_a/scenario.py` | Registry, contracts, context, workload, variants, and the dry-run catalog fixture |
| `src/deployspecter/research/track_a/pipeline.py` | The offline chain, inert `LiveScenario` building, and the exact initial-state check |
| `src/deployspecter/research/track_a/replay_policy.py` | Amendment 002's classes, stopping rule and a candidate server-choice signature |
| `src/deployspecter/research/track_a/acceptance.py` | The nine-condition acceptance judge, which fails closed |
| `tests/research_tracks/test_track_a_offline.py` | Offline tests (section 7) |

**Still missing,** all needing approval:

1. **OP-2, the catalog export.** **Proposed simplification for Track A,** to review:
    - **A parent-only exporter.** The parent already holds admin credentials, and the exporter is harness code, not
      application code. Running the fixed catalog queries in the parent, in one `REPEATABLE READ READ ONLY`
      transaction, avoids the child read-only role (K-a) and the unverifiable child return value (residual 5) entirely.
    - **What it still needs:** a new module, reviewed fixed statements, and deliberate-breakage tests.
2. **OP-6, the independent state check:** new parent-side code with fixed statements.
3. **A driver script** for OP-3 to OP-6 that assembles the evidence bundle and calls `acceptance.judge`.

## 4. Prepared database operations: WAITING_FOR_EXECUTION_APPROVAL

| # | Operation | Exact content | Credentials | Cleanup | Acceptance checks |
|---|---|---|---|---|---|
| OP-1 | Start the disposable cluster | The `docker run … postgres:16 -c cluster_name=ds_live_disposable …` command in `GATE2A_REPORT.md`, verbatim. Image digest `sha256:1beaa308…` | A random admin password in the git-ignored `.env`. `MODEL_API_KEY` empty | `docker stop`, with tmpfs data discarded | Gate 2A preflight passes. On any other cluster, everything refuses |
| OP-2 | Catalog export of `ds_run` after a bounded reset with `SETUP_SQL` | Parent-only, fixed statements (gate review §5 query set, schema constant `ds_run`), exported twice | Parent admin only | Reset `ds_run` | Two exports agree. `verify_context` gives **MATCH** with the exported snapshot, pinned by a reviewer. The offline chain is rerun with that pin |
| OP-3 | Buggy exploration | `explore_live(SupervisedRunner(...), live_scenario(chain, variant="buggy"), LiveBudget(max_runs=200, max_seconds=120), check=oracle)` | Gate 2A's per-run role | Gate 2A teardown per run | Every trace supervised and scoreable. `check_initial_state` is empty for every run, with every initial-state digest identical. The candidate is the first run violating R1 or R2, in discovery order |
| OP-4 | Fixed exploration | The same, with `variant="fixed"` and the same workload digest | As OP-3 | As OP-3 | Exhausted, `complete_schedule_coverage` true, no R1 or R2 violation, no inconclusive run |
| OP-5 | Confirmation | One at a time: `SupervisedRunner.run(buggy, ExplicitSchedule(candidate.chosen_schedule, strict=True))`, stopping under `replay_policy.confirm` (3 comparable, or 125) | As OP-3 | As OP-3 | Result `reproduced`, with every comparable attempt's oracle on the parent-read and the independent read (OP-6) |
| OP-6 | Independent state check | Parent-side, on a separate admin connection. For each requested row: `SELECT count(*) FROM ds_run.events WHERE id IS NOT DISTINCT FROM $1::int4 AND capacity IS NOT DISTINCT FROM $2::int4 AND reserved IS NOT DISTINCT FROM $3::int4`. For each table: `SELECT count(*)`, and `SELECT md5(string_agg(t::text, E'\n' ORDER BY t::text)) FROM ds_run.<table> t` | Parent admin | None, because it is read-only | Counts exactly as expected. Digests equal across all runs' initial states |
| OP-7 | Final teardown | Gate 2A's stale-role sweep and survivor scan, then OP-1's stop | — | — | No `ds_run_` role left. No session left |

**Statements expected from the application** (reconciled by Gate 2A, unchanged):

- `BEGIN`;
- `SELECT capacity, reserved FROM events WHERE id = $1`, with ` FOR UPDATE` on the fixed version;
- either `UPDATE events SET reserved = $1 WHERE id = $2` followed by `INSERT INTO reservations …`, or
  `INSERT INTO rejections …`;
- `COMMIT`.

Anything else is flagged, and the run isn't scoreable.

## 5. Supervision requirements

- **Gate 2A's accepted model only:** synchronous psycopg 3, registered entrypoints, one disposable PostgreSQL 16
  cluster, synthetic data.
- **The accepted deviations stand:** no full postmaster restart is tested, and an infrastructure failure stops the
  batch with no automated recreation.
- **No automatic retry.**
- **New mechanisms needing their own verification before their results count:**
    - the parent-only exporter (OP-2);
    - the independent check (OP-6);
    - the replay-policy signature.
- **No Gate 2B LangGraph path, and no Phase 9B.**
- **Not a sandbox for hostile code.** The application is the project's own synthetic code.

## 6. End-to-end acceptance (none claimed yet)

| # | Required evidence | Today |
|---|---|---|
| 1 | Genuinely validated proposal: stages 1 to 3, recheck, plan identity | `pass`, offline |
| 2 | Initial state matches the approved trusted schema: an exported snapshot, MATCH, two exports agreeing | `not_run`. The dry-run fixture is explicitly refused as evidence |
| 3 | Actual state constructed and independently checked: exact initial state each run, plus OP-6 | `not_run` |
| 4 | Supervised live stepping: every scored trace supervised and scoreable | `not_run` |
| 5 | Buggy version violates the oracle (R1 or R2) | `not_run` |
| 6 | Fixed version doesn't, on the same workload, exhausted, with no inconclusive run | `not_run` |
| 7 | Semantic replay `reproduced` under amendment 002, independently checked | `not_run` |
| 8 | Attributable traces and digests: chain digests, setup, application, oracle and harness source hashes, run ids | `fail` until the execution bundle records the source hashes and run ids |
| 9 | Fail-closed: no flags, opaque steps, refusals, unknown effects or missing evidence | `not_run` |

The verdict is `PASS` only if all nine pass.

## 7. Offline tests run

`uv run pytest -q -m "not live" tests/research_tracks/test_track_a_offline.py`, in the project environment, with
`MODEL_API_KEY` unset. All pass. The connection and socket constructors are replaced to refuse any connection. The
tests cover:

- the setup SQL digest;
- the variant switch not being a registered argument;
- forbidden imports (no oracle, scenarios, Phase 9B, LangGraph adapter or other track);
- every stage's outcome;
- digest stability;
- a changed snapshot giving `context_not_matched`;
- buggy and fixed scenarios sharing one workload;
- exact initial-state check failures: missing, extra, wrong-type `bool` or `float`, unrequested and unread tables;
- the oracle's independence;
- the replay policy's classes and the 125 limit;
- acceptance never passing on missing evidence or a dry-run snapshot.

## 8. Approvals needed to execute

1. OP-1: start the cluster.
2. Implementing OP-2's parent-only exporter, or the child-role exporter of the earlier gate review, and its
   deliberate-breakage tests.
3. Implementing OP-6's independent check, and its tests.
4. Implementing the driver script.
5. Running OP-2, then pinning the snapshot and context, which needs a reviewer.
6. Running OP-3 to OP-7.
7. Committing any evidence.

## Milestone 2 update

These are now implemented offline, with recording fake connections only. **None has touched PostgreSQL:**

- **`research/track_a/catalog_export.py`:**
    - the parent-side export, with fixed queries pinned by hash;
    - a strict assembler that rejects inconsistent or unknown catalog data;
    - an `information_schema` re-read and comparator;
    - `pin_from_evidence`, which needs two agreeing exports, an agreeing re-read, the right cluster, database and role,
      exact statements, and a reviewer attestation;
    - `pin_context`, which needs a MATCH.
- **`research/track_a/evidence_binding.py`:**
    - OP-6's independent check, with typed parameterised predicates;
    - exact initial-state verification across runs;
    - run records from traces;
    - evidence binding.
- **`research/track_a/acceptance.py`:** gates G0 to G9.

OP-1 to OP-7 are specified exactly in `EXECUTION_MANIFEST_V1.md` and `execution_manifest_v1.json`. **Status:
WAITING_FOR_EXECUTION_APPROVAL.**
