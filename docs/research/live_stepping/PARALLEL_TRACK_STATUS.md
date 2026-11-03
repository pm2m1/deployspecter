# Parallel Track A and Track B status

- **Status:** nonnormative status report, as of 2026-11-04 (first parallel milestone).
- **Branch:** `research/live-stepping` at `6fe3882`.
- **Unchanged and frozen:** WorkloadSpec v2, protocol v1.2 and amendments 001 and 002, Gate 2A, release evidence and
  Phase 9B.
- **Amendment 003:** revision 6, **DRAFT — NOT ADOPTED.**
- **Not done:**
    - no PostgreSQL, model, paid test, evaluation arm, Gate 2B, Phase 9B, held-out or confidential access;
    - no commit or push.
- **The ZIP the owner mentioned** wasn't seen in this session. This report covers the local worktree only.

## Layout: separate directories, one shared proposal

| Area | Code | Tests | Documents |
|---|---|---|---|
| Track A | `src/deployspecter/research/track_a/` | `tests/research_tracks/test_track_a_offline.py` | `docs/research/live_stepping/track_a/` |
| Track B | `src/deployspecter/research/track_b/`, plus `research_tools/o6_*.py` | `tests/research_tracks/test_track_b_arm_e.py` | `docs/research/live_stepping/track_b/`, `workload/o6_calibration/` |
| Shared (proposed) | `src/deployspecter/research/shared/` | `tests/research_tracks/test_shared_evidence_record.py` | `docs/research/live_stepping/shared/` |

**Isolation, enforced by tests:**

- neither track imports the other;
- neither imports the shared proposal yet;
- Track A imports no oracle, scenario, Phase 9B or LangGraph adapter;
- Track B imports no oracle, scenario, property or Phase 9B module.

## Track A: synthetic end-to-end demonstration

**Done, offline:**

- **Scenario:** Gate 2A's existing synthetic `reservations` lost update. Buggy and fixed versions are a harness-owned
  switch, never a workload argument.
- **The full chain runs:**
    - v2 parse, registry, argument validation, recheck, `vplan1` identity;
    - planner and literal translation, which agree;
    - catalog verification gives **MATCH against a dry-run fixture only**;
    - typed bridge;
    - inert buggy and fixed `LiveScenario`s sharing one workload.
- **Pure parts implemented:**
    - the exact, typed, count-exact initial-state check;
    - amendment 002's confirmation policy, as a candidate;
    - the nine-condition acceptance judge, which fails closed.
- **The demonstration uses arm C's construction rule.** Gate 2A's own reset inserts the bridged rows. **Phase 9B
  isn't used.**

**Blockers:**

- **OP-1 to OP-7 are WAITING_FOR_EXECUTION_APPROVAL:**
    - the cluster;
    - the catalog export, with a proposed parent-only exporter;
    - buggy and fixed exploration;
    - confirmation;
    - the independent state check;
    - teardown.
- **Two new mechanisms need implementing and independent verification:** the exporter and the independent check.
- **Acceptance today:**
    - condition 1 passes;
    - condition 8 fails until execution records its source hashes and run ids;
    - the rest are `not_run`.

## Track B: rigorous evaluation

**Done, offline:**

- **Arm E:**
    - E-1 to E-8 resolved as proposals, with comparator choices flagged;
    - an offline generator prototype: v1 gave 26 candidates, 24 valid and 2 rejected (kept), and 1 A test was listed
      as not expressible;
    - deterministic seeded orders;
    - round-robin allocation;
    - exact-state reuse for R;
    - planning use of at most 2 expansions and 10 checks.
- **O6:**
    - the original calibration and its declared failure are preserved;
    - a declared, post-hoc sensitivity grid has run;
    - **the bounded method is proposed:** fixed limits with recorded exhaustion, candidate values 65,536 and 32,768,
      needing confirmation on a new corpus.
