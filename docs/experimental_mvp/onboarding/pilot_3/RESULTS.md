# onb-pilot/3: same-app onboarding with a mandatory Muse checkpoint (AI-OPERATOR, NONBLIND EXPLORATORY), 2026-11-10

**Label.**
- Each session was a fresh AI agent context with no task-specific memory, in its own fresh worktree built from base
  `b7f6804`. That base is commit `f82e90d` plus the frozen onb-pilot/3 files, and it holds no earlier adapter for
  these applications.
- These are **not** independent human participants, and nothing here measures human onboarding.
- **Not new applications:** pgtq 0.5.0 and postgres-tq 1.2.1 were exposed by onb-pilot/2.

**What changed from onb-pilot/2.** In onb-pilot/2, Muse was called once in four M sessions. In onb-pilot/3 the
workflow has a predetermined **CHECKPOINT A**: right after the public documentation (`pkginfo`), before any source
reading or code.
- **M:** must call Muse there, read the response, and record whether it was accepted, modified or rejected. Nothing
  forces adoption.
- **D:** records a written plan at the same point.

Manifests: `PILOT_MANIFEST_v3_{D,M}.json`, frozen at `1531642` before any session. Analysis: `pilot2_analyze.py`
(lock-adjusted, unchanged) and `pilot3_checkpoints.py`, both frozen with the manifests.

## Sessions (every session reached S3 on its first run attempt; 0 failed attempts; 0 adapter revisions)

| App | Condition | Replicate | to S1 (s) | to S3 (s) | total (s) | interventions | adapter lines | CHECKPOINT A | Muse calls (USD) |
|---|---|---|---|---|---|---|---|---|---|
| pgtq | D | 1 | 88.2 | 97.8 | 102.4 | 3 | 84 | plan (6.0 s) | 0 |
| pgtq | M | 1 | 149.4 | 159.6 | 173.1 | 3 | 95 | **modified** (58.9 s) | 1 ($0.011759) |
| pgtq | D | 2 | 78.3 | 89.4 | 103.6 | 4 | 86 | plan (4.8 s) | 0 |
| pgtq | M | 2 | 129.3 | 137.2 | 146.2 | 3 | 84 | **modified** (56.5 s) | 1 ($0.012979) |
| postgres_tq | D | 1 | 177.1 | 187.9 | 204.1 | 7 | 175 | plan (5.7 s) | 0 |
| postgres_tq | M | 1 | 182.3 | 240.9 | 252.3 | 5 | 186 | **rejected** (60.3 s) | 1 ($0.014408) |
| postgres_tq | D | 2 | 147.8 | 157.8 | 167.5 | 5 | 153 | plan (4.9 s) | 0 |
| postgres_tq | M | 2 | 265.1 | 280.4 | 289.6 | 6 | 194 | **rejected** (71.3 s) | 1 ($0.017056) |

- **Lock waits** were 0 in every session; nothing else used the database meanwhile.
- **Corroboration:** every S3 run was corroborated, with 0 parameters mismatched.
- **Muse model:** `muse-spark-1.3` for every call.

## Muse's actual contribution (`CHECKPOINTS.json`)

- **pgtq, accepted with changes (2/2 modified).** Muse correctly named the package, the connection seam and a task
  shape. Its draft lacked the pinned spec fields and had an empty workload; both operators filled those in and
  validated it as a Muse-sourced proposal (0 rejected). The adapter itself was still written by the operator.
- **postgres-tq, rejected (2/2).** Muse gave the distribution name instead of the import package (`postgres-tq`
  for `postgrestq`), an "unknown" task and an empty workload. Only its list of unknowns was judged useful. Neither
  operator asked Muse again.

## Paired differences, M − D (predeclared, lock-adjusted)

| App | Replicate | seconds to S3 | adapter lines | interventions | failed attempts |
|---|---|---|---|---|---|
| pgtq | 1 | +61.8 | +11 | 0 | 0 |
| pgtq | 2 | +47.8 | −2 | −1 | 0 |
| postgres_tq | 1 | +53.0 | +11 | −2 | 0 |
| postgres_tq | 2 | +122.6 | +41 | +1 | 0 |
| **median, pgtq** | | **+54.8** | +4.5 | −0.5 | 0 |
| **median, postgres_tq** | | **+87.8** | +26.0 | −0.5 | 0 |

## Reading (descriptive; no claim)

- **Every pair took longer under M, by 48–123 s to S3.** Most of that is the checkpoint itself. D recorded its plan
  5–6 s after starting; M recorded its decision 56–71 s after starting, once the Muse request had returned (38–58 s)
  and been read. That is about 51–66 s of checkpoint cost per M session.
  - **Residual after subtracting it (POST-HOC):** about −4 to +9 s for pgtq, and about −2 to +56 s for postgres-tq.
  - **Interpretation:** for these libraries, the Muse consultation added time and did not shorten the rest of the
    work.
- **No effect on correctness or attempts.** Both conditions reached S3 on the first attempt in all 8 sessions, so
  failed attempts and correctness can't separate them (a ceiling effect).
- **No claim is possible.** The plan's Q4 rule needs at least 3 applications and a 30% median reduction in both
  minutes and lines. There are 2 applications, and M was slower, not faster.
- **What M contributed:** Muse supplied useful identification for pgtq and was rejected for postgres-tq. In neither
  application did it reduce effort for an AI operator that could read the source.

## Threats to validity

- **Operators:** all are one model family, with general knowledge of the libraries; isolation is by instruction
  only.
- **Exposed applications:** the main repository, outside the worktrees, holds onb-pilot/2's adapters for these apps.
- **Ceiling:** the tasks are short (2–5 minutes).
- **Sample:** 2 replicates per cell.
- **Timing:** the checkpoint's Muse latency depends on the provider.
- **Scope:** AI-operator sessions say nothing about human onboarding.
