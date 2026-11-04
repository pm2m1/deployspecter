# DRAFT, NOT ADOPTED: a prospective evaluation of Muse-generated application workloads

This is a draft for review. It does not change the frozen protocol (version 1.2 with Amendments 001 and 002), and it doesn't reuse or redefine that protocol's A to G or R arm names. The sources here are `curated`, `systematic`, `systematic + constructor` and `muse`.

## Research questions, kept separate

| | Question | Unit | Primary measure |
|---|---|---|---|
| Q1 | Does Muse generate more **valid** application-driver workloads? | proposal | the validator's acceptance rate under a fixed request budget; distinct handlers and migration-touching statements reached |
| Q2 | Does Muse construct better **initial states** for an identical workload? | workload × constructor | for the same invocations, the share of seeds that pass validation and reach the migration statements. The comparison is `systematic + constructor` against `muse` seeds, so a gain from better seeding isn't credited to workload choice |
| Q3 | Does Muse improve **independently verified defect detection** under equal budgets? | case (paired) | time to the first oracle-verified violation, in explored schedules, with right censoring at the budget |
| Q4 | Does Muse reduce **manual onboarding work**? | application | see below |

**Q4 is preregistered now, with all four questions.** It may not be chosen later as a fallback after Q3's results are seen.

Q4's measures, per application, from a timestamped onboarding log:

- setup time;
- hand-written lines of code and configuration;
- the number of manual interventions;
- unsupported features met;
- whether the first execution succeeded;
- the human-review burden (proposals a reviewer had to read and decide).

## Design

- **Paired:** every source runs on the same cases, the same builds, and the same schedule and request budgets. Each budget is stated before the run.
- **Analysis:**
  - **Q3:** time to the first verified violation, right-censored at the budget. Kaplan–Meier curves per source, and a paired, stratified log-rank test across cases.
  - **Q1 and Q2:** paired differences in proportions, with exact intervals.
  - **No winner from a single case.**
- **Verified means:** an independent oracle, written from the specification, reads the final state on its own connection. The fixed build is the negative control: a violation on it voids the case for that source.
- **The constructor arm:** `systematic + constructor` uses the same initial-state constructor as `muse`. That isolates whether an apparent Muse gain comes from better seed construction.

## Corpus

- **Rules:** the selection rules frozen in `CORPUS_SHORTLIST.json`. The held-out and development applications are excluded, and so is anything GitLab-derived.
- **Not blind:** the old GitLab-derived fixture and this sprint's trusted `reservations_mixed` fixture.
- **What this sprint is:** the `mvp_compare.py` results are an engineering smoke test on one known fixture.

## Open items before adoption

- extend the shortlist to a corpus;
- support framework and pool connection management;
- an async stepping path, at least for `pgqueuer`;
- per-case oracles written without access to fixes;
- the request budget per case;
- the owner's review.
