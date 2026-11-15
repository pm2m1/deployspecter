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
