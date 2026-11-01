# Audit of Muse Spark invariants

Offline re-execution of the 10 recorded v2 responses. Machine-readable data, including an example schedule per invariant: `invariant_audit.json`. Each invariant ran on at most 100 schedules, and where possible also on the other variant of its scenario.

## The 94 reported failures

All 94 come from one invariant in `backfill_overwrite__buggy__r0.json`: test `concurrent_backfills_duplicate_same_row`, invariant `t2_second_row_filled`. Re-running with the original budget share of 108 schedules reproduced exactly 94 failures with **one distinct final state**.

Two `LIMIT 1` backfills both read row 1, both write row 1, and row 2 stays NULL. Example: `concurrent_backfills_example_trace.json`.

| Category | Count of the 94 |
|---|---|
| Independently verified correctness violations | 0 |
| Potentially real, needs additional oracle evidence | 94 failures of 1 invariant, 1 underlying behaviour |
| Incorrect or unsupported invariant | 0 |
| Execution or instrumentation artifact | 0 (all outcomes `completed`) |

The behaviour does not depend on the seeded defect: the invariant also failed in 86 of 100 schedules on the corrected code. No data is overwritten or lost, and the skipped row stays NULL for a later batch to pick up. It would matter only under a requirement that concurrent batches must not skip work, which this benchmark does not state. It is **not counted as a bug found**.

The 76 failures the earlier report labelled "confirmed" were only same-schedule co-occurrence with an oracle violation. Checked properly, all 76 (61 + 15) match the oracle's row and column, and all report the one known defect.

## All 68 invariants

| Category | Invariants | Note |
|---|---|---|
| Held in every schedule run | 47 | |
| Independently verified | 15 | Duplicates of the 2 known defects: 7 backfill, 8 mixed-version |
| Incorrect or unsupported | 5 | Fail in a serial order; see below |
| Potentially real | 1 | The behaviour above |
| Execution artifact | 0 | |

The 5 unsupported invariants, in `backfill_overwrite` r1 responses and `corrected` r1, all expect `display_name` to track `legacy_name`. They fail in serial orders and identically on both variants. The code never promises this, since release 2 deliberately lets the two diverge. Whether a v1 rename during a backfill should propagate is a product question with no stated requirement here.

Categories are assigned mechanically by `invariant_audit.classify`. The readings above are human judgement over that output.
