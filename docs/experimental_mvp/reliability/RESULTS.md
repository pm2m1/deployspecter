# Reliability gate for the optional isolated suite (2026-11-06)

## Verdict: UNRESOLVED INTERMITTENT FAILURE

The one failure seen earlier (1 of 13, on 2026-11-06, in the run immediately after the core suite) **did not recur** in 58 bounded, evidence-complete invocations. It was not identified, and it is **not fixed**. No code was changed in response to it, and no regression test was added, because a regression test needs a reproduced cause.

### The original failure (what is and is not known)
- It occurred in `tests/thirdparty_isolated`, run straight after `tests/experimental`, at revision `cc173db`, with `-q` output piped through `tail -1`. That output showed only `1 failed, 12 passed in 107.62s`.
- The test's name, its traceback and its tmp_path are lost. The output was truncated, and pytest's tmp retention (3 sessions) had removed the directory before the failure was looked into.
- The run took 107.62s, against 104.4–104.9s for passing runs. No other signal exists.
- **Lesson:** the stress scripts below never pipe pytest's output, and they persist `--basetemp`.

## Runs

| Run | Design | Invocations | Result |
|---|---|---|---|
| `stress_1/` | 8 attempts of the whole suite (13 tests). Odd attempts run the core suite then the optional suite (the original failure's conditions); even attempts run the optional suite alone. | 8 (104 tests) | **104/104 passed**. All 4 core runs passed (373, then 392 tests once this sprint's offline tests landed). |
| `stress_2_targeted/` | 25 × each of the two most timing-sensitive tests, one pytest process each | 50 | **50/50 passed** |

**The timing-sensitive tests.**
- `test_an_interrupted_run_still_tears_down` sends SIGINT about 1.7s into a run of about 2.7s, so its `returncode != 0` check depends on the signal arriving before the runner exits.
- `test_third_party_path_gives_its_expected_verdict[chancy_runtime]` relies on `docker run --rm` auto-removal racing the runner's `docker rm -f` and then `network rm`.

Both are hypotheses for the original failure. **Neither was observed.**

## Evidence per invocation
Each `attempt_NN/` holds:
- `stdout.txt`, `stderr.txt` and `junit.xml` (the exact node ids);
- `exit_status`, taken from `$?` directly;
- `git_revision`, `condition` or `node_id`, and the start and finish times;
- `snapshot_before.json` and `snapshot_after.json`, plus `snapshot_between.json` after the core suite.

The snapshots (`research_tools/stress_snapshot.py`) record:
- Docker containers and networks;
- the disposable PostgreSQL's status, start time, restarts and OOM flag;
- its sessions, locks, prepared transactions, schemas and `public` objects;
- its data tmpfs, database size and WAL LSN;
- host disk and memory;
- the protected container's id and start time.

Each test's tmp_path persists outside the repo (`--basetemp`, the scratchpad).

**State checks across all 116 snapshots:**
- 0 leftover `ds-mvp-iso` containers or networks;
- 0 lingering sessions besides the probe;
- 0 prepared transactions;
- `public` empty;
- protected `deployspecter-pg` unchanged (`27b797df64eb…`, started `2026-11-01T01:14:47.681311882Z`).

**Seeds and concurrency.** The suite has no pytest-level randomization or xdist. Run seeds are fixed in the runner arguments, and tests run serially.

## What would change the verdict
A failing attempt, with its preserved evidence. The protocol is:
1. classify the cause;
2. write a regression test that fails without the correction;
3. correct it in a new module or commit;
4. rerun both stress designs.

Until then, a single isolated-suite failure in a confirmatory run must be preserved and reported, never retried away.
