#!/bin/bash
# runs inside python:3.13-slim sharing the postgres container's network namespace; /src = source copy, /out = logs
set -u
cd /src
step() { local name=$1; shift; echo "### $name: $*"; "$@"; local rc=$?; echo "### $name exit=$rc"; echo "$name $rc" >> /out/exit_codes.txt; return $rc; }
export UV_PROJECT_ENVIRONMENT=/venv SENTRY_SKIP_SERVICES_CHECK=1 PATH=/venv/bin:$PATH
step apt bash -c "apt-get update -qq && apt-get install -y -qq gcc pkg-config libxml2-dev libxmlsec1-dev libxslt1-dev libpq-dev libffi-dev git >/dev/null"
step pip_uv pip install -q uv==0.12.9
step uv_sync uv sync --frozen --quiet
step pip_psycopg3 uv pip install --python /venv/bin/python --index-url https://pypi.org/simple "psycopg[binary]"
uv pip list --python /venv/bin/python 2>/dev/null | grep -i -E "^(django|psycopg|psycopg-binary|psycopg2-binary|pytest) " > /out/versions_psycopg3.txt
python --version >> /out/versions_psycopg3.txt
step init_config sentry init --dev --no-clobber
BK='from django.db import connection; connection.ensure_connection(); print(type(connection.connection).__module__)'
step backend_psycopg3 sentry django shell -c "$BK" > /out/backend_psycopg3.txt 2>&1; tail -2 /out/backend_psycopg3.txt
step migrate sentry upgrade --noinput > /out/migrate.txt 2>&1; tail -3 /out/migrate.txt
step tests_psycopg3 pytest -p no:cacheprovider -n 4 tests/sentry/db tests/sentry/models --junitxml=/out/junit_psycopg3.xml -q > /out/tests_psycopg3.txt 2>&1; tail -3 /out/tests_psycopg3.txt
step uninstall_psycopg3 uv pip uninstall --python /venv/bin/python psycopg psycopg-binary
uv pip list --python /venv/bin/python 2>/dev/null | grep -i -E "^(django|psycopg|psycopg-binary|psycopg2-binary|pytest) " > /out/versions_psycopg2.txt
step backend_psycopg2 sentry django shell -c "$BK" > /out/backend_psycopg2.txt 2>&1; tail -2 /out/backend_psycopg2.txt
step tests_psycopg2 pytest -p no:cacheprovider -n 4 tests/sentry/db tests/sentry/models --junitxml=/out/junit_psycopg2.xml -q > /out/tests_psycopg2.txt 2>&1; tail -3 /out/tests_psycopg2.txt
echo done
