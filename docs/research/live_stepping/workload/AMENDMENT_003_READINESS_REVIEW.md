# Amendment 003: protocol-readiness review

- **Status:** nonnormative research review of `AMENDMENT_003_DRAFT.md` (revision 2), which stays **DRAFT, NOT
  ADOPTED**. Nothing here changes the protocol, adopts a decision or authorizes any action. Its recommendations are
  advisory and may be revised by later decision work.
- **Recommendation: NOT READY for adoption.** Section 9 lists the decisions and fixes it needs first.
- **Read exactly as committed:**
    - `EVALUATION_PROTOCOL.md` (v1);
    - `EVALUATION_PROTOCOL_AMENDMENT_001.md`, which makes v1.1;
    - `EVALUATION_PROTOCOL_AMENDMENT_002.md`, which makes v1.2.

  Their SHA-256 prefixes are `31d82afd…`, `28add0e8…` and `c3e939bd…`, and they're unchanged.
- **Results so far:** no evaluation arm has run, and no held-out result exists. For the held-out applications, only
  what protocol section 3.4 records has been seen (names, versions, commits, repository metadata and dependency
  lines); no source, issue, merge request or commit. This review rescores and reinterprets no evidence.

## 1. Original arms and their frozen semantics

| Arm | Frozen text (section 7) | What v1.2 fixes | What v1.2 leaves unspecified |
|---|---|---|---|
| R | Statement replay of SQL from a plain serial run of E's workloads; starting state "E's goals" | Replay; E's workloads | How E's goals become a state |
| A | The application's own tests; "Its fixtures" | Fixtures | — |
| B | Schema- and signature-derived workloads; "schema-derived boundary values" | Boundary values | — |
| C | Muse proposals; "Muse Spark seed goals, resolved literally, with no search. An infeasible goal fails and is counted" | No search; infeasible goals counted | **What "resolved literally" means for logical goals** |
| D | Muse proposals; "resolved by Phase 9B's deterministic constructor" | Phase 9B's constructor | How goals become the constructor's seed rows; constructor limits; which constructor classes count |
| E | Deterministic workloads; "Phase 9B constructor over deterministic goals" | The constructor | Where deterministic goals come from; the same gaps as D |
| F | E's workloads; Muse seed goals with the constructor | — | The same gaps as D; **whether F's goals are the same responses' goals as C and D** |
| G | Muse proposals; E's deterministic goals with the constructor | — | The same gaps as D and E |

**Rules section 7 also fixes:**

- arms can't see requirements, oracles, labels or schedules;
- E is frozen before any held-out Muse output;
- Phase 9B code comes in only through a separately approved integration step;
- prompts are tuned only on development applications;
- each paid run needs approval.

**Its ablation sentence** reads "A vs E isolates the deterministic additions, C vs D the constructor, and E vs R live
execution itself." It shares S3's ambiguity (section 3).

## 2. Hypotheses preserved verbatim

- **P1.** "Arm D detects more historical held-out defects than arm E at equal execution budget."
- **P2.** "Arm E detects more historical held-out defects than arm R at equal execution budget."
- **S3.** "D detects more than C (the constructor's contribution, given model goals)."
- **S1, S2 and S4 to S8,** and every statistical rule in section 9 as amended by 001: unchanged.

The amendment must quote these unchanged, and must not add a hypothesis.

## 3. S3's ambiguity, documented

"The constructor's contribution" can mean either of two things:

1. **Phase 9B's construction alone:** reconciliation, order search and verification gates.
2. **Everything between logical goals and an executed state:** value synthesis plus construction.

Under the proposed reading A of arm C, D against C differs in both. No definition of C makes D against C isolate
Phase 9B alone: reading B removes value synthesis but leaves reconciliation, search and the verification gates. So S3
stays as written, its result is reported with this ambiguity stated, and the narrower questions go to exploratory
comparisons (section 5).

## 4. Substantive changes in the draft

None of these is a mere clarification. Each changes what an arm operationally is, or adds something new.

