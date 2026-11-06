# Reliability gate for the optional isolated suite (2026-11-06)

## Verdict: UNRESOLVED INTERMITTENT FAILURE

> **Classification, confirmed 2026-11-06:** the original failure stays **UNRESOLVED**. The signal-handling teardown defect found later (addendum below) is a **confirmed separate finding**, demonstrated and fixed with a regression that fails on the unfixed runner. It is **not** proof that the original failure was explained.

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

## 2026-11-06 addendum: probes of the two suspected races (`race_probes_1/`, `stress_3_after_teardown_fix/`)

**The original failure is still UNRESOLVED.** Its test name and traceback are lost, so it can't be attributed. A **teardown defect in the suspected area was demonstrated and fixed**. Whether that defect caused the original failure is **not proven**.

### Race 2: `docker run --rm` auto-removal vs the runner's `docker rm -f` / `network rm`. Not demonstrated.
- **Probe:** `research_tools/probe_teardown_race.py`, 45 iterations, at the runner's exact lifecycle and flags.
- **Variants:** A, the container exits at once; B, a 0.3 s container; C, no `docker rm -f` at all.
- **Result:** `rm -f` returned 0 in 30/30 iterations. `network rm` returned 0 in 45/45, **including 15/15 with no `rm -f`**. Nothing was left.
- **Interpretation:** on this Docker Desktop, `docker run --rm` returns only after the container and its endpoint are gone. `teardown_race/`.

### Race 1: SIGINT timing in `test_an_interrupted_run_still_tears_down`. A defect demonstrated.
- **Natural timeline** (`interrupt_race/`, 20 attempts):
  - the container appears about 0.68 s after start and is gone at about 2.47 s;
  - the runner exits 0 at about 2.72 s, about 2.04 s after the container appears.
- **Signals at 0–2.0 s after appearance:** 18/18 runs exited by SIGINT (-2), with no leftovers.
- **A signal at 3.0 s:** the runner had already exited 0, which is the test's `returncode != 0` failure mode. It needs the test's detection latency to exceed about 2 s, against about 0.5 s plus two `docker` calls in normal operation. Not observed in the test itself.
- **Defect demonstrated.** In the dense probe (`interrupt_race_dense/`), a SIGINT arrived 0.04 s after the container disappeared (delay 1.85 s), inside `run_smoke`'s `finally`:
  - the KeyboardInterrupt surfaced in `docker network disconnect`;
  - it skipped `network rm`;
  - it left network `ds-mvp-iso-5756ea22` **with the disposable PostgreSQL container still attached**.

  The test's `leftovers() == ("", "")` would fail, and so would every later test that checks leftovers. The traceback is in `attempts.jsonl`, and the recorded cleanup in `CLEANUP_OF_LEFTOVER.txt`. The same unguarded teardown exists in `run_coordinated.simultaneous`.
- **Fix** (`98c8e29`, `research_tools/isolated/signal_safe.py`):
  - both teardowns run inside `deferred_signals()`;
  - SIGINT and SIGTERM are held until the teardown completes, then re-delivered once, so the run still ends by the interrupt;
  - the runner image is unchanged.
- **Regression** (`tests/experimental/test_teardown_signal_safety_offline.py`): it uses a real SIGINT during teardown with a recording fake `docker`.
  - On the unfixed runner: **pytest exit 1, 4 failed, 1 passed**. Teardown stopped at the interrupted command. The coordinator independently confirmed this on `e120a92`.
  - Fixed: **exit 0, 5 passed**.
- **Limits.** Only the main thread can defer. A terminal Ctrl-C sent to the whole process group can still kill a teardown `docker` child, which then fails visibly.

### After the fix (revision `c825c97`)

| Run | Evidence | Result |
|---|---|---|
| Dense interrupt probe | `interrupt_race_dense_after_fix/` | 16/16 attempts, **0 leftovers**, including signals landing 0.0–0.21 s after the container was gone, the teardown window. In 5 attempts the signal arrived after the runner had exited 0 (the test-timing boundary above) |
| Whole-suite stress (core, then isolated), 3 × 1 attempt | `stress_3_after_teardown_fix/suite_run_{1,2,3}` | core 411 passed (exit 0) ×3; isolated 13 passed (exit 0) ×3 |
| Targeted interrupt test | `stress_3_after_teardown_fix/interrupt_targeted` | 10/10 exit 0 |

**Per-attempt evidence and state:**
- Each attempt has stdout and stderr, JUnit, the exit status, the revision, and before/between/after snapshots.
- No leftover `ds-mvp-iso` container or network, no foreign session, an empty `public` schema, the disposable PostgreSQL never restarted, and the protected container unchanged.

**Suite run 3's wall time** (23:45:52Z → 01:47:27Z) came from gaps *between* processes, consistent with the host being suspended during a usage-limit stop: 43 min before `snapshot_between`, then 70 min. Its tests ran 111.6 s (core) and 107.5 s (isolated), with normal per-test durations.

**Zero failures in 58 + 29 later invocations does not resolve the original failure.**

### Stronger isolation: BLOCKED ON ENVIRONMENT
Docker Desktop (LinuxKit kernel 7.0.12) offers only `runc`, and its security options are seccomp (builtin) and cgroupns. None of these is installed:
- `runsc` (gVisor), Kata, Firecracker;
- Lima or colima, podman, multipass, vfkit, krunvm, qemu, OrbStack, nerdctl or finch;
- any VM application.

Adding a runtime would mean changing Docker Desktop's shared daemon, which is out of bounds. The boundary stays the restricted runc container.
