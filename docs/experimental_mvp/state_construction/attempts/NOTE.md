# Development attempts (kept as they ran; none edited)

These ran on 2026-11-06 against the disposable PostgreSQL (127.0.0.1:55441, `ds_mvp_disposable`), under
`research_tools/pg_lock.py`. The code was this branch's uncommitted working tree at the time, before the first
state-construction commit. The final evidence (`../runs/`) was produced after the commit, from a clean tree.

| Attempt | Command (mode) | Exit | What it showed |
|---|---|---|---|
| 00 | ad-hoc `generic.construct_typed` trial (printout only) | 0 | the first fixture classifications, and that Phase 9B refuses NaN and a `json` column |
| 01 | `run_state_construction.py fixtures` | 0 | all 16 fixtures as expected (before the `jsonb` fixture was added) |
| 02 | `run_state_construction.py chancy` | **1** | **failed as designed**: the pinned `chancy_jobs` column types came from Chancy's migrations v1 and v8 only, but v5 turns kwargs/limits/meta/errors into `jsonb`. The live-catalog precondition refused before anything was constructed; no worker ran. The pin was corrected, and an exact `jsonb` tag added. |
| 03 | `run_state_construction.py chancy` | 0 | calibration (0 differences), the constructed run and the negative control, all as expected |
| 04 | live pytest (`test_state_construction_live.py`), first run | **1** | 2 failures. (a) The pipeline refused at planning (`indeterminate`, `trigger_effect_unknown`), because another schema (`mvp_app`, left by the core suite's mixed-version app) holds a user trigger, and the live catalog export is database-wide for triggers. A correct fail-closed outcome, but the test assumed an empty database. (b) A test bug: it applied drift inside an open transaction, which `typed.verify` (REPEATABLE READ) can't start in. |
| 05 | live pytest, second run | **1** | 1 failure: a test expectation (the unknown-field refusal happens at `plan_state`, not `authorize`) |
| 06 | live + offline pytest, third run | 0 | 68 passed (54 offline, 14 live) |

The failed attempts' causes were classified from their own output, which is kept here in full.
