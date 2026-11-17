#!/bin/bash
# runs inside python:3.12-slim; /src = application source (copy), /out = logs
set -u
cd /src
step() { local name=$1; shift; echo "### $name: $*"; "$@"; local rc=$?; echo "### $name exit=$rc"; echo "$name $rc" >> /out/exit_codes.txt; return $rc; }
export DATABASE_URL=postgres://postgres:s4pw@s4pg:5432/tandoor TEST_DATABASE_URL=postgres://postgres:s4pw@s4pg:5432/tandoor SECRET_KEY=s4-throwaway-key DEBUG=0
step apt bash -c "apt-get update -qq && apt-get install -y -qq gcc pkg-config libldap2-dev libsasl2-dev libxml2-dev libxmlsec1-dev libxslt1-dev libpq-dev git >/dev/null"
step venv python -m venv /venv
step pip_reqs /venv/bin/pip install -q -r requirements.txt
step pip_psycopg3 /venv/bin/pip install -q "psycopg[binary]"
/venv/bin/pip show django psycopg psycopg-binary psycopg2-binary 2>/dev/null | grep -E "^(Name|Version)" > /out/versions_psycopg3.txt
/venv/bin/python --version >> /out/versions_psycopg3.txt
BK='from django.db import connection; connection.ensure_connection(); print(type(connection.connection).__module__)'
step backend_psycopg3 /venv/bin/python manage.py shell -c "$BK" | tee /out/backend_psycopg3.txt
step migrate /venv/bin/python manage.py migrate --noinput > /out/migrate.txt 2>&1; tail -3 /out/migrate.txt
step tests_psycopg3 /venv/bin/pytest -p no:cacheprovider -o addopts="" -n 4 cookbook/tests --junitxml=/out/junit_psycopg3.xml -q > /out/tests_psycopg3.txt 2>&1; tail -3 /out/tests_psycopg3.txt
step uninstall_psycopg3 /venv/bin/pip uninstall -y -q psycopg psycopg-binary
/venv/bin/pip show django psycopg psycopg-binary psycopg2-binary 2>/dev/null | grep -E "^(Name|Version)" > /out/versions_psycopg2.txt
step backend_psycopg2 /venv/bin/python manage.py shell -c "$BK" | tee /out/backend_psycopg2.txt
step tests_psycopg2 /venv/bin/pytest -p no:cacheprovider -o addopts="" -n 4 cookbook/tests --junitxml=/out/junit_psycopg2.xml -q > /out/tests_psycopg2.txt 2>&1; tail -3 /out/tests_psycopg2.txt
echo done
