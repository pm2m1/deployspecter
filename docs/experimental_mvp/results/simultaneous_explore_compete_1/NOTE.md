# simultaneous_explore_compete_1: bounded schedule exploration of `compete` (NONBLIND), original releases, `fad4a07`

**Setup.** Scenario `compete`: a 0.25.1 worker and a 0.26.0 worker on one queue (`q_shared`), with jobs 0–1 pushed by 0.25.1 and 2–3 by 0.26.0, all at the same time. Schedules come from the seeded policy, seeds 0–9: one candidate schedule per seed, from the policy family.

| Measure | Value |
|---|---|
| Candidate schedules | 10 |
| Executed | 10 |
| Refused / incomplete / timed out | 0 / 0 / 0 |
| SOUND | 10 |
| Statements corroborated | 2077; 0 parameters mismatched, 0 unchecked |
| Distinct transaction orders | 10 of 10 |
| Distinct canonical final states | 6: which version ran which job. Every job always `succeeded`, once |
| Oracle (P1–P8) violations | **0** |
| Time to first violation | none |

**Reading.** Under these 10 schedules, the two releases' claim queries never gave one job to both workers, and no job was lost or left stuck. In each window, both versions issued claim queries (4–6 each).

The result is bounded:
- 10 seeds sample a policy family;
- they are not an exhaustive search of the interleavings, nor of Chancy's internal (uncontrolled) task timing.

The mutation control on the same seeds is `simultaneous_mutant_compete_2`.
