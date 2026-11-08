# Case `saq-ad8d1c8`: the Postgres dequeue over-claims, stranding jobs `active` (NONBLIND development case)

**Frozen before the case's first live run.** Later corrections go in `ERRATA.md`.

## The upstream defect

| | |
|---|---|
| Repository | https://github.com/tobymao/saq (MIT) |
| Fix | `ad8d1c8d3cfdf57d6800098ff1680862a964f9fc` (PR #311): "chore: support python 3.11-3.15, pin ruff, fix flaky queue tests". Its queue part: "postgres dequeue could over-serve waiters. _poll refetched without checking its own buffer, and a schedule() tick double-served an in-flight dequeue, flipping jobs active that no consumer ever took - so a burst worker with max_burst_jobs left them stranded active." First release with it: v0.26.5 |
| Confound | A mixed commit: it also drops Python 3.9/3.10 (typing shims, `datetime.UTC`), pins ruff and deflakes tests. The saq/ changes outside the dequeue path are typing and import changes, plus `Worker.start` capping process slots at `max_burst_jobs` (R1 already caps `concurrency` in `__init__`). Attribution rests on the commit pair, which isn't a single-hunk change |

| Slot | Commit | Tree | Archive sha256 (normalized `git archive` of `saq/`) |
|---|---|---|---|
| R1 (buggy, the fix's first parent) | `525acc9251c95395b14090ebe481dac6a79062a5` | `c7ecb1cc79b9cbcd2e8e6bcf1c2d48a153e1fdd6` | `a1c757de7ccfe744ebc1dfb770936893179d5dfa63f0ff7092e4ed28855c93d9` |
| R2 (fixed, the fix commit) | `ad8d1c8d3cfdf57d6800098ff1680862a964f9fc` | `1b9c5f3daa0646966cf86724fb3145efb708795a` | `047b052dc11eff0946bf8b1463cded16b3f5fc149f4ea0436f2201f833e39acb` |

Reproduce the archives: `python -I research_tools/nbc/pin_saq.py --repo <mirror of https://github.com/tobymao/saq> --out <dir>` (verifies commit, tree and sha256).

## Contract and property

Single-version: each revision creates and migrates its own fresh schema; nothing mixes versions. The property (`oracle.py`, B0-B3) comes from the Worker docstring ("burst: whether to stop the worker once all jobs have been processed"; "max_burst_jobs: the maximum number of jobs to process in burst mode") and the maintainers' `test_max_burst_jobs` (3 jobs, `max_burst_jobs=1`: one COMPLETE, two QUEUED) and `test_burst`/`test_burst_poll_interval`: after the burst worker stops, every job is `complete` (run once) or `queued` (never run), none is left `active`, and at most `max_burst_jobs` complete.

## Workloads (RA-DET, frozen in `saq_burst_ad8d1c8.py`)

Shape `burst`: a producer enqueues `jobs` jobs; then (gate) one saq burst worker on the same queue drains it. Poll mode (`poll_interval > 0`) is forced: the harness can't instrument saq's LISTEN connection, so the maintainers' tests are translated to polling.
- W1 (replicate 0): `test_max_burst_jobs`: 3 jobs, concurrency 1, `max_burst_jobs` 1, dequeue timeout 3.0 s, poll 1.0 s, schedule timer 1 s.
- W2 (replicate 1): 3 jobs, concurrency 2, `max_burst_jobs` 2, otherwise W1.
- W3 (replicate 2): `test_burst_poll_interval`: 2 jobs, no cap, timeout 2.0 s, poll 0.5 s.

All three keep the dequeue timeout longer than the poll interval, deliberately: a timeout shorter than a claim exercises the separate, later-fixed defect of `saq-c570ba4`, which is still present in this case's R2 and would confound the control.

## Predictions (written before running)

- R1: the worker's schedule tick claims a job while the process task's poll loop sleeps; the poll loop then refetches before checking its buffer and claims another; the worker stops after `max_burst_jobs`, leaving a job `active` (B1). Expected in W1 and W2, not in W3 (no cap: the buffered extra job is still served).
- R2: no job is left `active` in any workload.
- The explorer has little freedom here: the gate puts the producer first, and the race is between the worker's own tasks on its one connection, which the coordinator doesn't order. Seeds mostly don't change the schedule; in-process timing may.

## Competing explanations, recorded before running

1. The race depends on in-process timing (the tick fires at worker start; the poll loop sleeps 1 s). If the process task wins the dequeue lock first, neither revision over-claims, and R1 may show nothing: NOT REPRODUCED would be the honest result.
2. Coordinated statements are slow (about 50 ms per grant), which stretches the claim transaction. This changes timing, not the code path.

## How it runs

`run_nbc.py run --arms RA-DET` under `pg_lock.py`, with the plan's §5 budgets (B = 12 per workload: 3 replicates × 4 schedules, one workload per replicate; T = 900 s per case and arm; never retried). Each schedule is one restricted container (CPython 3.12.16 runner image, saq tree copied in, hash-pinned dependencies, no network) plus the coordinator, on a fresh schema `saq_nbc` migrated by that revision's own `connect()`; every statement is granted by the coordinator under the seeded explorer (`real_executor.seed_for`); the merged trace is corroborated against the server log; the oracle decides only a SOUND run. Integration machinery: `research_tools/nbc/integrations/saq_common.py`, `saq_shapes.py`; container side `src/deployspecter/experimental/thirdparty/saq_coord_adapter.py` (a declared variant of the pilot adapter, with no code deviation in saq) and the `saq_coordinated` mode of `smoke_inside.py`.

**Label: NONBLIND development.** The case was chosen after reading the fix. The oracle is developer-written from the documented contract. A REPRODUCED result reproduces a documented upstream defect; it is never a new finding.

## Exposure (who read what, before which step)

All by the Track A agent on 2026-11-08 (UTC), recorded in `case_spec.json` `exposure`: the 12 screened candidates' messages and stats (04:49Z); this fix's diff and the neighbouring fixes' diffs (04:51-04:54Z); saq's docs, the Worker docstring and the maintainers' burst tests (04:54Z); the full parent `postgres.py`/`worker.py` sources and the diffs between the pinned revisions (05:00-09:40Z); bring-up runs on v0.26.5 (`../../bringup/`). All of it before the integration, workloads and oracle were written, and before any live run of R1 or R2. Training-data exposure of the public repository can't be excluded.
