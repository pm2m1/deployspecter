# Muse contribution, arms B1–B5 (`ds-mc-experiment/1`): results

**Status:** EXPERIMENTAL, NONBLIND, exploratory.
- **Cases:** development cases only: Chancy 2895c4a, procrastinate 3.3→3.4, and the trusted reservations fixture.
- **Arms:** B1–B5. These aren't the frozen protocol's A–G/R arms.
- **Gates:** no Gate 2A/A2 claim.
- **Analysis:** descriptive only. The sample (3 cases, 2 Muse replicates) supports no inferential claim and no claim of Muse superiority.

## Freeze and provenance

| Item | Value |
|---|---|
| Manifest | `EXPERIMENT_MANIFEST_v1.json`, sha256 `234a12ff4f727e91f467cf1ed0fdef67d7bf4ed923a7162f4c67252119142476`, committed `8d7a73e` before any outcome |
| Code at freeze | `af14c90`; every experiment file's sha256 is pinned in the manifest and re-verified by every run and by the analysis |
| Model | `muse-spark-1.3`, reasoning effort medium, store false, provider-default sampling |
| Meta calls | 13 (`generation_1/calls.jsonl`); 15,047 input and 59,194 output tokens; **$0.270384 estimated** (published rates, not billing) |
| Runs | `run_1/`: 13 parts, all exit 0. Each part ran under `research_tools/pg_lock.py` on the disposable PostgreSQL (127.0.0.1:55441) |
| Analysis | `analysis_1_frozen/` (frozen code) and `analysis_1_a1/` (amendment A1, below) |

**Amendment A1 (analysis only, after all runs).**
- **The bug:** the frozen `analyze.py` emitted no row for a Muse replicate that executed nothing. Reservations B3 replicate 0 had 0 of 8 proposals bridged: every one was refused `unsupported@plan_state` with `no_filler_policy`, because Muse left the event's capacity unconstrained.
- **The fix:** A1 (`research_tools/muse_contrib/analyze_a1.py`) adds that row, censored at 0 schedules with 8 invalid proposals. The frozen rules already count every replicate.
- **The effect:** one verdict changes. Q1 for reservations/buggy-vs-corrected is *matched* in the frozen analysis and *underperformed* under A1.

## Per-case results (`analysis_1_a1/results.jsonl`)

"First" is the schedule of the first detection, 1-based and cumulative over the arm's workloads; "–" means not detected (censored).

| Case | Arm | Rep | Schedules | Detected | First | Ref. violation | Invalid | Refused | Est. $ |
|---|---|---|---|---|---|---|---|---|---|
| chancy-2895c4a | B1 curated | 0 | 9 | yes | 1 | no | 0 | 2 | 0 |
| | B2 systematic | 0 | 9 | yes | 4 | no | 0 | 8 | 0 |
| | B3 Muse | 0 | 9 | yes | 1 | no | 0 | 8 | 0.013744 |
| | B3 Muse | 1 | 9 | yes | 1 | no | 0 | 8 | 0.016956 |
| procrastinate-3.3-3.4 (no known defect) | B1 | 0 | 3 | no | – | no | 0 | 0 | 0 |
| | B2 | 0 | 3 | no | – | no | 0 | 0 | 0 |
| | B3 | 0 | 3 | no | – | no | 0 | 0 | 0.015711 |
| reservations/buggy-vs-corrected | B1 | 0 | 120 | yes | 3 | no | 0 | 0 | 0 |
| | B2 | 0 | 106 | yes | 9 | no | 0 | 0 | 0 |
| | B3 | 0 | 0 | no | – | no | 8 | 0 | 0.046845 |
| | B3 | 1 | 120 | yes | 3 | no | 0 | 0 | 0.039034 |
| reservations/fixed-vs-corrected | B1 | 0 | 120 | yes | 22 | no | 0 | 0 | 0 |
| | B2 | 0 | 106 | yes | 68 | no | 0 | 0 | 0 |
| | B3 | 0 | 0 | no | – | no | 8 | 0 | (same call) |
| | B3 | 1 | 120 | yes | 82 | no | 0 | 0 | (same call) |

B4 vs B5 (identical actors and constructor; only the state differs):

| Actor set / target | B4 first | B5 r0 first | B5 r1 first |
|---|---|---|---|
| two_reserves / buggy | 3 | 3 | 3 |
| two_reserves / fixed | – | – | – |
| two_releases_same_holder / buggy | 8 | 2 | 2 |
| two_releases_same_holder / fixed | 8 | 2 | 2 |
| reserve_and_release / buggy | 43 | 23 | 38 |
| reserve_and_release / fixed | – | – | – |

