#!/bin/bash
# EXPERIMENTAL bounded reliability stress run of the optional isolated suite, with complete per-attempt evidence.
#
#   research_tools/stress_isolated.sh SDIST_DIR OUT_DIR BASETEMP_ROOT ATTEMPTS
#
# Attempts alternate between two conditions:
#   A (odd):  the core experimental suite, then immediately the optional isolated suite (the conditions under which the
#             one unidentified failure occurred);
#   B (even): the optional isolated suite alone.
# Each attempt writes, in OUT_DIR/attempt_NN/:
#   * stdout and stderr, JUnit XML, the exit status (taken from $? directly, never through a pipe);
#   * snapshots before and after (research_tools/stress_snapshot.py);
#   * the condition and the git revision.
# Each test's tmp_path (the isolated runner's reports) persists in BASETEMP_ROOT/attempt_NN (pytest --basetemp).
# No attempt is retried; a failure is kept as it is. The disposable PostgreSQL must already be running (55441).
set -u
SD=$1; OUT=$2; BT=$3; N=$4
cd "$(dirname "$0")/.."
mkdir -p "$OUT" "$BT"
PY="env -u MODEL_API_KEY -u DATABASE_URL DS_THIRDPARTY_SDIST_DIR=$SD .venv/bin/python"
for i in $(seq 1 "$N"); do
  A=$(printf "%s/attempt_%02d" "$OUT" "$i"); T=$(printf "%s/attempt_%02d" "$BT" "$i")
  mkdir -p "$A"
  if [ $((i % 2)) -eq 1 ]; then COND=A_core_then_optional; else COND=B_optional_alone; fi
  echo "$COND" > "$A/condition"; git rev-parse HEAD > "$A/git_revision"; date -u +%Y-%m-%dT%H:%M:%SZ > "$A/started_at"
  $PY research_tools/stress_snapshot.py "$A/snapshot_before.json"
  if [ "$COND" = A_core_then_optional ]; then
    $PY -m pytest tests/experimental -q -p no:cacheprovider -rA --junitxml="$A/core_junit.xml" \
      > "$A/core_stdout.txt" 2> "$A/core_stderr.txt"
    echo $? > "$A/core_exit_status"
    $PY research_tools/stress_snapshot.py "$A/snapshot_between.json"
  fi
  $PY -m pytest tests/thirdparty_isolated -v -p no:cacheprovider -rA --junitxml="$A/junit.xml" --basetemp="$T" \
    > "$A/stdout.txt" 2> "$A/stderr.txt"
  echo $? > "$A/exit_status"
  $PY research_tools/stress_snapshot.py "$A/snapshot_after.json"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$A/finished_at"
  echo "attempt $i $COND optional_exit=$(cat "$A/exit_status") core_exit=$(cat "$A/core_exit_status" 2>/dev/null || echo -)"
done
