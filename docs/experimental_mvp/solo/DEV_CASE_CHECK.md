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
