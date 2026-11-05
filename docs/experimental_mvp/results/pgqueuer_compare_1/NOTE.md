# pgqueuer_compare_1: reading notes

This measures structured queue workloads on PGQueuer 1.6.0's real lifecycle. Each run went through the isolated runner and the stepped Driver adapter.

**Scope.** There is no defective or corrected PGQueuer version, so this measures validity, coverage and oracle outcomes, **not detection**.

**Budget.** The first 4 valid workloads per source, with one isolated run each. Muse got 1 call for 6 proposals, at an estimated $0.010854.

| Source | Valid | Executed | Corroborated by the server log | Oracle (Q1–Q3) holds | Distinct PGQueuer statements reached |
|---|---|---|---|---|---|
| curated | 4/4 | 4 | 4 | 4 | 15 |
| systematic | 12/12 | 4 | 4 | 4 | 15 |
| Muse | 6/6 | 4 | 4 | 4 | 15 |

**Reading.** Muse's workloads were all valid and more varied: up to 5 jobs, mixed priorities, and batch size 3. They reached exactly the same 15 statements as the deterministic sources, and no oracle outcome differed. On this pathway Muse **matches and does not improve on** the deterministic baselines.
