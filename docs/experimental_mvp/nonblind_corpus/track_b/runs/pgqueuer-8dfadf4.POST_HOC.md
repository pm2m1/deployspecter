# POST-HOC observation, pgqueuer-8dfadf4 (not part of the predeclared analysis)

RA-MD made 2 requests and got 12 valid proposals. Under the frozen executor, each replicate runs one workload
(`work[rep % len(work)]`), so only proposals 0–2 ran:
- 0 and 1: `concurrency_limit` 0, one job, cancelled;
- 2: limit 1, two jobs, no cancel.

None of the three has the shape that triggers the defect: limit 1, at least two jobs and a cancel. Among the unused
proposals, numbers 5 and 10 have that shape.

This was read after the outcome, so it changes nothing:
- RA-MD is a nondetection under the frozen budgets;
- the unused proposals are not run.

It points to a design property worth deciding before any confirmatory use: with B/3 schedules per replicate and
one workload per replicate, an arm that proposes many workloads uses only its first three. That property applies
equally to RA-DET, which has exactly 3.
