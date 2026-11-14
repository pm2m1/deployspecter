# Three-application human onboarding study for r2 #30 (onb-study/2; DRAFT protocol; NOT RUN; nobody recruited)

**Status.**
- This is the study design that revision candidate r2 proposes under #30 (option A). r2 is PROPOSED, unsigned and
  unregistered, so this protocol is a draft too.
- It revises `PROTOCOL_r1_3APP.md` (unchanged, kept as a record) and replaces the 2-application package
  `onb-study/1` (unchanged, still pinned by its own `REPRODUCIBILITY_MANIFEST.json`) for RA-Q4.
- **Implemented since r1, on synthetic data only:** the six- and twelve-sequence allocation, Muse call-duration
  logging, end-to-end and execution-only timing, and the analysis. No participant has been recruited or simulated.
- **Still pending, and not the developers' to do:** consent, ethics or owner review, a named data owner, the
  application selection, independent scoring, and the frozen, registered study manifest.

## 1. Applications

- **Number:** three PostgreSQL job-queue libraries, called X, Y and Z here.
- **Eligibility:** never exposed in this project; pinned by sdist sha256; and meeting the onboarding pilots' rule
  (psycopg 3, an OSI licence, a pure-Python wheel, a self-contained sdist, CPython 3.12).
- **Excluded:** saq, taskiq-postgresql, pgtq, postgres-tq, chancy, pgqueuer, procrastinate, and everything listed in
  `handoff/EXTERNAL_RESEARCHER_SETUP.md` §3.
- **Who selects:** the study's data owner or an independent study coordinator, by a written rule fixed before
  recruitment. The developers don't select them.

## 2. Allocation and order balancing

Each participant onboards **two different applications**, one in each condition (D: deterministic and
documentation-assisted; M: the same plus Muse), in two periods.

| Sequence | Period 1 | Period 2 | Mirror | Period 1 | Period 2 |
|---|---|---|---|---|---|
| Q1 | X, D | Y, M | Q7 | Y, D | X, M |
| Q2 | X, M | Y, D | Q8 | Y, M | X, D |
| Q3 | X, D | Z, M | Q9 | Z, D | X, M |
| Q4 | X, M | Z, D | Q10 | Z, M | X, D |
| Q5 | Y, D | Z, M | Q11 | Z, D | Y, M |
| Q6 | Y, M | Z, D | Q12 | Z, M | Y, D |

- **Design `min18`:** Q1–Q6, randomized blocks of 6, at least 3 per sequence, so **at least 18 participants**. Each
  application appears equally often under D and M, and each condition equally often in each period. Application
  order within a pair is fixed (X always before Y and Z).
- **Design `pref24` (preferred):** Q1–Q12, randomized blocks of 12, at least 2 per sequence, so **at least 24
  participants**. Application × period is balanced too.
- **Assignment:** `study3.py allocate --design min18|pref24 --seed SEED`, from a seed recorded in the frozen manifest
  before recruitment. An allocation file is never overwritten. The design is fixed in the manifest, not chosen
  after any session.
- **Checked by tests:** `balance` counts application × condition, condition × period and application × period for
  each design.

## 3. Washout and contamination safeguards

- **A calibration task before period 1,** on an already-exposed practice library (saq). It is the same for everyone,
  unscored, and teaches the harness, so harness learning doesn't favour period 2.
- **Fresh checkouts:** a fresh worktree per period, with no earlier adapter for the study applications.
- **Muse is locked out in D:** `pilot_v2.py muse` refuses outside condition M (tested).
- **No sharing:** no code is copied between periods or participants, there is no shared scratch space, and
  participants agree not to discuss the task until the study ends.
- **No outside help:** outside AI tools and the libraries' web documentation are not allowed (as in `onb-study/1`).
- **Deviations:** sessions that don't match the participant's allocated sequence (application, condition and
  period) are counted as protocol deviations and excluded from the paired analysis.

## 4. Muse consultation and control checkpoint

- **CHECKPOINT A:** after the public documentation (`pkginfo`), and before any source reading or code.
- **In M:** the participant must call `pilot_v2.py muse` once, read the response, and record accepted, modified or
  rejected with a reason. Adoption is never forced; up to 2 further calls are optional, within the frozen call and
  dollar budget.
