# Research-paper outline (draft, 2026-11-06): testing mixed-version database application upgrades by driver-level stepping

**This is an outline of work in progress, not a completed study.** No confirmatory evaluation has run: Amendment 003 is unadopted, and no independent curator, oracle author or reviewer exists (`protocol/DECISIONS_003_v3.md`, `approval/ADOPTION_CHECKLIST.md`). The paper must not claim a novel defect discovery, Muse superiority, or confirmatory results.

**Evidence classes**, used on every claim below:
- **[IND]:** independently established. It comes from the application maintainers' own artefacts (fix commits, regression tests, documentation), or from the instrument (PostgreSQL's server log), and was not authored by this project.
- **[DEV]:** developer-written. Harness, oracles, workloads and selections by this project's developer: NONBLIND and exploratory.
- **[DEV-AUDIT]:** developer evidence that has been re-checked mechanically against its own saved record.

## 1. Problem

Online upgrades of PostgreSQL-backed applications run old and new application code, and old and new schema states, at the same time. A defect may appear only under a particular interleaving of the two versions' database statements.

- **Question.** Can a tool execute *real* application code of two versions against one database, control the interleaving of their statements, show that what it observed is what the database executed, and check application properties on the result?
- **Scope.** Python applications on psycopg 3 and PostgreSQL 16, in a restricted container, not hostile-grade isolation.

## 2. Supported mixed-version semantics (a contribution: contract-first testing)

A mixed-version result means something only relative to the application's **documented** upgrade contract (ruling #19, proposed).

| System | Documented contract | Evidence |
|---|---|---|
| Chancy 0.26.0 | single-version: "Stop your workers" before migrating; the fix commit says "Stop old workers, run migrations, then start updated workers" | [IND] `CHANGELOG.md` at `73fd582c`, `2895c4a` |
| procrastinate 3.4.0 | blue-green: apply `pre`, deploy the new version, apply `post`, stopping "at every version that provides migrations" | [IND] `docs/howto/production/migrations.md` at tag `3.4.0` (`41aec8f7`) |

**Lesson, with an exploratory result.** The simultaneous Chancy 0.25.1 and 0.26.0 runs lost cancellations in three of four cells, but that deployment is outside the documented contract. It is reported as a hazard, not a defect [DEV] (`simultaneous/RESULTS.md`).

## 3. Driver-level application stepping

- **Gates.** Statement-level gates on psycopg 3 connections, in sync and async engines, with a deterministic controller. The application code runs unmodified, through public seams: Chancy's pool, procrastinate's `open_async(pool=…)`, Django and SQLAlchemy [DEV].
- **Across processes.** `ds-coord/1` grants every statement of several processes in one global order, with lockstep decisions and cross-process lock-wait attribution, and fails closed [DEV] (`simultaneous/DESIGN.md`).
- **Safe cancellation.** It is deferred to statement boundaries, after a held-COMMIT and a drain-stall failure, each found by the server log and each fixed with a regression test [DEV-AUDIT] (`results/simultaneous_scenarios_1`, `simultaneous_diagnostics_1`).
- **What isn't controlled.** Intra-process task timing, and interleavings inside one SQL statement (for example, procrastinate's historical `fetch_job` race, `historical/CANDIDATES.md`).

## 4. PostgreSQL corroboration (the instrument)

- **What it checks.** Each run's driver trace is reconciled with PostgreSQL's csvlog: every session, statement, error, the global grant order, and typed parameters by exact value. Parameters arrive server-rendered, and are compared without loosening.
  - **Exact:** int, bool, NULL, string digest, bytea, 1-D arrays, json/jsonb by value, date/time types, interval, uuid, record arrays by `record_in` rules, and float8 bit-exact.
  - **UNVERIFIED and counted, never accepted:** everything else.
- **Typed-parameter audit.** It removed procrastinate's 20 unchecked values per run, with 0 mismatched on a fresh run (`rolling_upgrade/…/runs_typed_verification`) [DEV-AUDIT], and has 83 offline vectors, including verbatim PG16 captures (`tests/experimental/fixtures/pg16_typed_params.json`).
- **What a refusal means.** A refused run is never retried in place. All 17 refusals in the simultaneous work are kept and explained [DEV-AUDIT].

## 5. Typed initial-state construction

The unmodified WorkloadSpec v2 → planner → catalog verifier → Phase 9B bridge chain supplies typed, verified initial states. The live catalog export refuses drift, unknown triggers and CHECK constraints [DEV] (`README.md`, "Live catalog verification").

## 6. Historical Chancy reproduction (a known defect, reproduced; not a discovery)

- **The defect.** It is documented by its maintainers, which is what makes it [IND]:
  - fix commit `2895c4a` (execution claims), with the v8 migration;
  - its regression test `test_cancellation_fences_execution_completion`;
  - the 0.26.0 changelog: "Fixed cancellation races that could overwrite cancelled results".
