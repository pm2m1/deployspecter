A burst worker with `concurrency=2` and `max_burst_jobs=2` processes 3 jobs when 3 are queued. I expected at most 2.

**Versions:** saq 0.26.5 (PyPI; `main` has no changes to the burst code since), Python 3.12.16, psycopg 3.3.9,
PostgreSQL 16. Links point at `4a9ecc2` (v0.26.5).

### Why I read it as a hard limit

- The `Worker` docstring ([L73-L74](https://github.com/tobymao/saq/blob/4a9ecc242f04861552955a5a40f6232889f94354/saq/worker.py#L73-L74)): "max_burst_jobs: the maximum number of jobs to process in burst
  mode".
- The constructor clamps concurrency to it ([L152-L153](https://github.com/tobymao/saq/blob/4a9ecc242f04861552955a5a40f6232889f94354/saq/worker.py#L152-L153)):
  ```python
  if self.max_burst_jobs is not None:
      self.concurrency = min(self.concurrency, self.max_burst_jobs)
  ```

### Observed

| concurrency | max_burst_jobs | jobs queued | handler calls | iterations over the cap |
|---|---|---|---|---|
| 2 | 2 | 3 | 3 | 20 of 20 |
| 2 | 3 | 4 | 4 | 20 of 20 |
| 1 | 2 | 3 | 2 | 0 of 20 (control) |
| 2 | 2 | 2 | 2 | 0 of 20 (control) |

Each extra job runs once and ends `complete`. None is duplicated or left `active`.

### Mechanism, as far as I can tell

`_check_burst` increments `burst_jobs_processed` only when a process task **completes**
([L438-L466](https://github.com/tobymao/saq/blob/4a9ecc242f04861552955a5a40f6232889f94354/saq/worker.py#L438-L466)). When the first of 2 in-flight jobs finishes, the count is 1 of 2, so `_process`
starts a new task, which dequeues a third job while the second is still running. When the second finishes, the cap
is reached, but in-flight tasks aren't cancelled, so the third also runs. In general up to
`max_burst_jobs + concurrency - 1` jobs run. The slot limit in `start()` ("burst mode caps in-flight dequeues at
`max_burst_jobs`", #311) bounds jobs in flight, not the total. With `concurrency=1`, the case `test_max_burst_jobs`
covers, the count is exact.

**Suggestion (untested):** count a job when it is dequeued, or stop dequeuing once completed + in-flight >=
`max_burst_jobs`.

### Reproduction

Only saq's public API. Save both files in one directory, then:

```sh
docker compose run --rm repro                                              # concurrency 2, max_burst_jobs 2, 3 jobs, 20 iterations
docker compose run --rm repro python /repro/repro_max_burst.py 20 1 2 3    # control: concurrency 1
docker compose down -v
```

Output: `RESULT: more than max_burst_jobs=2 jobs processed in 20 of 20 iterations` (control: 0 of 20). The handler
sleeps 0.1 s so that both slots are busy at once; I haven't measured how often very short jobs overrun.

<details>
<summary><code>repro_max_burst.py</code></summary>

```python
"""max_burst_jobs overrun: standalone reproduction with saq's public API only (no other tooling).

A burst worker with concurrency=2 and max_burst_jobs=2 is given 3 queued jobs. Worker's docstring says
"max_burst_jobs: the maximum number of jobs to process in burst mode", so at most 2 handler calls are expected.
Each iteration uses its own queue name. Control: the same with concurrency=1.

    python repro_max_burst.py [N] [concurrency] [max_burst_jobs] [jobs]
"""
import asyncio
import os
import sys
import uuid

from saq import Queue, Worker

DSN = os.environ.get("SAQ_PG_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
calls: list[str] = []
RUN = uuid.uuid4().hex[:8]  # unique queue names, so no earlier run's jobs are picked up


async def work(ctx, n: int) -> int:
    calls.append(ctx["job"].key)
    await asyncio.sleep(0.1)  # long enough for both slots to be busy at once
    return n * 2


async def once(i: int, concurrency: int, cap: int, jobs: int) -> tuple[int, list[str]]:
    calls.clear()
    queue = Queue.from_url(DSN, name=f"burst-repro-{RUN}-{i}")
    await queue.connect()
    try:
        enqueued = [await queue.enqueue("work", n=n) for n in range(jobs)]
        worker = Worker(queue, functions=[work], concurrency=concurrency, burst=True,
                        max_burst_jobs=cap, dequeue_timeout=1)
        await asyncio.wait_for(worker.start(), timeout=30)
        statuses = []
        for job in enqueued:
            await job.refresh()
            statuses.append(str(job.status))
        return len(calls), statuses
    finally:
        await queue.disconnect()


async def main() -> None:
    n, concurrency, cap, jobs = (int(a) for a in (sys.argv[1:] + ["20", "2", "2", "3"][len(sys.argv) - 1:]))
    import saq
    print(f"saq {saq.__version__}, concurrency={concurrency}, max_burst_jobs={cap}, jobs={jobs}, iterations={n}")
    over = 0
    for i in range(n):
        handled, statuses = await once(i, concurrency, cap, jobs)
        over += handled > cap
        print(f"iteration {i}: handler called {handled} times ({len(set(calls))} distinct jobs); statuses {statuses}")
    print(f"RESULT: more than max_burst_jobs={cap} jobs processed in {over} of {n} iterations")


if __name__ == "__main__":
    asyncio.run(main())
```

</details>

<details>
<summary><code>docker-compose.yml</code></summary>

```yaml
# Disposable: PostgreSQL 16 and a Python 3.12 container with saq 0.26.5 from PyPI. No host port is published.
#   docker compose run --rm repro                      # 20 iterations, concurrency 2, max_burst_jobs 2, 3 jobs
#   docker compose run --rm repro python /repro/repro_max_burst.py 20 1 2 3   # control: concurrency 1
#   docker compose down -v
services:
  db:
    image: postgres:16
    environment: {POSTGRES_PASSWORD: postgres}
    healthcheck: {test: ["CMD", "pg_isready", "-U", "postgres"], interval: 1s, retries: 30}
  repro:
    image: python:3.12-slim
    depends_on: {db: {condition: service_healthy}}
    environment: {SAQ_PG_URL: "postgresql://postgres:postgres@db:5432/postgres", PIP_DISABLE_PIP_VERSION_CHECK: "1"}
    volumes: ["./repro_max_burst.py:/repro/repro_max_burst.py:ro"]
    entrypoint: ["sh", "-c", "pip install -q 'saq[postgres]==0.26.5' 'psycopg[binary]' && exec \"$@\"", "--"]
    command: ["python", "/repro/repro_max_burst.py", "20", "2", "2", "3"]
```

</details>

### This may be intended

If `max_burst_jobs` is meant as a soft limit, could the docstring say so? For example: "max_burst_jobs: stop taking
new jobs once this many have finished; with `concurrency > 1`, up to `concurrency - 1` jobs already started may also
run."
