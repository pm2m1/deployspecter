# Solo design: the adapter and the generator, checked on the development cases (2026-11-16; NONBLIND, EXPLORATORY)

These are the four reproduced ds-nbc/1 development cases. They were chosen and encoded after their fixes were read,
and are never counted in any result. This file checks the solo tooling, not DeploySpecter's detection rate.

## Live runs (RA-DET arm, disposable PostgreSQL 16 on 127.0.0.1:55441, archives verified against the case specs)

`research_tools/nbc/run_nbc.py run --arms RA-DET` was run twice per case: once plainly, and once wrapped by
`research_tools/solo/capture_final_states.py` to save each oracle call's final state. Both runs of each case gave
the same summary, and it matches `nonblind_corpus/CORPUS_RESULTS.md`.

| Case | Detected | Schedules to first R1 violation | Runs | R2 | Parameters mismatched |
|---|---|---|---|---|---|
| chancy-2895c4a | yes | 1 | 2 VIOLATED, 2 HOLDS, 2 INCOMPLETE | no violation | 0 |
| pgqueuer-8dfadf4 | yes | 2 | 3 VIOLATED, 3 HOLDS | no violation | 0 |

The captured final states (10 chancy and 16 pgqueuer oracle calls) are test fixtures in
`tests/experimental/fixtures/solo/captured/`.

## The maintainer-test adapter on all four cases

The specs are in `maintainer_specs/`; the upstream test files at the fix commits, byte for byte (renamed `.py.txt` so
that no tool rewrites them), are in `tests/experimental/fixtures/solo/upstream_tests/`.

| Case | Fix's regression test | Adapter | On the captured runs |
|---|---|---|---|
| chancy-2895c4a | `test_cancellation_fences_execution_completion` (1 of 13 changed test functions matches the workload) | **admitted**: 2 of 8 assertions expressed (lines 164, 166); 2 schema added by the fix, 1 intermediate, 1 return value, 2 mocks | R1 VIOLATED 2 of 2 (line 164); R2 HOLDS 8 of 8; agrees with the case oracle on 10 of 10 |
| pgqueuer-8dfadf4 | `test_cancel_keeps_picked_row_until_worker_logs` (1 of 3 changed functions; the in-memory backend's file isn't used) | **admitted**: 5 of 11 expressed (253, 254, 258, 264, 265); line 258 is **translated** through the integration's handler table | R1 VIOLATED 3 of 4 (line 258); R2 HOLDS 12 of 12; agrees with the case oracle on 16 of 16 |
| saq-c570ba4 | `test_dequeue_takes_job_buffered_while_waiting` | **refused**: its only assertion is on the object `dequeue()` returns (`in_memory_return_value`) | — |
| saq-addee31 | none: the fix changes only `tests/helpers.py` | **refused**: no regression test (`select_cases.py` would skip it as `no_regression_test`) | — |

**So, through their maintainer tests, 2 of the 4 reproduced cases are detected and their controls hold, and 2 are
refused by design.** A blind run should expect refusals at a similar rate, and so need more screens than
`benchmark_prep/FEASIBILITY.md` estimated.

## The mutant generator on real trees (throwaway seed and store; not the sealed seed)

| Tree | Files scanned | Sites RM1 / RM2 / RM3 / RM4 / RM5 / RM6 | Sampled | Unparseable |
|---|---|---|---|---|
| chancy 2895c4a | 78 | 12 / 11 / 0 / 25 / 0 / 2 | 17 | 0 |
| pgqueuer 8dfadf4 | 85 | 12 / 2 / 0 / 0 / 4 / 0 | 11 | 0 |
| saq v0.26.5 | 19 | 2 / 2 / 0 / 3 / 0 / 1 | 8 | 0 |

RM3 found no site: these libraries have no batched backfill with a multi-conjunct `WHERE` in a migration file. No
mutant was run.

## Spec format 2: assertion coverage (2026-11-16, before registration)

The two admitted specs were converted to `ds-maintainer-spec/2`. Each expressed assertion declares
`uses_deployspecter_code`, which `check-spec` verifies against each spec's declared sources:
- **chancy:** `jobs_detail` is plain SQL over `chancy_jobs`;
- **pgqueuer:** `log` and `queue` are plain SQL over pgqueuer's tables, while `runs` is DeploySpecter's handler table
  `ds_pgq_runs`.

The mechanically checkable reasons are verified. Two transcription changes:
- **chancy line 146** (`assert await chancy.cancel_job_ex(cursor, ref) == 1`) was `in_memory_return_value`, which
  fails mechanical verification because the assertion is itself a call. It is now `not_in_final_state`, an
  investigator judgement: the returned row count isn't stored in the database.
- **pgqueuer lines 253, 254, 264 and 265** now tell picked jobs from pgqueuer's own log (a `picked` entry) instead of
  the handler table. The precondition no longer reads the handler table either. Line 258 still reads it, and is
  flagged `true`.

| Coverage | chancy-2895c4a | pgqueuer-8dfadf4 |
|---|---|---|
| Assertions in the chosen test function | 8 | 11 |
| Expressed over the final state | 2 (164, 166) | 5 (253, 254, 258, 264, 265) |
| Expressed without DeploySpecter code | 2 | 4 |
| Expressed with DeploySpecter code | 0 | 1 (258, the handler table) |
| Not expressible, by reason | mock 2, schema_added_by_fix 2, intermediate_state 1, not_in_final_state 1 | in_memory_return_value 3, intermediate_state 2, exception 1 |
| Reasons verified mechanically | 2 (both mocks) | 4 (3 return values, 1 `pytest.raises`) |
| Reasons by investigator judgement | 4 | 2 |
| In the strict subset | **no** (judgement) | **no** (judgement, and DeploySpecter code) |

saq-c570ba4 is still refused, but its only reason (`in_memory_return_value`, line 583) is now verified
mechanically. saq-addee31 is still refused: there is no regression test.

**Live re-run with the format-2 oracles** (RA-DET, both cases, captured as before; same pinned archives):
- **chancy-2895c4a:** detected on schedule 1; runs 2 VIOLATED, 2 HOLDS, 2 INCOMPLETE; 0 parameters mismatched. The
  format-2 oracle agrees with the case oracle on 10 of 10 calls: R1 VIOLATED 2 of 2 through line 164 (no
  DeploySpecter code), and R2 HOLDS 8 of 8.
- **pgqueuer-8dfadf4:** detected on schedule 2; runs 3 VIOLATED, 3 HOLDS; 0 mismatched. It agrees on 16 of 16 calls:
  R1 VIOLATED 3 of 4, R2 HOLDS 12 of 12.

**Every pgqueuer detection rests only on line 258**, the assertion that reads DeploySpecter's handler table. The
four assertions over pgqueuer's own tables hold on R1 as well. The verdicts are unchanged from the format-1 oracles,
and the controls still hold.
