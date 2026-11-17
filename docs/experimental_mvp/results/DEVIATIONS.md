# Deviations from the registered plan (https://osf.io/34ytz)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Every entry is written before the departure takes effect. Entries are only ever added.

## D1. The PostHog mirror is cloned in steps (2026-11-17)

- **Plan:** `benchmark_prep/RUNBOOK.md` §2 mirrors the 20 candidate repositories with
  `select_cases.py fetch`, which runs one `git clone --mirror` per repository.
- **What happened:** `PostHog/posthog` failed five times in a row (2026-11-17, 01:5x–03:05 UTC). Each failure was a
  connection reset or HTTP/2 stream cancel partway through the single pack (`curl 56`, `curl 92`, `early EOF`). No
  partial mirror was left behind. Zulip and Sentry were mirrored. `fetch` stops at its first failure, so the 17
  repositories after PostHog weren't mirrored yet.
- **Departure:** the PostHog mirror is built at the same path (`~/ds-mirrors/PostHog__posthog.git`) in steps:
  1. `git clone --mirror --depth 1` from the same URL;
  2. repeated `git fetch --deepen=<n>`, each step retried on a network error;
  3. `git fetch --unshallow`;
  4. `git fsck --connectivity-only`, which must pass. The repository must not be shallow at the end.

  Then the frozen `select_cases.py fetch` is run unchanged. It skips existing mirrors, so it mirrors the remaining
  17. `select` does not run until all 20 mirrors exist.
- **Expected effect:** none on what counts as a case, an arm or a budget. The result is a complete mirror clone
  with the same refspec (`+refs/*:refs/*`, `mirror = true`) and the full history. `select` reads only the default
  branch's history and trees, which are identical to a one-shot mirror clone's. No frozen file is changed.

### D1, addendum (2026-11-17, before it takes effect)

The first stepwise attempt left a depth-1 mirror at the path. Its `--deepen` steps all hit a client-side
low-speed timeout (`curl 28`) while the server was still building the pack. The deepening continues with smaller
steps and no low-speed limit. Because the PostHog path now exists, the frozen `fetch` skips it, and it runs for the
other 17 repositories at the same time instead of afterwards. `select` still does not run until the PostHog mirror is
complete (not shallow) and `git fsck --connectivity-only` passes. Expected effect: none.

### D1, addendum 2 (2026-11-17, before it takes effect)

From 04:04 to 05:50 UTC, no `--deepen=500` step on PostHog completed. The failures were connection resets, timeouts,
two DNS outages and a bad pack header. The reason is the mirror refspec: it includes GitHub's `refs/pull/*` (Zulip's
mirror, for comparison, has 36,129 of them), and every deepen step deepens all of those refs.
- **Departure:** the PostHog mirror is rebuilt in a separate scratch directory under `~/ds-mirrors/`. It fetches
  `refs/heads/*` and `refs/tags/*` only, without `refs/pull/*`:
  1. depth 1 first;
  2. then `--deepen` steps;
  3. then `--unshallow`.

  `HEAD` points at the remote's default branch (`master`). It must end up not shallow and pass
  `git fsck --connectivity-only`. Then it replaces the depth-1 directory at `~/ds-mirrors/PostHog__posthog.git`. The
  other 19 mirrors are one-shot `select_cases.py fetch` mirrors, all verified not shallow.
- **Expected effect:** none on selection. `select_cases.py` reads only the default branch:
  - `git log --no-merges --first-parent … HEAD`;
  - `diff-tree`, `diff`, `show` and `ls-tree` of those commits and their parents.

  These are fully present. PostHog's mirror lacks only the pull-request refs, which `select` never reads. This is
  recorded so that a re-run of `select` on a one-shot mirror can be compared.

## D2. S4 commands use the application's own Django entry point (2026-11-17)

- **Plan:** `osf/SELECTION_RULE.md` S4 steps 3 and 4 name `python manage.py shell -c …` and `python manage.py
  migrate`.
- **Departure:** an application without a `manage.py` uses its documented equivalent for the same command.
  - For `getsentry/sentry` that is:
    - `sentry django shell -c "<the same code>"` for step 3;
    - for step 4, the databases its `tools/dev_db.py migrate` creates (`sentry`, `control`, `region`,
      `secondary`), then `sentry upgrade --noinput`, which runs Django's `migrate`. This is what
      `make apply-migrations` does.
  - The code checked in step 3 is unchanged.
  - Each S4 plan names the commands it uses, and is committed before its check runs.
- **Expected effect:** none on what counts as a case. The checks are the same checks, run through the application's
  own management command. No application source file is changed.

## Owner decisions of 2026-11-17 (after `selection_1`, before any spec was written)

Asked and answered in the session. Each answer is quoted as given.

### D3. r3 #12: the Django seam is validated before any Django case runs

- **Question:** 7 of the 10 admitted cases (pretix 1, kiwitcms 1, plane 3, wger 2) are Django applications. Registered
  r3 #12: "The Django seam stays development-only until a development Django application of comparable scale has run
  through it." Only the small fixture app has used it (`protocol/OWNER_DECISIONS.md:67`).
- **Owner's answer:** "Validate seam first (Recommended)". Before any Django case runs, an already-seen, non-candidate
  Django application of comparable scale (InvenTree or paperless-ngx, both excluded from the pool) is run through the
  seam.
- **What happens:**
  - The validation is recorded under `results/django_seam_validation/`.
  - Specs and integrations for the Django cases may be written before it. No Django case's arm or oracle-error check
    runs until it is done.
  - If the seam can't run that application, the Django cases fail E3 and are refused (`REFUSED.json`, reason
    `E3: r3 #12 Django seam not validated`). The owner is told before that refusal is recorded.
- **Expected effect:** none on which cases are admitted, if the validation succeeds. It costs integration time.

### D4. E2–E6 are applied through the existing refusal channels

- **Question:** the frozen `select_cases.py` doesn't screen E2–E6, though `SELECTION_RULE_SOLO.md` says they still
  apply.
- **Owner's answer:** "Existing refusal channels (Recommended)". The code stays authoritative.
  - E2 and E5 take effect through `maintainer_oracle.py check-spec`: a case whose regression test expresses nothing
    over the final database state is refused.
  - E3: an integration that can't run the case's code path under the stepping seam at that revision is refused,
    with reason `E3: <what failed>`.
  - Each refusal goes into `solo/REFUSED.json` with its reason, and `select` is re-run into the next `selection_<n>`.
    No new rule.
- **Expected effect:** cases that fail E2, E3 or E5 leave the sample through the registered refusal channel (skip
  code `adapter_refused`, with the reason copied).

### D5. One generic final-state read for every integration

- **Question:** specs must name the final-state keys the oracle reads, but the Stage 2 integrations are written by an
  agent that never sees the spec.
- **Owner's answer:** "Generic table export (Recommended)".
- **Convention:** every integration's final-state read returns:
  - `final_state.tables.<table name>`: every row of every application table (the tables the application's own
    migrations create), as plain dicts, read with plain SQL after the run;
  - `final_state.db_now`: the database clock (`SELECT now()`) at the time of that read.

  Specs reference only these keys, plus the workload record in `raw_results` where the spec declares it.
  `tables.*` are `"application"` sources. `db_now` is declared a `"deployspecter"` source, so any assertion that
  uses it is flagged `uses_deployspecter_code: true`, which keeps it out of the strict subset.
- **Expected effect:** the integrator learns nothing about any case. What the oracle can see is fixed before any
  arm runs, and is the same for every arm.
