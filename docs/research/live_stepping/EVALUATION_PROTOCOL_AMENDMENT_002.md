# Amendment 002 to the evaluation protocol

**Status:** in effect from the Git commit that adds this file. Applied after amendment 001, it changes protocol version 1.1 to version 1.2. The frozen file itself is never edited.

- **Number and date:** 002, 2026-11-03.
- **Sections changed:** section 5, from "Replay is reported at two levels" through the paragraph that begins "Fewer than 3 of 3".
- **Reason:** measurements on synthetic scenarios only. Over 200 attempts per setting, the deadlock victim varied between sessions (minority share 5.5% to 28.5%), and so did four-actor lock wakeup order (16% to 28.5%), including on plain psycopg with no scheduler. Given the same victim, trace and final state matched in every attempt measured. Under the frozen rule, a state reached only with a 5.5% victim would pass 3 of 3 about 0.02% of the time, so a real defect could be labelled unstable because of PostgreSQL's choice and not the tool's replay.
- **Held-out material or results seen when written:** none. No evaluation arm has run.
- **Affects a primary hypothesis:** yes, through the definition of detection. P1, P2, their tests and thresholds don't change. The rule applies identically to every arm.

## Replaced text

```text
**Replay is reported at two levels:**

- **Semantic outcome reproduction:** the same run outcome class, the same requirement ids violated, and the same committed final state (ignoring generated identifiers and masked timestamps).
- **Exact trace reproduction:** an identical behaviour fingerprint, covering statements, results, lock waits and decisions.

Exact trace reproduction isn't always achievable. PostgreSQL chooses deadlock victims and lock wakeups, and the tool doesn't control them.

**Detection** requires all of:

1. The oracle reports a violation on the pre-fix version.
2. Semantic replay succeeds in 3 of 3 attempts. Exact replay is reported but not required.
3. The same workload doesn't violate that requirement on the fix.

Fewer than 3 of 3 is "unstable", not a detection. A violation of a different requirement is an "other finding", which isn't a detection and goes to triage. Written descriptions of a defect in a model's text are never detection.
```

## Replacement

````text
**Who chooses what.** Replay separates two kinds of choice:

- **Scheduler-controlled decisions:** which actor is admitted at each decision point. These are saved and replayed strictly.
- **PostgreSQL-selected outcomes:** the deadlock victim, which transaction receives a serialization failure, and which of several lock waiters wakes first. These are observed and recorded. The tool never tries to steer them, including through `deadlock_timeout` or other session settings.

**Server-choice signature.** For each replay attempt, a signature lists the PostgreSQL-selected outcomes as the trace shows them: for each deadlock or serialization failure, the actor and its step; for each lock wakeup, which waiter completed under which decision. It contains no result rows, requirement ids or committed state, so a difference in those can never make an attempt "not comparable". Its code is frozen with the tool. A choice the trace can't show isn't in the signature, so attempts differing only in that choice count as comparable and can fail.

**Each attempt is classified as one of:**

| Class | Meaning |
|---|---|
| Exact | Same signature and identical behaviour fingerprint. Also counts as conditional semantic |
| Conditional semantic | Same signature, with the same run outcome class, the same requirement ids violated, and the same committed final state (ignoring generated identifiers and masked timestamps) |
| Not comparable | The signature differs from the detecting run's. Logged with its outcome, and counts neither for nor against |
| Failed | Same signature, but outcome class, requirement ids or committed state differ, or the saved decisions couldn't be followed before any signature difference appeared |
| Inconclusive | The attempt ended in an inconclusive outcome under section 12 |

**Unconditional semantic reproduction** is also reported: the share of all attempts with the same outcome class, requirement ids and committed state, whatever PostgreSQL selected.

**Confirmation policy.** For each defect, arm and replicate, the one candidate is the first run in discovery order that violates the target requirement. Attempts run one at a time, and stop at the third comparable attempt or at 125 attempts in total, whichever comes first. Inconclusive attempts count toward the 125. The candidate's result is:

- **Reproduced:** the first 3 comparable attempts are all conditional semantic or exact.
- **Failed reproduction:** any of the first 3 comparable attempts is failed.
- **Rare outcome not encountered:** fewer than 3 comparable attempts in 125, and none inconclusive. The report states this explicitly, with the number of comparable attempts seen.
- **Inconclusive reproduction:** fewer than 3 comparable attempts in 125, with at least one inconclusive.

Only "Reproduced" can count toward detection. The other three are "unstable" for section 12, and the report names which. A failed or unconfirmed candidate gets no second candidate and no further attempts.

**Fixed limit.** 125 attempts is the same for every candidate, arm and application, is never extended, and isn't adapted to any result. It was chosen on synthetic measurements only: it gives a 95.2% chance of 3 comparable attempts for an outcome PostgreSQL selects 5% of the time. The chance is 45.7% at 2% and 13.1% at 1%, so rarer outcomes will often be reported as not encountered. Rates on real applications are unknown.

**No arm is favoured.** Every arm, including R, gets the same candidate rule, attempt limit, run limits and stopping rule. Replay sends no model requests, and uses only a workload saved during discovery.

**Reporting and cost.** Every attempt is reported, including its class, signature, outcome and nonmatching victim choices. Reported per candidate and per arm: attempts by class, and execution seconds. These are separate from the discovery budgets B and T, don't consume them, and aren't part of time to detection.

**Independent oracle validation.** A reproduced violation counts only if:

1. The independent oracle of section 5, reading committed state through a separate plain connection, reports the violation in each of the 3 matching attempts. The tool's own trace is not enough.
2. The oracle author reviews it and confirms the requirement was actually breached, rather than an artefact of the harness. Where the path is plain SQL or plain psycopg, the author also reproduces it on uninstrumented connections, using up to the same 125 attempts if it depends on a PostgreSQL-selected outcome. If that isn't confirmed, it isn't a detection, and the reason is logged.

**Detection** requires all of:

1. The oracle reports a violation on the pre-fix version.
2. The candidate's result is "Reproduced", and it passes independent oracle validation.
3. The same workload doesn't violate that requirement on the fix. The report states whether the candidate's server-choice signature occurred on the fix. If it never did, the absence of a violation is labelled "server choice not observed on fix".

A violation of a different requirement is an "other finding", which isn't a detection and goes to triage. Written descriptions of a defect in a model's text are never detection.

**Claims.** A result is described as "reproduced conditional on PostgreSQL's choices". The rate of exact attempts is reported descriptively. No result may be called exact or deterministic replay on the basis of a conditional reproduction, and a strict replay that merely completes is never evidence of reproduction.

**No retrospective changes.** The primary hypotheses, the 3-attempt requirement, the 125-attempt limit, the signature definition, the candidate rule and the scoring thresholds are fixed before any evaluation result. Any later change follows section 14, is labelled exploratory if results have been seen, and can't alter a primary analysis.
````
