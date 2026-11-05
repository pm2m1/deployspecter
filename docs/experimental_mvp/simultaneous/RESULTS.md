# Simultaneous mixed-version Chancy execution: results (EXPERIMENTAL, NONBLIND, 2026-11-05)

**Status: true simultaneous mixed-version application execution is LIVE-TESTED.**

Chancy 0.25.1 and Chancy 0.26.0 workers ran at the same time against one disposable PostgreSQL. They ran in separate restricted containers, and every database statement was granted by one coordinator. All of it was corroborated against the server log.

This is an experimental research pilot. It is **not** blind, confirmatory or independent. It is **not** Gate A2 evidence, and **not** under any adopted protocol.

## Provenance

| | |
|---|---|
| Final code revision | `d3630b57ebf9a21560724b9895577c2b3945a8fc` (clean) |
| Frozen properties | `PROPERTIES.md` sha256 `d701a2f4…22d4cd`. Checker `chancy_properties.py` `72320a82…75e9`. First frozen at `03967c4`, before any simultaneous run; one recorded amendment to the read query, before the first window (`61fb31a`) |
| `uv.lock` | `356ef882b363778afddc685d2c9ced92d5e67ca3603e0cc2e2cbd1b0555cc2a2` |
| Container A | `ds-mvp-iso:69050a83c73a90a8`, image `sha256:a9c0e6d4…96315`: Chancy 0.25.1 from sdist `264ed4f7…122d7`. Verified inside the container by source hashes: `app.py` `e18bdf44…`, `worker.py` `2742f88d…`, `migrate.py` `b25d63e0…`, `asyncex.py` `b3617071…` |
| Container B | `ds-mvp-iso:71640ee3a572293a`, image `sha256:fee4cd98…ba54`: Chancy 0.26.0 from sdist `ac98b9c5…aef06`. Source hashes: `app.py` `403e4712…`, `worker.py` `0f541cbf…`, `migrate.py` `2f19e937…`, `asyncex.py` `f6495e78…` |
| Coordinator | the B image, running only `deployspecter.experimental.coordinated` (`ds-coord/1`); no database credentials |
| Base image / PostgreSQL | `python@sha256:b18b261e…31ab30`; `postgres:16@sha256:1beaa308…77c61c` (disposable, 55441) |

## 1. Proof that both versions were active at the same time (final scenario set `simultaneous_scenarios_4`)

**Readiness.** In every two-worker scenario, a window gate held every producer statement until both workers, 0.25.1 in A and 0.26.0 in B, had each completed a claim query. So both were started, registered and polling before the first job existed.

**The window.** Inside it, statements of the two releases alternate in one global grant order, on distinct sessions:

| Scenario | Sessions (one per actor) | Window grants: 0.25.1 / 0.26.0 | Version switches in the window | Claim queries in the window: 0.25.1 / 0.26.0 | Statements corroborated |
|---|---|---|---|---|---|
| `old_to_new` | 3 | 65 / 65 | 129 | 8 / 7 | 328 |
| `new_to_old` | 3 | 63 / 63 | 125 | 7 / 7 | 328 |
| `distinct` | 4 | 48 / 48 | 95 | 5 / 5 | 310 |
| `compete` | 4 | 35 / 35 | 69 | 4 / 4 | 286 |
| `retry_handoff` | 3 | 28 / 29 | 56 | 4 / 4 | 277 |
| `cancel_new_old` | 2 | 23 / 4 | 7 | 3 / 0 | 80 |
| `cancel_old_new` | 2 | 4 / 23 | 7 | 0 / 3 | 62 |
| `cancel_new_new` / `cancel_old_old` | 2 (one release only: controls) | n/a | 0 | n/a | 59 / 71 |

**Real old and new operations overlap inside one window.** Yes. For example, in `cancel_new_old` (see `--interleaving`):

| Grant | Container | Version | Statement | Detail |
|---|---|---|---|---|
| 40 | A | 0.25.1 | claim | 1 row, holding the row lock |
| 41 | B | 0.26.0 | `cancel_job` UPDATE | blocks on that lock |
| 42 | A | 0.25.1 | COMMIT | decided with `blocked = {"new_push": ["old_worker"]}` |
| 44 | B | 0.26.0 | COMMIT | |
| 53 | A | 0.25.1 | unfenced completion flush | |

