# saq `max_burst_jobs`: what it promises, and what our clause B3 assumes

NONBLIND, written 2026-11-15, after ds-nbc/2 (developers who had seen the results). Earlier results and the
saq-c570ba4 oracle are unchanged.

## Versions read

| Tree | Commit | Pinned as |
|---|---|---|
| saq-c570ba4 R1 (parent) | `59f32fce50428e5fb0073204f31cbe7ffc84c293` | archive sha256 `5adf904d…` (`research_tools/nbc/pin_saq.py`) |
| saq-c570ba4 R2 (fix) | `c570ba4ee8366404db97e21074390361328f0d8b` | archive sha256 `d5de08ee…` |
| saq-ad8d1c8 R2 | `ad8d1c8d3cfdf57d6800098ff1680862a964f9fc` | archive sha256 `047b052d…` |
| **Latest PyPI release** | v0.26.5 = `4a9ecc242f04861552955a5a40f6232889f94354` | `saq-0.26.5.tar.gz`, sha256 `75e8664ca1351f46a400d28d0441612be4feb6c51ab206cdfad9dc06fcb42acc` (uploaded 2026-10-29); its `saq/` directory is byte-identical (`diff -r`) to the pinned git archive `bringup/v0.26.5.tar`, sha256 `39fd0698…` |

The burst code below (`saq/worker.py`) is the same at 59f32fc, c570ba4, ad8d1c8 and v0.26.5: the same lines,
at the same line numbers.

## What saq says

**The only documentation is the `Worker` docstring.** `docs/` (`worker.md`, `settings.md`, `usage.md`,
`getting_started.md`), the README and the CLI never mention `burst` or `max_burst_jobs` at v0.26.5. The repository
has no changelog file, and no GitHub release note mentions burst.

- `saq/worker.py:73-74` at v0.26.5
  ([link](https://github.com/tobymao/saq/blob/4a9ecc242f04861552955a5a40f6232889f94354/saq/worker.py#L73-L74)):
  > `burst: whether to stop the worker once all jobs have been processed`
  > `max_burst_jobs: the maximum number of jobs to process in burst mode`
- The introducing PR, [#179 "Feat: Support burst mode"](https://github.com/tobymao/saq/pull/179) (merged
  2024-10-18, commit `6ecd146`), has an empty description. Its only review comment is about code structure.
- [#311](https://github.com/tobymao/saq/pull/311) (commit `ad8d1c8`), body:
  > "burst mode caps in-flight dequeues at `max_burst_jobs`."
- The maintainers' only test is `tests/test_worker.py::test_max_burst_jobs`
  ([L707-L726](https://github.com/tobymao/saq/blob/4a9ecc242f04861552955a5a40f6232889f94354/tests/test_worker.py#L707-L726)).
  It uses `max_burst_jobs=1` and default concurrency, which the constructor reduces to 1. Of three jobs, it asserts
  that exactly one is `COMPLETE` and two are `QUEUED`.

## What the code does

- `worker.py:152-153`: in burst mode with a cap, `self.concurrency = min(self.concurrency, self.max_burst_jobs)`.
- `worker.py:198-202`: it starts `min(concurrency, max_burst_jobs)` process slots. This is the in-flight cap from
  #311.
- `worker.py:438-466` (`_process`, `_check_burst`): a job is counted only **after** its process task finishes.
  While the count is still below `max_burst_jobs`, each finished task starts a new one. A slot that is still busy
  isn't counted. So with `c = concurrency > 1`, up to `max_burst_jobs - 1 + c` jobs can start. For example,
  `c = 2`, `max_burst_jobs = 2`: 3 jobs. With `c = 1` it is exact, which is the only case the test covers.

## Reading: per worker or global? per window or lifetime? hard cap or best effort?

| Question | Docstring | Code |
|---|---|---|
| Scope | per `Worker` instance (a constructor argument) | per instance: `burst_jobs_processed` is an instance counter, never shared between workers |
| Window | "in burst mode": the life of the burst run, which ends when the worker stops | lifetime of one `start()`; the counter is never reset |
| Hard or best effort | "the **maximum** number of jobs to process" reads as a hard cap | best effort for `concurrency > 1`. Extra jobs started before the count catches up are processed to completion. #311 states only an in-flight cap |

## Our clause B3, and whether it matches

B3 (`saq-c570ba4/oracle.py`): "with `max_burst_jobs = m`, at most `m` jobs are `complete`", for **one burst worker**
over its whole run. It is the docstring's reading: per worker, over the burst, a hard maximum.

- **It matches the docstring.** "the maximum number of jobs to process" is a statement about processed jobs, not in-flight ones.
- **It doesn't match the implementation** when `concurrency > 1`, and #311's wording ("caps in-flight dequeues")
  describes a weaker property than the docstring.

So the docs don't permit what we saw. **B3 is not an oracle error under the documented contract.** It's either a
defect in the code, or a docstring that overstates an intentionally best-effort cap. Only the maintainers can say
which. Phase 2B goes ahead (`RERUN.md`).

## Related upstream items (searched 2026-11-15, open and closed, "burst" and "max_burst_jobs")

| Item | State | Relevance |
|---|---|---|
| [#179 Feat: Support burst mode](https://github.com/tobymao/saq/pull/179) | merged 2024-10-18 | introduced `burst` and `max_burst_jobs`; no discussion of semantics |
| [#253 Fix race conditions in Worker and stabilize async tests](https://github.com/tobymao/saq/pull/253) | closed, not merged | touches `burst_jobs_processed` locking (threading vs asyncio lock); no cap semantics |
| [#311 Support Python 3.11-3.15, pin ruff, fix flaky queue tests](https://github.com/tobymao/saq/pull/311) | merged | Postgres dequeue fix found by `test_max_burst_jobs`; adds "burst mode caps in-flight dequeues at `max_burst_jobs`" |
| [#98 not working in windows due to asyncio has no add_signal_handler](https://github.com/tobymao/saq/issues/98) | closed | matched "burst" in the search; unrelated |

No issue about `max_burst_jobs` being exceeded was found. Searches: `gh search issues --repo tobymao/saq burst`,
`gh search prs --repo tobymao/saq burst`, and the same for `max_burst_jobs`.
