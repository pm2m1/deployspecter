# DeploySpecter: external evaluation handoff (2026-11-10)

This is for the people who would run an independent, confirmatory evaluation. **Everything reported here is
developer-run, NONBLIND or AI-operator evidence. None of it is confirmatory, and no independent reviewer or
participant has taken part.**

## 1. What DeploySpecter demonstrably does

- **Steps real application code against real PostgreSQL:** unmodified application code (psycopg 3, sync and async;
  SQLAlchemy; Django; Chancy, PGQueuer, procrastinate and saq job runtimes) runs on a real database. Every statement
  passes through a deterministic scheduler, so interleavings are chosen and replayable, inside restricted runc
  containers.
- **Checks every run against PostgreSQL's server log:** each statement and typed parameter is matched, and a run with
  any mismatched parameter or unexplained statement is refused.
- **Checks outcomes independently:** oracles read the final database state and the actors' results. A run is
  scored only when it is SOUND.
- **Reproduces historical defects at commit level:** buggy and fixed trees are pinned by hash, each runs on its own
  fresh schema, and the fixed version serves as a control.

Limits: restricted containers, not hostile-grade isolation; PostgreSQL 16 only; one connection per actor; a stop at
45 s; lock waits inside a run make corroboration refuse; parse-time server errors can't be corroborated.

## 2. Historical defects reproduced (development cases; developers read the fixes)

| Case | Defect (documented upstream) | Buggy → fixed | Fixed control |
|---|---|---|---|
| chancy-2895c4a | a late completion overwrites a cancellation | 80f570f → 2895c4a | holds |
| pgqueuer-8dfadf4 | a cancelled job's picked row is lost, breaking `concurrency_limit` (#1032) | f258cbc → 8dfadf4 | holds |
| saq-c570ba4 | a timed-out Postgres dequeue leaves its buffered job `active`, never run | 59f32fc → c570ba4 | holds |
| saq-addee31 | `init_db`'s advisory lock was session-scoped | f827b52 → addee31 | holds (case **re-encoded after outcomes**: a disclosed deviation) |

Also attempted (`nonblind_corpus/CORPUS_RESULTS.md`):
- **chancy-b8a77df:** REFUSED by the instrument.
- **saq-ad8d1c8:** CONTROL FAILED.
- **Screening:** 34 of 68 candidates screened, most failing because the defect is in-process logic, not
  database-mediated.

## 3. Muse results (exact, with caveats)

| Experiment | Design | Result (primary) | Status |
|---|---|---|---|
| ds-nbc/1 | Fix-informed deterministic workloads vs Muse workloads; frozen executor | 4/4 vs 1/4; McNemar p = 0.25; INSUFFICIENT SAMPLE | **Original primary.** Audit: only 3 Muse workloads ran per case (36 of 48 never did), and the baseline knew the fixes (`track_b/AUDIT_1.md`) |
| ds-nbc/2 | D1 (fix-informed) vs D2 (outcome-blind schema generator) vs M (Muse); every proposal run; equal budgets; same seeds | **4/4, 4/4, 4/4**; M vs D2 McNemar p = 1.0; Gehan 2 vs 2; Kaplan–Meier median schedules 5 (M) vs 9 (D2); INSUFFICIENT SAMPLE | **Corrected primary.** Secondary, post-hoc: counting only workloads that hold on the fixed version, D1 4/4, D2 3/4, M 4/4 (p = 1.0) (`nbc2/RESULTS.md`, `nbc2/AUDIT_2.md`) |

Caveats:
- 4 developer-selected cases from 3 applications.
- The workload schemas and Muse context were written by fix-aware developers, so both fix-blind arms searched a
  fix-shaped space.
- saq-c570ba4's fixed version violates an unrelated property (a burst overrun) under some D2 and M workloads.
- Model cost was small ($0.06–$0.11 per experiment).

**Neither superiority nor inferiority of Muse is shown.**

## 4. Onboarding experiments (AI operators, not people)

| Pilot | Apps | Design | Result |
|---|---|---|---|
| onb-pilot/1 | saq (D), taskiq-postgresql (M) | one app per condition | not comparable (app confounded with condition) |
| onb-pilot/2 | pgtq, postgres-tq | both conditions per app, 2 replicates | Muse called once in 4 M sessions; no claim |
| onb-pilot/3 | pgtq, postgres-tq (already exposed) | a **mandatory** Muse checkpoint in M; the same checkpoint without the model in D | Muse used 4/4 (pgtq modified ×2, postgres-tq rejected ×2). M was slower in all 4 pairs (median +54.8 s and +87.8 s), mostly the checkpoint's own cost. All 8 sessions reached S3 first time (a ceiling) |

Limitations:
- The operators are AI agents of one model family.
- Contamination controls are by instruction only.
- The tasks are short.
- There are only 2 applications, while a claim needs at least 3.
- Nothing here measures human productivity.

