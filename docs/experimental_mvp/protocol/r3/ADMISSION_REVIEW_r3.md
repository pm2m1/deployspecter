# Review of r3's admission clarifications against the implementation (2026-11-15)

**Candidate:** `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`, sha256
`af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`, **unchanged by this review**. It remains
PROPOSED, unsigned and unregistered. No r4 was made.

**Implementation:** `research_tools/confirmatory_prep/prep_r3.py` (development-only), tested in
`tests/experimental/test_confirmatory_prep_r3_admission_offline.py`. This review added two reporting fields to the
tooling (`requests.transmitted` and `slot_plan`), and 3 tests. They change no admission decision.

## C1–C4: consistent with the text

| | r3 text (§4, "Proposal admission") | Implementation | Tests |
|---|---|---|---|
| **C1** repetition | step 7: with 1 to K − 1 admitted workloads, the remaining slots repeat admitted ones with their own seeds, counted as filled by repetition; a slot is unfilled only when nothing is admitted | `prep.slots` (slot k runs entry k mod n); `slots.filled_by_repetition`; **`slot_plan`** names each slot's workload and, for a repetition, the slot it repeats; diversity is `proposals.distinct_valid`, `proposals.admitted` and the executed count (`executed_counts`) | fewer than K, slot plan, all invalid |
| **C2** failed responses | step 1: a failed, timed-out, refused or model-mismatched request is a failed request; step 3: proposals in a failed reply count as generated and never valid ("provider failure") | `reply_record`, and the reason `provider failure` in `invalid_by_reason` | missing and failed requests |
| **C3** planned, attempted, transmitted | step 1: exactly R attempted; a pre-send refusal is a failed request that counts against R | `requests.planned` (R), `attempted` (indexes recorded), **`transmitted`** (not missing and not refused before sending), `failed`, `by_status` | planned, attempted and transmitted |
| **C4** deduplication | step 5: after full validation (step 4), by the sha256 of canonical JSON | `prep.sha(prep.canonical(...))` on proposals that passed the shared validator, including frozen eligibility rules; an ineligible proposal never reaches deduplication | canonical JSON after full validation, cross-request duplicates |

## Items that need reviewer confirmation (not resolved by the developers)

These are substantive enough to confirm, but none changes K × S budgets, scoring or which workloads run.

1. **An unparseable reply.** The provider answers, but the reply isn't JSON or has no proposal list. The tooling
   records it as a failed request (`unparseable_reply`), with no proposals generated. r3's step 1 doesn't list this
   case among failed requests. Another reading counts it as a successful request with zero proposals. Either
   reading admits nothing from it, and the validity denominator is the same. Only the failed-request count
   differs. **Reviewers confirm which reading applies.**
2. **What "canonical JSON" means exactly.** The implementation's form is Python's `json.dumps` with sorted keys,
   compact separators and no ASCII escaping. It is deterministic, but it is not RFC 8785: `1` and `1.0` are
   distinct, Unicode strings aren't normalized, and Python's parser accepts `NaN`. For the integer, boolean and
   enum schemas used so far this makes no difference. **Reviewers confirm that the implementation's definition,
   frozen by the code's hash, is the one that applies**, or ask for a stricter one before the freeze.
3. **A missing request.** A planned request index with no record at all breaks step 1 ("exactly R requests are
   attempted"). The tooling counts it as a failed request, and the arm proceeds with what it has. r3 doesn't say
   whether a missing request is instead an infrastructure failure that excludes the case for every arm, as the
   time rule's safety ceiling does. **Reviewers decide.** Recommended: an infrastructure failure, because a missing
   record means the journal is incomplete.

**Noted, not an ambiguity:** the second request is always sent, even when the first already gives K distinct valid
workloads. Its proposals are then counted as duplicates or overflow and never run, and their cost is spent. This
follows from rule 1 and keeps the request schedule independent of outcomes.
