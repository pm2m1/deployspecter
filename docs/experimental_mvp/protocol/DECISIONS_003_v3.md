# Amendment 003: complete decision register (v3, 2026-11-06)

**Every entry is PROPOSED.** Nobody has accepted, implemented or verified any of them. No reviewer, curator or signatory exists.

Earlier versions are kept unchanged as records:
- `DECISIONS_003.md` (v1);
- `DECISIONS_003_v2.md`, with `DECISIONS_003_v2_ADDENDUM.md`;
- the drafts v1–v3, `REVIEW_ISSUES.md` and `ADOPTION_READINESS.md`.

The frozen protocol 1.2, amendments 001 and 002, and the A–G and R registrations are untouched.

**What approvers sign.** They approve the exact text of `AMENDMENT_003_ADOPTION_CANDIDATE.md`, identified by its sha256, through the forms in `approval/`. Each form lists every decision below, with approve, amend or reject.

**Scope.** The evaluation stays **separate from the certified Gate 2A/A2 boundary** (#14). Nothing here touches a frozen gate.

Statuses: PROPOSED → ACCEPTED (signed by the decider) → IMPLEMENTED → VERIFIED (`DECISIONS_003_v2.md` gives the evidence each needs).

## Corpus and selection

| # | Proposed ruling (exact clause text for the candidate) | Alternatives on record | Decider | Effect if accepted |
|---|---|---|---|---|
| 1 | "For a Django project with several app-level `migrations/` directories, the core migration area is the `migrations/` directory of the app that holds the most `NNNN_*.py` files, determined from file names alone, before any file in it is read (ties: the app whose label sorts first). The selected area is that directory's highest-numbered file, plus `django.db.migrations.executor`." | A1b (the root app's label equals the distribution name, else NONE); A1c (exclude multi-app Django projects) | owner + curator | weblate: app `trans` (102 files), decided from names alone |
| 2 | "For Alembic, the selected area is the single head of the revision graph named by the project's own `alembic.ini` `script_location`, as `alembic heads` would report it, read from the `revision` and `down_revision` headers only. If the project has several graphs, the one its default server or CLI upgrades is the core graph. If that is ambiguous, the case needs a written curator ruling before inclusion." | A2b (the revisions introduced by the pinned release, from the previous sdist's file list) | owner + curator | mlflow: `mlflow/store/db_migrations`, head `f4a9b2c7d1e6` |
| 3 | "A PostgreSQL driver declared only under an optional extra whose name designates PostgreSQL counts as a declared dependency. The extra is recorded. This applies to every candidate alike." | A3b (only required dependencies count, which retroactively removes pgqueuer, recorded as such) | owner | keeps pgqueuer's past admission consistent; admits weblate `postgres`, mlflow `db` and saq `postgres` on this point |
| 4 | "RA-Q3 is preregistered as descriptive unless at least 10 eligible blind cases exist at the freeze." | none | owner | |
| 5 | "A historical case is a commit-level fix pair (the fix commit and its first parent), each built from a hash-pinned tree. Release-level pairs may be run in addition, but are reported as confounded. Mutants are secondary, scored per frozen §4.2 operator, and never called defects." | historical pairs only, or mutants only | owner | as feasible: `historical/chancy_2895c4a` |
| 19 | "Mixed-version operations count only where the application's own documentation supports them (a rolling or blue-green procedure). The curator records the citation in the case labels. Otherwise the case runs single-version, or its mixed-version results are exploratory and stated as outside the documented contract." | none (no rule) | owner + curator | Chancy's simultaneous P7 cells are outside the contract; procrastinate's pre/post overlap is inside it |
| 21 | "A directory of version-named migration files (`.py` or `.sql`) inside the core package (e.g. `procrastinate/sql/migrations/xx.yy.zz_ab_{pre|post}_*.sql`) counts as its migration area. The selected area is the highest version's files, plus the documented procedure that applies them." | keep the frozen `.py`-only rule (such projects remain NEEDS_PROTOCOL_RULING) | owner | matters only for prospective candidates; procrastinate itself stays development-only |
| A4 | "'The module that applies migrations' is recorded by framework, as metadata: Django `django.db.migrations.executor`; Alembic `alembic.command.upgrade` and the project's `env.py`; custom: the module the tool finds by name, file name only." | none | owner | |
| A5 | "The selection tool's two content reads (`INSTALLED_APPS` literals; Alembic `revision` and `down_revision` headers) are metadata-level exposure, and don't make a case non-blind." | forbid them (the tool must then switch to file names only) | owner | |

## Independence (needs named people; see `DECISIONS_003_v2.md` for roles and evidence)

| # | Proposed ruling | Decider |
|---|---|---|
| 6 | An independent Label Curator selects and seals each case's labels with `seal_labels.py`, and submits cases through `research_tools/corpus_intake.py` (sealed revision pair, contract, exposure). The runner never sees the labels before scoring. | owner + the named curator |
| 7 | An independent Oracle Author writes each blind case's properties from documentation and the public API only, never from the fix, its tests or its changelog entry. An Oracle Reviewer checks the encoding. Each spec is hashed into the freeze. | owner + the named author and reviewer |
| 8 | Two Triage Reviewers, blind to the arm, classify every corrected-version violation. A predeclared Adjudicator resolves disagreements, and κ is reported. | owner + the named reviewers and adjudicator |
| 9 | The adoption commit's sha256 is registered (OSF Registries, plus an RFC 3161 or OpenTimestamps token) before the first evaluation run. | owner only (public) |
| 14 | RA makes no Gate 2A/A2 claim. The Gate A2 demonstration stays blocked, and results are never pooled. | owner |
| 20 | Each exposure ledger records "training-data exposure not excludable" for public cases. Blindness rests on the process (sealing, independent oracles, leak checks). | owner |

## Instrumentation and analysis

| # | Proposed ruling | Decider |
|---|---|---|
| 10 | A per-application adapter counts as RA-Q4 onboarding work. A generic engine capability counts as tool development. | owner |
| 11 | A run with LISTEN/NOTIFY disabled (polling) is faithful for database-statement semantics. A case whose defect needs the notification path is excluded and reported. | owner + oracle reviewer |
| 12 | The Django seam stays development-only until a development Django application of comparable scale has run through it. | owner |
| 13 | The restricted runc container is acceptable only for pinned, publicly released, statically reviewed code. It is disclosed in every result. | owner |
| 15 | B = 12 schedules per workload per build (forced plus seeds 0–10), T = 900 s per case per arm, 3 replicates; R = 2 requests, P = 6 proposals, C = $1 per case per arm (`PROSPECTIVE_MUSE_PLAN.md` §5). | owner, before the freeze |
| 16 | The deterministic arms' model budget is zero. | owner |
| 17 | Model-proposed invariant hypotheses stay exploratory. | owner |
| 18 | `RA-SMOKE`, the simultaneous Chancy pilot, `historical/chancy_2895c4a` and `rolling_upgrade/procrastinate_3.3_3.4` are cited only as development evidence. | owner |
| 22 (new) | **Server-log corroboration of typed parameters.** A run's corroboration reports compared, mismatched and unchecked values separately. A confirmatory result requires 0 mismatched. Unchecked values are disclosed per type, and never counted as matched (record arrays and float8 are now compared exactly: `rolling_upgrade/.../runs_typed_verification`). | owner |
