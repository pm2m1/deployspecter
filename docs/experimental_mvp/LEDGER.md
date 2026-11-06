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

## Increment 4 (2026-11-05): third-party runtimes, corpus and protocol package

20. **PGQueuer 1.6.0.** `thirdparty/pgqueuer_adapter.py` adds a Driver-port adapter. `results/third_party_pgqueuer_lifecycle_1` records the lifecycle, and `…_public_factory_refuted_1` the negative control.
21. **Chancy 0.26.0.** `thirdparty/chancy_adapter.py` adds a one-connection stepped pool. `results/third_party_chancy_runtime_1` records the job runtime, and `…_notifications_refuted_1` the negative control.
22. **Async engine additions.** Raw cursors, recorded notifications, and `Handler.serialize_tasks`. The server-log check now compares arrays and `bytea`.
23. **Isolated runner.** It gains the `pgqueuer` and `chancy-runtime` modes and their negative controls, hashed requirements (`requirements.pgqueuer.txt`) and a pinned sdist fetcher.
24. **Tests and demo.** The optional suite `tests/thirdparty_isolated` covers the four verdicts, a tampered-sdist refusal and teardown after an interruption. `mvp_demo.sh` gains the `thirdparty` and `full` modes.
25. **Isolation.** `results/isolation_runtime_scan_1` records that hostile-grade isolation is NOT COMPLETE: only runc, and no VM manager.
26. **Corpus.** `CORPUS_MANIFEST.json` adds five new blind-eligible candidates (dbos, weblate, mlflow, saq, pgmq), pinned, with no defect labels and the rulings still needed. The frozen rules are unchanged.
27. **Protocol.** `protocol/AMENDMENT_003_DRAFT.md` (DRAFT, not adopted, outside the protocol directory) and `protocol/REVIEW_ISSUES.md`.
28. **Muse.** `results/pgqueuer_compare_1`: Muse matched the deterministic sources and did not improve on them.

### Meta usage in this increment (estimates at Meta's published rates, not billing data)

| Run | Requests | Estimated USD |
|---|---|---|
| `pgqueuer_compare_1` | 1 completed | 0.010854 |
| **Increment total** | 1 sent, 0 failed | **0.010854** (cap $10) |

Account balance was not checked through the API. The Responses API returns per-request usage, which is what is recorded here.

## Increment 5 (2026-11-05): engineering completion

29. **Typed parameters.** Date/time, interval, UUID and json/jsonb values are now reconciled exactly. PGQueuer: 72 compared, 0 mismatched, 0 unchecked. Chancy: 79/0/0.
30. **Django 5.2.** A trusted backend (`experimental/django_backend`) and the ledger fixture (`apps/django_ledger`, `apps/django_fixture.py`), in both binding modes. Django was added to the `experimental` extra.
31. **Chancy concurrency.** Workers as concurrent actors. Engine fix: a task cancelled at the gate no longer leaves its actor pending (regression test shown to fail without the fix).
32. **Corpus selection.** `research_tools/corpus_select.py` and `results/corpus_selection_1`, with `protocol/PROPOSED_SELECTION_RULE_AMENDMENTS.md` (not applied).
33. **Protocol.** `protocol/AMENDMENT_003_DRAFT_v2.md` and `protocol/ADOPTION_READINESS.md`. NOT READY: 5 blocking decisions.
34. **Isolation.** `results/isolation_runtime_scan_2`: hostile-grade isolation BLOCKED.
35. **Capability matrix.** `CAPABILITY_MATRIX.md`.

**Meta usage in this increment:** none (0 requests, $0). No new pathway had an independent outcome on which a Muse comparison would mean anything.

## Increment 6 (2026-11-05): from engineering MVP to a real-application pilot

36. **Mixed-version pilot (NONBLIND).** Chancy 0.25.1 → 0.26.0 runs as a four-phase rolling deploy in the isolated runner (`chancy-mixed`).
    - The migration area (v8) was selected by the frozen rules.
    - 0.25.1 is pinned by its source hashes.
    - The server log now matches a str that the server coerced to uuid by canonical or 32-hex form; anything else is UNVERIFIED.
    - Evidence: `results/real_app_pilot_chancy_mixed_1`. All phases corroborated; oracle M1–M4 holds; **no defect**.
