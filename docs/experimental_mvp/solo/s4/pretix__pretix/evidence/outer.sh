#!/bin/bash
set -u
echo "start $(date -u +%FT%TZ)"
docker run -d --rm --name s4pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=pretix postgres:16 -c max_connections=300 >/dev/null
for i in $(seq 1 60); do docker exec s4pg pg_isready -U postgres -d pretix -q && break; sleep 1; done
sleep 3; docker exec s4pg pg_isready -U postgres -d pretix
docker exec s4pg psql -U postgres -tAc "select version()" > <scratch>/s4_pretix/out/pg_version.txt
docker run --rm --name s4app --network container:s4pg -v <scratch>/s4_pretix/src:/src -v <scratch>/s4_pretix/out:/out -v <scratch>/s4_pretix/inner.sh:/inner.sh:ro python:3.13-slim bash /inner.sh
echo "app container exit=$?"
docker stop s4pg >/dev/null
echo "end $(date -u +%FT%TZ)"
