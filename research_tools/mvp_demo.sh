#!/bin/bash
# EXPERIMENTAL MVP, end to end, in one command: start a disposable PostgreSQL 16 (never 55432), run the prototype's
# offline and live tests, re-execute buggy and reference builds with server-log corroboration, then always tear down.
#
#   research_tools/mvp_demo.sh [OUT_DIR] [sync|async|all|thirdparty|full]
#
#   sync  (default, as before): the migration race, the SQLAlchemy ledger and the planned Gate 2A reservations app.
#   async: the async psycopg engine: the async migration race, the SQLAlchemy asyncio ledger, and the release path
#          (the frozen lock_row build vs the experimental corrected reference).
#   all:   sync and async, then the isolated-runner smoke test (a restricted container on an internal network).
#   thirdparty: fetch and verify the pinned PGQueuer 1.6.0 and Chancy 0.26.0 sdists, then, in the isolated runner:
#          PGQueuer's lifecycle through the stepped Driver adapter, Chancy's job runtime through the stepped pool,
#          and both negative controls (each library's uninstrumented path, which the server log must refute).
#   full:  all, then thirdparty.
#   simultaneous: fetch and verify the pinned sdists, then run Chancy 0.25.1 and 0.26.0 workers AT THE SAME TIME
#          (three restricted containers, one coordinator granting every statement): old producer -> new consumer,
#          new producer -> old consumer, both workers competing for one queue, and two cancellation cells. Exit 0
#          means every run was isolated, coordinated to completion and corroborated (property verdicts are in
#          each report.json, whatever they are).
# Every step's exit status is checked; the first failure stops the run (and teardown still happens).
set -u
cd "$(dirname "$0")/.."
OUT=${1:-docs/experimental_mvp/results/demo_$(date -u +%Y%m%dT%H%M%SZ)}
MODE=${2:-sync}
case "$MODE" in sync|async|all|thirdparty|full|simultaneous) ;; *) echo "mode must be sync, async, all, thirdparty, full or simultaneous" >&2; exit 64 ;; esac
export DS_MVP_PORT=${DS_MVP_PORT:-55441}
PY="env -u MODEL_API_KEY -u DATABASE_URL .venv/bin/python"
SDISTS=$(mktemp -d "${TMPDIR:-/tmp}/ds_sdists.XXXXXX")
trap 'research_tools/mvp_pg.sh stop; rm -rf "$SDISTS"' EXIT
uv sync --frozen --extra experimental -q || exit 1  # SQLAlchemy (with asyncio) for the ORM fixtures
research_tools/mvp_pg.sh start || exit 2
if [ "$MODE" != thirdparty ] && [ "$MODE" != simultaneous ]; then
  $PY -m pytest -q -p no:cacheprovider tests/experimental || exit 3
fi
if [ "$MODE" = sync ] || [ "$MODE" = all ] || [ "$MODE" = full ]; then
  $PY -m deployspecter.experimental.demo --out "$OUT/race" || exit 4
  $PY -m deployspecter.experimental.demo_integrated --out "$OUT/integrated" || exit 5
fi
if [ "$MODE" = async ] || [ "$MODE" = all ] || [ "$MODE" = full ]; then
  $PY -m deployspecter.experimental.demo_async --out "$OUT/async" || exit 6
fi
if [ "$MODE" = all ] || [ "$MODE" = full ]; then
  $PY research_tools/isolated/run_isolated.py smoke --out "$OUT/isolated" || exit 7
fi
if [ "$MODE" = thirdparty ] || [ "$MODE" = full ]; then
  research_tools/isolated/fetch_pinned.sh "$SDISTS" || exit 8
  PGQ="$SDISTS/pgqueuer-1.6.0.tar.gz"; CHANCY="$SDISTS/chancy-0.26.0.tar.gz"
  $PY research_tools/isolated/run_isolated.py pgqueuer --pgqueuer-sdist "$PGQ" --out "$OUT/pgqueuer" || exit 9
  $PY research_tools/isolated/run_isolated.py pgqueuer --public-factory --pgqueuer-sdist "$PGQ" \
    --out "$OUT/pgqueuer_public_factory_refuted" || exit 10
  $PY research_tools/isolated/run_isolated.py chancy-runtime --chancy-sdist "$CHANCY" --out "$OUT/chancy_runtime" || exit 11
  $PY research_tools/isolated/run_isolated.py chancy-runtime --with-notifications --chancy-sdist "$CHANCY" \
    --out "$OUT/chancy_notifications_refuted" || exit 12
  $PY research_tools/isolated/run_isolated.py chancy-runtime --concurrent-workers --chancy-sdist "$CHANCY" \
    --out "$OUT/chancy_concurrent_workers" || exit 13
  $PY research_tools/isolated/run_isolated.py chancy-runtime --concurrent-workers --mutant no_skip_locked_claim \
    --chancy-sdist "$CHANCY" --out "$OUT/chancy_mutant_no_skip_locked_claim" || exit 14
  $PY research_tools/isolated/run_isolated.py chancy-mixed --chancy-old-sdist "$SDISTS/chancy-0.25.1.tar.gz" \
    --chancy-sdist "$CHANCY" --out "$OUT/chancy_mixed_version_pilot" || exit 15
fi
if [ "$MODE" = simultaneous ]; then
  research_tools/isolated/fetch_pinned.sh "$SDISTS" || exit 8
  i=20
  for SC in old_to_new new_to_old compete cancel_new_new cancel_new_old; do
    $PY research_tools/isolated/run_coordinated.py run --scenario "$SC" \
      --chancy-old-sdist "$SDISTS/chancy-0.25.1.tar.gz" --chancy-sdist "$SDISTS/chancy-0.26.0.tar.gz" \
      --out "$OUT/simultaneous_$SC" || exit $i
    i=$((i + 1))
  done
fi
