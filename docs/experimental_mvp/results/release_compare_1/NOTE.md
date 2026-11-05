# release_compare_1: reading notes

**What this is.** A measurement on Gate 2A's synthetic reservations app, a developer-written fixture. Its release defect was first reached by a Muse proposal, so nothing here is blind.
- **Budgets:** K = 6 bridged workloads per source and S = 60 schedules per workload per build.
- **Builds:** the frozen `buggy` and `fixed`, the experimental `corrected`, and four mutants of `corrected`.
- **Muse:** 2 calls, an estimated $0.081551.

**Key name.** In `report.json`, `hypothesis_workloads_distinguishing_per_build` counts **accepted hypotheses** violated on a build, not workloads. The runner now calls it `accepted_hypotheses_violated_per_build`.

**Readings:**
- **Validity:** Muse's 12 documents all bridged through the unmodified chain, and the first 6 ran. Its 19 hypotheses all held on the initial state and on every explored run of the corrected reference.
- **What the hypotheses distinguished:** per workload, exactly the builds the frozen oracle (R1, R2, R5) did. `equals_child_sum` restates R1, and `reserved >= 0` caught only the double release. No hypothesis distinguished a build the oracle didn't.
- **Mutant kills (any workload violating):**

  | Source | buggy | fixed | no_event_lock | off_by_one |
  |---|---|---|---|---|
  | curated | ✓ | ✓ | ✓ | ✓ |
  | systematic | ✓ | ✓ | ✓ | ✓ |
  | Muse | ✓ | ✓ | – | ✓ |

  Muse's executed workloads never released for two holders on one event, so `no_event_lock` survived.
- **Never killed by any source:** `no_reservation_lock` (its delete guard turns the double release into an error: safe, but not live) and `no_guard` (equivalent under the locks).
- **Conclusion:** no Muse advantage on this fixture.
