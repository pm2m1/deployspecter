# Progress: the preregistered single-investigator evaluation (https://osf.io/34ytz)

Label for everything: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

## Where it stands

| Stage | Status |
|---|---|
| 0. Preflight | **done** 2026-11-17: FREEZE.json `6fb0a189…aeced47` matches a rebuild; REGISTRATION.json accepted; sealed seed hashes match; Docker 29.7.2; Python 3.12.14; running from `research/benchmark-prep` d56ad87. No Muse health check exists in the frozen tooling, so no model call was made. |
| 1. Case selection | **done** (`solo/STAGE1_SUMMARY.md`): final `solo/selection_7`, 4 cases from 3 applications (pretix 1, plane 2, wger 1) after 100 screens; 15 refused by check-spec; strict subset 0; S4: Tandoor pass, Sentry fail, pretix pass, Zulip fail. RA-Q3 descriptive only. |
| Gate 1 | reported 2026-11-17; the owner answered "continue" with conditions. Concurrency classification added to `solo/STAGE1_SUMMARY.md` (0 of 4 concurrency-dependent). |
| 2. Integrations | **stopped 2026-11-17.** D3 seam validation on paperless-ngx v3.4.2 (`django_seam_validation/`): **FAIL** (P1 pass, P2 fail: 5 of 25 runs time out on the application's search-index file lock; P3 pass; P4 fail: W3 differs, because the seam refuses the reconnect Celery's fixup causes). The 4 admitted cases were refused (E3: r3 #12 Django seam not validated). **0 eligible historical cases.** No integration was started. |
| 3. Mutants | not started (owner: "Do not start integrations or mutants") |
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

**Closed 2026-11-17: terminated at Stage 2 (`DEVIATIONS.md` D6, `RESULTS.md`).** Re-selection `solo/selection_8`: 100 screens, 0 admitted.
Closing records:
- `RESULTS.md` (10aa32d);
- `OSF_UPDATE.md`, a draft for the owner to paste (bf22f5b);
- the draft GitHub release `results-2026-11-17` (not published), whose tarball sha256 is `e9f4d657…0038` and whose
  `SHA256SUMS` covers every file in it;
- PR #4 off `main`, which updates RESEARCH.md's status (not merged).

The paperless-ngx image was removed from the local Docker cache.

Previous resume point: **stopped at Stage 2, waiting for the owner.** The D3 validation failed, and the 4 admitted
cases are refused with E3. The owner's instruction: "If it fails, record the E3 refusals, stop, and report: the study
ends at Stage 2 with 0 eligible historical cases. Do not start integrations or mutants." Nothing runs until the owner
decides otherwise.
- `select` was not re-run after these refusals (the D4 re-selection step), because the owner said to stop.
- The 100-screen budget is spent in any case.

Each S4 check: write `solo/s4/<owner>__<repo>/PLAN.md` (commands and test set) and commit it **before** running.
