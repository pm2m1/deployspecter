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

---

## Parallel milestone 3

**Committed first:** `fb0b4fd`, `66017f3`, `e44f863`, `b2b123b`, covering milestone 2's offline work.

**Track A:**

- **New modules:**
    - `parent_reconciliation.py`: parent sessions reconciled from the server log only;
    - `research_tools/track_a_run.py`: the bounded driver, with no commands, public Gate 2A interfaces only, approvals
      per phase, a reviewer attestation, a batch ceiling, and new evidence directories;
    - a structural catalog digest;
    - independent final-state reads.
- **Manifests:** v1 is EXECUTION_BLOCKED, and v2 replaces it. Status: WAITING_FOR_EXECUTION_APPROVAL.
- **Tests:** 37 driver and reconciliation tests, plus a mutation pass. See `track_a/EXECUTION_MANIFEST_REVIEW.md`.
- **Blockers:**
    - parent SQL and log format uncharacterized on PostgreSQL 16;
    - the parent is a superuser;
    - amendment 002's manual steps (oracle-author review, uninstrumented reproduction) are absent, so no "detection"
      claim is possible;
    - the replay signature is unreviewed.

**Track B:**

- option-generated units are labelled `+companion` or `+partner`;
- `ARM_E_FAIRNESS.md`;
- `O6_FIXED_BUDGET_PROPOSAL.md`: 65,536 and 65,536 as a policy, not a proven bound.

**Shared:** Amendment 003 revision 8, still DRAFT — NOT ADOPTED and uncommitted.

---

## Parallel milestone 4 (uncommitted except `dd87cd4`)

**Committed first:** `dd87cd4`, milestone 3's Track A (reconciliation, driver, manifest v2).

**Track A:**

- **Statement coverage:** `track_a/STATEMENT_COVERAGE_AND_PER_RUN_ASSURANCE.md`.
    - A2's export, construction and execution operations are EXECUTION_BLOCKED: they rely on unreconciled `Sandbox`
      statements.
    - Per-run initial-state assurance is NOT ESTABLISHED, and the whole-batch log audit is proposed.
- **Replay review:** `track_a/REPLAY_SIGNATURE_REVIEW.md`.
    - An empty signature is correct for the buggy lost update.
    - Contended wakeups now record their contenders, and `followed` is taken from the trace.
    - `detection_status` stays `automated_characterization_only` without both 002 manual confirmations.
- **Gate A1:** `track_a/GATE_A1_CHARACTERIZATION_APPROVAL.md`, `gate_a1_manifest.json`,
  `research_tools/gate_a1_characterize.py` and `characterization.py`.
    - Characterization only: no Sandbox, supervisor, child, application or command.
    - **After the final review: READY_FOR_USER_AUTHORIZATION, which is not an authorization.** The manifest is
      `04f1d751…`.
    - The driver now fixes its target from the manifest before connecting, and keeps libpq parse errors out of records.
    - Mutation testing in isolated copies, under a watchdog: A1 28 of 28 and A2 21 of 22 killed by test assertions; the
      remaining A2 mutant is equivalent, with a stated reason; none was killed by the watchdog.
    - A2's `RealEffects` has no target or model-key check. That is recorded as an A2 blocker.
    - **First real run (version 1 manifest, 2026-11-04): `refused_fail_closed` at op0p.** It ran on PostgreSQL 16.15
      under the owner's signed approval. Two causes: PostgreSQL 16's `connection authenticated` record, and the
      driver's own end-of-session poll falling inside the window.
    - **Version 2, offline:** strict connection-record and log-reader accounting, built from the real rows; a pinned
      real-log fixture; the approval copied byte for byte. READY_FOR_NEW_USER_AUTHORIZATION, which needs a new
      signature.

**Track B:**

- `track_b/ARM_E_STRONG_POLICY.md`, with default-policy rejections disclosed under the strong policy;
- the O6 values marked as retrospectively selected;
- Amendment 003 revision 9, still DRAFT — NOT ADOPTED.
