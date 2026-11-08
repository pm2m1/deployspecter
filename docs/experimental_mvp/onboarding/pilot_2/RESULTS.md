# onb-pilot/2: same-application D vs M onboarding (AI-OPERATOR, NONBLIND EXPLORATORY), 2026-11-08

**Label.**
- Each session was a fresh AI agent context with no task-specific memory, working in a fresh worktree made from
  the frozen commit `f82e90d`.
- These are **not** independent human participants. Nothing here measures human onboarding productivity.

**Design.**
- Apps: pgtq 0.5.0 and postgres-tq 1.2.1, the only eligible apps found.
- Both conditions on each app, in two replicates (1 and 2b; replicate 2 is VOID, see `DEVIATION_1`).
- Within each replicate, all four sessions started together.
- Analysis: `pilot2_analyze.py`, frozen with the manifests. Times are lock-adjusted.

## Sessions (lock-adjusted seconds; every session reached S3)

| App | Condition | Replicate | to S1 | to S3 | total | lock wait | interventions | attempts (failed) | adapter lines | Muse calls (USD) |
|---|---|---|---|---|---|---|---|---|---|---|
| pgtq | D | 1 | 100.4 | 113.4 | 118.7 | 0.0 | 4 | 1 (0) | 91 | 0 |
| pgtq | M | 1 | 174.6 | 184.8 | 199.4 | 0.0 | 3 | 1 (0) | 89 | 1 ($0.016494) |
| pgtq | D | 2b | 130.4 | 142.9 | 157.8 | 303.8 | 4 | 1 (0) | 82 | 0 |
| pgtq | M | 2b | 108.7 | 121.1 | 129.9 | 326.0 | 2 | 1 (0) | 81 | 0 |
| postgres_tq | D | 1 | 201.6 | 517.6 | 522.8 | 142.8 | 6 | 3 (2) | 191 | 0 |
| postgres_tq | M | 1 | 166.3 | 178.0 | 191.8 | 0.0 | 6 | 1 (0) | 170 | 0 |
| postgres_tq | D | 2b | 199.2 | 213.0 | 220.2 | 235.6 | 5 | 1 (0) | 181 | 0 |
| postgres_tq | M | 2b | 219.1 | 231.5 | 247.5 | 227.1 | 3 | 1 (0) | 177 | 0 |

Every S2/S3 run was corroborated with 0 parameters mismatched. Adapter lines revised: 0 in every session.

## Paired differences, M − D (predeclared)

| App | Replicate | seconds to S3 | adapter lines | interventions | failed attempts |
|---|---|---|---|---|---|
| pgtq | 1 | +71.4 | −2 | −1 | 0 |
| pgtq | 2b | −21.8 | −1 | −2 | 0 |
| postgres_tq | 1 | −339.6 | −21 | 0 | −2 |
| postgres_tq | 2b | +18.5 | −4 | −2 | 0 |
| **median, pgtq** | | **+24.8** | −1.5 | −1.5 | 0 |
| **median, postgres_tq** | | **−160.6** | −12.5 | −1.0 | −1.0 |

## Reading (descriptive; no claim)

- **No claim is possible** by the plan's Q4 rule, which needs at least 3 applications and a 30% median reduction
  in both minutes and lines. Only 2 apps were eligible.
- **Muse was rarely used.** In the 4 M sessions, Muse was called once: pgtq M r1, one call, $0.016494. That
  operator recorded the reply as only partly useful and didn't use it. The other three M operators never called
  Muse.
  - **So this is an intention-to-treat comparison of *Muse available* vs *not available*.** What it measured is
    almost entirely operator-to-operator variation, not the effect of Muse's content.
- **The one large difference comes from the environment, not Muse.** postgres_tq replicate 1's −339.6 s comes from
  incident 1: D's first two attempts failed because of an unlocked test run on the shared database
  (`INCIDENT_1_UNLOCKED_SUITE_RUN.md`).
  - **POST-HOC:** without that pair, the three remaining differences are +71.4, −21.8 and +18.5 s, with no
    consistent direction.
- **Ceiling effect.** Every session reached S3, mostly on the first attempt, in 2–9 minutes of work. For an AI
  operator that can read the source, these libraries are easy to onboard. The task leaves Muse little room to
  help, and the measures can't tell the conditions apart.

## Threats to validity

- **Same model family.** All operators were instances of one model family, so they share general knowledge.
- **Isolation by instruction only.** Some operators unpacked the pinned sdists into new scratch folders. Those were
  pristine upstream files, so no adapter code could have passed between sessions that way.
- **Lock waits.** They were large in replicate 2b (up to 326 s). They are subtracted, but with second-level
  precision.
- **Replacement replicate.** Replicate 2 was voided and replaced by 2b.
