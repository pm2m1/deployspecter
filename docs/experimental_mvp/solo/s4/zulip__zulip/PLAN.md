# S4 check: zulip/zulip (plan, fixed before anything runs)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Rule: `osf/SELECTION_RULE.md` S4, steps 1–6. `select_cases.py select` stopped at this application (exit 4) on
2026-11-17, after TandoorRecipes/recipes (pass), getsentry/sentry (fail) and pretix/pretix (pass). No fix of this
application has been read. Only the following were read at the revision below, to set up the environment:
`pyproject.toml` (dependency names), `tools/ci/setup-backend`, `tools/ci/activate-venv`, `tools/lib/provision.py`
(PostgreSQL version per OS), `tools/rebuild-test-database`, `tools/setup/postgresql-init-test-db`, the startup part
of `tools/test-backend` and `.github/workflows/zulip-ci.yml`.

1. **Revision:** default branch `main`, head on the screening date (mirror fetched 2026-11-17):
   `afcd0cae679ed84eecd3570d072c93e50a71d865` (committed 2026-11-16T10:16:06-08:00). Checked out from the local
   mirror into the container, not modified.
2. **Environment:**
   - the application's own CI image `zulip/ci:noble` (Ubuntu 24.04, in its CI matrix). For it, the application's
     provisioning installs **PostgreSQL 16** (`tools/lib/provision.py`) inside the disposable container. No port is
     published;
   - `./tools/ci/setup-backend --skip-dev-db-build`, exactly as its CI runs it. This installs the declared
     dependencies (`uv.lock`) and the services, and builds the test database with psycopg2;
   - then `uv pip install --python .venv/bin/python "psycopg[binary]"` into the application's virtualenv.
3. **Backend:** `DJANGO_SETTINGS_MODULE=zproject.test_settings ./manage.py shell -c "from django.db import
   connection; connection.ensure_connection(); print(type(connection.connection).__module__)"` must print a module
   starting with `psycopg` and not `psycopg2`.
4. **Migrations from an empty database:** `./tools/rebuild-test-database` with psycopg 3 installed. It recreates
   `zulip_test` from `zulip_test_base` and runs `./manage.py migrate --noinput` on it (then `populate_db`).
   - Before it runs, the number of tables in `zulip_test_base`'s `zerver`/`public` schemas is recorded, to show that
     the database is empty.
   - Step 4 passes if `./manage.py migrate --noinput` completes, as shown by the script's `set -x` trace reaching
     the next command. The script's own exit code is recorded too.
5. **Test set, fixed now:** the application's backend suite, as its CI runs it, without the coverage options:

   `./tools/test-backend --include-webhooks --include-transaction-tests --xml-report --no-html-report --ban-console-output`

   - The suite runs first with psycopg 3 installed. Then `psycopg` and `psycopg-binary` are uninstalled from the
     same virtualenv, the backend check is repeated (it must print `psycopg2…`), and the same command runs again.
   - **Pass criterion:** every test that fails or errors under psycopg 3 also fails or errors under psycopg2, by
     JUnit test id from `var/xml-reports`. If no XML is written, the per-test failure lines of the console output
     are compared instead.
6. The verdict, commands, versions and exit codes go into `LOG.md` and `../../S4.json`. Initials: PM (run on their
   behalf under the authorization above).

## Addendum (2026-11-17, committed before the second attempt)

The first attempt (06:34:59–06:35:00 UTC) failed at its first command. `git clone` of the read-only mirror exited 128
with `fatal: detected dubious ownership in repository at '/mirror'`: the `-c safe.directory='*'` option doesn't
reach git's `upload-pack`. The checkout never happened, and every later command failed with exit 127 or 128. No
step ran against the application, so no verdict was reached. That attempt's log is kept in
`attempt1_environment_failure/`.

The second attempt runs `git config --global --add safe.directory /mirror` in the container before cloning. Nothing
else changes.

## Addendum 2 (2026-11-17, committed before the third attempt)

The second attempt (06:35:34–06:44:00 UTC) failed in step 2, inside the application's provisioning:
- `scripts/setup/configure-rabbitmq` exited 69 (`epmd reports: node 'rabbit' not running at all`), in both passes
  of `tools/ci/setup-backend`;
- the cause is that the provisioning starts its services (Redis, memcached, RabbitMQ, PostgreSQL) only when
  `CONTINUOUS_INTEGRATION` is true, and `tools/lib/provision.py:32` sets that to `"GITHUB_ACTIONS" in os.environ`.
  The application's CI always has that variable, because it runs under GitHub Actions; this container didn't;
- the later steps ran in a half-provisioned environment, so their results say nothing about the application (exit
  codes in `attempt2_environment_failure/`). No verdict was reached.

The third attempt sets `GITHUB_ACTIONS=true` in the container, which reproduces the environment of the CI job named
in step 2. Nothing else changes. The container is new.
