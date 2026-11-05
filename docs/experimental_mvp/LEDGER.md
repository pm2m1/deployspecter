# EXPERIMENTAL real-application MVP: implementation ledger

**This is a prototype, not certified Gate A2 evidence.** It shares no code path with the frozen Gate 2A supervisor and has none of its isolation: handlers run in-process, as the same OS user, against a disposable local PostgreSQL only.

| | |
|---|---|
| Branch | `research/real-application-mvp`, in worktree `~/Desktop/deployspecter-real-app-mvp` |
| Starting commit | `ac6ff66a77afaf49a8f123d28c8619f37610f6be`: Gate A2.3, whose supervisor characterization ran once with 31 claims verified, 7 unobservable and 3 not tested |
| Untouched | frozen Gate 2A, Gate A1, WorkloadSpec v2, the Gate A2 demonstration guard (still blocked), all historical evidence |
| New code | `src/deployspecter/experimental/` only; tests in `tests/experimental/`; `research_tools/mvp_pg.sh` |
| Database | `deployspecter-mvp-pg` on `127.0.0.1:55441`: the pinned `postgres:16@sha256:1beaa308…` image, tmpfs, 1 GB, 2 CPUs, 256 PIDs, cluster `ds_mvp_disposable`. Never the protected 55432 |

## Scope of the prototype

1. `stepping.py`: real handlers on psycopg 3, one `SteppedConnection` per actor thread, a deterministic controller, lock-wait detection through `pg_blocking_pids`, bounded exploration, and provenance for every operation.
2. `apps/reservations_mixed.py`: a trusted mixed-version fixture with old and new handlers, an expand migration with a sync trigger, and a buggy and a fixed backfill.
3. `oracle.py`: independent invariants I1 to I4, plus a typed state digest.
4. `workloads.py`: the `ds-mvp-workload/1` schema, its deterministic validator, and two deterministic sources (curated, systematic).
5. `muse.py`: Muse proposals through the same validator.
6. `harness.py`: a verified initial state, one run, and exploration.

## Increment 2 (2026-11-05): integration and real-application compatibility

7. `corroborate.py`: reconciles each run's trace with PostgreSQL's csvlog, covering statements, bound values, sessions, errors and order. `harness.RunResult.bundle()` gives the machine-readable evidence.
8. `apps/ledger_orm.py`: a trusted SQLAlchemy 2.0.57 ORM fixture with buggy (no lock) and fixed (`with_for_update`) builds. Its engine binding uses only `creator` and a pool class.
9. `stepping.py` additions:
   - non-autocommit actor connections;
   - framework-aware call sites, recording the application line and the SQLAlchemy entry point;
   - `setup` grants for commuting framework operations;
   - exploration over schedule positions only;
   - wrapped refusals classified through the cause chain;
   - `dict_row` reads traced with their values.
10. `planning_adapter.py`: the unmodified WorkloadSpec v2 → planner → catalog verifier → Phase 9B bridge chain with explicit outcomes, typed rows, pinned trusted handlers and a verified initial state.
11. `muse_apps.py` and `research_tools/mvp_compare_apps.py`: Muse on the ledger and on the planner path, against curated and systematic sources.
12. `CORPUS_SHORTLIST.json` v2 pins chancy 0.26.0 and pgqueuer 1.6.0. Both are async and blocked.
13. `PROTOCOL_AMENDMENT_DRAFT.md` is still a draft and not adopted.

### Meta usage in this increment (estimates at Meta's published rates, not billing data)

| Run | Requests | Estimated USD |
|---|---|---|
| `apps_compare_1` (failed attempt) | 2 completed, 1 timed out at 300 s | 0.068449, plus the timed-out request: its tokens are unknown, at most about 0.07 if billed at the 16,000-token output cap |
| `apps_compare_2` | 4 completed | 0.148953 |
| **Increment total** | 7 sent | **0.217 known; at most about 0.29** (cap $10) |

## Increment 3 (2026-11-05): async execution, the release path, isolation, live catalog

14. **Release path.** The `release_seats` root cause was established live: a missing claim on the reservation, plus a transition that doesn't check the reservation was removed. It needs concurrency. The fix is a separately named `corrected` reference (`apps/reservations_corrected.py`), an R5 release-semantics check, a guard against initial states that already violate the oracle, and four mutants (`apps/reservations_mutants.py`). See `RELEASE_PATH.md` and `results/release_path_1`.
15. **Tracer fixes.**
    - Zero-row SELECTs no longer fail the rewind.
    - The run identity includes the planned state.
    - The harness's cleanup ROLLBACK is recorded and expected.
    - Ungranted exits during a halt are expected.
16. **Async.** `stepping_async.py` and `apps/reservations_mixed_async.py` add the async psycopg engine and fixture. `apps/ledger_orm_async.py` adds the SQLAlchemy asyncio ledger, and SQLAlchemy now comes with its `asyncio` extra.
17. **Isolated runner.** `research_tools/isolated/` holds the runner, its trusted smoke test (`results/isolated_smoke_1`, `_2`) and the chancy 0.26.0 migration capture (`results/third_party_chancy_capture_1`).
18. **Live catalog.** `catalog_export.py` and `planning_adapter.run_planned_live` add live catalog verification.
19. **Measurements.** `results/release_compare_1` covers the release path with mutants, and `results/async_compare_1` covers `reservations_mixed` on the async engine.

### Meta usage in this increment (estimates at Meta's published rates, not billing data)

| Run | Requests | Estimated USD |
|---|---|---|
| `release_compare_1` | 2 completed | 0.081551 |
| `async_compare_1` | 2 completed | 0.062097 |
| **Increment total** | 4 sent, 0 failed | **0.143648** (cap $10) |