- **Common verification standard, version 1:** exact counts (E1 and E2), no masking, and construction verification
  kept apart from the oracle. It is a prospective change for D and E.
- **Amendment 003 revision 6:**
    - exact estimands and denominators;
    - operational against infrastructure failures;
    - every blocker listed.

**Blockers:**

- **O5:** needs authorized execution.
- **O6:** needs the owner's choice of method, and a corpus v2 confirmation.
- **O10:** needs comparator approvals, the goal-policy format, and a frozen generator.
- **N4:** unestablished.
- **Phase 9B integration:** separate approval.
- **A paid-run monetary ceiling.**

## Shared dependencies, to be reviewed before either track depends on them

1. **The evidence record** (`evidence-record/0-proposed`): harness-only attribution, and no synthetic records in a
   primary analysis.
2. **The common verification standard, version 1:**
    - Track A uses its C rule through Gate 2A's reset and its parent-side initial-state read;
    - Track B uses it for every arm.
3. **The replay policy and server-choice signature** (amendment 002 as code): Track A's candidate, which an evaluation
   would freeze with the tool.
4. **The catalog export path:** Track A needs it for condition 2, and Track B for every application's `MATCH`.

## Next independently reviewable milestones

| # | Milestone | Track | Needs |
|---|---|---|---|
| 1 | Review the shared evidence record and the verification standard (count and masking semantics) | Shared | Owner review, no execution |
| 2 | Review the replay policy and signature against amendment 002 | Shared and A | Owner review |
| 3 | Implement the parent-only exporter and independent check, with deliberate-breakage tests, offline where possible | A | Approval to implement |
| 4 | Run OP-1 to OP-7 for the synthetic demonstration | A | Approval to execute |
| 5 | Declare and run O6 corpus v2 offline | B | Approval |
| 6 | Approve E's comparator choices and goal-policy format, then review the generator for freezing | B | Owner decisions |
| 7 | A worst-case planner bound (Option A), as an alternative to milestone 5 | B | Analysis only |
| 8 | Audit N4 (Phase 9B's held-out exposure) through someone with access | B | Owner action |

---

## Parallel milestone 2 (2026-11-04)

**Committed first:** `0700701` (Track A), `7dbd96e` (Track B and O6 v1 evidence), `3398c34` (shared).

**Corrected while committing:**

- **The two O6 tools had been placed in `tools/`,** which Gate 2A's frozen-files guard forbids. The guard had been
  failing since Step 6C without being reported.
- **They were moved byte for byte to `research_tools/`.** The recorded results reproduce from the new location, apart
  from wall time.

**Track A:**

- **Implemented offline, with fake connections only:**
    - the parent-side catalog export, with fixed hashed queries and a strict assembler;
    - the `information_schema` re-read;
    - pins from verified evidence only;
    - the independent typed state check;
    - exact initial-state verification;
    - evidence binding;
    - gates G0 to G9.
- **The first-run manifest** (`track_a/EXECUTION_MANIFEST_V1.md` and `.json`) is checked against the code by a test.
- **Status:** WAITING_FOR_EXECUTION_APPROVAL.
- **Blockers:**
    - a parent-session log reconciliation (new supervision authority);
    - the driver `research_tools/track_a_run.py`;
    - first contact of OP-2 and OP-6 SQL with PostgreSQL 16;
    - replay-signature review.

**Track B:**

- **Coverage audit:** default-policy exclusions come from `requires` predicates and single-instance entrypoints. Two
  default-off options remove them, and are [comparator] choices.
- **O6 corpus v2:** NOT CONFIRMED (one case needs 33,344 checks against a candidate of 32,768). The values stay
  unresolved.
- **Amendment 003:** revision 7, still DRAFT — NOT ADOPTED.

**Shared:** the evidence record is now `0.2-proposed`:

- `track` must agree with `context`;
- execution requires a verified catalog, bridge and state;
- every attempt is listed.

Neither track depends on it yet.
