#!/bin/bash
set -u
echo "start $(date -u +%FT%TZ)"
docker run -d --rm --name s4pg -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16 -c max_connections=400 >/dev/null
docker run -d --rm --name s4redis --network container:s4pg redis:7 >/dev/null
for i in $(seq 1 60); do docker exec s4pg pg_isready -U postgres -q && break; sleep 1; done
sleep 3; docker exec s4pg pg_isready -U postgres
docker exec s4pg psql -U postgres -tAc "select version()" > <scratch>/s4_sentry/out/pg_version.txt
for db in sentry control region secondary; do docker exec s4pg createdb -U postgres -E utf-8 $db; echo "createdb $db exit=$?"; done
docker run --rm --name s4app --network container:s4pg -v <scratch>/s4_sentry/src:/src -v <scratch>/s4_sentry/out:/out -v <scratch>/s4_sentry/inner.sh:/inner.sh:ro python:3.13-slim bash /inner.sh
echo "app container exit=$?"
docker stop s4redis s4pg >/dev/null
echo "end $(date -u +%FT%TZ)"
