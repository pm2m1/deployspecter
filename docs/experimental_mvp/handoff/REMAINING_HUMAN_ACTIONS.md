# Remaining human actions for the independent evaluation (2026-11-08)

**Nothing here is done.** No person has been named, nothing is signed, Amendment 003 is not adopted or
registered, and real confirmatory execution stays locked. The work of the ds-nbc/1 sprint (NONBLIND corpus,
RA-NONBLIND paired runs, AI-operator onboarding) is developer evidence. It does not replace any step below.

## Verified state (2026-11-08)

| Item | State | Check |
|---|---|---|
| Amendment 003 adoption candidate | sha256 `ad5ea33111ac56e1…` matches the recorded hash; 0 integrity problems | `research_tools/verify_amendment.py` (exit 0) |
| Decisions | 24 unresolved (#1–#22, A4, A5); every one PROPOSED | the same check; `protocol/APPROVAL_DASHBOARD.md` (`dashboard.py --check`, exit 0) |
| Approval forms | 4 forms, every one unsigned with nothing ticked; all 5 approval documents cite the candidate hash | `protocol/approval/` |
| Handoff bundles | curator v3: 16 files match; oracle author and reviewer v3: 14 files match | `shasum -a 256 -c handoff/send/SHA256SUMS_{curator,oracle}_v3` |
| Confirmatory runner | real mode refuses, exit 3, 29 reasons | `research_tools/confirmatory/run.py real` |
| Participant onboarding study package | 27 pinned files, all matching; NOT RUN | `onboarding/participant_study/REPRODUCIBILITY_MANIFEST.json` |
| Frozen Muse model and prompt manifest (plan §4) | not frozen at adoption; a confirmatory Muse arm refuses without it | `real_executor.Executor.preflight` |

## Actions, in order

1. **Name the people** (each independent of the developers, with a completed COI declaration):
   - one curator;
   - one oracle author and one **different** oracle reviewer;
   - two triage reviewers and one adjudicator.

   Each fills in and signs their own form: `CURATOR_ACCEPTANCE.md`, `ORACLE_REVIEWER_ACCEPTANCE.md` (two rows) and
   `TRIAGE_REVIEWERS_ACCEPTANCE.md`. The developers must not fill these in on anyone's behalf.
2. **The owner signs `OWNER_APPROVAL.md`,** ruling on each of the 24 decisions (ACCEPT or REJECT, never left
   PROPOSED).
3. **Adoption:** commit the candidate byte-identically next to the frozen protocol. Then register it prospectively
   with a trusted timestamp before any confirmatory run. That registration is the owner's decision; nothing has
   been registered or published as a registration.
4. **Plan §4 manifest:** at adoption, re-hash the Muse model, settings, prompt and context builders, and commit the
   result.
5. **The curator selects and seals cases** through `research_tools/confirmatory/intake_v2.py`, on accounts or
   machines separate from the evaluator.
   - **Not eligible:** any application exposed in this project, including chancy, pgqueuer, procrastinate, saq,
     taskiq-postgresql, pgtq and postgres-tq, and the held-out set.
   - **Who selects:** the curator alone; the developers do not.
6. **Independent oracles:** the oracle author writes each oracle from documentation, and the reviewer validates
   it on the fixed version before it is approved.
7. **A resource and retry policy file**, signed by the owner. It is one of the 29 refusal reasons today.
8. **Reviewed integrations** for every application the curator picks that has none (today only
   `chancy-sv/1` and the ds-nbc/1 development integrations exist).
9. **Run and reveal:** the evaluator runs `run.py real`. The owner then writes the reveal record for
   `real-authorize`, and scoring runs only after that.
10. **Onboarding study:**
    - recruit independent participants (at least 5 per arm before any verdict);
    - name a data owner and a retention period;
    - complete an ethics or owner review and a consent form;
    - choose two applications never exposed here;
    - freeze and register the study manifest;
    - a scorer who doesn't know each participant's condition scores the sessions.
11. **Optional:** approve the Gate 2B live-stepping cluster, so the 163 root tests that need it can run.
