#!/bin/bash
# EXPERIMENTAL: a disposable PostgreSQL 16 for the real-application stepping prototype. Never the protected 55432.
#   mvp_pg.sh start   start it (fresh password in $DS_MVP_SECRET_FILE, mode 600, outside the repository)
#   mvp_pg.sh stop    stop and remove it, delete the password file, confirm the port is free
set -u
NAME=deployspecter-mvp-pg
PORT=${DS_MVP_PORT:-55441}
IMAGE=postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c
SECRET=${DS_MVP_SECRET_FILE:-$HOME/.ds_mvp_pg_password}
die() { echo "mvp_pg: $1" >&2; exit 2; }

case "${1:-}" in
start)
  [ "$PORT" = 55432 ] && die "55432 is the protected development database"
  [ -n "$(docker ps -aq --filter "name=^${NAME}$")" ] && die "$NAME already exists"
  [ -n "$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null)" ] && die "port $PORT is in use"
  (umask 177; openssl rand -hex 24 > "$SECRET")
  docker run -d --rm --name "$NAME" -p "127.0.0.1:${PORT}:5432" --tmpfs /var/lib/postgresql/data:rw,size=512m \
    --memory 1g --cpus 2 --pids-limit 256 -e POSTGRES_USER=ds_mvp -e POSTGRES_DB=ds_mvp \
    -e POSTGRES_PASSWORD="$(cat "$SECRET")" "$IMAGE" -c cluster_name=ds_mvp_disposable -c log_statement=all \
    -c log_connections=on -c log_disconnections=on -c logging_collector=on -c log_destination=csvlog \
    -c log_directory=log -c log_filename=mvp.log -c autovacuum=off -c max_connections=50 >/dev/null \
    || { rm -f "$SECRET"; die "docker run failed"; }
  for i in $(seq 1 60); do
    if docker exec "$NAME" pg_isready -h 127.0.0.1 -p 5432 -U ds_mvp -d ds_mvp >/dev/null 2>&1 \
       && docker logs "$NAME" 2>&1 | grep -q "PostgreSQL init process complete"; then
      echo "mvp_pg: ready on 127.0.0.1:$PORT after ~${i}s"; exit 0
    fi
    sleep 1
  done
  docker stop "$NAME" >/dev/null 2>&1; rm -f "$SECRET"; die "not ready within 60 s"
  ;;
stop)
  docker stop "$NAME" >/dev/null 2>&1 && echo "mvp_pg: stopped $NAME" || echo "mvp_pg: $NAME wasn't running"
  rm -f "$SECRET"
  [ -z "$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null)" ] && echo "mvp_pg: port $PORT free" \
    || die "port $PORT still in use"
  ;;
log)
  docker exec "$NAME" cat /var/lib/postgresql/data/log/mvp.csv
  ;;
*) die "usage: mvp_pg.sh start|stop|log" ;;
esac
