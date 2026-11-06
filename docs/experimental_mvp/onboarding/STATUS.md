# Onboarding study status (2026-11-06)

## Classification: STUDY NOT RUN. The measurement framework is implemented and tested.

A fair prospective comparison of deterministic-assisted and Muse-assisted onboarding needs **independent
participants**: people who didn't build DeploySpecter, onboarding applications they haven't seen, each in a fresh
checkout, with the recorder running. None exist (the same blocker as Amendment 003's roles). Running it with this
project's developer would measure a person who already knows every seam. That is a labelled baseline, not a study.

## Framework (`research_tools/onboarding/onboard.py`, 7 offline tests)

- **Recording.** Each session is a hash-chained, append-only event log written live: start, intervention, attempt,
  generated, model_call, unsupported, first_corroborated_run (its evidence is required), end. Time is the
  recorder's clock. A broken chain, events after `end` or time going backwards are refused.
- **Measured:**
  - wall-clock setup time, and time to the first corroborated run;
  - manual interventions, failed attempts;
  - handwritten adapter and configuration lines: added lines under the declared paths since the base commit,
    untracked files included and generated files excluded;
  - generated code that required correction: an `ndiff` against the copy kept at generation time;
  - model tokens and dollars;
  - unsupported features.
- **`compare`.** It reports per-arm medians over independent, completed sessions. Developer sessions (`--developer`)
  are counted separately and never as participants. Below 5 independent participants per arm, the verdict is
  **INSUFFICIENT SAMPLE**. No significance test is predeclared, because onboarding is descriptive.

Run a session (one per fresh checkout):

    python research_tools/onboarding/onboard.py start --session S --app APP --arm muse --participant P \
        --repo CHECKOUT --paths src/deployspecter/experimental/thirdparty --declaration path/to/declaration.md
    python research_tools/onboarding/onboard.py event --session S intervention --detail "..."
    python research_tools/onboarding/onboard.py event --session S generated --path FILE --detail muse
    python research_tools/onboarding/onboard.py event --session S model_call --tokens-in N --tokens-out N --usd X
    python research_tools/onboarding/onboard.py event --session S attempt --failed --detail "..."
    python research_tools/onboarding/onboard.py event --session S first_corroborated_run --evidence RUN/report.json
    python research_tools/onboarding/onboard.py end --session S
    python research_tools/onboarding/onboard.py compare --sessions S1 S2 ... --out compare.json

Participants complete `handoff/templates/participation_declaration.md` first. A participant who contributed to
DeploySpecter is recorded with `--developer`.

## NONBLIND developer-authored engineering baseline (`developer_baseline.json`, from git history)

Written by this project's developer, an AI coding-assistant session working with the owner. It is **not** an
independent participant, and **neither** onboarding arm: no Muse-assisted onboarding was ever attempted.

| App | Integration files (current lines) | Lines added over history | Commits | Commits recording a correction (keyword proxy) |
|---|---|---|---|---|
| Chancy | adapter 343, properties 78, historical oracles 38 (459) | 498 | 11 | 1 (deferred cancellation, after a held-COMMIT stall) |
| PGQueuer | adapter 133 | 138 | 3 | 0 |
| procrastinate | adapter 231, properties 74, Muse target 148, rolling orchestrator 266 (719) | 736 | 4 | 2 (erratum E1; public-schema teardown reset) |

**Not recoverable from history, so null:** manual interventions, time to the first corroborated run, and
generated-code corrections.

**Commit spans are not setup times:**
- commits were made in batches;
- the counts are lower bounds, since shared harness modules (isolated runner, coordinator) carry app-specific hooks
  that history can't separate;
- the keyword proxy is weak, because subjects describe batches.

Reproduce: `python research_tools/onboarding/baseline.py --out docs/experimental_mvp/onboarding/developer_baseline.json`.