| # | Change | Kind | Affects a primary hypothesis? |
|---|---|---|---|
| 1 | Arm C defined as literal translation, then one strict check in the bridge's order | Defines an undefined term | No, directly. But C is in S3 |
| 2 | Every constructor arm (D, E, F, G, and R's goals) reaches the constructor through the step 5B planner and the 5C.3 bridge | Defines an unspecified pipeline | **Yes, operationally** (P1: D, E; P2: E, R) |
| 3 | Constructor acceptance: only proven classes count; `CONSTRUCTED_WITH_ASSUMPTIONS` is reported separately | New rule | **Yes, operationally** |
| 4 | Equal, operation-counted planning and construction budgets, with wall-clock time only as a safety ceiling | New operational parameters | **Yes, operationally** |
| 5 | A context `MATCH` against a pinned catalog snapshot is required before any arm runs on an application | New precondition | **Yes**: it can block an application for every arm |
| 6 | Bridge refusals (ambiguous values, cycles, more than 10 rows) stop a test from executing | New rule | **Yes, operationally**, for every constructor arm |
| 7 | The 10-row cap: raise it, or count requests above it as blocked | New rule, with a decision open | **Yes, operationally** |
| 8 | New exploratory arm C′ | New exploratory analysis | No |
| 9 | Optional verification gates applied to C and C′ | New rule, with a decision open | No, but it changes C, which is in S3 |
| 10 | Separate counts for each stage, and deterministic-computation accounting | Reporting addition | No |

**On the estimand.** Changes 2 to 7 don't alter P1's or P2's wording or tests. But they fix what "D", "E" and "R's
starting state" operationally *are*, and v1.2 left that open. That's the same kind of change as amendment 002, which
said "Affects a primary hypothesis: yes, through the definition of detection". So amendment 003 must say "yes,
through the operational definition of the starting state". It applies identically to every arm that uses a
constructor, and it must be adopted prospectively, before any held-out arm.

## 5. What each comparison can legitimately establish

| Comparison | Status | Legitimate claim | Not a legitimate claim |
|---|---|---|---|
| D against E (P1), E against R (P2) | Primary | As preregistered, under the operational definitions above | Anything about the planner or constructor in isolation |
| D against C (S3) | Preregistered secondary | The combined effect of value synthesis, reconciliation, order search and verification gates, given Muse responses | "The constructor's contribution" in either narrow sense |
| C against C′ | **Exploratory, new** | The effect of value synthesis, if the strict check, verification gates, order and budgets are identical | Anything about search |
| C′ against D | **Exploratory, new** | Phase 9B's construction beyond one strict check. Reconciliation plus search only if C′ also gets D's verification gates and acceptance classes | **Insertion-order search alone**, because reconciliation still differs and Phase 9B has no reconcile-off, search-on mode |

**Every comparison also differs in its acceptance set.** Different arms accept different tests, so the fixed execution
budget is spread over different tests. Accepted and executable test counts must be reported beside every detection
comparison.

## 6. Accounting requirements (items 11 to 16)

| Requirement | Draft status | Needed |
|---|---|---|
| Proposal attempts, model calls, failed planning attempts and original arm attribution | Partly (draft section 5) | State that every request is logged once under section 8; that C, C′, D, F and G draw on one response pool per defect and replicate if decision O4 says so; that each arm's use of a response is recorded; and that planning failures are attributed to the arm that ran them |
| No oracle-informed state selection | Present (draft section 7) | Keep. Add that bridge order and refusals use only the request and the trusted context |
| Rejected and unsupported proposals stay in accounting | Present (draft section 6) | Map each new outcome onto section 12's categories (O8) |
| Deduplication never reallocates budget | **Missing** | Add: `vplan1` grouping is a reporting view only. It never removes a proposal, gives an arm extra attempts, moves budget between arms or replaces a response. Every proposal counts as generated |
| Preregistered against exploratory | Present (draft section 8) | Keep. Add that C′ results can't be used to confirm S3 |
| No retroactive rescoring | **Missing** | Add: no existing evidence is rescored; held-out material seen when written: none; results seen: none |

## 7. Format gaps against section 14

The draft lacks every required header:

- the number and date;
- the sections it changes;
- the reason;
- whether held-out material or results had been seen;
- whether it affects a primary hypothesis.

It also needs committing on its own, being listed in the final report, and the freeze-limit note: the commit's
timestamp is externally checkable only once published.

## 8. Draft content to keep

From revision 2: the definitions of C, C′ and D; the factor table and the C′ against D limitation; the fixed shared
machinery; budgets and accounting; outcome counts; no oracle-informed choices; analysis tiers; and the
before-evaluation checklist.

## 9. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| O1 | Arm C: literal translation (reading A) or the shared planner (reading B) | **A**, matching "resolved literally" |
| O2 | Include C′ as exploratory | **Yes**. It needs no model calls, only computation |
| O3 | Apply D's verification gates (clean rebuild, repeat, independent check) to C and C′ | **Yes**, so a strict-check state is held to D's standard. It makes C stricter than a bare strict check, so say so |
| O4 | Do C, C′, D, F and G share one pool of Muse responses per defect and replicate? | **Yes**: it pairs the comparisons, and section 8's per-request logging still applies once per request. If not, each arm's responses are independent and C′ uses C's |
| O5 | Construction budget values: `max_operations`, `max_candidates`, `max_rebuilds`, safety ceiling | Set on development applications and frozen with B and T |
| O6 | Planning budget values | The same |
| O7 | The 10-row cap: raise it with the budget, or count "construction blocked by constructor limit" | Count it as blocked for the first evaluation. Raising it is a separate Phase 9B change |
| O8 | Mapping new outcomes onto section 12 | Planner and translator `unsupported` and bridge refusals become "blocked by an unsupported path"; `infeasible_under_assumptions` becomes "seed goal infeasible"; `indeterminate` and `construction_timeout` become inconclusive. Each is also reported by code |
| O9 | `CONSTRUCTED_WITH_ASSUMPTIONS` | Never counted as proven. Reported separately, with a sensitivity analysis that admits it |
| O10 | E's deterministic goal source, key and filler policies | Frozen with E, before any held-out Muse output (an existing section 7 rule) |
| O11 | An application whose context can't reach `MATCH` | Blocked for every arm, kept in the denominator, and reported |
| O12 | Deduplication | A reporting view only (section 6) |
| O13 | The section 14 headers, standalone commit, publication timestamp | Required |
| O14 | Reporting S3 and the ablation sentence | Quote both verbatim, state the ambiguity, and point to the exploratory comparisons |

## 10. Recommended final text, once decisions are made

The block below is a proposal written for review. It is not in effect, and its "Status" line would apply only to a
separately adopted amendment file.

```text
# Amendment 003 to the evaluation protocol

Status: in effect from the Git commit that adds this file. Applied after amendment 002, it changes protocol
version 1.2 to version 1.3. The frozen file itself is never edited.

- Number and date: 003, <date of adoption>.
- Sections changed: 7 (arm C's starting state; how every constructor arm's starting state is produced; a new
  exploratory arm C′), 8 (planning and construction budgets; deterministic-computation accounting), 9 (coverage
  counts), 12 (outcome mapping).
- Reason: v1.2 doesn't say how logical seed goals become rows, how construction is limited, or which constructor
  results count, so arms C to G and R's starting state can't run as written.
- Held-out material or results seen when written: no held-out results, source, issues or commits; only the
  section 3.4 metadata. No evaluation arm has run.
- Affects a primary hypothesis: yes, through the operational definition of the starting state for D, E and R. P1,
  P2, their tests and thresholds don't change. The definitions apply identically to every arm that uses them.

1. Arm C. Seed goals are translated literally: values come only from goal eq literals and is_null, with no key,
   filler or comparison value, and no search. The rows are checked once by Phase 9B's strict check, in the bridge's
   dependency order, with the shared verification gates. Unsupported and infeasible goals are counted.
2. Arms D, E, F, G and R's starting state. Goals pass through the frozen deterministic planner and the typed bridge,
   then Phase 9B's constructor. Only ORIGINAL_VALID, RECONCILED_EQUIVALENT and CONSTRUCTED_EQUIVALENT count;
   CONSTRUCTED_WITH_ASSUMPTIONS is reported separately and never as proven.
3. C′, exploratory, not preregistered: the planner and bridge, then the same single strict check and gates as C,
   using C's responses. Its comparisons are reported as exploratory and can't confirm any hypothesis.
4. S3 and the section 7 ablation sentence are unchanged. Their result is reported with the statement that D against
   C combines value synthesis, reconciliation, order search and verification gates. C′ against D isn't a measure of
   order search alone.
5. Shared machinery, frozen and hashed: translator, planner, bridge, catalog verifier and Phase 9B versions; one
   schema context per application with a MATCH against its pinned catalog snapshot. An application without a MATCH
   is blocked for every arm and stays in the denominator.
6. Budgets: B and T as in v1.2. One planning budget and one construction budget, operation-counted, identical for
   every arm that uses them, set on development applications and frozen with B and T. Wall-clock time is a safety
   ceiling only; reaching it is inconclusive.
7. Accounting: every model request is logged once under section 8. <Shared response pool rule, per O4.> Planning,
   bridging and construction computation is reported per arm, defect and replicate. Deduplication by plan identity
   is a reporting view only: it never removes a proposal, reallocates budget or attempts, or replaces a response.
8. Outcomes: unsupported and bridge-refused requests are "blocked by an unsupported path", infeasible goals are
   "seed goal infeasible", indeterminate plans and construction timeouts are inconclusive, and requests above the
   constructor's limit are "construction blocked by constructor limit". Each is also reported by code.
9. Nothing in translation, planning, bridging or construction reads requirements, oracles, labels, schedules or
   fixes. No test is selected retrospectively, and no existing evidence is rescored.
```

## 11. Recommendation

**NOT READY.** To become adoptable, it needs:

1. **Decisions O1 to O4 and O7 to O11,** made by the owner;
2. **O5 and O6 set,** on development applications;
3. **The missing deduplication and no-rescoring rules** added;
4. **The section 14 headers** added, and the text committed on its own.

None of this needs a held-out application, a model request or a database. Adopting it, and running anything it
governs, need separate approval.
