# Issues requiring human scientific review before draft amendment 003 can be adopted

None of these is decided here. Each needs a written ruling, committed before any RA evaluation run.

## Corpus and selection

1. **Django migration area.** Weblate has many app-level `migrations/` directories and none at package level. Which one is the "core package's migrations/ directory", and must that be decided before any file in it is read?
2. **Alembic revisions.** For mlflow, how does "highest-numbered" apply to hash-named Alembic revisions: the revision graph's head, the latest release's head, or not at all?
3. **Drivers under an extra.** Does a driver declared only under an optional extra (weblate `postgres`, mlflow `db`, saq `postgres`) satisfy "declared dependency"? pgqueuer entered the shortlist that way, and the rule should be confirmed or narrowed for everyone alike.
4. **Corpus size.** With five blind-eligible candidates, of which none is yet instrumentable end to end with a defect and its fix, RA-Q3 will have far fewer than 10 cases. Should RA-Q3 be preregistered as descriptive only until the corpus grows?
5. **What a defective version is for an RA case.** A historical fix pair, a mutant of the current release, or both? Under what sealing?

## Independence

6. **Label curation.** Who curates and seals historical defect labels, and with what process, so that the tool authors never see them before the arms run?
7. **Oracle authorship.** Who writes the per-case oracles without access to fixes? How is "hasn't seen the fix" verified?
8. **Blind evaluation.** Is a blind evaluator needed for triaging corrected-version violations, as frozen §9's "two reviewers" requires? Who are they?
9. **Timestamping.** How and where is the adoption commit, or its SHA-256, published with a verifiable timestamp before the first run, as frozen §1 requires?

## Instrumentation and isolation

10. **Adapter cost.** pgqueuer needs a Driver-port adapter, because its public psycopg factory bypasses the gate. Chancy needs a one-connection pool adapter with notifications off. Do these per-library adapters count as "registration" work under RA-Q4, or as tool development?
11. **Polling mode.** Chancy runs only with `notifications=False` in the supported mode, which changes its runtime behaviour (it polls instead of listening). Is a case run that way still a faithful execution of the application?
12. **Django.** Weblate needs a Django database-backend seam that doesn't exist. Building it is tool development and must happen on development applications only.
13. **Isolation.** The isolated runner is not hostile-grade, and no gVisor, Kata or microVM runtime is available on this host. Is the restricted runc container an acceptable boundary for running pinned public code? Or must RA wait for a stronger runtime?
14. **Supervision.** Should RA runs go through Gate 2A's supervised path, which is still blocked at Gate A2, or is the experimental runner acceptable with this disclosure? This must not bypass the Gate A2 decision.

## Analysis

15. **Budgets.** Budget values B, T, R, P and C, and the replicate shape. Which development cases are they fixed on?
16. **Equal model budget.** Whether "equal model budget" means zero for deterministic arms, which is the current draft, or an equal compute-time allowance spent on extra schedules.
17. **Hypothesis scoring.** Whether mutant discrimination by model-proposed hypotheses is a reportable secondary outcome or exploratory only.
18. **Exploratory evidence.** How the existing `RA-SMOKE` evidence, all on developer-written fixtures, may be cited. The draft says it may be cited only as engineering evidence of tool capability, never as detection evidence.