- **Validity:** every B4 and B5 state was bridged and verified live (24 of 24 Muse states valid). No construction was refused.
- **References:** no reference build (corrected, or the fix commit) ever violated the oracle in any arm.

## Answers (rules frozen in the manifest)

- **Q1, workload drivers: matched or underperformed, never improved.**
  - **chancy-2895c4a: matched.** B1 and both B3 replicates detect at schedule 1; B2 at schedule 4.
  - **procrastinate: matched.** No arm detected anything; there's no known defect, so the case can't speak to detection.
  - **reservations: underperformed** under A1. Replicate 0 produced no valid workload. On fixed-vs-corrected, replicate 1 needed 82 schedules against B1's 22 and B2's 68.
- **Q2, state proposals with workload and constructor held constant: improved in 3 of 6 actor-set/target cells, matched in 3.**
  - Muse states reached first detection sooner in both replicates for two_releases_same_holder (2 vs 8, both targets) and reserve_and_release/buggy (23 and 38 vs 43).
  - The other three cells matched: one tie at 3; in two, no arm detected.
  - It never underperformed. This is a small exploratory sample on one synthetic fixture.
- **Q3, invariants: matched on both domains** (`../invariants/RESULTS.md`). No Muse candidate killed anything that no deterministic candidate killed. Muse's useful candidates (mutant-sensitive) restate deterministic ones, and 2 of its 8 Chancy candidates were invalid (false on the fix commit).
- **Q4, onboarding: not answerable here.** This experiment doesn't measure onboarding effort.

## Execution notes (preserved, not excluded)

- **Chancy INCOMPLETE runs (26 of 86 units).**
  - **What:** every two-job workload that cancels a job before any worker claims it. That job never runs, so the coordinator's `covers` stop can't be met, and the run stops at `after_s`.
  - **Why it's the engine:** it's a limitation of the fixed engine design, the same for every arm; the analysis counts these runs as refused.
  - **Who it hit:** B2 and both B3 replicates each had two such workloads (8 refused), B1 one (2 refused). This is why B2's and B3's refused counts are high.
- **Chancy REFUSED runs (4).** Under seed 2, with two workers, the scheduler held the producer's transaction open past PostgreSQL's 15 s `idle_in_transaction_session_timeout`. The server terminated it, and corroboration refused the run (an unexplained server-log row).
- **System sleep.**
  - **What happened:** the host entered macOS maintenance sleep during part of the run (`run_1/SLEEP_OVERLAP.json`, from `pmset -g log`).
    - 16 of 86 Chancy units overlapped a sleep; 8 of the 30 non-SOUND units did.
    - The same outcomes occur in units with no overlap: 20 of 26 INCOMPLETE and 2 of 4 REFUSED had none.
  - **Interpretation:** sleep isn't their explanation. No unit was rerun, removed or replaced.
  - **Prevention:** `caffeinate -w` kept the remaining queues awake.
- **Shared units.** A Chancy (tree, mutant, workload, seed) unit is executed once and shared between arms that select the same workload (`run_1/chancy_units/`, `cached` in each part's `units.jsonl`).
- **Not measured here:** procrastinate application mutants. Patching procrastinate would require changing the shared isolated runner and adapter, which this workstream doesn't own, so procrastinate is limited to B1–B3.

## Reproduce

Paths are relative to the worktree root. `SDISTS` is the pinned-source directory.

```
export PYTHONPATH=$PWD/src DS_THIRDPARTY_SDIST_DIR=SDISTS
M=docs/experimental_mvp/muse_contrib/EXPERIMENT_MANIFEST_v1.json
python research_tools/muse_contrib/experiment.py generate --manifest $M --out GEN            # Meta calls, no DB
python research_tools/pg_lock.py -- python research_tools/muse_contrib/experiment.py \
    run-reservations --manifest $M --gen GEN --part B1 --out RUN/reservations_B1           # likewise B2-B5, mutants
python research_tools/pg_lock.py -- python research_tools/muse_contrib/experiment.py \
    run-chancy --manifest $M --gen GEN --part B1 --out RUN/chancy_B1                       # likewise B2, B3, mutants
python research_tools/pg_lock.py -- python research_tools/muse_contrib/experiment.py \
    run-procrastinate --manifest $M --gen GEN --arm B1 --out RUN/procrastinate_B1          # likewise B2, B3
python research_tools/muse_contrib/analyze_a1.py --manifest $M --gen GEN --run RUN --out ANALYSIS
python research_tools/muse_contrib/sleep_overlap.py RUN RUN/SLEEP_OVERLAP.json
```
