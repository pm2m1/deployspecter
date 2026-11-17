# Progress: the preregistered single-investigator evaluation (https://osf.io/34ytz)

Label for everything: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

## Where it stands

| Stage | Status |
|---|---|
| 0. Preflight | **done** 2026-11-17: FREEZE.json `6fb0a189…aeced47` matches a rebuild; REGISTRATION.json accepted; sealed seed hashes match; Docker 29.7.2; Python 3.12.14; running from `research/benchmark-prep` d56ad87. No Muse health check exists in the frozen tooling, so no model call was made. |
| 1. Case selection | **in progress**: 20 mirrors in `~/ds-mirrors` (PostHog built stepwise, DEVIATIONS D1). `select` stops (exit 4) at each S4 application in the seeded order. Verdicts so far (`solo/S4.json`): TandoorRecipes/recipes **pass**, getsentry/sentry **fail**, pretix/pretix **pass**; zulip/zulip check running (`solo/s4/zulip__zulip/PLAN.md`). No case admitted yet; `selection_1` not yet written. |
| Gate 1 | owner's "continue" needed |
| 2. Integrations | not started |
| 3. Mutants | not started |
| 4. Arms | not started |
| 5. Decode and analyse | not started |

Muse spend so far: $0.00 of the $100 cap.

## Resume

Every stage starts with:
```sh
cd "<checkout of research/benchmark-prep>"
.venv/bin/python research_tools/solo/build_freeze.py --check     # must print "FREEZE.json matches a rebuild"
```
Stage 1 (RUNBOOK §2):
```sh
.venv/bin/python research_tools/solo/select_cases.py fetch  --mirrors ~/ds-mirrors
.venv/bin/python research_tools/solo/select_cases.py select --mirrors ~/ds-mirrors \
    --registration docs/experimental_mvp/osf/REGISTRATION.json --out docs/experimental_mvp/solo/selection_<n> \
    [--s4-verdicts docs/experimental_mvp/solo/S4.json] [--refused docs/experimental_mvp/solo/REFUSED.json]
```
Use the next unused `selection_<n>`; never overwrite an earlier one. A stop at exit 4 writes nothing.

Current resume point: if `solo/S4.json` lacks `zulip/zulip`, run that check per its committed
`solo/s4/zulip__zulip/PLAN.md`, record `LOG.md` and the verdict in `S4.json`, commit, then:
```sh
.venv/bin/python research_tools/solo/select_cases.py select --mirrors ~/ds-mirrors \
    --registration docs/experimental_mvp/osf/REGISTRATION.json --out docs/experimental_mvp/solo/selection_1 \
    --s4-verdicts docs/experimental_mvp/solo/S4.json
```
Each S4 check: write `solo/s4/<owner>__<repo>/PLAN.md` (commands and test set) and commit it **before** running.
