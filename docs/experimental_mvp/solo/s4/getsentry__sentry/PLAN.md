# S4 check: getsentry/sentry (plan, fixed before anything runs)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Rule: `osf/SELECTION_RULE.md` S4, steps 1–6, with the entry-point adaptation of deviation D2. `select_cases.py select`
stopped at this application (exit 4) on 2026-11-17, after TandoorRecipes/recipes passed S4. No fix of this
application has been read. Only the following were read at the revision below, to set up the environment:
`pyproject.toml`, `uv.lock` (versions), `.python-version`, `Makefile`, `tools/dev_db.py`, `src/sentry/conf/server.py`
(`DATABASES`), `src/sentry/db/postgres/base.py` (imports), `src/sentry/testutils/pytest/sentry.py` and
`.github/workflows/backend.yml`.

1. **Revision:** default branch `master`, head on the screening date (mirror fetched 2026-11-17):
   `7516bd5cf5634ea7178f1abaf17d23647df3609f` (committed 2026-11-16T16:20:06-08:00). Exported with `git archive`
   and not modified.
2. **Environment:**
   - Docker image `python:3.13-slim` (`.python-version` 3.13.18), with build packages `gcc pkg-config libxml2-dev
     libxmlsec1-dev libxslt1-dev libpq-dev libffi-dev git`;
   - `uv sync --frozen` (the declared dependencies, `uv.lock`, default groups, from the application's own index),
     then `uv pip install --index-url https://pypi.org/simple "psycopg[binary]"`;
   - services: a disposable `postgres:16` (user `postgres`, trust authentication) and `redis:7`. The application
     container shares their network namespace, because the configuration expects `127.0.0.1`. No port is published;
   - configuration: `sentry init --dev --no-clobber` (`make init-config`), `SENTRY_SKIP_SERVICES_CHECK=1`.
3. **Backend:** `sentry django shell -c "from django.db import connection; connection.ensure_connection();
   print(type(connection.connection).__module__)"` must print a module starting with `psycopg` and not `psycopg2`.
4. **Migrations:**
   - create the databases `sentry`, `control`, `region` and `secondary` (`tools/dev_db.py create`, run through
     `psql` because there's no Docker CLI in the container);
   - then `sentry upgrade --noinput`, which must exit 0.
5. **Test set, fixed now:** a database-backed subset, the directories `tests/sentry/db` and `tests/sentry/models`,
   with the application's own pytest configuration:

   `pytest -p no:cacheprovider -n 4 tests/sentry/db tests/sentry/models --junitxml=<out>`

   - The suite runs first with psycopg 3 installed. Then `psycopg` and `psycopg-binary` are uninstalled in the same
     environment, the backend check is repeated (it must print `psycopg2…`), and the same command runs again.
   - **Pass criterion:** every test that fails or errors under psycopg 3 also fails or errors under psycopg2. The
     comparison uses the JUnit XML, by test id.
   - Failures shared by both runs, such as tests that need Snuba, Kafka or other services that aren't started, don't
     count against the application.
6. The verdict, commands, versions and exit codes go into `LOG.md` and `../../S4.json`. Initials: PM (run on their
   behalf under the authorization above).
