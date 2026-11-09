# Audit of the original ds-nbc/1 Track B comparison (2026-11-10)

**This audit changes nothing in the original experiment.** `MANIFEST.json`, `ADMISSIONS.jsonl`, `runs/`, `analysis_1/`
and `RESULTS.md` are unchanged. The original result stands as the primary result of that experiment:

> RA-DET 4/4 vs RA-MD 1/4; exact McNemar p = 0.25; INSUFFICIENT SAMPLE.

Everything below that involves unexecuted proposals is **POST-HOC** and never replaces that result.

## 1. Why only the first three Muse proposals ran

`research_tools/confirmatory/real_executor.py` line 211 picks `workload = work[rep % len(work)]`.
- **The shape:** the plan's §5 has 3 replicates of B/3 = 4 schedules, and each replicate runs **one** workload. An
  arm therefore executes at most 3 distinct workloads, the first 3 of its list.
- **For RA-MD:** with R = 2 requests × P = 6 proposals = 12 valid proposals per case, proposals 3–11 could never run.
- **For RA-DET:** it has exactly 3 workloads, so all of them ran.

Neither the manifest nor the executor's docstring stated this asymmetry. It is a design property of the frozen
executor, not a runtime fault.

## 2. Valid but unexecuted proposals

| Case | Valid Muse proposals | Executed | Valid, not executed |
|---|---|---|---|
| chancy-2895c4a | 12 | 3 | 9 |
| pgqueuer-8dfadf4 | 12 | 3 | 9 |
| saq-c570ba4 | 12 | 3 | 9 |
| saq-addee31 | 12 | 3 | 9 |
| **total** | **48** (0 invalid) | **12** | **36** |

## 3. Unexecuted proposals whose structure matches the defect (POST-HOC)

The predicates below were written **after** the outcomes. They come from the reproducing RA-DET workloads and the
fix descriptions, so they are fix-informed. They describe structure only and prove nothing about detection: none of
these proposals was run.

| Case | Predicate (post-hoc) | Unexecuted proposals matching |
|---|---|---|
| chancy-2895c4a | the first job is cancelled (a cancel that can land after the worker claims it) | 4, 5, 6, 7, 8, 10, 11 (7 of 9) |
| pgqueuer-8dfadf4 | `concurrency_limit` 1, at least 2 jobs, at least one cancelled | 5, 10 (2 of 9) |
| saq-c570ba4 | `dequeue_timeout_s < poll_interval_s` (a dequeue can time out while the schedule tick holds a job). Weak: RA-DET workload 2 meets it and held | 3, 10 (2 of 9) |
| saq-addee31 | the two queues use different table sets | 3, 4, 5, 8, 9, 11 (6 of 9) |

- **Unexecuted but matching:** 17 of the 36 unexecuted proposals.
- **Executed and matching:**
  - **chancy:** proposals 0 and 1 matched, and both detected.
  - **pgqueuer, saq-c570ba4, saq-addee31:** none of the executed proposals matched.
- **Implication:** in this experiment Muse's result depended heavily on proposal *order*, not only on whether it
  could propose a triggering workload. This motivates ds-nbc/2's allocation rule, which executes every admitted
  proposal.

## 4. Was workload selection outcome-independent?

**Yes.**
- **Muse:** proposals were taken positionally (index = replicate). They were journaled before any RA-MD run in
  every case: the last reply came 38–40 s before the first RA-MD ledger entry (`muse/calls.jsonl` against
  `RESULTS_LEDGER.jsonl`).
- **RA-DET:** its workloads were frozen in each case's integration before that case's first live run.
- **After seeing outcomes:** no proposal was substituted, reordered or dropped.

## 5. Did the deterministic workloads use knowledge of the fixes?

**Yes, in every case.** Each case's `CASE.md` records that the developer read the fix commit, its message and diff,
and (where present) the maintainers' test before writing the integration and its 3 RA-DET workloads.
- The RA-DET workloads are curated regression workloads, which the protocol allows for RA-DET.
- They encode the triggering structure: the cancel-first job in chancy; limit 1 plus cancel in pgqueuer;
  `dequeue_timeout < poll_interval` in saq-c570ba4; different table sets in saq-addee31.
- Muse saw only the neutral `MUSE_CONTEXT`.

The original comparison is therefore **biased toward RA-DET by design**. It shows that fix-informed regression
workloads detect the fixed defect, not that Muse is worse at finding unknown defects.

## 6. Were oracles or workload definitions changed after outcomes?

| Case | Integration, oracle, spec, CASE.md | After the first live run? |
|---|---|---|
| chancy-2895c4a | each 1 commit (`b38142e`), before reproduction (`55bf505`) | no |
| pgqueuer-8dfadf4 | each 1 commit (`359c2aa`), before reproduction (`9eabcfc`) | no |
| saq-c570ba4 | each 1 commit (`f1cf4b8`), before reproduction (`6275858`) | no |
| saq-addee31 | v1 files in `f1cf4b8`; **`saq_two_queues_addee31_v2.py`, `oracle_v2.py`, `case_spec_v2.json` in `7e867b8`, after attempts 1–2** | **yes: an explicit deviation** |

**saq-addee31** was re-encoded after its first two attempts were refused (a stale image, then a parse-time error
the corroborator can't pair). Version 2 changed the workload space, so the second queue only connects, and the
oracle, so the tables must exist afterwards. It is recorded in that case's `ERRATA.md` and `RESULTS.md`, and stays
an explicit deviation.

## 7. Sensitivity analysis excluding saq-addee31 (original runs, no new execution)

| | RA-DET | RA-MD |
|---|---|---|
| Cases detected | 3 of 3 (exact 95% CI 0.292–1.000) | 1 of 3 (exact 95% CI 0.008–0.906) |
| Discordant | 2 RA-DET only, 0 RA-MD only | |
| Exact McNemar | p = 0.50 | |
| Verdict | INSUFFICIENT SAMPLE | |

The direction is the same as the full analysis, with wider uncertainty. Like the original, this comparison is
biased toward RA-DET (§5).
