# simultaneous_repeat_cancel_new_old_1: repeatability of `cancel_new_old` under `alternate` (3 runs)

The cell: a 0.26.0 producer pushes a job to `q_old`, a 0.25.1 worker claims it, and the 0.26.0 producer's `cancel_job` is scheduled between the claim and the worker's completion flush.

| Run | Verdict | Grants | Transaction order | Final state |
|---|---|---|---|---|
| 00 | SOUND | 67 | `58a8cc5c…` | `succeeded`: the cancel was overwritten (P7) |
| 01 | SOUND | 73 | `43f847a1…` | the same |
| 02 | SOUND | 73 | `43f847a1…` | the same |

**Outcome: 3 of 3 identical.** The P7 entry is the same in all three, and every statement was corroborated.

**Trace: 2 of 3 identical.** Run 00 has six fewer statements, from Chancy's polling timing, while the gated claim → cancel → flush order is the same in all three.

Interpretation: see `../simultaneous_scenarios_1/NOTE.md`. This is a mixed-deployment hazard outside the documented upgrade procedure, not a defect.