So:
- the two processes' transactions interleave;
- one waits on the other's row lock;
- the wait is seen by B's own monitor and attributed to A's actor through the coordinator's global pid map.

Lock-wait behaviour is preserved: PostgreSQL resolved it, and the scheduler only ordered the grants.

## 2. Schedules attempted and completed

| | Runs |
|---|---|
| Simultaneous run reports, every set | **141**: 124 SOUND, 17 REFUSED, 0 INCOMPLETE |
| Harness crashes before any simultaneous statement | 2 (bring-up attempt 1, `simultaneous_mutant_compete_1`) |
| Diagnostic runs (`simultaneous_diagnostics_1`) | 7: 4 SOUND, 3 REFUSED |
| A pytest run whose temporary directory is gone | 1 REFUSED (corroboration ok) |
| **Final sets at `d3630b5`** | **35 simultaneous + 1 sequential: 36 SOUND, 0 refused, 0 incomplete.** 7428 simultaneous statements corroborated, 0 typed parameters mismatched, 0 unchecked |

Every refusal was a harness fault, and every one failed closed. None was retried in place; each fix was followed by a new set:

| Set | Refused | Cause | Fixed in |
|---|---|---|---|
| `simultaneous_scenarios_1` | 2 | a quiesce hold caught a COMMIT (idle in transaction); a long in-transaction gate in the retry scenario | `fd592d7` |
| `simultaneous_mutant_compete_2` | 10 | the claim-query markers matched `SKIP LOCKED`, which the mutant removes, so the window never opened (stall) | `f2cb878` |
| `simultaneous_scenarios_3`, `explore_compete_3`, `mutant_compete_4` | 3 + 1 + 1 | a stale grant after a framework's gate cancel (protocol); a COMMIT cancelled before its grant left psycopg's transaction open | `d3630b5` |
| `simultaneous_diagnostics_1` | 3 | the hold blocked 0.26.0's own drain, stalling the lockstep | `4e94b94` (deferral) |

**Superseded sets: caveat.** In the sets before `4e94b94`, 0.26.0's `stop()` often timed out its drain, so many SOUND runs there were not quiescent, and **P6 was not evaluated** in them. Their notes said "clean" for some of those runs without saying so; this is the correction. In the final sets, P6 was evaluated in **35 of 35** runs.

## 3. Server-log reconciliation (final sets)

For every simultaneous run:
- every session was explained: the actors of both releases, plus both processes' lock monitors;
- every statement and every typed parameter matched (0 mismatched, 0 unchecked);
- every error matched;
- the server's statement order agrees with the coordinator's global grant order;
- no session outlived the run, and no session was left after teardown (`pg_stat_activity` checked);
- containers and networks were removed, and `deployspecter-pg` was unchanged.

A run with any unexplained connection or row is refused. 13 of the 17 refusals above were refused by this check, or by the coordinator.

## 4. Independent property checks (frozen P1–P8; final sets)

| Scenario | Result |
|---|---|
| `old_to_new`, `new_to_old`, `distinct` | clean: every job ran once, on the intended version. Jobs crossing versions ran (P5) |
| `compete` (alternate, 10 seeds, 3 repeats) | clean in 14 of 14: each job ran exactly once, by one of the two versions |
| `retry_handoff` | clean: failed under 0.25.1 (`retrying`), retried and succeeded under 0.26.0. Attempts 2, errors 1 (P8) |
| `cancel_new_new` | clean: **the documented 0.26.0 guarantee holds**. The job stays `failed` with attempts 0, although its handler ran |
| `cancel_old_old` | **P7 violated** (class X): 0.25.1's own late completion overwrote its cancel |
| `cancel_new_old` | **P7 violated** (X): the 0.25.1 worker's unfenced completion overwrote a 0.26.0 cancel. 3 of 3 repeats, plus the scenario run |
| `cancel_old_new` | **P7 violated** (X): a 0.25.1 cancel, which doesn't invalidate the claim, was overwritten by the 0.26.0 worker's fenced completion |

