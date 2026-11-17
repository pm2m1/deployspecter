#!/bin/bash
set -u
echo "start $(date -u +%FT%TZ)"
docker network create ds-s4-net >/dev/null
docker run -d --rm --name s4pg --network ds-s4-net -e POSTGRES_PASSWORD=s4pw -e POSTGRES_DB=tandoor postgres:16 >/dev/null
for i in $(seq 1 60); do docker exec s4pg pg_isready -U postgres -q && break; sleep 1; done
docker exec s4pg psql -U postgres -tAc "select version()" > <scratch>/s4_tandoor/out/pg_version.txt
docker run --rm --name s4app --network ds-s4-net -v <scratch>/s4_tandoor/src:/src -v <scratch>/s4_tandoor/out:/out -v <scratch>/s4_tandoor/inner.sh:/inner.sh:ro python:3.12-slim bash /inner.sh
echo "app container exit=$?"
docker stop s4pg >/dev/null; docker network rm ds-s4-net >/dev/null
echo "end $(date -u +%FT%TZ)"
