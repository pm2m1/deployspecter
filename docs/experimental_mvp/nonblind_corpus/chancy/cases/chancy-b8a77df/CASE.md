# ds-nbc/1 case `chancy-b8a77df`: pushes of existing unique keys and the worker's update batch deadlock (NONBLIND development)

**Frozen before this case's first live run (bring-up included).** Changes go in `ERRATA.md`, never here.

**Label: NONBLIND development.** Chosen by screening (`../../SCREENING.md`, candidate 1 of 5) after reading the fix's
message, diff, regression test and changelog entry. Not blind, confirmatory or independent evidence. A reproduction
is of a documented upstream defect, never a new finding.

## Upstream

| | |
|---|---|
| Repository | `https://github.com/tktech/chancy` (MIT), public mirror (bare) |
| Fix commit (R2) | `b8a77df3a514ce19ebcaf31453cff8da8af9f212` (2026-09-10): "Acquire job row locks in a consistent order to prevent deadlocks (#89)" |
| Buggy parent (R1) | `6716e0db1924d3393e51cd18076415e340b7c508` (its first parent) |
| Issue / PR | #89 |
| Changelog | 0.26.0 (entry `4fe6633`): "Pushing jobs with unique keys and the worker's batched job updates now lock rows in the same order, fixing a deadlock between overlapping pushes and updates that could fail either side (@AudeCstg, #89)." |
| Maintainers' tests | `tests/regressions/test_89.py::test_regression_89`, `::test_regression_89_sync_push_preserves_order` (added in the fix) |

## Pinned trees (`research_tools/nbc/pin_chancy.py`, `../../PINS.json`)

| Slot | Commit | Root tree | Package tree | Normalized sha256 |
|---|---|---|---|---|
| R1 (buggy) | `6716e0d` | `74128ffe0e4026d48fbcb8d5a467de997b68186d` | `2e8c904d0d4e41cf4eff67f1e7ca5f681cbd44ad` | `560e17b12963ac79837a84e7e7607c515ff0c84218cf2a733ae450129691aea3` |
| R2 (fixed) | `b8a77df` | `e70e4d053116e441d44e7c0f84b6837bb615b07c` | `306b899bf8009f29b22ff727e496112573e5b00e` | `203199dfe9f096b4f1e2022c5f4eab03c16f920c7ef6da9b6c478ade808fd85d` |

Both trees migrate to schema version 7 (the fix adds no migration).

## Contract

Single-version: each run migrates a fresh schema with the revision under test alone. No mixed-version operation is
documented or run.

## Reviewed code path (E6)

The fix diff (`chancy/app.py`, `chancy/utils.py`, `chancy/worker.py`), and at the parent: `push_many`,
`push_many_ex`, `_push_job_sql` (`INSERT ... ON CONFLICT (unique_key) WHERE ... DO UPDATE SET state = state`),
`Worker._maintain_updates` (one `executemany` UPDATE per batch, re-queued and retried on `OperationalError`), the
fetch query (`FOR UPDATE ... SKIP LOCKED`, `ORDER BY priority DESC, id ASC`), `AsyncExecutor`, and `chancy_uuid`
(time-ordered ids). The trees run only in the restricted container, with no network.

## Instrument

- **Adapter:** `src/deployspecter/experimental/thirdparty/chancy_adapter_nbc.py`, a declared variant of
  `chancy_adapter` (unchanged): it registers the two trees' source hashes and adds `nbc_setup`, `nbc_work` and
  `nbc_push_many`. Selected in the container by `DS_CHANCY_ADAPTER` (an additive change to
  `research_tools/isolated/smoke_inside.py`; unset, nothing changes).
- **Integration:** `research_tools/nbc/integrations/chancy_pushmany.py` (`chancy-pushmany/1`): fresh schema per run
  (queue `q_nbc`, concurrency 2), one container with a worker and two producers (`seed`, `repush`), each producer one
  `push_many` transaction of jobs with `unique_key` `u<n>`. Gates: both producers wait for the worker's first fetch;
  `repush` waits until a fetch has returned a job. The seeded explorer decides everything else. Corroborated; the
  oracle only on SOUND runs; a `report.json` per run with the final state and the oracle's detail.
- **RA-DET workloads (curated, frozen here):**
  1. `{"seed": [0, 1], "repush": [1, 0]}`: the maintainers' test shape. The parent's worker fetches by `id ASC`, i.e.
     in the seed's insertion order, so its update batch would touch `u0` then `u1`, and the re-push takes `u1` then
     `u0`.
  2. `{"seed": [1, 0], "repush": [0, 1]}`: the mirror image (seed inserted in descending key order).
  3. `{"seed": [0, 1], "repush": [1, 5, 6, 7, 8, 0]}`: as 1, with four fresh keys between the two conflicting ones,
     which widens the re-push transaction.

  These were chosen from reading the fix, its test and the parent's code path (above). Replicate r runs workload r
  for 4 seeded schedules per slot (plan §5: B = 12, T = 900 s per case and arm).
- **Oracle:** `oracle.py` here: D1 no statement of the worker or either producer, and neither `push_many`, failed
  with SQLSTATE 40P01; D2 each completed `push_many` returned references in the caller's order (each names a final row
  with the requested `n`). Written from the CHANGELOG entry and the maintainers' tests, not the fix's code.

## Exposure (who read what, before which step)

| When (2026-11-08) | Who | What | Before |
|---|---|---|---|
| Screening | this Track A agent | the fix's commit message, full diff (incl. `tests/regressions/test_89.py`), the 0.26.0 CHANGELOG entry and its commit `4fe6633`, the parent's code path listed above | integration, workloads, oracle |
| Earlier | the harness developer | Chancy 0.25.1/0.26.0 sources and the 0.26.0 CHANGELOG (adapter work, earlier increments) | this case |

The issue and PR #89 discussions were not read. Training-data exposure of the public repository can't be excluded.

## Predictions (before any run)

- R1: on some seeded schedules, the worker's batch locks one seed row, the re-push locks the other, and each then
  waits for the other: PostgreSQL aborts one (deadlock_detected, after `deadlock_timeout`). D1 VIOLATED. Whether a
  schedule produces that order is the explorer's; the window is narrow (the re-push must take its first conflicting
  row before the worker's flush, and its second after the flush started), so NOT REPRODUCED within B is possible.
- R2: no deadlock on any schedule: both paths take the rows in `unique_key` order. D1 and D2 HOLD.
- Risks to the instrument, recorded beforehand: the server log of a deadlock (its DETAIL lines) or a lock wait might
  not corroborate (REFUSED); a failed `executemany` midway might not reconcile; the worker's batch might hold one
  update instead of two (no overlap, HOLDS).

## Bring-up (infrastructure only, after this file is frozen)

Before the first reproduction: build both images and run, per tree, the setup phase and one coordinated run with a
bring-up spec (worker plus `seed` [3] only, no `repush`), to check that the variant adapter loads, pins and
corroborates. It uses none of the RA-DET workloads and no oracle; its outcome can't change the workloads above.