**Triage of the P7 results.** None is claimed as a Chancy defect. The competing explanations, recorded before running in `PROPERTIES.md`, all apply:

1. **Undocumented deployment.** Simultaneous old and new workers are outside the documented upgrade procedure: the 0.26.0 changelog says "Stop your workers" before migrating.
2. **No promise without notifications.** 0.25.1's `cancel_job` says cancelling a running job needs notifications, and the harness disables them.
3. **Already known.** The 0.26.0 changelog already says it "Fixed cancellation races that could overwrite cancelled results". `cancel_old_old` reproduces that described behaviour on real 0.25.1 code, but the case was chosen after reading the changelog.

**What the cells do show** (corroborated, at the statement level): 0.26.0's cancellation guarantee holds only when both the canceller and the worker are 0.26.0. In a mixed deployment, either version's cancel can be lost.

## 5. Original versus mutation control (`simultaneous_explore_compete_4` vs `simultaneous_mutant_compete_5`)

Both arms used scenario `compete`, seeds 0–9, the same revision and the same harness.

| | Original 0.25.1 + 0.26.0 | Original 0.25.1 + mutant 0.26.0 (`no_skip_locked_claim`) |
|---|---|---|
| SOUND | 10 / 10 | 10 / 10 |
| Statements corroborated (mismatched) | 2283 (0) | 2246 (0) |
| Runs with a violation | **0** | **2** (seeds 1 and 4) |
| Violated | none | **P2**: one job run by both versions with `max_attempts = 1` (seed 4: two jobs) |
| Time to first violation | none | seed 1, 244 grants, 15.4 s |

**Mechanism** (seed 1, `simultaneous_mutant_compete_3`, the same in `_5`):
1. 0.25.1's locked claim is granted.
2. The mutant's unlocked claim selects the same row, then waits on 0.25.1's row lock (attributed across containers).
3. 0.25.1 commits.
4. The mutant's UPDATE re-checks only the `id` join, and claims the job too.

The same seeds on the released code never do this. The mutant is an injected control: **not a historical Chancy defect, and not Muse evidence.** P3 held even in the violating runs, so the duplicate is visible only through execution counting.

## 6. Repeated-run stability (from verified identical initial states)

| Set | Outcome (canonical final state) | Statement trace (transaction order) |
|---|---|---|
| `simultaneous_repeat_compete_3`, 3× alternate | 3 of 3 identical | 2 of 3 identical (one run had 6 more polling statements) |
| `simultaneous_repeat_cancel_new_old_3`, 3× alternate | 3 of 3 identical (P7 each time) | 2 of 3 identical |
| `simultaneous_explore_compete_4` vs the same seeds earlier (`_2`, `_3`) | the same verdicts | most seeds give the same transaction order across sets |

**Not claimed:** global deterministic scheduling. The coordinator controls statement grants. Which task in a worker asks next, and Chancy's timers, are not controlled.

## 7. Quiescence and cancellation

Final sets:
- **Stops:** every worker stopped through Chancy's own `stop()`, cleanly (`worker_stopped` true); P6 was evaluated in 35 of 35.
- **Mid-flight cancellations:** 0 statements cancelled while on the wire.
- **Deferred cancellations:** 3, each deferred to its statement boundary and recorded (`provenance.cancellation`).
- **Declines:** 2 stale grants declined and decided again.
- **The hold:** `cancelled_while_held` 0; the hold is no longer used by `stop_worker`.

Adversarial controls, live tests:
- With both protections disabled, a mid-statement cancel is recorded as such, and the server log refuses the run.
- With deferral alone, the same cancel waits for the statement and the log agrees.
- A COMMIT cancelled before its grant rolls the transaction back, ungranted and recorded.

## 8. Isolation and limits

