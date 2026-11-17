# S4 check: getsentry/sentry (result)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

**Verdict: fail.** It fails at step 4 (migrations) and at step 5 (tests). Final for this application
(`SELECTION_RULE.md` S4 step 6). Its candidate fixes are screened as `s4_failed`, and none is read.

- **Date:** 2026-11-17, 06:14:22–06:18:06 UTC.
- **Initials:** PM (run on their behalf under the authorization above).
- **Plan:** committed before the run, in `PLAN.md` (d7941f1), using the entry point of DEVIATIONS D2. Evidence and
  scripts are in `evidence/`, with sha256s in `EVIDENCE.sha256`.

| Step | Command | Exit | Result |
|---|---|---|---|
| 1 | `git archive 7516bd5cf5634ea7178f1abaf17d23647df3609f` (`master` head) | 0 | source unmodified |
| 2 | `uv sync --frozen`; `uv pip install --index-url https://pypi.org/simple "psycopg[binary]"` | 0, 0 | Python 3.13.18, Django 5.2.17 (≥ 4.2), psycopg 3.3.9 + psycopg-binary 3.3.9, psycopg2-binary 2.9.10; PostgreSQL 16 |
| 3 | `sentry django shell -c "…print(type(connection.connection).__module__)"` | 0 | `psycopg` |
| 4 | `createdb` ×4 (0 each); `sentry upgrade --noinput` | **1** | `AttributeError: 'Jsonb' object has no attribute 'adapted'`, raised in `src/sentry/db/models/fields/jsonfield.py:216` (`get_db_prep_value`) during a data migration after `workflow_engine.0126` |
| 5a | `pytest -p no:cacheprovider -n 4 tests/sentry/db tests/sentry/models` with psycopg 3 | 1 | 1,210 tests: 520 failed, 156 errors |
| 5b | `uv pip uninstall psycopg psycopg-binary`; backend check | 0, 0 | `psycopg2.extensions` |
| 5c | the same pytest command with psycopg2 | 1 | 1,210 tests: 6 failed, 136 errors |

**Comparison (`evidence/comparison.json`, by JUnit test id):**
- the same 1,210 test ids in both runs;
- **534 fail or error only under psycopg 3**; 0 only under psycopg2;
- 142 under both.

Step 4 alone decides the verdict. Step 5 agrees with it: Sentry doesn't run unmodified with psycopg 3.

Containers (`s4pg`, `s4redis`, `s4app`) were removed after the run. No other database was touched.
