#!/bin/bash
# EXPERIMENTAL bounded targeted stress: run each given pytest node id ATTEMPTS times, one pytest process per attempt,
# with the same per-attempt evidence as stress_isolated.sh (stdout, stderr, JUnit XML, exit status from $? directly,
# snapshots before and after, git revision, persistent --basetemp). No retries.
#   research_tools/stress_targeted.sh SDIST_DIR OUT_DIR BASETEMP_ROOT ATTEMPTS NODEID...
set -u
SD=$1; OUT=$2; BT=$3; N=$4; shift 4
cd "$(dirname "$0")/.."
mkdir -p "$OUT" "$BT"
PY="env -u MODEL_API_KEY -u DATABASE_URL DS_THIRDPARTY_SDIST_DIR=$SD .venv/bin/python"
k=0
for i in $(seq 1 "$N"); do
  for node in "$@"; do
    k=$((k + 1)); A=$(printf "%s/attempt_%03d" "$OUT" "$k"); T=$(printf "%s/attempt_%03d" "$BT" "$k")
    mkdir -p "$A"; echo "$node" > "$A/node_id"; git rev-parse HEAD > "$A/git_revision"
    $PY research_tools/stress_snapshot.py "$A/snapshot_before.json"
    $PY -m pytest "$node" -v -p no:cacheprovider -rA --junitxml="$A/junit.xml" --basetemp="$T" \
      > "$A/stdout.txt" 2> "$A/stderr.txt"
    echo $? > "$A/exit_status"
    $PY research_tools/stress_snapshot.py "$A/snapshot_after.json"
    echo "attempt $k exit=$(cat "$A/exit_status") $node"
  done
done
