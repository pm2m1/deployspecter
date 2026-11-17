#!/bin/bash
set -u
cd /src
step() { local name=$1; shift; echo "### $name: $*"; "$@"; local rc=$?; echo "### $name exit=$rc"; echo "$name $rc" >> /out/exit_codes.txt; return $rc; }
export PATH=/venv/bin:$PATH PRETIX_CONFIG_FILE=tests/ci_postgres.cfg
step apt bash -c "apt-get update -qq && apt-get install -y -qq gettext gcc g++ pkg-config libpq-dev libffi-dev libjpeg-dev zlib1g-dev git >/dev/null"
step pip_uv pip install -q uv==0.12.9
step venv uv venv -q /venv
step pip_reqs uv pip install -q --python /venv/bin/python -e ".[dev]" psycopg2-binary
step pip_psycopg3 uv pip install -q --python /venv/bin/python "psycopg[binary]"
uv pip list --python /venv/bin/python 2>/dev/null | grep -i -E "^(django|psycopg|psycopg-binary|psycopg2-binary|pytest) " > /out/versions_psycopg3.txt
python --version >> /out/versions_psycopg3.txt
cd /src/src
BK='from django.db import connection; connection.ensure_connection(); print(type(connection.connection).__module__)'
step backend_psycopg3 python manage.py shell -c "$BK" > /out/backend_psycopg3.txt 2>&1; tail -2 /out/backend_psycopg3.txt
step migrate python manage.py migrate --noinput > /out/migrate.txt 2>&1; tail -3 /out/migrate.txt
step tests_psycopg3 py.test -p no:cacheprovider -n 4 tests --ignore=tests/e2e --junitxml=/out/junit_psycopg3.xml -q > /out/tests_psycopg3.txt 2>&1; tail -3 /out/tests_psycopg3.txt
step uninstall_psycopg3 uv pip uninstall --python /venv/bin/python psycopg psycopg-binary
uv pip list --python /venv/bin/python 2>/dev/null | grep -i -E "^(django|psycopg|psycopg-binary|psycopg2-binary|pytest) " > /out/versions_psycopg2.txt
step backend_psycopg2 python manage.py shell -c "$BK" > /out/backend_psycopg2.txt 2>&1; tail -2 /out/backend_psycopg2.txt
step tests_psycopg2 py.test -p no:cacheprovider -n 4 tests --ignore=tests/e2e --junitxml=/out/junit_psycopg2.xml -q > /out/tests_psycopg2.txt 2>&1; tail -3 /out/tests_psycopg2.txt
echo done
