"""Oracle for the NONBLIND development case ``saq-c570ba4`` (ds-nbc/1). Developer-written.

Property (developer-written, NONBLIND), on a SOUND run only, for a producer that enqueued jobs ``default-<n>`` and a
saq burst worker that then stopped by itself:
* B0: exactly the enqueued jobs exist in ``saq_jobs``;
* B1: after the worker stopped, every job is ``complete`` or ``queued`` (none is left ``active``, i.e. taken from the
  queue but never processed, nor failed or aborted);
* B2: a ``complete`` job's handler ran exactly once and its result is ``n * 2``; a ``queued`` job's handler never ran;
* B3: with ``max_burst_jobs = m``, at most ``m`` jobs are ``complete``.
NO_VERDICT when the precondition fails: not every job was enqueued, an actor reported a database error, or the worker
was stopped by the harness guard instead of stopping by itself.

Deliberately not asserted: that *every* job completes when no cap is set. A burst worker whose dequeue times out
stops by design ("dequeue_timeout: how long it will wait to dequeue"); jobs it never took stay ``queued`` for
another worker and aren't lost. The property is about jobs that left the queue.

Sources (the documented contract; nothing here is taken from the fix's code):
* ``saq/worker.py`` Worker docstring (both trees): "burst: whether to stop the worker once all jobs have been
  processed"; "max_burst_jobs: the maximum number of jobs to process in burst mode"; "dequeue_timeout: how long it will
  wait to dequeue".
* ``saq/job.py`` ``Status`` (queued, active, aborting, aborted, failed, complete).
* maintainers' tests in ``tests/test_worker.py`` (present, with these assertions, in both trees):
  ``test_max_burst_jobs`` (3 jobs, ``max_burst_jobs=1``: the first ``Status.COMPLETE``, the other two
  ``Status.QUEUED``) and ``test_burst`` / ``test_burst_poll_interval`` (each enqueued job ``Status.COMPLETE``).
* the maintainers' test added by the fix, ``tests/test_queue.py::test_dequeue_takes_job_buffered_while_waiting``
  ("a dequeue that times out must still take it instead of reporting an empty queue"), and the fix's commit message,
  read during screening: "A burst worker then stops with the job stranded active and never processed".
"""

from __future__ import annotations

from typing import Any

JOBS_TABLE = "saq_jobs"


def check(final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
    producer, worker = raw_results.get("producer"), raw_results.get("worker")
    if not isinstance(producer, dict) or not isinstance(worker, dict):
        return "NO_VERDICT"
    keys = producer.get("enqueued") or []
    if producer.get("db_error") or not keys or None in keys or len(keys) != len(producer.get("ns") or []):
        return "NO_VERDICT"  # the precondition (every job enqueued) doesn't hold
    if worker.get("db_error") or worker.get("stopped_by") != "burst":
        return "NO_VERDICT"  # the worker didn't stop by itself (a guard stop or an error): no claim about burst
    rows = {r["key"]: r for r in (final_state.get("jobs") or {}).get(JOBS_TABLE, [])}
    if set(rows) != set(keys):
        return "VIOLATED"  # B0: exactly the enqueued jobs exist
    handled = [f"{t}-{n}" for t, n in worker.get("handled") or []]
    complete = 0
    for k in keys:
        r, runs = rows[k], handled.count(k)
        if r["status"] == "complete":
            complete += 1
            n = int(k.split("-")[1])
            if runs != 1 or r.get("result") != n * 2:
                return "VIOLATED"  # B2: a complete job ran exactly once, with its result
        elif r["status"] == "queued":
            if runs != 0:
                return "VIOLATED"  # B2: a queued job never ran
        else:
            return "VIOLATED"  # B1: after the burst worker stopped, a job is neither complete nor queued
    cap = worker.get("max_burst_jobs")
    if cap is not None and complete > cap:
        return "VIOLATED"  # B3: at most max_burst_jobs jobs processed
    return "HOLDS"
