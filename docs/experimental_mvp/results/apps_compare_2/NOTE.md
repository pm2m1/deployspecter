# apps_compare_2: reading notes

This is an engineering smoke test on trusted synthetic fixtures whose defects the developers knew, run on 2026-11-05. It is not a benchmark.

- **Budgets:** K = 6 valid workloads per source and S = 60 schedules per workload per build. Muse got 2 calls per target with 6 proposals each.
- **Cost:** the four Muse calls cost an estimated $0.148953 (Meta's published rates, not billing data).
- **Metric name:** in `report.json`, the key `fixed_build_false_positives` (since renamed `fixed_build_violations` in the runner) counts workloads whose **fixed** build violated the oracle in any explored schedule.

**The one fixed-build violation is not a false positive.** It comes from Muse's planner document `muse_planner_1#4` (`double_release_same_holder`), and the server log corroborates it (see the regression test `test_the_fixed_build_still_double_releases_one_reservation`).

In Gate 2A's synthetic `live_app.reservations`, `release_seats` reads the holder's reservation without a lock. Two releases by one holder can both pick the same reservation. Both then decrement `reserved`, and the second DELETE removes 0 rows.

The `lock_row` switch locks only the event row, so the "fixed" build isn't a valid negative control for `release_seats`. Under the draft amendment's rule, that case is void for its source. The finding is a second defect in a developer-written fixture, not a blind detection.

**Dict-row values in the traces:** these runs recorded `dict_row` reads (`release_seats`) as hashed column names instead of values. The tracer has been fixed since. The oracle, the final states and the server-log corroboration don't use the traced rows, so the counts here are unaffected.
