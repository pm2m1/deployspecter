# Track D: Muse value, question by question (ds-nbc/1 sprint, 2026-11-08/09)

**All evidence here is NONBLIND or AI-operator exploratory.** The predeclared analyses were not changed after outcomes
were seen. Post-hoc observations are labelled.

## Q1: Does Muse generate more useful valid workloads?

**No more useful; equally valid.**
- **Validity:** Track B: 48 of 48 Muse workload proposals were valid (8 requests, 4 cases). So were RA-DET's 12.
  On validity alone, Muse matched the deterministic workloads (the plan forbids an advantage claim from validity
  alone).
- **Usefulness:** the workloads that actually ran (the first 3 valid per case) reproduced the defect on 1 of 4
  cases. RA-DET's reproduced 4 of 4.
- **POST-HOC:** on pgqueuer-8dfadf4, 2 of the 9 unused Muse proposals had the triggering shape. Muse's pool did
  hold useful workloads, but its ordering put non-triggering ones first.
- **Earlier evidence agrees:** muse_contrib Q1 "matched or underperformed, never improved".

## Q2: Does Muse improve state construction or reduce schedules to detection?

- **State construction: not testable in this sprint.** The four historical-case integrations have no state-goal or
  constructor stage, so RA-MS, RA-MDS and RA-DET-C are NOT APPLICABLE (Track B manifest).
  - **The only state evidence is earlier:** muse_contrib Q2, on one synthetic fixture, where Muse states reached
    first detection sooner in 3 of 6 cells and matched in 3, with n = 2 replicates. That result is still exploratory
    and wasn't replicated here.
- **Schedules to detection: no reduction.** On the one case both arms detected (chancy-2895c4a), both detected at
  schedule 1, a tie. On the other 3, RA-MD was censored at 12 while RA-DET detected at 1–2. The Gehan sign test gave
  0 vs 3 with 1 tie (p = 0.25).

## Q3: Does Muse improve independently checked defect detection in the developer-selected historical cases?

**No.** Detection was 1 of 4 (RA-MD) against 4 of 4 (RA-DET), with no false positives on either side.
- **Discordant cases:** 0 vs 3; McNemar p = 0.25.
- **Verdict:** INSUFFICIENT SAMPLE (4 < 10).
- **Caveats:** the "independent" checking here is the server-log corroboration and the fixed-version control. The
  oracles are developer-written and NONBLIND, so this is not independent evaluation in the protocol's sense.

## Q4: Does Muse reduce developer-only automated onboarding effort?

**No reduction was detected, and the experiment couldn't test Muse's content.** onb-pilot/2: 2 apps × 2 conditions
× 2 replicates, AI operators, every session reaching S3.
- **Times:** the median M − D lock-adjusted seconds to S3 was +24.8 for pgtq and −160.6 for postgres_tq. The latter
  comes from one D session that lost two attempts to an environment incident; without it (POST-HOC) the
  differences have no consistent direction.
- **Muse use:** Muse was called once in 4 M sessions ($0.016), and its reply wasn't used.
- **Claim:** none is possible under the plan's rule (≥ 3 applications, ≥ 30% reduction in both minutes and lines).
- **Scope:** AI-agent sessions say nothing about human onboarding.

## Cost and equal budgets

- **Spend:** $0.130662 for the sprint (cap $5): $0.114168 for Track B and $0.016494 for onboarding.
- **Budgets:** every comparison used identical schedule budgets, seeds, explorer and builds.
- **Value for cost:** no Muse benefit survived the equal-budget comparison, so the cost bought no measurable
  improvement.

## Evidence quality and remaining threats

- **Too few cases:** 4 historical cases, from exposed development apps.
- **Fix-aware baseline:** the deterministic workloads were written by fix-aware developers.
- **Muse used only its first 3 workloads:** the executor runs one workload per replicate.
- **One post-hoc case:** saq-addee31 v2.
- **AI operators** of one model family, with a ceiling effect.

A fair test of Muse's detection value needs the protocol's blind setting:
- independent curators and oracles;
- deterministic workloads written without seeing the fixes;
- sealed cases;
- at least 10 cases.
