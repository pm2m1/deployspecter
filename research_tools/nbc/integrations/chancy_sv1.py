"""ds-nbc/1 integration for chancy, shape "push, cancel, one worker" (NONBLIND development).

A thin wrapper. It re-exports the reviewed, hash-pinned integration ``chancy-sv/1``
(``research_tools/confirmatory/integrations/chancy_sv.py``, unchanged; its sha256 is checked at import) and adds
only what ds-nbc/1 needs on top: ``WORKLOAD_JSON_SCHEMA`` (one workload, strict structured output) and
``MUSE_CONTEXT`` (the operation registry and the R1 schema; no fix, test, oracle or result).

``validate``, ``workloads``, ``prepare`` and ``run`` are ``chancy-sv/1``'s own:
* each run migrates a fresh schema with that revision itself, runs one restricted container (a worker and a
  producer), grants every statement under the seeded explorer with the given seed, corroborates against the server
  log, and applies the approved oracle only to a SOUND run;
* ``workloads("RA-DET")`` is ``chancy-sv/1``'s frozen grid of 3 workloads; any other arm but RA-DET-C raises
  ``ValueError`` (ds-nbc/1 runs RA-DET only).
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "research_tools" / "confirmatory" / "integrations" / "chancy_sv.py"
BASE_SHA256 = "338580d2e9f08f006ef75c30c060738342ad6a165c1183a57d01c8a2b5dfab76"
NAME = "chancy-sv/1 (ds-nbc/1 wrapper chancy_sv1)"

if hashlib.sha256(BASE.read_bytes()).hexdigest() != BASE_SHA256:
    raise ImportError("research_tools/confirmatory/integrations/chancy_sv.py isn't the pinned chancy-sv/1")
_spec = importlib.util.spec_from_file_location("ds_nbc_chancy_sv_base", BASE)
_base = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_base)

validate = _base.validate
prepare = _base.prepare
run = _base.run
schema_version = _base.schema_version


def workloads(arm: str) -> list[dict[str, Any]]:
    if arm != "RA-DET":
        raise ValueError(f"{arm}: ds-nbc/1 runs only RA-DET deterministically")
    return _base.workloads(arm)


# One workload, as chancy-sv/1's validate() accepts it. Strict: every object closes its properties and requires all.
WORKLOAD_JSON_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["jobs", "max_attempts"],
    "properties": {
        "jobs": {"type": "array", "minItems": 1, "maxItems": 2,
                 "items": {"type": "object", "additionalProperties": False, "required": ["n", "cancel"],
                           "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 99},
                                          "cancel": {"type": "boolean"}}}},
        "max_attempts": {"type": "integer", "enum": [1, 2]}}}

# The R1 schema: research_tools/nbc/chancy_schema_ddl.py over the pinned R1 archive (its output, verbatim).
R1_SCHEMA_DDL = '''-- migration v1
CREATE TABLE "chancy_jobs" (
    id UUID PRIMARY KEY,
    queue TEXT NOT NULL,
    func TEXT NOT NULL,
    kwargs JSON NOT NULL DEFAULT '{}',
    limits JSON NOT NULL DEFAULT '[]',
    meta JSON NOT NULL DEFAULT '{}',
    state VARCHAR (25) NOT NULL DEFAULT 'pending',
    priority INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 1,
    taken_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unique_key TEXT,
    errors JSON NOT NULL DEFAULT '[]'
);

-- migration v1
CREATE UNIQUE INDEX "chancy_jobs_unique_key"
ON "chancy_jobs" (unique_key)
WHERE
    unique_key IS NOT NULL
        AND state NOT IN ('succeeded', 'failed');

-- migration v1
CREATE UNLOGGED TABLE "chancy_leader" (
    id BIGSERIAL PRIMARY KEY,
    worker_id TEXT NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE "chancy_leader"
    ADD CONSTRAINT "chancy_leader_worker_id_unique" UNIQUE
        (worker_id);

-- migration v1
CREATE UNLOGGED TABLE "chancy_workers" (
    -- The UUID of the worker.
    worker_id TEXT NOT NULL PRIMARY KEY,
    -- The tags associated with this worker.
    tags TEXT[],
    -- The queues this worker is currently processing.
    queues TEXT[],
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- migration v1
CREATE UNLOGGED TABLE "chancy_queue_rate_limits" (
    queue VARCHAR(255) PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
);

-- migration v1
CREATE TABLE "chancy_queues" (
    -- A unique name for the queue.
    name TEXT PRIMARY KEY,
    -- The state of the queue. This can be used to pause
    -- processing of jobs in the queue.
    state TEXT NOT NULL DEFAULT 'active',
    -- The number of jobs that can be processed concurrently
    -- per worker.
    concurrency INTEGER DEFAULT 1,
    -- Only worker's matching these tags will be able to
    -- process jobs from this queue, or None for all workers.
    tags TEXT[],
    -- The executor to use for processing jobs in this queue.
    executor TEXT NOT NULL DEFAULT
        'chancy.executors.process.ProcessExecutor',
    -- Options to pass to the executor.
    executor_options JSON,
    -- The interval at which the queue should be polled for
    -- jobs.
    polling_interval INTEGER DEFAULT 1,
    -- The time at which the queue was first declared.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- The number of jobs that can be processed per period.
    rate_limit INTEGER,
    -- The period of time over which the rate limit applies.
    rate_limit_window INTEGER
);

-- migration v2
CREATE INDEX IF NOT EXISTS "chancy_idx_jobs_queue_state"
ON "chancy_jobs" (queue, state);

-- migration v2
CREATE INDEX IF NOT EXISTS "chancy_idx_jobs_fetch_prune" ON "chancy_jobs"
(queue, state, attempts, scheduled_at, priority DESC, id DESC)
WHERE state IN ('pending', 'retrying');

-- migration v3
ALTER TABLE "chancy_jobs"
ALTER COLUMN max_attempts SET NOT NULL,
ALTER COLUMN max_attempts SET DEFAULT 1;

-- migration v4
ALTER TABLE "chancy_queues"
ADD COLUMN resume_at TIMESTAMP WITH TIME ZONE;

-- migration v5
ALTER TABLE "chancy_jobs"
ALTER COLUMN kwargs TYPE JSONB USING kwargs::JSONB,
ALTER COLUMN limits TYPE JSONB USING limits::JSONB,
ALTER COLUMN meta TYPE JSONB USING meta::JSONB,
ALTER COLUMN errors TYPE JSONB USING errors::JSONB;

-- migration v5
ALTER TABLE "chancy_queues"
ALTER COLUMN executor_options TYPE JSONB USING
CASE WHEN executor_options IS NULL THEN NULL
ELSE executor_options::JSONB END;

-- migration v6
ALTER TABLE "chancy_queues"
ADD COLUMN IF NOT EXISTS eager_polling BOOLEAN NOT NULL DEFAULT FALSE;

-- migration v7
ALTER TABLE "chancy_leader" RENAME CONSTRAINT "leader_worker_id_unique" TO "chancy_leader_worker_id_unique";
'''

MUSE_CONTEXT: dict[str, Any] = {
    "application": "chancy (a PostgreSQL-backed job queue, psycopg 3, asyncio), one release",
    "operations": {
        "workload": "A workload lists 1 or 2 jobs, each with a distinct n and a cancel flag, and one max_attempts "
                    "value shared by all its jobs.",
        "push": {"args": {"n": "integer 0..99, distinct within the workload",
                          "max_attempts": "1 or 2 (the workload's value)"},
                 "semantics": "The producer pushes each job, in list order, with Chancy.push(job), a job whose "
                              "handler takes n and returns n * 2 without failing, on queue q_shared. "
                              "Chancy.push: 'Push a job onto the queue. This method will push a job onto the "
                              "queue, making it available for processing by workers. A Reference object is "
                              "returned that can be used to track the progress of the job and retrieve the result "
                              "when it is complete.' Job.max_attempts: 'The maximum number of times this job can "
                              "be attempted before it is considered failed.'"},
        "cancel_job": {"args": {"cancel": "boolean per job"},
                       "semantics": "After all pushes, the producer calls Chancy.cancel_job(ref) for each job with "
                                    "cancel=true, in list order, once the worker has fetched at least one job to run. "
                                    "Chancy.cancel_job: 'Cancel a job by reference. This will attempt to cancel a "
                                    "job that is currently running, if it is possible to do so. Notifications must "
                                    "be enabled for this to work.'"},
        "worker": {"args": {},
                   "semantics": "One Chancy Worker polls q_shared (concurrency 1, in-process async executor) for "
                                "the whole run, and stops once the handler of every pushed job has returned, or "
                                "after a time limit. "
                                "Job states: pending, running, failed, retrying, succeeded."},
        "environment": "Notifications are disabled (the worker polls). A deterministic explorer chooses the order "
                       "of every database statement; a workload never names SQL, code, timing or schedules."},
    "schema_ddl": R1_SCHEMA_DDL,
    "schema_note": "The DDL the release's own migrations v1..v7 apply on a fresh database, prefix chancy_ (v7's "
                   "rename only runs when an unprefixed legacy constraint exists). Migrations also keep a "
                   "chancy_schema_version table.",
}
