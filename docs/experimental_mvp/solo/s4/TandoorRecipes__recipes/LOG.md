# S4 check: TandoorRecipes/recipes (result)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

**Verdict: pass.** Final for this application (`SELECTION_RULE.md` S4 step 6).

- **Date:** 2026-11-17, 06:07:44–06:10:58 UTC.
- **Initials:** PM (run on their behalf under the authorization above).
- **Plan:** committed before the run, in `PLAN.md` (f48ce0b). Evidence and scripts are in `evidence/`, with sha256s in
  `EVIDENCE.sha256`.

| Step | Command | Exit | Result |
|---|---|---|---|
| 1 | `git archive d9282c0cab5add50c3a952428049d907e228d69a` (`develop` head) | 0 | source unmodified |
| 2 | `apt-get install …`; `pip install -r requirements.txt`; `pip install "psycopg[binary]"` | 0, 0, 0 | Python 3.12.16, Django 5.2.18 (≥ 4.2), psycopg 3.3.9 + psycopg-binary 3.3.9, psycopg2-binary 2.9.12; PostgreSQL 16.15 |
| 3 | `python manage.py shell -c "…print(type(connection.connection).__module__)"` | 0 | `psycopg` |
| 4 | `python manage.py migrate --noinput` on the empty database | 0 | all migrations applied |
| 5a | `pytest -p no:cacheprovider -o addopts="" -n 4 cookbook/tests` with psycopg 3 | 1 | 1,316 tests: 36 failed, 1,280 passed |
| 5b | `pip uninstall -y psycopg psycopg-binary`; backend check | 0, 0 | `psycopg2.extensions` |
| 5c | the same pytest command with psycopg2 | 1 | 1,316 tests: 36 failed, 1,280 passed |

**Comparison (`evidence/comparison.json`, by JUnit test id):**
- the same 1,316 test ids in both runs;
- failed only under psycopg 3: **none**; failed only under psycopg2: none;
- 36 failed under both.

The shared failures are in `test_api_shopping_recipe` (10), `test_social_auth` (10), `test_views_general` (5),
`test_api_import_log` (3), `test_api_meal_plan` (2), `test_api_recipe` (2), and one each in
`test_api_recipe_book_entry`, `test_api_shopping_list_recipe`, `test_api_userpreference` and `test_markdown_renderer`.
They occur under both drivers in the same environment, so under S4 step 5 they don't count against the application.

Containers (`s4pg`, `s4app`) and the network `ds-s4-net` were removed after the run. No other database was touched.
