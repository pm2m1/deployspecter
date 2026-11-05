#!/bin/bash
# EXPERIMENTAL MVP, end to end, in one command: start a disposable PostgreSQL 16 (never 55432), run the prototype's
# offline and live tests, re-execute buggy and reference builds with server-log corroboration, then always tear down.
#
#   research_tools/mvp_demo.sh [OUT_DIR] [sync|async|all]
#
#   sync  (default, as before): the migration race, the SQLAlchemy ledger and the planned Gate 2A reservations app.
#   async: the async psycopg engine: the async migration race, the SQLAlchemy asyncio ledger, and the release path
#          (the frozen lock_row build vs the experimental corrected reference).
#   all:   both, then the isolated-runner smoke test (a restricted container on an internal network).
set -u
cd "$(dirname "$0")/.."
OUT=${1:-docs/experimental_mvp/results/demo_$(date -u +%Y%m%dT%H%M%SZ)}
MODE=${2:-sync}
case "$MODE" in sync|async|all) ;; *) echo "mode must be sync, async or all" >&2; exit 64 ;; esac
export DS_MVP_PORT=${DS_MVP_PORT:-55441}
PY="env -u MODEL_API_KEY -u DATABASE_URL .venv/bin/python"
uv sync --frozen --extra experimental -q || exit 1  # SQLAlchemy (with asyncio) for the ORM fixtures
trap 'research_tools/mvp_pg.sh stop' EXIT
research_tools/mvp_pg.sh start || exit 2
$PY -m pytest -q -p no:cacheprovider tests/experimental || exit 3
if [ "$MODE" = sync ] || [ "$MODE" = all ]; then
  $PY -m deployspecter.experimental.demo --out "$OUT/race" || exit 4
  $PY -m deployspecter.experimental.demo_integrated --out "$OUT/integrated" || exit 5
fi
if [ "$MODE" = async ] || [ "$MODE" = all ]; then
  $PY -m deployspecter.experimental.demo_async --out "$OUT/async" || exit 6
fi
if [ "$MODE" = all ]; then
  $PY research_tools/isolated/run_isolated.py smoke --out "$OUT/isolated" || exit 7
fi
