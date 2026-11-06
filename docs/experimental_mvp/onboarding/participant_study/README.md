# Onboarding study with independent participants (onb-study/1): **NOT RUN**

No participant has been recruited or contacted, and no participation record exists. This directory is an
executable package, ready for when real people are available. The developer-only pilot is reported separately
(`../pilot_1/RESULTS.md`, NONBLIND EXPLORATORY).

| File | Purpose |
|---|---|
| `PARTICIPANT_INSTRUCTIONS.md` | What a participant does, the success criteria, and how their actions are recorded |
| `ENVIRONMENT_SETUP.md` | The machine, the checkout, the disposable PostgreSQL, the pinned sdists |
| `ALLOWED_TOOLS_AND_BUDGETS.md` | Equal resources per condition; Muse only in M; the budgets |
| `SCORING_PROCEDURE.md` | Evaluator-blind re-derivation of S2 and S3 |
| `CONSENT_AND_CONFIDENTIALITY.md` | Consent and confidentiality text (placeholders; needs the owner's or an ethics review) |
| `REPRODUCIBILITY_MANIFEST.json` | sha256 of every file the study uses, at the commit that prepared it |

**Code** (tests: `tests/experimental/test_onboarding_pilot_participant_offline.py`, synthetic data only):
- `onboarding_pilot/participant_study.py`: the Latin-square allocation, blind bundles and the predeclared analysis.
- `research_tools/onboarding/study.py`: the CLI.
- `research_tools/onboarding/pilot.py`: the measurement collector.

## Before running it (human actions)
1. **Choose two pinned apps A and B,** eligible by the pilot manifest's rules, and **new to every participant and to
   the repository.** saq and taskiq-postgresql now have adapters here, so they can't be used unless their adapters
   are removed from the participants' checkout.
2. **Freeze a study manifest:** the apps, budgets, allocation seed, analysis code hash, and the participant count
   target (at least 12, at least 3 per sequence). Register it before recruitment.
3. **Recruit** independent participants, who must not be DeploySpecter developers. Each one completes the
   declaration (`../../handoff/templates/participation_declaration.md`) and gives consent.
4. **Allocate** with `study.py allocate`, then run the sessions, then `study.py bundle`, then have an independent
   scorer score them, then `study.py analyze`.