37. **Mutation control.** Declared mutants (`--mutant`) are applied to the isolated build copy only. `results/mutation_control_chancy_1`: the original holds, and the `no_skip_locked_claim` mutant violates K1/K2. It is a control, not a defect, and not Muse evidence.
38. **Muse pilot (NONBLIND).** `results/real_app_pilot_chancy_compare_1`, comparing curated, systematic and Muse workloads at equal budgets. No source found a violation, and Muse showed **no advantage**.
39. **Protocol.** `protocol/DECISIONS_003.md` records proposed rulings for issues #6–#9 and #14, all **PROPOSED**: none accepted, implemented or verified. Confirmatory research stays blocked.
40. **Independent-evaluation handoff.** `handoff/` holds the curator, oracle-reviewer and runner packages, the access-separation procedure and templates, with `research_tools/seal_labels.py` for sealing and leak checks. No reviewer has been engaged.
41. **Engine fix.** `AsyncRun.quiesced` makes framework stops safe; it was exposed by the failed `demo_full_3`. It has a regression test, and the pilot was reproduced in 5 of 5 runs.
42. **Release candidate rc1.** `RELEASE_CANDIDATE.md` and `.json`, `research_tools/rc_manifest.py`, and the acceptance run `results/demo_full_4` (exit 0).

### Meta usage in this increment (estimates at Meta's published rates, not billing data)

| Run | Requests | Estimated USD |
|---|---|---|
| `real_app_pilot_chancy_compare_1` | 1 completed (408 input / 2177 output tokens) | 0.009762 |
| **Increment total** | 1 sent, 0 failed | **0.009762** (cap $10) |

## Increment 7 (2026-11-05): actual simultaneous mixed-version execution

43. **`ds-coord/1`** (`coordinated.py`): one statement scheduler across processes.
    - Global grant numbering, and lockstep across processes.
    - Cross-process lock attribution, through a global pid map.
    - Window gates and markers; stale-grant declines.
    - Fail-closed on lost peers, timeouts, unexpected messages and stalls.
    - Tests: 13 offline, 5 live.
44. **Simultaneous runner** (`research_tools/isolated/run_coordinated.py`): three restricted containers (coordinator, 0.25.1, 0.26.0) on one internal network, nine scenarios, explore and repeat modes, refusal reasons, and the `simultaneous` demo mode.
45. **Frozen properties P1–P8** (`simultaneous/PROPERTIES.md`, `chancy_properties.py`), frozen at `03967c4` before any run, with one recorded amendment to the read query.
46. **Engine fixes this increment exposed.** Each has a regression test that fails without it.
    - Corroboration orders each session by completion.
    - A cancellation is deferred to the statement boundary.
    - A COMMIT cancelled before its grant rolls back.
    - Client-side driver refusals are classified as such.
    - The `quiesced` hold no longer catches COMMITs, and is no longer used for stops.
47. **Results** (`simultaneous/RESULTS.md`): the final sets, 36 of 36 SOUND.
    - The mutation control is distinguished: P2 in 2 of 10 runs, against 0 of 10 for the released code.
    - The cancel cells violate P7 under an undocumented deployment; this is not claimed as a defect.
    - 17 earlier refusals are all kept and explained.

**Meta usage in this increment:** none (0 requests, $0). A Muse question is recorded as a proposed future experiment.

## Increment 8 (2026-11-05): historical-defect research

