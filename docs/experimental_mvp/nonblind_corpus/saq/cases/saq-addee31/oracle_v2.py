"""Oracle v2 for the NONBLIND development case ``saq-addee31`` (ds-nbc/1), for integration
``saq-two-queues-addee31/2`` (attempt 3 onward; ERRATA E2). Developer-written.

Property, on a SOUND run only. A first queue connected, enqueued its jobs and runs a worker; then a second queue,
configured with the other table set (or the same one), connected and issued nothing else:
* Q0: the second queue's ``connect`` returned without a database error;
* Q1: after it returned, that queue's documented tables (its ``jobs_table``, ``stats_table`` and ``versions_table``)
  exist in the schema;
* Q2: each job the first queue enqueued is present in its jobs table.
NO_VERDICT when the precondition fails: the first queue didn't connect and enqueue without error.

Sources (the documented contract; nothing here is taken from the fix's code): ``saq/queue/postgres.py``
``PostgresQueue`` docstring (both trees): "versions_table: name of the Postgres table SAQ will use to maintain
migrations"; "jobs_table: name of the Postgres table SAQ will write jobs to"; "stats_table: name of the Postgres table
SAQ will write stats to". The maintainers' statement of the defect (the fix's commit message, read during screening):
"Any other queue running init_db meanwhile silently skipped migrations and then failed with UndefinedTable". v1's
Q1 observed that downstream failure; v2 checks the skipped migration itself, which needs no failing statement.
"""

from __future__ import annotations

from typing import Any

TABLES = {"default": ("saq_jobs", "saq_stats", "saq_versions"),
          "alt": ("saq_alt_jobs", "saq_alt_stats", "saq_alt_versions")}


def check(final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
    first, second = raw_results.get("first"), raw_results.get("second")
    if not isinstance(first, dict) or not isinstance(second, dict):
        return "NO_VERDICT"
    if not first.get("connected") or first.get("db_error") or None in (first.get("enqueued") or []):
        return "NO_VERDICT"
    if not second.get("connected") or second.get("db_error"):
        return "VIOLATED"  # Q0
    present = set(final_state.get("tables") or [])
    if not set(TABLES[second["tables"]]) <= present:
        return "VIOLATED"  # Q1
    jobs = {r["key"] for r in (final_state.get("jobs") or {}).get(TABLES[first["tables"]][0], [])}
    if not set(first.get("enqueued") or []) <= jobs:
        return "VIOLATED"  # Q2
    return "HOLDS"
