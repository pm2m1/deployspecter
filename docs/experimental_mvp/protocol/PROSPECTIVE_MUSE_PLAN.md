# Prospective Muse comparison: predeclared plan (PLAN, not executed; 2026-11-05)

**Status.** This plan is ready for review, and **nothing in it has run**.
- **Confirmatory execution is blocked** until Amendment 003 is adopted with #6, #7, #8, #9 and #14 ACCEPTED (`DECISIONS_003_v2.md`), and blind cases are sealed.
- NONBLIND exploratory runs are allowed only if labelled `RA-NONBLIND` and never pooled with confirmatory results.
- No Meta credits were spent writing this plan.

**Wording.** No claim of a Muse advantage may rest on workload validity alone (RA-Q1). Defect detection (RA-Q3) is the only outcome that can support a detection claim, and only on blind, sealed cases with independent oracles.

## 1. Questions (kept distinct)

| | Question | Unit | Primary measure | What it can't show |
|---|---|---|---|---|
| Q1 | Valid workload generation and relevant path coverage | proposal, paired by case | the share accepted by the deterministic validator; distinct registered handlers and migration-touching statements reached | detection |
| Q2 | Initial-state construction under the common deterministic constructor | case × fixed driver | the share of state goals bridged by the unmodified WorkloadSpec v2 chain and MATCHed against the live catalog, then reaching the case's target statements; RA-DET-C (literal translation, no search) reported beside it | detection; any gain the constructor causes |
| Q3 | Independently verified defect detection | case, paired | detected on the fix parent with no violation on the fix commit, under equal budgets; time to first detection, right-censored | anything on NONBLIND or unsealed cases |
| Q4 | Manual onboarding effort | application | timestamped minutes, and handwritten lines counted by script, with and without Muse | detection |

## 2. Case selection (exact)

