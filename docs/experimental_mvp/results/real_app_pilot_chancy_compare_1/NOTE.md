# real_app_pilot_chancy_compare_1: reading notes (NONBLIND, exploratory)

This is a limited pilot on a real mixed-version deploy: Chancy 0.25.1 → 0.26.0, whose new migrations v7 and v8 are the area the frozen rule selects.

**Design.**
- Each workload is four bounded integers, validated deterministically: jobs pushed by the old code before the migration; jobs the old producer pushes on the new schema; the old worker's hand-off point; jobs pushed by the new code.
- Each workload runs as four isolated containers, each phase corroborated against the server log, with oracle M1–M4 written from Chancy's documented job states.
- **Budget:** the first 2 valid workloads per source. Muse got 1 call for 4 proposals, at an estimated $0.009762.

| Source | Valid | Sound runs | Oracle violations | Distinct statements | Statements touching v8's `claim_id` |
|---|---|---|---|---|---|
| curated | 2/2 | 2 | 0 | 30 | 3 |
| systematic | 16/16 | 2 | 0 | 28 | 3 |
| Muse | 4/4 | 2 | 0 | 28 | 3 |

**Reading:**
- **No defect** was detected by any source. This is a negative result for these workloads and schedules.
- **Old-release handling:** only curated workload #0 had the 0.25.1 worker actually handle jobs on the new schema. Both of Muse's executed proposals set `old_worker_handles = 0`, so within this budget Muse exercised less of the cross-version path than the curated source.
- **Conclusion:** no Muse advantage.
- **Not explored:** interleavings between the two releases' workers. They run in separate processes, so they can't be scheduled against each other.