48. **Engineering milestone record** `MILESTONE_simultaneous_1.md`. rc1 is untouched.
49. **Amendment 003 review package v2** (`protocol/DECISIONS_003_v2.md`): recommended rulings, roles, qualifications and the evidence for each status, for #6–#9 and #14. All are PROPOSED, and no role is filled. Also recommendations for the other 13 issues, and two new issues (#19 compatibility contract, #20 training-data exposure).
50. **Review-ready draft v3** (`protocol/AMENDMENT_003_DRAFT_v3.md`) with 10 `[PENDING]` markers. Not adopted.
51. **Candidate record** (`historical/CANDIDATES.md`): BLIND_ELIGIBLE none; NONBLIND_DEVELOPMENT chancy, procrastinate, pgqueuer; INELIGIBLE dbos, saq, pgmq (NONE_BY_RULE) and the held-out applications. weblate and mlflow are NEEDS_PROTOCOL_RULING.
52. **Historical case `chancy-2895c4a`** (`historical/chancy_2895c4a/`): frozen before any run. Commit-pinned trees of the fix and its parent, a single-version case runner, and 34/34 sound runs. The buggy parent and 0.25.1 violate the maintainer-sourced property; the fix commit and 0.26.0 hold it, under identical forced schedules. Seeded exploration: 9/10 vs 0/10.
53. **Prospective Muse plan** (`protocol/PROSPECTIVE_MUSE_PLAN.md`): predeclared, not executed, and not ready for confirmatory execution.

**Meta usage in this increment:** none (0 requests, $0).

## Increment 9 (2026-11-06): causal validation and a supported rolling upgrade