- **In D:** the participant records a written plan at the same point, with no model.

## 5. Timing (implemented)

- **Harness:** `research_tools/onboarding/pilot_v2.py`, which is `pilot.py` unchanged except that `muse` records
  `call_started_t` and `call_seconds` (a monotonic duration) on each `model_call` event, failed calls included.
- **End-to-end** (primary): the recorder's clock from `start` to S3.
- **Execution-only** (secondary): end-to-end minus the logged durations of the Muse calls made before S3. If any such
  call has no logged duration, execution-only time is unavailable for that session, never imputed.
- **Censoring:** unmet milestones are censored at the session budget (5,400 s).
- **Also recorded:** failed attempts, interventions, adapter lines (final and revised), Muse calls, tokens and
  estimated USD, and S2/S3 verification outcomes.

## 6. Independent blind scoring

- **Scorers:** two scorers, who are neither participants nor developers, score each session from a blind bundle
  (`study3.py bundle`, onb-study/1's bundle with the condition, application, operator and every Muse trace
  removed).
- **What they score:** S3 correctness and adapter quality against a frozen rubric.
- **Agreement:** a third scorer adjudicates disagreements, and κ is reported.

## 7. Analysis (implemented: `participant_study_3app.analyze`)

- **Standardization:** each session's time is divided by its application's median time over all sessions, with
  censored values at the budget, because each participant's two sessions are on different applications.
- **Primary:** an exact two-sided sign test on the standardized M − D differences.
  - Both sessions censored: a tie, excluded and counted.
  - One censored: counted only if the censored session's lower bound is at least the other's value (the censored
    session is then slower); otherwise undetermined, excluded and counted.
- **Secondary:** the same on execution-only time; descriptive medians; model cost per condition.
- **Sample rule:** below the design's minimum (18 or 24), or with any sequence below its per-sequence minimum (3 or
  2), the verdict is INSUFFICIENT SAMPLE and the results are descriptive only.
- **Claim rule (RA-Q4), descriptive:** a median reduction of at least 30% in both minutes and adapter lines on every
  one of the three applications, with no lost detection (assessed separately).
- **Sample size:** `SAMPLE_SIZE_r2.md`. With 18 determined pairs, 80% power needs about 82% of participants
  favouring one condition; with 24, about 80%. A null result is not evidence of no effect.

## 8. Recruitment, consent, retention and ethics (pending; not the developers' to do)

- **Recruitment target:** 24 (`pref24`), minimum 18 (`min18`); the owner fixes the design in the manifest.
  - **Eligible:** independent developers familiar with Python and PostgreSQL. Never DeploySpecter contributors, and
    never anyone with access to the confirmatory materials.
  - **Declaration:** each completes `handoff/templates/participation_declaration.md`.
- **Consent** (`CONSENT_AND_CONFIDENTIALITY.md`): what is recorded (event logs, adapters, timings); pseudonymous study
  ids only, never names in any tool; the right to withdraw; no payment implied unless the owner sets one.
- **Data:** a named data owner; retention for a fixed period (to be set, for example 12 months after publication),
  then deletion of identifiable records; only pseudonymized, aggregate results are published.
- **Ethics:** the participants' institutional review board where one applies, otherwise a documented owner review of
  risks, consent and data handling, before recruitment.

## 9. Before the study can start

1. **The owner** adopts a candidate whose #30 is option A (r2 proposes it), and approves this protocol.
2. **Governance:** the ethics or owner review, the named data owner and the consent form.
3. **The applications** are selected by the written rule.
4. **The manifest** is frozen and registered: applications, budgets, design (`min18` or `pref24`), seed, code hashes
   (`pilot_v2.py`, `study3.py`, `participant_study_3app.py`, `session.py`, and the harness files) and participant
   count. onb-study/1's manifest pins an older `research_tools/isolated/smoke_inside.py` (it changed additively on
   2026-11-08 for ds-nbc/1, after that manifest), so the onb-study/2 manifest must pin the current file.
5. **Then** recruitment, sessions, blind scoring, and analysis with `study3.py analyze`.
