# Track A: replay signature and detection claims (milestone 4)

- **Status:** nonnormative.
- **Source:** the committed `EVALUATION_PROTOCOL_AMENDMENT_002.md`, read as committed.

## What amendment 002 requires

- **The server-choice signature** lists only PostgreSQL-selected outcomes:
    - "for each deadlock or serialization failure, the actor and its step";
    - "for each lock wakeup, which waiter completed under which decision".

  It "contains no result rows, requirement ids or committed state".
- **Scheduler decisions** are replayed strictly. An attempt is **failed** when "the saved decisions couldn't be followed
  before any signature difference appeared".
- **Attempt classes:**
    - exact (same signature and fingerprint);
    - conditional semantic (same signature, outcome, requirement ids and state);
    - not comparable (different signature);
    - failed;
    - inconclusive.
- **Stopping rule:** stop at the third comparable attempt, or at 125 attempts in total.
- **Detection** additionally needs independent oracle validation in each of the 3 matching attempts, and the oracle
  author's review. Where the path is plain psycopg, it also needs a reproduction on uninstrumented connections, using up
  to 125 attempts if the result depends on a PostgreSQL choice.

## Is the signature discriminating enough?

- **Buggy lost update.**
    - The buggy version takes no locks and raises no deadlock or serialization failure, so PostgreSQL makes **no**
      choice. **An empty signature is the correct 002 signature, not a defect.**
    - Discrimination comes from the rest of 002's test: the strict schedule replay, plus the outcome class, requirement
      ids and canonical committed state, which must all match the candidate.
    - **A mere repeated oracle violation from a different interleaving can't pass.** It would be failed (state or
      decisions differ), or would never be attempted, because replay is strict.
- **Fixed version.** With `FOR UPDATE` there is one waiter, and PostgreSQL has no choice among waiters, so nothing belongs
  in the signature. With two or more waiters there would be a choice.
- **Changes made, offline:**
    - **Wakeups now record which waiters were contending** as well as which completed. Two orders over different waiter
      sets can't then look alike.
    - **The A2 driver sets `followed` from the trace,** as `outcome != "schedule_divergence"`, instead of assuming it.
      Before this change, divergence was still classified as failed, through the outcome mismatch.
- **Minimum adequate signature for this scenario:**
    - deadlock and serialization failures by actor and step;
    - contended wakeups, recording who woke, against whom and at which decision.

  It is still a candidate. Amendment 002 requires the signature code to be frozen with the tool, after review.

## Independent confirmation checks

| Check | Status |
|---|---|
| Independent oracle on every attempt (op6f, separate tagged session, equal to the parent read) | Implemented offline; needs execution |
| Oracle author's review that R1 or R2 was actually breached | **Manual; not automated** |
| Uninstrumented reproduction: two plain psycopg connections stepped by a hand-written script, not the tool, up to 125 attempts | **Not built.** A new operation, needing approval, and outside Gate A1 |
| "Server choice not observed on fix" (002 criterion 3) | Computable from the fixed runs' records; no report field yet |

## Detection claims

- **The verdict is separate.** `acceptance.detection_status(bundle)` returns
  `detection_confirmed_under_amendment_002` only when all the gates pass **and** both manual confirmations are recorded:
    - an oracle-author review, with a reviewer named and `confirmed: true`;
    - an uninstrumented reproduction with `reproduced: true` in 1 to 125 attempts.
- **Otherwise** it returns `automated_characterization_only` or `not_established`.
- **The first synthetic run may be reported as automated characterization evidence only.**
