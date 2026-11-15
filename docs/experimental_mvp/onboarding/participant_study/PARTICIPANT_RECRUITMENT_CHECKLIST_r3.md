# Onboarding-study participant recruitment checklist (onb-study/2, design `pref24`; NOT STARTED)

**Status.** For the owner and the study's data owner. Nobody has been recruited, contacted or simulated, and no
application has been chosen. The protocol is `PROTOCOL_r2_3APP.md` (RA-Q4 under #30 option A, unchanged in r3); the
sample-size justification is `SAMPLE_SIZE_r2.md`. Every box below is unticked.

## A. Governance, before any recruitment

- [ ] **Owner** adopts a candidate whose #30 is option A (r3 proposes it) and approves the study protocol.
- [ ] **Data owner** named in writing: holds the identifiable records, the allocation key and the blind-scoring key.
- [ ] **Retention** fixed (for example 12 months after publication), then deletion of identifiable records; only
      pseudonymized, aggregate results published.
- [ ] **Ethics:** the participants' institutional review board where one applies; otherwise a documented owner
      review of risks, consent and data handling. Its date and outcome recorded.
- [ ] **Consent form** finalized from `CONSENT_AND_CONFIDENTIALITY.md`: what is recorded (event logs, adapters,
      timings), pseudonymous ids only, the right to withdraw, payment (only if the owner sets one).
- [ ] **Private tracker** kept outside the repository (or in the git-ignored `handoff/private/`): names, contacts,
      consent dates. Never committed.

## B. Three applications, chosen before recruitment

- [ ] An **independent study coordinator** (not a developer) is named.
- [ ] The coordinator writes the **selection rule** before looking at candidates: PostgreSQL job-queue library,
      psycopg 3, OSI licence, pure-Python wheel, self-contained sdist, CPython 3.12, never exposed in this project.
- [ ] **Excluded:** saq (it is the calibration task), taskiq-postgresql, pgtq, postgres-tq, chancy, pgqueuer,
      procrastinate, and everything in `handoff/EXTERNAL_RESEARCHER_SETUP.md` §3.
- [ ] Three applications X, Y, Z chosen by that rule and pinned by sdist sha256; the developers learn them only when
      the manifest is frozen.

## C. Study manifest, frozen and registered before recruitment

- [ ] Design `pref24` (12 sequences × 2) recorded; `min18` stated as the fallback minimum only if the owner prefers.
- [ ] Allocation seed recorded; `study3.py allocate --design pref24 --seed SEED` run once (never overwritten).
- [ ] Budgets (session 5,400 s; Muse calls and dollars) and code hashes (`pilot_v2.py`, `study3.py`,
      `participant_study_3app.py`, `session.py`, the harness, and the **current** `smoke_inside.py`) recorded.
- [ ] The manifest's hash registered with a trusted timestamp, with the owner's approval.

## D. Recruiting 24 participants

- [ ] **Eligible:** independent developers familiar with Python and PostgreSQL.
- [ ] **Not eligible:** DeploySpecter or Muse contributors; anyone with access to the confirmatory materials; anyone
      in one of the seven confirmatory roles or a blind scorer; maintainers of X, Y or Z.
- [ ] Each completes `handoff/templates/participation_declaration.md` and signs the consent form before any session.
- [ ] Each gets a pseudonymous study id (never a name in any tool) and is assigned the next sequence in enrolment
      order.
- [ ] Target 24 completed pairs (at least 2 per sequence). Below 24 the `pref24` verdict is INSUFFICIENT SAMPLE.

## E. Running sessions

- [ ] Calibration task (saq) first, unscored, identical for everyone.
- [ ] Fresh worktree per period; no adapter for X, Y or Z present.
- [ ] Sessions recorded with `pilot_v2.py` (`--operator-kind independent`, `--operator` = study id), which logs
      Muse call durations.
- [ ] Muse only in condition M, at CHECKPOINT A; the written-plan checkpoint in D.
- [ ] No outside AI tools, no library web documentation, no discussion between participants until the study ends.
- [ ] Deviations recorded as they happen, never corrected afterwards.

## F. Blind scoring

- [ ] Two scorers and an adjudicating scorer named; none is a participant, a developer or the coordinator.
- [ ] `study3.py bundle` makes the blind bundle; the key stays with the data owner.
- [ ] The scoring rubric is frozen before the first bundle is sent.
- [ ] Each scorer scores independently; the third adjudicates disagreements; κ is reported.

## G. Analysis and reporting

- [ ] `study3.py analyze` on the frozen allocation; verdict reported exactly as produced.
- [ ] Results reported pseudonymized and aggregated; a null result is not evidence of no effect.
- [ ] Identifiable records deleted at the end of the retention period, and the deletion recorded.