## 5. Independent case selection and oracle workflow

1. **Curator** (independent of the developers):
   - selects candidates under the frozen rules;
   - pins each fix pair;
   - writes or approves each case's workload schema and Muse context without the fix (proposed #29);
   - seals labels with `seal_labels.py`, then submits through `research_tools/confirmatory/intake_v2.py` on a
     separate account or machine.
2. **Oracle Author:** writes each oracle from public documentation and the API only, and declares the clause(s) that
   encode the target defect (proposed #23).
3. **Oracle Reviewer** (a different person): checks the encoding and validates that the oracle holds on the fixed
   version under the full budget.
4. **Evaluator:** runs the gated confirmatory runner (`run.py real`). It refuses until every prerequisite exists, and
   the evaluator never sees the labels.
5. **Triage Reviewers** (two, blind to the arm) and an **Adjudicator**: classify every fixed-version violation.
6. **Owner:** authorizes the reveal (`real-authorize`), after which scoring runs.

## 6. Excluded from any future independent corpus

These have been exposed to the developers:
- the development apps chancy, pgqueuer, procrastinate, InvenTree, paperless-ngx and langgraph-checkpoint-postgres;
- saq, taskiq-postgresql, pgtq and postgres-tq (onboarding pilots);
- anything GitLab-derived.

The frozen protocol's held-out applications are reserved for that protocol and stay unread. They are not part of
this corpus.

## 7. Human onboarding study requirements

The package is in `onboarding/participant_study/`, not run.
- **Participants:** independent humans, never DeploySpecter contributors; at least 5 per arm before any verdict.
- **Allocation:** a two-period crossover (Latin square over application × condition).
- **Assistance:** a fixed checkpoint where Muse is consulted (M), or the same checkpoint without it (D).
- **Applications:** two never exposed here.
- **Governance:** consent, a named data owner and retention period, an ethics or owner review.
- **Scoring:** blind.
- **Claim rule:** ≥ 3 applications with ≥ 30% median reduction in both minutes and lines.

## 8. Roles and approvals needed (none exists today)

| Role | Approves or produces | Form |
|---|---|---|
| Owner | the adoption, the 24 decisions (and any of proposed #23–#30), registration, reveal | `protocol/approval/OWNER_APPROVAL.md` |
| Label Curator | case selection, schemas and context (proposed), sealing | `CURATOR_ACCEPTANCE.md` |
| Oracle Author | oracles and target clauses | `ORACLE_REVIEWER_ACCEPTANCE.md` (author row) |
| Oracle Reviewer (a different person) | oracle validation | `ORACLE_REVIEWER_ACCEPTANCE.md` (reviewer row) |
| Triage Reviewers ×2 and an Adjudicator | fixed-version violation classification | `TRIAGE_REVIEWERS_ACCEPTANCE.md` |
| Study data owner and an ethics or owner reviewer | the human onboarding study | `onboarding/participant_study/CONSENT_AND_CONFIDENTIALITY.md` |
| Independent participants (≥ 5 per arm) | onboarding sessions | consent forms |

## 9. Dependency-ordered checklist to enable a real confirmatory evaluation

1. **Rule on the proposed revision.** The owner rules on `protocol/AMENDMENT_003_REVISION_PROPOSAL_r1.md`. If any
   part is accepted, a new candidate is written with a new hash; otherwise the current one (`ad5ea331…`) stands.
2. **Name the people.** Name and declare (COI) the curator, the oracle author and reviewer, the triage reviewers and
   the adjudicator. Each signs their own form against the chosen candidate hash.
3. **Owner approval.** The owner signs `OWNER_APPROVAL.md` and the resource and retry policy file.
4. **Adopt and register.** Commit the candidate byte-identically; freeze the Muse model, prompt, proposer and
   per-case contexts (§12, proposed #27); register the hash with a trusted timestamp before any run.
5. **If #24, #25 or #28 is accepted, update the runner** (allocation, RA-DET-G, accounting) on development cases
   only, and freeze it.
6. **Curator cases.** The curator selects and seals at least 10 cases from applications not in §6, with at most 3
   per application (proposed #26); fix-blind integrations (schemas, contexts) are written or approved.
7. **Oracles.** The oracle author writes oracles with target clauses; the reviewer validates them on the fixed
   versions; their hashes are frozen.
8. **Run.** The evaluator runs `run.py real` (it refuses until steps 1–7 are complete). Triage then classifies
   fixed-version violations, and the owner authorizes the reveal.
9. **Score and analyse.** Scoring and the predeclared analysis follow; results are reported as confirmatory only if
   every rule held.
10. **Separately, the human onboarding study:** recruitment, consent, allocation, sessions and blind scoring.

This checklist is a plan, not a record. Nothing on it has been done by independent people.