1. **Admission.** Candidates come only from `CORPUS_MANIFEST.json`, through the frozen rules. A NEEDS_PROTOCOL_RULING candidate enters only after the ruling is ACCEPTED and committed, **before** its migration files are read.
2. **The unit.** A case is a commit-level fix pair, as in draft v3 §17, sealed by the curator (#6). It includes a documented compatibility contract (#19): mixed-version operations only where supported.
3. **Requirements.** The case needs an oracle by an independent author (#7) that holds on the fix commit under the full budget. Otherwise the case drops for every arm.
4. **NONBLIND cases** are excluded from Q3's confirmatory analysis, but may tune budgets:
   - `historical/chancy_2895c4a`;
   - the Chancy simultaneous pilot;
   - pgqueuer;
   - every `RA-SMOKE` fixture.

## 3. Arms

RA-DET, RA-MD, RA-MS, RA-MDS and RA-DET-C, exactly as in draft v2 §2.

**The deterministic baseline (strong):**
- curated workloads;
- the systematic grid over registered handler pairs and argument boundaries;
- the seeded schedule explorer (`coordinated.Policy` "seeded") with the same seeds for every arm;
- for historical cases, the case's forced schedule where a maintainers' test defines one.

**Muse's role.** Muse proposes only workloads (handlers and arguments) and state goals, as JSON against a frozen schema. It never proposes SQL, Python, gates or schedules. Schedules belong to the explorer in every arm, so a detection difference can't come from scheduling.

## 4. Model, prompt and schema (frozen at adoption, re-hashed then)

| | Value now (to be re-hashed in the adoption commit) |
|---|---|
| Model | `muse-spark-1.3` (`DEFAULT_MODEL`; `MODEL_NAME` unset), reasoning effort `medium`, `store=False`, base URL `https://api.meta.ai/v1` |
| Prompt and context builders | `experimental/muse.py` `913cce1f…`, `experimental/muse_apps.py` `910a7df3…` (targets `ledger`, `planner`, `release`, `pgqueuer`, `chancy_mixed`) |
| Validator | `experimental/workloads.py` `6b3afb6d…` |
| A new target per case | written on development cases only; its context builder may expose only the registry and the schema (no docstrings, fixes, tests, oracles or results), and is leak-checked against the sealed labels |

A request whose provider-reported model differs from the frozen one is a provider failure. A change of model or prompt after adoption needs a new amendment and starts a new evaluation.

## 5. Budgets (candidate values, fixed on development cases before the freeze; #15)

| Budget | Candidate | Basis |
|---|---|---|
| B (schedules per workload per build) | 12: the forced schedule plus seeds 0–10 | `historical/chancy_2895c4a` and the `compete` exploration (10 seeds, 9–10 sound per arm) |
| T (seconds per case per arm) | 900 | one coordinated run takes 20–60 s |
| Replicates | 3, each with B/3 schedules and T/3 seconds (frozen §8 shape) | frozen protocol |
| R (Muse requests per case per arm) | 2 | earlier pilots: 1–2 requests gave 4–12 valid proposals |
| P (proposals per request) | 6 | earlier pilots |
| C (estimated cost cap) | $1.00 per case per arm; $10 per evaluation | earlier requests cost $0.01–$0.07 |
| Maximum schedules per run | 1000 grants, with a 170 s coordinator deadline | `run_coordinated.py` |
| Deterministic arms' model budget | 0 (#16) | draft |

## 6. Accounting (predeclared)

- **Proposals:** every proposal keeps its place in the denominator: shape-rejected, validator-rejected (with a code), chain outcome (with stage and code), live-catalog mismatch, or bridged and executed (draft v2 §5).
- **Runs:**
  - SOUND, REFUSED (with `refusal_reasons`) or INCOMPLETE, never retried;
  - corroboration totals (statements, values compared, mismatched, unchecked);
  - quiescence (whether P6 ran).
- **Negative controls:** every arm runs the fix commit under the same schedules. Any violation there voids the case for that arm, and goes to blinded two-reviewer triage (#8).
- **Requests:** failed or timed-out requests count against R, with unknown usage recorded. Every reply is saved on arrival. No retries, except logged transport failures.

## 7. Outcomes and analyses (paired)

- **Primary (Q3):** the case-level detection difference RA-MDS − RA-DET. An exact two-sided McNemar test is used only with at least 10 eligible blind cases; otherwise the result is descriptive, as declared here. Secondary: RA-MD − RA-DET, with Holm correction.
- **Time to detection:** schedules to the first corroborated violation on the fix parent, right-censored at B.
  - Kaplan–Meier curves per arm.
  - The paired median difference, with censored values set to B.
  - An exact Wilcoxon signed-rank test only with at least 10 cases that both arms detect.
- **Q1 and Q2:** paired differences in proportions, with exact intervals. Descriptive co-primary.
- **Q4:** frozen §10 and §11. A claim needs at least a 30% median reduction in both minutes and lines across at least 3 applications, with no lost detection.
- **Cost:** requests, tokens, latency, and estimated USD per arm and per detection.
- **Not outcomes:** the number of proposals, call counts, or diversity.

## 8. Exposure and leakage

Before any arm runs:
- the per-case exposure ledger is committed (draft v2 §10), including "training-data exposure not excludable" (#20);
- the curator runs `seal_labels.py leak-check` over each arm's frozen inputs: prompts, contexts, registries and workload sources;
- Muse prompts contain only the registry and the schema.

## 9. Readiness

| Item | Status |
|---|---|
| Q1–Q4 definitions, arms, accounting, analyses | written (here and in draft v3) |
| Cross-process scheduler, corroboration, isolated runner, case runner, commit-pinned builds | exist and are live-tested (development cases) |
| Label sealing and leak-check tool | exists (offline-tested only) |
| A Muse target for a historical case | **missing** (to be written on a development case) |
| Independent curator, oracle author, triage reviewers | **missing** |
| Sealed blind cases | **none** |
| Registration and timestamp | **not done** (needs the owner's approval) |
| **Verdict** | **NOT READY for confirmatory execution.** A NONBLIND exploratory comparison on `chancy_2895c4a` is technically possible, but would answer nothing Q3 asks |
