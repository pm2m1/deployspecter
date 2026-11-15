<!-- DRAFT for the owner to review and post. Not posted. Paste from the title line down. -->

**Title:** `max_burst_jobs` is exceeded when `concurrency > 1` (burst worker processes up to `max_burst_jobs + concurrency - 1` jobs)

A burst worker with `concurrency=2` and `max_burst_jobs=2` processes 3 jobs when 3 are queued. The `Worker`
docstring describes `max_burst_jobs` as "the maximum number of jobs to process in burst mode", so I expected at
most 2.

Verified on saq 0.26.5 (PyPI), Python 3.12.16, psycopg 3.3.9, PostgreSQL 16. Line links point at `4a9ecc2`
(v0.26.5). The burst code is unchanged since #311.

### Expected

From the `Worker` docstring
([worker.py#L73-L74](https://github.com/tobymao/saq/blob/4a9ecc242f04861552955a5a40f6232889f94354/saq/worker.py#L73-L74)):

> burst: whether to stop the worker once all jobs have been processed
> max_burst_jobs: the maximum number of jobs to process in burst mode

With `max_burst_jobs=2`, at most 2 jobs are processed and the rest stay `queued`. That is what `test_max_burst_jobs`
asserts, with `max_burst_jobs=1`.

### Actual

| concurrency | max_burst_jobs | jobs queued | handler calls | runs over the cap |
|---|---|---|---|---|
| 2 | 2 | 3 | 3 | 20 of 20 |
| 2 | 3 | 4 | 4 | 20 of 20 |
| 1 | 2 | 3 | 2 | 0 of 20 |
| 2 | 2 | 2 | 2 | 0 of 20 |

Every extra job runs once and ends `complete`. None is duplicated or left `active`.

### Why, as far as I can tell

`_check_burst` counts a job only when its process task finishes
([worker.py#L438-L466](https://github.com/tobymao/saq/blob/4a9ecc242f04861552955a5a40f6232889f94354/saq/worker.py#L438-L466)).
With 2 slots, the first job to finish brings the count to 1 of 2, so `_process` starts a new task, which dequeues
a third job. Meanwhile the second slot is still running and hasn't been counted. The constructor's
`concurrency = min(concurrency, max_burst_jobs)` and the slot limit in `start()` cap jobs in flight ("burst mode
caps in-flight dequeues at `max_burst_jobs`", #311), not the total. So up to `max_burst_jobs + concurrency - 1` jobs
can run. With `concurrency=1`, the only case the test covers, the count is exact.

### Minimal reproduction

`repro_max_burst.py` uses only saq's public API, and `docker-compose.yml` provides PostgreSQL 16 plus Python 3.12
with `saq[postgres]==0.26.5`:

```sh
docker compose run --rm repro                                         # concurrency 2, max_burst_jobs 2, 3 jobs, 20 runs
docker compose run --rm repro python /repro/repro_max_burst.py 20 1 2 3   # control, concurrency 1
docker compose down -v
```

```python
worker = Worker(queue, functions=[work], concurrency=2, burst=True, max_burst_jobs=2, dequeue_timeout=1)
await worker.start()   # with 3 jobs queued: `work` is called 3 times
```

Output: `RESULT: more than max_burst_jobs=2 jobs processed in 20 of 20 iterations`. The handler sleeps 0.1 s, so
both slots are busy at once. With very short jobs the overrun may be less frequent. I haven't measured that.

### This may be intended

If `max_burst_jobs` is meant as a soft limit, or only as a cap on jobs in flight, could the docstring say so? For
example: "stop taking new jobs once `max_burst_jobs` have finished; with `concurrency > 1` up to
`concurrency - 1` further jobs already started may also complete". If a hard cap is intended, one option is to
count a job when it is dequeued rather than when it finishes, which I haven't tested. [Owner: keep or delete] I'm happy to send a PR
either way.

<details>
<summary>Appendix: the same overrun under a controlled schedule</summary>

I first saw this in a research tool (DeploySpecter) that runs a library under a coordinator granting each database
statement in a chosen order, and checks the trace against the PostgreSQL server log. On saq 0.26.5, with 3 jobs,
concurrency 2 and `max_burst_jobs` 2, the worker's statements were:

```
grant  28  claim (dequeue)   WITH locked_job AS ( SELECT key FROM "saq_jobs" WHERE status = 'queued' ...   (2 jobs)
grant  56  update+expire_at  UPDATE "saq_jobs" SET ... expire_at ...                                          (job 1 done)
grant  60  update+expire_at  UPDATE "saq_jobs" SET ... expire_at ...                                          (job 2 done)
grant  64  claim (dequeue)   WITH locked_job AS ( ... )                                                       (job 3)
grant  77  update+expire_at  UPDATE "saq_jobs" SET ... expire_at ...                                          (job 3 done)
```

The result was the same under 5 different schedules per workload, and on the commits before and after `c570ba4`.
These are research results from a non-blind development study. I'm not reporting anything beyond the issue above.

</details>
