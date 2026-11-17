# S4 check: pretix/pretix (result)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

**Verdict: pass.** Final for this application (`SELECTION_RULE.md` S4 step 6).

- **Date:** 2026-11-17. The second attempt ran 06:21:32–06:30:25 UTC.
- **First attempt:** 06:20:19–06:21:01 UTC. It failed while building the environment (no `g++` for `libsass`), and
  no S4 step reached the application. See the `PLAN.md` addendum (ac33257) and `attempt1_environment_failure/`.
- **Initials:** PM (run on their behalf under the authorization above).
- **Plan:** committed before the run, in `PLAN.md` (2efe27b, addendum ac33257). Evidence is in `evidence/`, with
  sha256s in `EVIDENCE.sha256`.

| Step | Command | Exit | Result |
|---|---|---|---|
| 1 | `git archive f346acb0d253482097b6227e2f15ba2b3045d2eb` (`master` head) | 0 | source unmodified |
| 2 | `uv pip install -e ".[dev]" psycopg2-binary`; `uv pip install "psycopg[binary]"` | 0, 0 | Python 3.13.18, Django 5.2.19 (≥ 4.2), psycopg 3.3.9 + psycopg-binary 3.3.9, psycopg2-binary 2.9.13; PostgreSQL 16.15 |
| 3 | `python manage.py shell -c "…print(type(connection.connection).__module__)"` | 0 | `psycopg` |
| 4 | `python manage.py migrate --noinput` on the empty database | 0 | all migrations applied |
| 5a | `py.test -p no:cacheprovider -n 4 tests --ignore=tests/e2e` with psycopg 3 | 1 | 6,121 tests: 15 failed, 6,076 passed, 27 skipped, 3 xfailed |
| 5b | `uv pip uninstall psycopg psycopg-binary`; backend check | 0, 0 | `psycopg2.extensions` |
| 5c | the same command with psycopg2 | 1 | 6,121 tests: 15 failed, 6,076 passed, 27 skipped, 3 xfailed |

**Comparison (`evidence/comparison.json`, by JUnit test id):**
- failed only under psycopg 3: **none**; failed only under psycopg2: none;
- the same 15 failed under both: `tests.helpers` 8, `presale.test_widget` 2, `presale.test_style` 2, and one each in
  `api`, `plugins.sendmail` and `control.test_mail_settings_preview`.
- 6,119 test ids are shared. The other 2 ids differ between the runs: the parameter of
  `tests.base.test_rich_text::test_linkify_safelinks` embeds a signature generated afresh in each process. That test
  passes in both runs.

The containers (`s4pg`, `s4app`) were removed after each attempt. No other database was touched.
