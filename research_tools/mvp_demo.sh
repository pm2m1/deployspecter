#!/bin/bash
# EXPERIMENTAL MVP, end to end, in one command: start a disposable PostgreSQL 16 (never 55432), run the prototype's
# offline and live tests, re-execute the buggy and fixed builds on the race schedule, then always tear down.
#   research_tools/mvp_demo.sh [OUT_DIR]
set -u
cd "$(dirname "$0")/.."
OUT=${1:-docs/experimental_mvp/results/demo_$(date -u +%Y%m%dT%H%M%SZ)}
export DS_MVP_PORT=${DS_MVP_PORT:-55441}
trap 'research_tools/mvp_pg.sh stop' EXIT
research_tools/mvp_pg.sh start || exit 2
env -u MODEL_API_KEY -u DATABASE_URL .venv/bin/python -m pytest -q -p no:cacheprovider tests/experimental || exit 3
env -u MODEL_API_KEY -u DATABASE_URL .venv/bin/python -m deployspecter.experimental.demo --out "$OUT"
