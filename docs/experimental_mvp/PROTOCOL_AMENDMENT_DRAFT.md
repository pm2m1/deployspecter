# DRAFT, NOT ADOPTED: a prospective evaluation of Muse on real-application workloads

This is a draft for the owner's review.

- **It changes nothing in force.** The frozen protocol (version 1.2, Amendments 001 and 002), its A–G and R arms, Gate 2A, Gate A1 and the Gate A2 guards stay as they are. No arm name is reused or redefined.
- **Its sources have their own names:** `curated`, `systematic`, `systematic+constructor`, `muse-driver` and `muse-state`.
- **No benchmark has run under it.** Every result in `docs/experimental_mvp/results/` is an engineering smoke test on trusted synthetic fixtures. The developers wrote those fixtures and know their defects.

## Four questions, kept separate

| | Question | Unit | Primary measure |
|---|---|---|---|
| Q1 | Does Muse propose more **valid driver workloads** (handlers and arguments)? | proposal | acceptance by the deterministic validator under a fixed request budget, plus distinct handlers and distinct application statements reached |
| Q2 | Does Muse construct better **initial states** for a fixed driver workload? | workload × constructor | for the same invocations, the share of state goals the unmodified chain (WorkloadSpec v2 → planner → catalog verifier → bridge) bridges and verifies, and the share that then reach the case's target statements |
| Q3 | Does Muse improve **independently verified defect detection**? | case, paired | schedules explored until the first oracle-verified violation, right-censored at the schedule budget |
| Q4 | Does Muse reduce **manual onboarding work**? | application | the manual-work measures below |

**Keeping Q1 and Q2 apart.** A Muse gain in Q3 is credited only to the factor that was varied.

| Arm | Driver | State |
|---|---|---|
| `muse-driver` | Muse | the deterministic constructor |
| `muse-state` | the deterministic driver | Muse's goals, through the same chain |
| `systematic+constructor` | systematic | the same deterministic constructor |

**All four questions are preregistered together.** None may be chosen after Q3's results are seen.

## Equal budgets, stated before each run

- **Requests:** Muse requests per case, the proposals asked for per request, and a cap on estimated cost.
- **Workloads:** at most K valid workloads per source per case. Deterministic sources get the same K.
- **Schedules:** at most S explored schedules per workload per build, for every source.
- **Wall time:** a per-case limit applies to all sources. A source that hits it is censored, not dropped.
- **Failures:** a failed or timed-out request still counts against the request budget. It is reported with its unknown token usage.

## Paired outcomes, censoring and analysis

- **Pairing:** every source runs on the same cases, the same two builds (with the defect and with its fix) and the same budgets.
- **Q3:** time is measured in explored schedules, not seconds. A source with no verified violation within S schedules is right-censored at S. Analysis: Kaplan–Meier curves per source, and a log-rank test stratified by case.
- **Q1 and Q2:** paired differences in proportions, with exact intervals.
- **Ties and hard cases:** a case no source detects is reported, not excluded.
- **No winner from a single case.** No case may be a fixture built with the prototype, such as `reservations_mixed`, `ledger_orm` or `live_app.reservations`.

## What counts as verified

- **Oracle independence:** each case's oracle is written from the application's specification, before any source runs. It reads the final state on its own plain connection and doesn't import the application.
- **Hypotheses:** Muse's invariant hypotheses are evaluated and reported. They never replace or amend the oracle.
- **Negative control:** the fixed build. A violation on it voids that case for that source and is reported as a false positive.
- **Corroboration:** a run counts only when PostgreSQL's own log corroborates its driver trace (statements, values, sessions, order). A run with an unexplained statement or session is refused, not counted.

## Manual-work measures (Q4), per application

These come from a timestamped onboarding log written as the work happens:

- setup time;
- hand-written lines of adapter, allowlist and configuration;
- manual interventions;
- unsupported features met (e.g. async connections, server-side cursors, hstore type lookups);
- whether the first execution succeeded;
- reviewer burden: proposals and refusals a human had to read and decide.

## Corpus

- **Rules:** the selection rules frozen in `CORPUS_SHORTLIST.json`, including the migration-area rule fixed before any migration file was read.
- **Exclusions:** the protocol's held-out and development applications, anything GitLab-derived, and every prototype fixture.
- **Status:** the two pinned candidates (chancy 0.26.0 and pgqueuer 1.6.0) are both asynchronous, which the prototype can't yet step. The shortlist isn't a corpus yet.

## Open items before adoption

- an async stepping path;
- running third-party code in an isolated container;
- extending the shortlist to a corpus under the same rules;
- per-case oracles written without access to fixes;
- the request budget per case;
- the owner's review and signature, under the protocol's own amendment process.
