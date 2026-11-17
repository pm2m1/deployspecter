# S4 check: zulip/zulip (result)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

**Verdict: fail.** It fails at step 3 (the backend doesn't load) and at step 4 (migrations). Final for this
application (`SELECTION_RULE.md` S4 step 6). Its candidate fixes are screened as `s4_failed`, and none is read.

- **Date:** 2026-11-17. The third attempt ran 06:45:11–07:23:44 UTC.
- **Earlier attempts:** two, both environment failures that reached no S4 step: the clone (`PLAN.md` addendum,
  e491ef6) and provisioning without `GITHUB_ACTIONS` (addendum 2, bf3c507). Their logs are in
  `attempt1_environment_failure/` and `attempt2_environment_failure/`.
- **Initials:** PM (run on their behalf under the authorization above).
- **Plan:** committed before the run, in `PLAN.md` (40640b2, addenda e491ef6 and bf3c507). Evidence is in
  `evidence/`, with sha256s in `EVIDENCE.sha256`.

| Step | Command | Exit | Result |
|---|---|---|---|
| 1 | `git clone` from the mirror; `git checkout afcd0cae679ed84eecd3570d072c93e50a71d865` (`main` head) | 0, 0 | source unmodified |
| 2 | in `zulip/ci:noble` with `GITHUB_ACTIONS=true`: `./tools/ci/setup-backend --skip-dev-db-build`; `uv pip install "psycopg[binary]"` | 0, 0 | Python 3.12.3, Django 5.2.18 (≥ 4.2), psycopg 3.3.9 + psycopg-binary 3.3.9, psycopg2 2.9.13; PostgreSQL 16.16 |
| 3 | `DJANGO_SETTINGS_MODULE=zproject.test_settings ./manage.py shell -c "…"` | **1** | `AttributeError: module 'psycopg' has no attribute 'extensions'`, raised at import by the declared dependency `django-bitfield` (`bitfield/types.py:256`: `Database.extensions.register_adapter(...)`) |
| 4 | `./tools/rebuild-test-database` (`zulip_test_base` has 0 tables); its `./manage.py migrate --noinput` | **1** | the same `AttributeError`, raised as `migrate` starts |
| 5a | `./tools/test-backend --include-webhooks --include-transaction-tests --xml-report --no-html-report --ban-console-output` with psycopg 3 | 1 | no test ran; the same import error |
| 5b | `uv pip uninstall psycopg psycopg-binary`; backend check | 0, 0 | `zerver.lib.db`, the application's own subclass of the psycopg2 connection |
| 5c | the same `test-backend` command with psycopg2 | 0 | 5,992 tests, OK (5 skipped) |

**Comparison:** under psycopg 3 the application doesn't start, so no test can run. Under psycopg2 all 5,992 pass.
Zulip doesn't run unmodified with psycopg 3.

The container (`s4zulip`) was removed after each attempt. Its PostgreSQL lived and died inside it. No other database
was touched.
