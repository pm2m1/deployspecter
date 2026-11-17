# S4 check: pretix/pretix (plan, fixed before anything runs)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Rule: `osf/SELECTION_RULE.md` S4, steps 1–6. `select_cases.py select` stopped at this application (exit 4) on
2026-11-17, after TandoorRecipes/recipes (pass) and getsentry/sentry (fail). No fix of this application has been
read. Only the following were read at the revision below, to set up the environment: `pyproject.toml`, `setup.cfg`
(`tool:pytest`), `.github/workflows/tests.yml`, `src/tests/ci_postgres.cfg` and `src/pretix/settings.py`
(database section).

1. **Revision:** default branch `master`, head on the screening date (mirror fetched 2026-11-17):
   `f346acb0d253482097b6227e2f15ba2b3045d2eb` (committed 2026-11-13T14:34:41+01:00). Exported with `git archive` and
   not modified.
2. **Environment:**
   - Docker image `python:3.13-slim` (a Python in the application's CI matrix), with `gettext gcc pkg-config
     libpq-dev libffi-dev libjpeg-dev zlib1g-dev git`;
   - in a virtualenv, as the application's CI does: `uv pip install -e ".[dev]" psycopg2-binary`, then
     `uv pip install "psycopg[binary]"`;
   - a disposable `postgres:16` with the application's own CI configuration `src/tests/ci_postgres.cfg` (user
     `postgres`, password `postgres`, database `pretix`, host `127.0.0.1`) through `PRETIX_CONFIG_FILE`. The
     application container shares the database container's network namespace. No port is published.
3. **Backend** (in `src/`): `python manage.py shell -c "from django.db import connection;
   connection.ensure_connection(); print(type(connection.connection).__module__)"` must print a module starting with
   `psycopg` and not `psycopg2`.
4. **Migrations** (in `src/`): `python manage.py migrate --noinput` on the empty database must exit 0.
5. **Test set, fixed now:** the application's whole suite as its CI runs it, minus the browser-driven `tests/e2e`
   that its CI runs in a separate job. In `src/`:

   `py.test -p no:cacheprovider -n 4 tests --ignore=tests/e2e --junitxml=<out>`

   - It omits CI's coverage options and its `--maxfail=100`, so that both runs report every test id.
   - The suite runs first with psycopg 3 installed. Then `psycopg` and `psycopg-binary` are uninstalled in the same
     environment, the backend check is repeated (it must print `psycopg2…`), and the same command runs again.
   - **Pass criterion:** every test that fails or errors under psycopg 3 also fails or errors under psycopg2, by
     JUnit test id.
   - Failures shared by both runs, such as tests that need compiled front-end assets, don't count against the
     application.
6. The verdict, commands, versions and exit codes go into `LOG.md` and `../../S4.json`. Initials: PM (run on their
   behalf under the authorization above).
