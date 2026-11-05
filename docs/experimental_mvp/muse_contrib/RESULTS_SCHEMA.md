# Result rows of the Muse-contribution experiment (`ds-mc-experiment/1`)

Written by `research_tools/muse_contrib/experiment.py analyze` into `<analysis>/results.jsonl`, one JSON object a line:
one row per **(case, arm, replicate)**. NONBLIND and exploratory throughout: every row has `"nonblind": true`.

| Field | Type | Meaning |
|---|---|---|
| `case` | str | `chancy-2895c4a`, `procrastinate-3.3-3.4`, `reservations/<target>`, or `reservations-state:<actor set>/<target>`; a reservations target is `buggy-vs-corrected` or `fixed-vs-corrected` |
| `arm` | str | `B1` curated, `B2` systematic deterministic, `B3` Muse workloads, `B4` deterministic state, `B5` Muse state (not the frozen protocol's A–G/R arms) |
| `replicate` | int | 0, 1 for the Muse arms (independent calls); 0 for deterministic arms |
| `run_id` | str | sha256 prefix of (manifest freeze time, case, arm, replicate) |
| `schedules_used` | int | schedules executed on the defective build (procrastinate: rolling-upgrade runs) |
| `detected` | bool | some sound run on the defective build violated the case oracle |
| `first_detection_schedule` | int or null | 1-based cumulative index (over the arm's workloads, in order) of the first detecting schedule; null = not detected, censored at `schedules_used` |
| `wall_seconds` | float | wall time of everything the arm executed (defective and reference builds) |
| `wall_seconds_to_first_detection` | float or null | cumulative wall time up to and including the detecting run |
| `invalid_proposals` | int | proposals rejected by validation (Muse arms), or curated/grid documents the chain didn't bridge |
| `refused` | int | refused runs or constructions (corroboration, isolation, an initial state the harness refuses) |
| `fixed_version_violation` | bool | any oracle violation on the reference (corrected) build; procrastinate: any violation after errata |
| `tokens_in`, `tokens_out` | int | the arm's Meta usage (0 for deterministic arms) |
| `est_usd` | float | estimated at Meta's published rates, not billing data |
| `workloads_executed` | int | workloads (or states) that reached execution |
| `no_verdict_runs` | int | chancy only: sound runs whose H0 sanity check failed (never detection) |
| `known_defect` | bool | false for procrastinate (no defect is known in the tested pair) |

`results_workloads.jsonl` has the same detection fields per executed workload, plus `label` and the workload.
