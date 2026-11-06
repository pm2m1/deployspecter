#!/bin/bash
# EXPERIMENTAL integrated acceptance run: Ruff, the core experimental suite, the optional isolated suite and the root
# suite (DATABASE_URL unset), each with stdout, stderr, JUnit XML and its exit status taken from $? directly.
# Database suites run under research_tools/pg_lock.py, with snapshots before and after.
#   research_tools/acceptance_run.sh SDIST_DIR OUT_DIR
set -u
SD=$1; OUT=$2
cd "$(dirname "$0")/.."
mkdir -p "$OUT"
git rev-parse HEAD > "$OUT/git_revision"; git status --porcelain --untracked-files=no > "$OUT/git_dirty"
ENV="env -u MODEL_API_KEY -u DATABASE_URL DS_THIRDPARTY_SDIST_DIR=$SD"
LOCK=".venv/bin/python research_tools/pg_lock.py --"
.venv/bin/ruff check . > "$OUT/ruff.txt" 2>&1; echo $? > "$OUT/ruff_exit_status"
$ENV .venv/bin/python research_tools/stress_snapshot.py "$OUT/snapshot_before.json"
$LOCK $ENV .venv/bin/python -m pytest tests/experimental -q -p no:cacheprovider -rfE --junitxml="$OUT/core_junit.xml" \
  > "$OUT/core_stdout.txt" 2> "$OUT/core_stderr.txt"; echo $? > "$OUT/core_exit_status"
$LOCK $ENV .venv/bin/python -m pytest tests/thirdparty_isolated -v -p no:cacheprovider -rfE --junitxml="$OUT/isolated_junit.xml" \
  --basetemp="$OUT.basetemp" > "$OUT/isolated_stdout.txt" 2> "$OUT/isolated_stderr.txt"; echo $? > "$OUT/isolated_exit_status"
$ENV .venv/bin/python -m pytest tests -q -p no:cacheprovider -rfE --junitxml="$OUT/root_junit.xml" --ignore=tests/experimental \
  --ignore=tests/thirdparty_isolated --ignore=tests/test_baselines.py --ignore=tests/test_documented_public.py \
  --ignore=tests/test_meta_client.py --ignore=tests/test_scenarios.py --ignore=tests/test_site.py \
  > "$OUT/root_stdout.txt" 2> "$OUT/root_stderr.txt"; echo $? > "$OUT/root_exit_status"
$ENV .venv/bin/python research_tools/stress_snapshot.py "$OUT/snapshot_after.json"
for s in ruff core isolated root; do echo "$s exit=$(cat "$OUT/${s}_exit_status")"; done
