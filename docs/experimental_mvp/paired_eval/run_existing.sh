#!/bin/bash
# Paired comparisons on the repository's existing development outputs (all NONBLIND). Reproduce from any checkout:
#   PY=/path/to/.venv/bin/python docs/experimental_mvp/paired_eval/run_existing.sh
set -u
cd "$(dirname "$0")/../../.."
PY=${PY:-.venv/bin/python}
py() { env -u MODEL_API_KEY -u DATABASE_URL PYTHONPATH="$PWD/src" "$PY" "$@"; }
O=docs/experimental_mvp/paired_eval/existing_1
rm -rf "$O"; mkdir -p "$O"
py research_tools/paired_eval.py --from-existing "$O/runs.jsonl" > "$O/convert.stdout"; echo $? > "$O/convert.exit"
run() { name=$1; shift
  py research_tools/paired_eval.py --runs "$O/runs.jsonl" "$@" --out "$O/$name" > "$O/$name.stdout" 2> "$O/$name.stderr"
  rc=$?; echo "$rc" > "$O/$name.exit"; echo "$name exit=$rc $(cat "$O/$name.stderr")"; }
run explore_vs_forced_unequal_budget --arms seeded_explore forced_schedule_fix_informed
run explore_vs_forced_budget3 --arms seeded_explore forced_schedule_fix_informed --budget 3
run curated_vs_muse_budget2 --arms curated muse --budget 2
run systematic_vs_muse_budget2 --arms systematic muse --budget 2
run curated_vs_systematic_budget2 --arms curated systematic --budget 2
