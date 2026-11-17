# Progress: the preregistered single-investigator evaluation (https://osf.io/34ytz)

Label for everything: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

## Where it stands

| Stage | Status |
|---|---|
| 0. Preflight | **done** 2026-11-17: FREEZE.json `6fb0a189…aeced47` matches a rebuild; REGISTRATION.json accepted; sealed seed hashes match; Docker 29.7.2; Python 3.12.14; running from `research/benchmark-prep` d56ad87. No Muse health check exists in the frozen tooling, so no model call was made. |
| 1. Case selection | **selection done, specs not started (paused for owner decisions)**: `solo/selection_1` (e8c3b32): 10 admitted from 5 applications in 90 screens (pretix 1, kiwitcms 1, plane 3, wger 2, airflow 3). S4 verdicts (`solo/S4.json`): TandoorRecipes/recipes pass, getsentry/sentry fail, pretix/pretix pass, zulip/zulip fail. Open owner questions before any spec is written: r3 #12 (Django seam development-only; 7 of 10 cases are Django), E2–E6 (not screened by the frozen code), and the final-state convention for blinded integrations. |
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

Current resume point: waiting for the owner's answers on r3 #12, E2–E6 and the final-state convention. Then, for
each admitted case in `solo/selection_1/SELECTION.json`, write `solo/maintainer_specs/<repo>-<commit10>.json`, run
`maintainer_oracle.py check-spec`, record refusals in `solo/REFUSED.json`, and re-run `select` into `selection_2`
with `--s4-verdicts solo/S4.json --refused solo/REFUSED.json`.

Each S4 check: write `solo/s4/<owner>__<repo>/PLAN.md` (commands and test set) and commit it **before** running.
