#!/bin/bash
# runs inside zulip/ci:noble as user github; /mirror = read-only mirror, /out = logs
set -u
step() { local name=$1; shift; echo "### $name: $*"; "$@"; local rc=$?; echo "### $name exit=$rc"; echo "$name $rc" >> /out/exit_codes.txt; return $rc; }
cd ~
step clone git -c safe.directory='*' clone -q /mirror zulip
cd ~/zulip
step checkout git checkout -q afcd0cae679ed84eecd3570d072c93e50a71d865
git rev-parse HEAD > /out/revision.txt
step provision ./tools/ci/setup-backend --skip-dev-db-build > /out/provision.txt 2>&1; tail -5 /out/provision.txt
source tools/ci/activate-venv
UV=$(command -v uv || echo /usr/local/bin/uv); echo "uv=$UV"
step pip_psycopg3 $UV pip install --python .venv/bin/python "psycopg[binary]"
$UV pip list --python .venv/bin/python 2>/dev/null | grep -i -E "^(django|psycopg|psycopg-binary|psycopg2|psycopg2-binary) " > /out/versions_psycopg3.txt
python --version >> /out/versions_psycopg3.txt
psql -h localhost -U zulip -d zulip_test -tAc "select version()" > /out/pg_version.txt 2>&1
BK='from django.db import connection; connection.ensure_connection(); print(type(connection.connection).__module__)'
step backend_psycopg3 env DJANGO_SETTINGS_MODULE=zproject.test_settings ./manage.py shell -c "$BK" > /out/backend_psycopg3.txt 2>&1; tail -2 /out/backend_psycopg3.txt
psql -h localhost -U zulip -d zulip_test_base -tAc "select count(*) from information_schema.tables where table_schema in ('zulip','public')" > /out/base_tables.txt 2>&1
step rebuild_test_db ./tools/rebuild-test-database > /out/migrate.txt 2>&1; tail -5 /out/migrate.txt
step tests_psycopg3 ./tools/test-backend --include-webhooks --include-transaction-tests --xml-report --no-html-report --ban-console-output > /out/tests_psycopg3.txt 2>&1; tail -5 /out/tests_psycopg3.txt
mkdir -p /out/xml_psycopg3 && cp -r var/xml-reports/. /out/xml_psycopg3/ 2>/dev/null; rm -rf var/xml-reports
step uninstall_psycopg3 $UV pip uninstall --python .venv/bin/python psycopg psycopg-binary
$UV pip list --python .venv/bin/python 2>/dev/null | grep -i -E "^(django|psycopg|psycopg-binary|psycopg2|psycopg2-binary) " > /out/versions_psycopg2.txt
step backend_psycopg2 env DJANGO_SETTINGS_MODULE=zproject.test_settings ./manage.py shell -c "$BK" > /out/backend_psycopg2.txt 2>&1; tail -2 /out/backend_psycopg2.txt
step tests_psycopg2 ./tools/test-backend --include-webhooks --include-transaction-tests --xml-report --no-html-report --ban-console-output > /out/tests_psycopg2.txt 2>&1; tail -5 /out/tests_psycopg2.txt
mkdir -p /out/xml_psycopg2 && cp -r var/xml-reports/. /out/xml_psycopg2/ 2>/dev/null
echo done
