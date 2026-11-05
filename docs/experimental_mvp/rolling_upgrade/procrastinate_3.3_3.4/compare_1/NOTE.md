# compare_1: curated vs systematic-deterministic vs Muse on the procrastinate rolling-upgrade overlap (EXPLORATORY, NONBLIND)

**Design.** The budget was equal: **K = 2 executed overlap workloads per source**. Every workload went through the identical seven-phase pipeline, the same containers and the same `alternate` schedule policy, and was judged by the frozen checker read with errata E1 and E2.
- **Curated:** the frozen case's workload, plus one written for cross-version retries.
- **Systematic:** the first 2 of a fixed-order grid over the four job shapes on both sides, then each admin variant (`muse_procrastinate.systematic`).
- **Muse:** one request (`muse-spark-1.3`, reasoning `medium`, `store=False`, prompt `6a092ac9…`) for 4 structured proposals. The first 2 valid ones were executed.

**Muse output.** It returned 4 proposals, and the deterministic validator accepted all 4 (`muse_validation.jsonl`). No model output was executed as SQL or code; the harness built every actor argument. Cost: 1 request, 628 input / 3,741 output tokens, an estimated **$0.016684**. Every reply was saved on arrival (`muse_procrastinate_0.reply.json`, `muse_ledger.jsonl`).

**Revisions.** The runs were at clean revisions `13af6b3` and `f8e5aa4`, which differ only in documentation committed mid-batch. Each run's code revision is in its reports.

| Run | Verdict | Statements (all phases) | Mismatched / unchecked | v1 retries (3.3.0) | v2 retries (3.4.0 worker) | v2 admin retries | Jobs run by the other release | Jobs run by both | Violations after errata |
|---|---|---|---|---|---|---|---|---|---|
| curated_0 | SOUND, 7/7 | 282 | 0 / 20 | 2 | 2 | 1 | 6 | 2 | none |
| curated_1 | SOUND, 7/7 | 246 | 0 / 16 | 1 | 0 | 2 | 2 | 2 | none |
| systematic_0 | SOUND, 7/7 | 213 | 0 / 16 | 1 | 0 | 0 | 2 | 2 | none |
| systematic_1 | SOUND, 7/7 | 231 | 0 / 16 | 2 | 2 | 0 | 2 | 2 | none |
| muse_0 | SOUND, 7/7 | 219 | 0 / 15 | 1 | 2 | 0 | 3 | 2 | none |
| muse_1 | SOUND, 7/7 | 219 | 0 / 15 | 1 | 0 | 1 | 4 | 2 | none |

**Per source (union of its 2 workloads):**
- **Curated** reached all three migration-touching retry paths: v1, v2 worker and v2 admin.
- **Muse** reached all three.
- **Systematic** didn't reach the v2 admin path in its first 2 grid workloads. The admin variants come later in its fixed order. That is a property of this budget and ordering, not of the method.

Muse's proposals sent more jobs across releases than the systematic ones (7 against 4), and about as many as curated (8).

**What can and can't be concluded.**
- No source detected any application violation, because none was found in this release pair. So the comparison can't speak to defect detection (Q3).
- It shows only that all three sources produced valid workloads at this tiny budget, and covered the migration-touching operations to a similar extent.
- **No Muse advantage is claimed.** K = 2 per source is a pilot, NONBLIND, on a development application.

Evidence digest (all files, sorted): `9aaf5a5bf0ecff3f`.
