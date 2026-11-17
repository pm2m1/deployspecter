# S4 check: TandoorRecipes/recipes (plan, fixed before anything runs)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Rule: `osf/SELECTION_RULE.md` S4, steps 1–6, as `osf/SELECTION_RULE_SOLO.md` step 5 requires. `select_cases.py
select` stopped at this application (exit 4) on 2026-11-17. No fix of this application has been read. Only its
packaging, settings, test configuration and CI workflow at the revision below were read, to set up the environment.

1. **Revision:** default branch `develop`, head on the screening date (mirror fetched 2026-11-17):
   `d9282c0cab5add50c3a952428049d907e228d69a` (committed 2026-11-16T19:13:36+01:00). The source is exported with
   `git archive` and isn't modified.
2. **Environment:**
   - Docker image `python:3.12-slim` (the Python of the application's CI), with the system packages needed to build
     its requirements: `gcc pkg-config libldap2-dev libsasl2-dev libxml2-dev libxmlsec1-dev libxslt1-dev libpq-dev
     git`;
   - `pip install -r requirements.txt` (all declared dependencies, including the development section), then
     `pip install "psycopg[binary]"`;
   - the database is a disposable `postgres:16` container on a private Docker network, with no published port;
   - environment: `DATABASE_URL` and `TEST_DATABASE_URL` point at it; `SECRET_KEY` is set to a throwaway value.
3. **Backend:** `python manage.py shell -c "from django.db import connection; connection.ensure_connection();
   print(type(connection.connection).__module__)"` must print a module starting with `psycopg` and not `psycopg2`.
4. **Migrations:** `python manage.py migrate --noinput` on the empty database must exit 0.
5. **Test set, fixed now:** the application's whole test suite, which is `pytest.ini`'s `testpaths =
   cookbook/tests`:

   `pytest -p no:cacheprovider -o addopts="" -n 4 cookbook/tests --junitxml=<out>`

   - `addopts` is cleared only to drop the coverage and HTML report plugins. Their output paths are inside the
     source tree.
   - `-n 4` keeps the suite's own xdist parallelism (`-n auto` in `pytest.ini`) at a fixed width.
   - The suite runs first with psycopg 3 installed. Then `psycopg` and `psycopg-binary` are uninstalled in the same
     environment, the backend check is repeated (it must print `psycopg2`), and the same command runs again.
   - **Pass criterion:** every test that fails or errors under psycopg 3 also fails or errors under psycopg2. The
     comparison uses the two JUnit XML files, by test id.
   - Failures shared by both runs, such as missing front-end build files, don't count against the application.
6. The verdict, commands, versions and exit codes go into `LOG.md` and `../../S4.json`. Initials: PM (the
   investigator; run on their behalf under the authorization above).