54. **Audit of the 34-run chancy-2895c4a record** (`research_tools/audit_historical.py`): 0 problems. Hashes, versions, traces and recomputed verdicts all agree.
55. **Code and schema ablations of the fix** (`historical/chancy_2895c4a/ablation/RESULTS.md`): 36/36 sound, with the independent SQL check agreeing.
    - Removing the claim assignment, the completion fence or the cancel's claim invalidation each restores the overwrite (3/3).
    - The schema change alone (the parent's code on v8) doesn't remove it.
    - Result: each part is necessary within `2895c4a`. Sufficiency of a minimal patch wasn't tested.
56. **procrastinate 3.3.0 → 3.4.0**, an exploratory development case (`rolling_upgrade/procrastinate_3.3_3.4/`).
    - The pair was chosen by a mechanical rule, and the blue-green contract read at the 3.4.0 tag. It was frozen before any run.
    - Built with an adapter on the public `open_async(pool)` seam and dependencies pinned as of the release date, then run through six contract phases, old and new code simultaneous in the overlap.
    - 5/5 runs sound, with **no application violation**. Two oracle errors in harness assumptions (E1, E2) are recorded in `ERRATA.md`.
57. **Muse vs deterministic on the overlap** (`compare_1`): K = 2 per source, all sound, no violation in any source. Muse 4/4 valid at $0.016684. No advantage claimed.
58. **Ready-to-send curator and oracle-review packages** (`handoff/send/`), not sent. Amendment 003 addendum (#21). All decisions still PROPOSED; confirmatory evaluation BLOCKED.

**Meta usage in this increment:** 1 request, an estimated $0.016684 (cap $10).

## Increment 10 (2026-11-06): research freeze and independent-evaluation preparation

59. **Typed-parameter audit.** procrastinate's 20 unchecked values per run were record arrays (15) and float8 (5).
    - Their logged forms were probed live on PostgreSQL 16 and captured verbatim (`tests/experimental/fixtures/pg16_typed_params.json`).
    - They are now compared exactly: record arrays by `record_in` rules, float8 bit-exact. 26 adversarial tests.
    - Fresh runs (`rolling_upgrade/…/runs_typed_verification`): 0 unchecked, 0 mismatched. The originals are unchanged.
60. **Amendment 003.** Complete decision register v3 (all PROPOSED, with #22 new) and an exact adoption candidate (sha256 `ad5ea331…`). Unsigned approval forms and the adoption checklist. Nothing is accepted or registered.
61. **Sealed corpus intake** (`research_tools/corpus_intake.py`): opaque R1/R2 revision pairs, sealed labels, hash-chained split registries with a freeze, and immutable run ids. 13 offline tests on synthetic cases. No real case has been submitted.
62. **Concise v2 curator and oracle-reviewer packages** with acceptance checklists (not sent).
63. **Research-paper outline**, with evidence classes ([IND] vs [DEV]).

**Meta usage in this increment:** none ($0).

## Increment 11 (2026-11-06): final reliability gate and evaluation handoff

64. **Reliability gate: UNRESOLVED INTERMITTENT FAILURE.** 0 failures in 58 evidence-complete invocations:
    - `stress_1`: 8 whole-suite attempts, 104/104 tests;
    - `stress_2_targeted`: 50/50.

    The earlier single failure stays unidentified, because its output was truncated and its tmp_path was gone. Nothing was changed for it (`reliability/RESULTS.md`).
65. **Sealed-workflow rehearsal** (`research_tools/sealed_evaluation.py`; 15 tests, synthetic only).
    - **The full path:** submission → seal → freeze → evaluator package → blinded results → bound reveal authorization → scoring → verification.
    - **Found and closed:** `git archive` writes the commit id into a pax header, and commit-time mtimes order the revisions. Archives are now normalized, and unnormalized ones are refused.
    - **Documented limit:** diffing R1 against R2 shows the fix location.
66. **Amendment 003 integrity verifier** (`research_tools/verify_amendment.py`; 4 tests).
    - The candidate is `ad5ea331…`, and all 4 forms name it.
    - Unadopted and unsigned; 24 decisions unresolved.
    - A silent edit, a mismatched form or an adoption before signatures is detected.
67. **External one-page overview** (`handoff/send/OVERVIEW.md`) and a blank participation and conflict-of-interest declaration (not sent).

**Meta usage in this increment:** none ($0).

## Increment 12 (2026-11-06): parallel completion sprint (four worktrees, one serialized disposable PostgreSQL)

68. **State construction (A).** COMPLETE WITH SUPPORTED-SCOPE LIMITATIONS (`state_construction/README.md`).
    - **Reservations pipeline:** WorkloadSpec v2 → goals → Phase 9B constructor → exact typed writes → independent re-read → stepped handlers → corroboration → oracle.
    - **Chancy narrow path:** Chancy's unmodified worker consumed the constructed state.
    - **Fixtures:** 17/17.
    - **Constructor source:** copied byte-identically and sha256-pinned from uncommitted files in `deployspecter-trigger-aware-dev`. No private terms were copied.
    - **BLOCKED:** json/jsonb and NaN in Phase 9B; WorkloadSpec v2 registries for third-party apps; live-catalog mode in the shared database; procrastinate.
69. **Muse contribution, B1–B5 (B).**
    - Frozen manifest `234a12ff…`.
    - 13 Meta calls, estimated $0.270384.
    - **Results:** Q1 matched or underperformed; Q2 favourable but not established (paired sign test p = 0.25 at a common budget); Q3 matched; Q4 not answerable.
    - A1 is a post-outcome analysis amendment; the frozen analysis stays primary.
70. **Invariants (C).** ds-invariant/1, an eight-check validator. No genuine new property. Chancy mutants: 4/8 killed by the deterministic candidates and 1/8 by Muse's; 2 Muse candidates were invalid. Procrastinate mutants are BLOCKED.
71. **Paired evaluation (D).** Censoring-aware analysis with a budget-mismatch refusal; every comparison is INSUFFICIENT SAMPLE. The onboarding framework is implemented; the study was NOT RUN.
72. **Digital twin (E).** Seven phases, 4 runs. Seeded_2's exit 1 was a classifier bug in the twin, preserved and reclassified. Upgraded schema = fresh 3.4.0 install. Backfill is NOT SUPPORTED: there is none in this pair.
73. **Reliability (F).**
    - Teardown-under-SIGINT defect demonstrated and fixed. The regression fails on the unfixed runner (independently confirmed).
    - The original failure stays UNRESOLVED.
    - Hostile-grade isolation is BLOCKED ON ENVIRONMENT.
74. **Readiness (G).** `OWNER_DECISIONS.md` covers all 24 decisions. `OWNER_APPROVAL.md` has no lines for #6–#8 that the owner co-decides; this needs the owner's authorization. v3 bundles have checksums, not sent. Evaluator-access tests pass.
75. **Integrated acceptance** (`reliability/acceptance_1/`):
    - core 549 passed;
    - isolated 13 passed;
    - root 1746 passed, with 322 failing only because `DATABASE_URL` is unset;
    - Ruff clean.

**Meta usage in this increment:** 13 calls, $0.270384 estimated (ceiling $15).
