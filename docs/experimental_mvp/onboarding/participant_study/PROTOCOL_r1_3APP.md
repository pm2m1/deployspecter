# Three-application human onboarding study for r1 #30 (DRAFT protocol; NOT RUN; nobody recruited)

**Status.**
- This drafts option A of proposed decision #30: 3 applications, provisionally 18 participants.
- It revises the existing 2-application package (`onb-study/1`), which stays unchanged as a record.
- Nothing is frozen, registered or approved. No participant has been recruited or simulated.

## 1. Applications
- **Number:** three PostgreSQL job-queue libraries, called X, Y and Z here.
- **Eligibility:** never exposed in this project, pinned by sdist sha256, and satisfying the onboarding pilots'
  eligibility rule (psycopg 3, an OSI licence, a pure-Python wheel, a self-contained sdist, CPython 3.12).
- **Excluded:** saq, taskiq-postgresql, pgtq, postgres-tq, chancy, pgqueuer, procrastinate, and everything listed in
  `handoff/EXTERNAL_RESEARCHER_SETUP.md` §3.
- **Who selects:** the study's data owner or an independent study coordinator, by a written rule fixed before
  recruitment. The developers don't select them.

## 2. Allocation and order balancing
- **Tasks per participant:** **two different applications**, one in each condition (D and M), in two periods. Using
  different applications removes application carry-over.
- **Sequences:** 6 in total, from 3 application pairs × 2 condition orders:

  | Sequence | Period 1 | Period 2 |
  |---|---|---|
  | 1 | X, D | Y, M |
  | 2 | X, M | Y, D |
  | 3 | X, D | Z, M |
  | 4 | X, M | Z, D |
  | 5 | Y, D | Z, M |
  | 6 | Y, M | Z, D |

- **What is balanced:** each application appears equally often under D and under M, and each condition equally
  often first and second.
- **What is not:** application order is fixed within a pair; X always comes before Y and Z. **18 participants** = 6
  sequences × 3 per sequence is the minimum.
- **Preferred: 24 participants.** That adds the 6 mirror sequences with application order reversed, 2 per sequence,
  and fully balances application × period.
- **Assignment:** randomized blocks of 6 (or 12), from a seed recorded in the frozen manifest before recruitment
  (generalizing `participant_study.allocate`).

## 3. Washout and contamination safeguards
- **A calibration task before period 1,** on an already-exposed practice library (saq). It is the same for everyone,
  unscored, and teaches the harness, so harness learning doesn't favour period 2.
- **Fresh checkouts:** a fresh worktree per period, with no earlier adapter for the study applications.
- **Muse is locked out in D:** `pilot.py muse` refuses outside M.
- **No sharing:** no code is copied between periods or participants, there is no shared scratch space, and
  participants agree not to discuss the task until the study ends.
- **No outside help:** outside AI tools and the libraries' web documentation are not allowed (as in `onb-study/1`).

## 4. Muse consultation and control checkpoint
- **CHECKPOINT A:** after the public documentation (`pkginfo`), and before any source reading or code.
- **In M:** the participant must call `pilot.py muse` once, read the response, and record accepted, modified or
  rejected with a reason. Adoption is never forced, and up to 2 further calls are optional.
- **In D:** the participant records a written plan at the same point, with no model.
- This is onb-pilot/3's checkpoint, which was used in 4 of 4 sessions there.

## 5. Timing
- **End-to-end** (primary for onboarding effort): the recorder clock from `start` to S3, minus database-lock waits.
- **Execution-only** (secondary): end-to-end minus the time spent inside Muse calls. **This needs one instrumentation
  change before freezing:** `pilot.py muse` must log each call's start and end time. Today only the call's
  completion is recorded, and `latency_s` is empty.
- **Censoring:** unmet milestones are censored at the session budget (5400 s), never imputed.
- **Also recorded:** failed attempts, interventions, adapter lines (final and revised), Muse calls, tokens and
  estimated USD, and S2/S3 verification outcomes.

## 6. Independent blind scoring
- **Scorers:** two scorers, who are not participants or developers, score each session from a blind bundle
  (`study.py bundle`, with the condition and any Muse text removed).
- **What they score:** S3 correctness and adapter quality against a frozen rubric.
- **Agreement:** a third scorer adjudicates disagreements, and κ is reported.

## 7. Analysis and sample-size justification
- **Within participant:** the M minus D difference in end-to-end seconds to S3, on application-standardized times
  (each application's times divided by its median across all sessions), because each participant does M and D on
  different applications.
- **Test:** an exact two-sided sign test on these differences, the direction of censored pairs being taken where it
  is determined.
- **Power** (two-sided α = 0.05, sign test):

  | Participants | Needed to favour one condition |
  |---|---|
  | 12 | at least 10 of 12 (p = 0.039) |
  | **18** | at least **14 of 18** (p = 0.031) |
  | 24 | at least 18 of 24 (p = 0.023) |

  So 18 participants can detect only a large, consistent effect, about 78% of participants favouring one condition.
  A null result at this size is not evidence of no effect.
- **Claim rule:** unchanged (RA-Q4): at least a 30% median reduction in both minutes and lines across at least 3
  applications, with no lost detection.
- **Below 18 completed participants,** or fewer than 3 per sequence, the verdict is INSUFFICIENT SAMPLE.
- **Code:** `participant_study.analyze` must be updated to this design before the manifest is frozen. Not yet done.

## 8. Recruitment target, consent, retention and ethics
- **Recruitment target:** 24, minimum 18.
  - **Eligible:** independent developers familiar with Python and PostgreSQL. Never DeploySpecter contributors,
    never anyone with access to the confirmatory materials.
  - **Declaration:** each completes `handoff/templates/participation_declaration.md`.
- **Consent** (`CONSENT_AND_CONFIDENTIALITY.md`):
  - what is recorded: event logs, adapters, timings;
  - pseudonymous study ids only, never names in any tool;
  - the right to withdraw;
  - no payment is implied unless the owner sets one.
- **Data:** a named data owner; retention for a fixed period (to be set, for example 12 months after publication),
  then deletion of identifiable records; only pseudonymized, aggregate results are published.
- **Ethics:** the participants' institutional review board where one applies. Otherwise a documented owner review
  of risks, consent and data handling before recruitment.

## 9. Before the study can start
1. **The owner** chooses option A under #30 and approves this protocol.
2. **The applications** are selected by the written rule.
3. **Instrumentation:** the Muse call-duration logging and the 6- or 12-sequence allocation and analysis code, tested
   on synthetic data only.
4. **The manifest** is frozen and registered: applications, budgets, seed, code hashes and participant count.
5. **Governance:** the ethics or owner review, the named data owner and the consent form.
6. **Then** recruitment, sessions and blind scoring.