- **The reproduction.** It used commit-pinned trees of the parent `80f570f` and of `2895c4a`, each on its own schema, under the maintainers' test order:
  - forced schedule: the parent violated 3/3, the fix held 3/3, with identical statement positions;
  - seeded exploration: 9/10 against 0/10;
  - 34/34 sound; audit 0 problems [DEV-AUDIT] (`historical/chancy_2895c4a/RESULTS.md`, `ablation/audit_of_34_runs.json`).
- **Ablations.** Each of claim assignment, the completion fence and cancel claim-invalidation is necessary within the fix, and the schema change alone is not sufficient. 36/36 sound, with an independent SQL check agreeing [DEV] (`ablation/RESULTS.md`).
- **Caveats.** The case was chosen after reading the fix. The oracle is maintainer-sourced but developer-encoded. Notifications were disabled, which is argued from source, not executed.

## 7. procrastinate rolling upgrade (a negative result)

- **The run.** 3.3.0 → 3.4.0, with the pair chosen by a mechanical rule. The six contract phases each passed a host-checked precondition, and in the overlap old and new producers, workers and the 3.4.0 admin retry ran at the same time.
- **Results.** 5/5 runs sound; 2 more after the typed audit with 0 unchecked. **No application violation.** Two oracle errors were found in the developer's own assumptions, E1 (the `attempts` semantics) and E2 (a non-atomic read). They are recorded as errata, without editing the frozen case [DEV] (`rolling_upgrade/procrastinate_3.3_3.4/RESULTS.md`, `ERRATA.md`).
- **Status.** Exploratory: procrastinate is a frozen-protocol development application, and its migration area needs ruling #21.

## 8. Muse and deterministic baselines (exploratory pilots; no superiority claimed)

| Pilot | Budget | Outcome |
|---|---|---|
| Chancy mixed (earlier) | 2 workloads per source | no source found a violation; Muse no better [DEV] |
| procrastinate overlap (`compare_1`) | K = 2 per source | all sound, no violation in any source; Muse 4/4 valid at an estimated $0.016684; coverage comparable to curated [DEV] |

Neither pilot can address detection (Q3), because no defect was present in the tested pairs. The predeclared prospective design is in `protocol/PROSPECTIVE_MUSE_PLAN.md`.

## 9. Threats to validity

1. **Independence:** everything except the maintainers' artefacts and the server log is developer-written, and the cases were chosen with knowledge.
2. **Training-data exposure:** the developer and Muse are language models trained on public code (#20).
3. **Scheduling:** statement-level control only; no global determinism (traces repeat in 2/3 trials, outcomes in 3/3).
4. **Fidelity:** LISTEN/NOTIFY disabled; adapters per application; restricted runc only.
5. **Instrument:** server-side rendering is assumed for some types (verified on PG16 only). Unknown representations are counted, not accepted.
6. **Scale:** two applications, one historical defect, small budgets.
7. **Oracles:** two errata in developer oracles show the need for independent authorship (#7).

## 10. Related work (references to verify before submission)

**Verified in this session (bibliographic details checked):**
- Zhang et al., "Understanding and Detecting Software Upgrade Failures in Distributed Systems", SOSP 2021, doi:10.1145/3477132.3483577. 123 upgrade failures; DUPTester and DUPChecker.
- Rae et al., "Online, Asynchronous Schema Change in F1", PVLDB 6(11), 2013, doi:10.14778/2536222.2536229. Safe multi-step schema change while servers run different schema versions.
- Kingsbury and Alvaro, "Elle: Inferring Isolation Anomalies from Experimental Observations", PVLDB 14(3), 2020. Checking database behaviour from observed histories.

**Not re-verified in this session:**
- Musuvathi et al., "Finding and Reproducing Heisenbugs in Concurrent Programs" (CHESS), OSDI 2008: systematic concurrency testing.
- Burckhardt et al., "A Randomized Scheduler with Probabilistic Guarantees of Finding Bugs" (PCT), ASPLOS 2010.
- Curino et al., "Graceful Database Schema Evolution: the PRISM Workbench", VLDB 2008.
- Ambler and Sadalage, *Refactoring Databases*, 2006: expand/contract.

**Positioning.** Upgrade-failure studies and testers target distributed storage systems and messages. This work targets application-level mixed-version database interactions, with statement-level control and server-log corroboration, under an application's documented contract.

## 11. Future independent evaluation

The path, all of it **not done**:
1. the owner signs; curator, oracle author and triage reviewers are named (`approval/`);
2. cases are sealed through `research_tools/corpus_intake.py`: opaque R1/R2 revisions, hash-chained split registries, immutable run ids;
3. oracles are written from documentation only, and validated on the fixed revision by the curator;
4. the registration is timestamped before the first run;
5. the confirmatory comparison is run per `PROSPECTIVE_MUSE_PLAN.md`.

RA-Q3 stays descriptive unless there are at least 10 blind cases.
