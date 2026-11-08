"""Oracle for the NONBLIND development case ``saq-addee31`` (ds-nbc/1). Developer-written.

Property (developer-written, NONBLIND), on a SOUND run only. A first queue (``first``) connected, enqueued its jobs
and runs a worker; then a second queue (``second``), configured with its own table set or the same one, connected
and enqueued its jobs:
* Q1: the second queue's ``connect`` and every ``enqueue`` returned without a database error, and every ``enqueue``
  returned a job (none was already enqueued);
* Q2: each job the second queue enqueued is present in that queue's jobs table, and so is each job the first queue
  enqueued in its own.
NO_VERDICT when the precondition fails: the first queue didn't connect and enqueue without error.

Sources (the documented contract; nothing here is taken from the fix's code):
* ``saq/queue/postgres.py`` ``PostgresQueue`` docstring (both trees): "versions_table: name of the Postgres table SAQ
  will use to maintain migrations"; "jobs_table: name of the Postgres table SAQ will write jobs to (default
  "saq_jobs")"; "stats_table: ..." (the table sets are documented constructor arguments);
* ``saq/queue/base.py`` ``Queue.enqueue`` docstring: "Returns: If the job has already been enqueued, this returns
  None, else Job";
* the maintainers' description of the defect in the fix's commit message (read during screening): "Any other queue
  running init_db meanwhile silently skipped migrations and then failed with UndefinedTable". The fix adds no
  regression test; this is the weakest-sourced of the saq cases (see SCREENING.md, E4).
"""

from __future__ import annotations

from typing import Any

TABLE = {"default": "saq_jobs", "alt": "saq_alt_jobs"}


def check(final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
    first, second = raw_results.get("first"), raw_results.get("second")
    if not isinstance(first, dict) or not isinstance(second, dict):
        return "NO_VERDICT"
    if not first.get("connected") or first.get("db_error") or None in (first.get("enqueued") or []):
        return "NO_VERDICT"
    want = len(second.get("ns") or [])
    keys = second.get("enqueued") or []
    if not second.get("connected") or second.get("db_error") or len(keys) != want or None in keys:
        return "VIOLATED"  # Q1
    jobs = final_state.get("jobs") or {}
    for q in (first, second):
        present = {r["key"] for r in jobs.get(TABLE[q["tables"]], [])}
        if not set(q.get("enqueued") or []) <= present:
            return "VIOLATED"  # Q2
    return "HOLDS"
