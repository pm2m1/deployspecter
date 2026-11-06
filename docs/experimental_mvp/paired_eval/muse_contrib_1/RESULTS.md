# Paired analysis of the Muse-contribution experiment (ds-mc-experiment/1). NONBLIND, exploratory.

**What was run.**
- Input: `docs/experimental_mvp/muse_contrib/analysis_1_{frozen,a1}/results.jsonl`.
- Adapter: converted by `research_tools/paired_eval_from_muse_contrib.py`, whose mappings are documented and tested.
- Analysis: `research_tools/paired_eval.py`, with its predeclared rules.
- Outputs: every invocation's stdout, stderr and exit status are in this directory.

**Interpretation.** Every comparison that ran is **INSUFFICIENT SAMPLE**: fewer than 10 paired defect cases. No advantage is claimed.

**The frozen analysis is primary.** A1 is a post-outcome amendment by the experimenter. It adds rows for a Muse replicate whose proposals were all invalid, and it is reported alongside, never instead.

## Refusals (exit 2), kept as results
paired_eval refuses arms whose case budgets differ.

**In the frozen analysis:**
- B1 vs B2: reservations budgets are 120 vs 106 schedules.
- B2 vs B3.r1: the same mismatch.
- B4 vs B5: budgets are 46/60/55 without truncation.

**In A1, additionally:**
- B1 vs B3.r0 and B2 vs B3.r0: the reservations cases there executed **0 schedules**, because all 8 proposals were invalid.

Arms that ran different numbers of schedules can't be compared as equal-budget evidence.

## Comparisons that ran

| Arms (frozen) | Paired defect cases | Detection (McNemar) | Schedules to detection (Gehan sign) | Verdict |
|---|---|---|---|---|
| B1.r0 vs B3.r0 | 1 (Chancy) | 0/0 discordant, p=1 | tie | INSUFFICIENT |
| B1.r0 vs B3.r1 | 3 | 0/0, p=1 | B1 faster 1, ties 2, p=1 | INSUFFICIENT |
| B2.r0 vs B3.r0 | 1 (Chancy) | 0/0, p=1 | B3 faster (1 vs 4), p=1 | INSUFFICIENT |
| B4.r0 vs B5.r0, `--budget 46` | 6 | 0/0, p=1 | **B5 faster 3, ties 3, p=0.25** | INSUFFICIENT |
| B4.r0 vs B5.r1, `--budget 46` | 6 | 0/0, p=1 | **B5 faster 3, ties 3, p=0.25** | INSUFFICIENT |

The A1 results for these pairs are the same; see the A1 files.

**B4 vs B5** compares the same workload with deterministic and Muse-assisted initial states. `--budget 46` is the predeclared symmetric truncation to the smallest cell budget.
- Kaplan–Meier median schedules to detection: 8 (deterministic) vs 3 (Muse) in both replicates.
- Muse cost: $0.0717 and $0.0591 for the two replicates.

**Caveats.**
- The 6 cases are 3 actor sets × 2 targets, so they are not 6 independent units.
- The application is the trusted synthetic reservations fixture.
- The developer chose the cases knowing the defects.

**Direction for Q2.** Muse-assisted states matched or were faster in every pair: favourable but not significant. Under the protocol, that answers Q2 as *not established*.