- **Isolation:** the existing runc mode only. It is **not hostile-grade isolation**: the containers share the Docker Desktop VM kernel, and hostile-code testing stays BLOCKED (`isolation_runtime_scan_2`). The application code was the pinned, reviewed public releases.
- **Credentials:** no model credentials or host keys in any container, and no database credentials in the coordinator.
- **NONBLIND:** the scenarios, gates and properties were written by the harness author after reading Chancy's code and changelog. Oracle authorship is not independent.
- **Bounded exploration:** 10 seeds per arm. This is not exhaustive.
- **Scope:** only Chancy's `AsyncExecutor`; `notifications = False`; one connection per actor (the stepped pool).

## 9. Reproduce

```bash
cd ~/Desktop/deployspecter-real-app-mvp
research_tools/mvp_demo.sh OUT simultaneous        # its own disposable PostgreSQL, 5 scenarios, teardown
# or, with research_tools/mvp_pg.sh start / stop around it:
research_tools/isolated/fetch_pinned.sh SDISTS
python research_tools/isolated/run_coordinated.py run --scenario cancel_new_old \
  --chancy-old-sdist SDISTS/chancy-0.25.1.tar.gz --chancy-sdist SDISTS/chancy-0.26.0.tar.gz --out OUT/x
python research_tools/isolated/run_coordinated.py explore --scenario compete --seeds 0-9 [--mutant no_skip_locked_claim] \
  --chancy-old-sdist SDISTS/chancy-0.25.1.tar.gz --chancy-sdist SDISTS/chancy-0.26.0.tar.gz --out OUT/y
python research_tools/coordinated_summary.py OUT/y          # aggregate
python research_tools/coordinated_summary.py --interleaving OUT/x/00_alternate
```

## 10. Proposed future experiment (recorded, not run; no Meta credits spent)

**Question.** Can an independently specified workload generator propose the *gate placements*: which statement of which version waits for which marker? Those placements, not the job payloads, are what exposed the cancel-overwrite cells here.

**Preconditions:**
- an independently authored property set;
- a sealed case (`handoff/`);
- an adopted protocol.

Without these, a Muse comparison would repeat the earlier inconclusive one.

## Evidence directories (sha256 over all files, sorted)

| Directory | Digest |
|---|---|
| `simultaneous_scenarios_4` | `5527115e252ce825` |
| `simultaneous_explore_compete_4` | `0e36911d31ccb327` |
| `simultaneous_mutant_compete_5` | `9559c4db76e37db8` |
| `simultaneous_repeat_compete_3` | `bb20968e043461bc` |
| `simultaneous_repeat_cancel_new_old_3` | `346a1eda0a53b54f` |
| `sequential_baseline_repro_4` | `20dc0780674181ff` |
| `demo_simultaneous_1` | `339440ba77613f3d` |
| `sequential_baseline_repro_1` | `5cc8161636f9d255` |
| `sequential_baseline_repro_2` | `2ebd94b8c0653723` |
| `sequential_baseline_repro_3` | `6c8788d3aa3649fb` |
| `simultaneous_bringup_1` | `6c34fddc371db9a6` |
| `simultaneous_scenarios_1` | `51dac1e71d8f48ef` |
| `simultaneous_scenarios_2` | `90be6d3864520149` |
| `simultaneous_scenarios_3` | `4b2c59f16de0bf98` |
| `simultaneous_explore_compete_1` | `b504374cc35a097b` |
| `simultaneous_explore_compete_2` | `8efc0fa86c305f79` |
| `simultaneous_explore_compete_3` | `7680fe07cb97245f` |
| `simultaneous_mutant_compete_1` | `f79f924f743f30b3` |
| `simultaneous_mutant_compete_2` | `e2a4d4d93b31d2ab` |
| `simultaneous_mutant_compete_3` | `78c5ca61de7a53c9` |
| `simultaneous_mutant_compete_4` | `d4029642507cdcff` |
| `simultaneous_repeat_compete_1` | `b4dba54a7f6b1763` |
| `simultaneous_repeat_compete_2` | `258256151a13c9a9` |
| `simultaneous_repeat_cancel_new_old_1` | `07ab68967e9c7b0d` |
| `simultaneous_repeat_cancel_new_old_2` | `aeba979c3ab9cc2e` |
| `simultaneous_diagnostics_1` | `6a23a01230d24a24` |
