"""EXPERIMENTAL, NONBLIND (ds-nbc/1): the two workload shapes the saq integrations use, with their frozen workload
schemas, validators, coordinated run specs and operation registries. Not an integration itself.

* **burst**: a producer enqueues jobs on a queue; then one saq burst worker drains that queue and stops by itself.
* **two_queues**: a first queue starts and runs a worker; a second queue, configured with its own or the same table
  set, starts while the first is running and enqueues jobs.

Nothing here names a fix, a test, an oracle or a result.
"""

from __future__ import annotations

from typing import Any

# The application schema the pinned trees' own ``connect``/``init_db`` creates for saq's default table names, as
# their ``saq/queue/postgres_migrations.py`` (migrations 1-3) and ``PostgresQueue.init_db`` write it. The table names
# are the ``PostgresQueue`` constructor's documented ``jobs_table``/``stats_table``/``versions_table`` arguments.
SCHEMA_DDL = """\
CREATE TABLE IF NOT EXISTS saq_versions (version INT);
-- migration 1
CREATE TABLE IF NOT EXISTS saq_jobs (
    key TEXT PRIMARY KEY,
    lock_key SERIAL NOT NULL,
    job BYTEA NOT NULL,
    queue TEXT NOT NULL,
    status TEXT NOT NULL,
    priority SMALLINT NOT NULL DEFAULT 0,
    group_key TEXT,
    scheduled BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
    expire_at BIGINT
);
CREATE INDEX IF NOT EXISTS saq_jobs_dequeue_idx ON saq_jobs (status, queue, scheduled);
CREATE TABLE IF NOT EXISTS saq_stats (worker_id TEXT PRIMARY KEY, stats JSONB, expire_at BIGINT);
-- migration 2
ALTER TABLE saq_stats ADD COLUMN IF NOT EXISTS metadata JSONB;
ALTER TABLE saq_stats ADD COLUMN IF NOT EXISTS queue_key TEXT;
CREATE INDEX IF NOT EXISTS saq_stats_expire_at_idx ON saq_stats (expire_at);
CREATE INDEX IF NOT EXISTS saq_stats_queue_key_idx ON saq_stats (queue_key);
-- migration 3
CREATE INDEX IF NOT EXISTS saq_jobs_status_queue_group_key_idx ON saq_jobs (status, queue, group_key);
CREATE INDEX IF NOT EXISTS saq_jobs_status_queue_priority_scheduled_idx
    ON saq_jobs (status, queue, priority, scheduled);
DROP INDEX saq_jobs_dequeue_idx;
-- the version row
DELETE FROM saq_versions; INSERT INTO saq_versions (version) VALUES (3);
"""

TASK = ("ds_task(ctx, *, n, tables): a trusted, deterministic task. It records its execution and returns n * 2. Its "
        "job key is '<tables>-<n>'.")
STATUS_DOC = ("saq.job.Status: new, queued, active, aborting, aborted, failed, complete. TERMINAL_STATUSES = "
              "complete, failed, aborted.")

# ------------------------------------------------------------------------------------------------------ burst

DEQUEUE_TIMEOUTS = [0.25, 0.5, 1.0, 2.0, 3.0]
POLL_INTERVALS = [0.1, 0.25, 0.5, 1.0]
SCHEDULE_TIMERS = [1, 5, 3600]

BURST_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["jobs", "concurrency", "max_burst_jobs", "dequeue_timeout_s", "poll_interval_s", "schedule_s"],
    "properties": {
        "jobs": {"type": "integer", "minimum": 1, "maximum": 4,
                 "description": "how many jobs the producer enqueues before the worker starts"},
        "concurrency": {"type": "integer", "minimum": 1, "maximum": 2},
        "max_burst_jobs": {"type": "integer", "minimum": 0, "maximum": 4, "description": "0 means not set"},
        "dequeue_timeout_s": {"type": "number", "enum": DEQUEUE_TIMEOUTS},
        "poll_interval_s": {"type": "number", "enum": POLL_INTERVALS},
        "schedule_s": {"type": "integer", "enum": SCHEDULE_TIMERS}}}


def _int(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _num_in(v: Any, allowed: list[Any]) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v in allowed


def burst_validate(w: Any) -> list[str]:
    req = BURST_SCHEMA["required"]
    if not isinstance(w, dict) or set(w) != set(req):
        return [f"exactly {sorted(req)}"]
    p = []
    if not _int(w["jobs"], 1, 4):
        p.append("jobs: an int in 1..4")
    if not _int(w["concurrency"], 1, 2):
        p.append("concurrency: an int in 1..2")
    if not _int(w["max_burst_jobs"], 0, 4):
        p.append("max_burst_jobs: an int in 0..4 (0: not set)")
    if not _num_in(w["dequeue_timeout_s"], DEQUEUE_TIMEOUTS):
        p.append(f"dequeue_timeout_s: one of {DEQUEUE_TIMEOUTS}")
    if not _num_in(w["poll_interval_s"], POLL_INTERVALS):
        p.append(f"poll_interval_s: one of {POLL_INTERVALS}")
    if not (_int(w["schedule_s"], 1, 3600) and w["schedule_s"] in SCHEDULE_TIMERS):
        p.append(f"schedule_s: one of {SCHEDULE_TIMERS}")
    return p


def burst_spec(w: dict[str, Any]) -> dict[str, Any]:
    ns = list(range(w["jobs"]))
    producer = {"actor": "producer", "fn": "enqueue", "args": {"tables": "default", "ns": ns}}
    worker = {"actor": "worker", "fn": "burst_work", "args": {
        "tables": "default", "concurrency": w["concurrency"], "max_burst_jobs": w["max_burst_jobs"] or None,
        "dequeue_timeout_s": float(w["dequeue_timeout_s"]), "poll_interval_s": float(w["poll_interval_s"]),
        "schedule_s": w["schedule_s"], "timeout_s": 90}}
    # the burst worker starts on a queue that already holds the jobs: no worker statement before the n-th enqueue
    gates = [{"actor": "worker", "match": None, "until": [{"actor": "producer", "marker": "insert",
                                                           "count": len(ns)}]}]
    return {"processes": {"A": [producer, worker]}, "gates": gates, "covers": ["producer", "worker"],
            "consumer_of": {}}


BURST_REGISTRY = {
    "shape": "One container, two actors, each with its own database connection. The producer runs first; the worker "
             "starts once the producer's jobs are enqueued. A separate deterministic explorer orders every database "
             "statement.",
    "operations": {
        "producer.enqueue": "PostgresQueue(pool, name='ds_q') (saq's default tables), connect(), then "
                            "Queue.enqueue('ds_task', key='default-<n>', n=n) for n in 0..jobs-1. Queue.enqueue: "
                            "'Enqueue a job by instance or string.' 'Returns: If the job has already been enqueued, "
                            "this returns None, else Job'.",
        "worker.burst_work": "PostgresQueue(pool, name='ds_q'), connect(), then saq.Worker(queue, functions=[ds_task],"
                             " concurrency, burst=True, max_burst_jobs, dequeue_timeout, poll_interval, timers="
                             "{'schedule': schedule_s, 'worker_info': 3600, 'sweep': 3600, 'abort': 3600}).start(), "
                             "which returns when the worker stops."},
    "documented_semantics": {
        "Worker": {"concurrency": "number of jobs to process concurrently",
                   "timers.schedule": "how often we poll to schedule jobs",
                   "dequeue_timeout": "how long it will wait to dequeue",
                   "burst": "whether to stop the worker once all jobs have been processed",
                   "max_burst_jobs": "the maximum number of jobs to process in burst mode",
                   "poll_interval": "If > 0.0, dequeue will use polling instead of listen/notify to trigger "
                                    "dequeues. This only affects Postgres. (default 0.0)"},
        "Status": STATUS_DOC, "task": TASK},
    "argument_ranges": {"jobs": "1..4", "concurrency": "1..2", "max_burst_jobs": "0..4 (0: not set)",
                        "dequeue_timeout_s": DEQUEUE_TIMEOUTS, "poll_interval_s": POLL_INTERVALS,
                        "schedule_s": SCHEDULE_TIMERS},
    "constants": "poll_interval > 0 always (the harness can't instrument a LISTEN connection); one database connection "
             "per actor; job timeout and heartbeat are saq's defaults."}

# ------------------------------------------------------------------------------------------------- two queues

TABLE_SETS = ["default", "alt"]
TWO_POLLS = [0.25, 0.5, 1.0]
TWO_QUEUES_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["first", "second", "poll_interval_s"],
    "properties": {
        "first": {"type": "object", "additionalProperties": False, "required": ["tables", "jobs"],
                  "properties": {"tables": {"type": "string", "enum": TABLE_SETS},
                                 "jobs": {"type": "integer", "minimum": 0, "maximum": 2}}},
        "second": {"type": "object", "additionalProperties": False, "required": ["tables", "jobs"],
                   "properties": {"tables": {"type": "string", "enum": TABLE_SETS},
                                  "jobs": {"type": "integer", "minimum": 1, "maximum": 3}}},
        "poll_interval_s": {"type": "number", "enum": TWO_POLLS}}}


def two_queues_validate(w: Any) -> list[str]:
    if not isinstance(w, dict) or set(w) != {"first", "second", "poll_interval_s"}:
        return ["exactly first, second and poll_interval_s"]
    p = []
    for name, lo, hi in (("first", 0, 2), ("second", 1, 3)):
        q = w[name]
        if not isinstance(q, dict) or set(q) != {"tables", "jobs"}:
            p.append(f"{name}: exactly tables and jobs")
            continue
        if q["tables"] not in TABLE_SETS:
            p.append(f"{name}.tables: one of {TABLE_SETS}")
        if not _int(q["jobs"], lo, hi):
            p.append(f"{name}.jobs: an int in {lo}..{hi}")
    if not _num_in(w["poll_interval_s"], TWO_POLLS):
        p.append(f"poll_interval_s: one of {TWO_POLLS}")
    return p


def two_queues_spec(w: dict[str, Any]) -> dict[str, Any]:
    first = {"actor": "first", "fn": "run_worker", "args": {
        "tables": w["first"]["tables"], "ns": list(range(w["first"]["jobs"])),
        "poll_interval_s": float(w["poll_interval_s"]), "timeout_s": 90}}
    second = {"actor": "second", "fn": "enqueue", "args": {
        "tables": w["second"]["tables"], "ns": list(range(10, 10 + w["second"]["jobs"]))}}
    # the second queue starts while the first is running: none of its statements before the first worker's poll
    gates = [{"actor": "second", "match": None, "until": [{"actor": "first", "marker": "fetch", "count": 1}]}]
    return {"processes": {"A": [first, second]}, "gates": gates, "covers": ["second"], "consumer_of": {}}


TWO_QUEUES_REGISTRY = {
    "shape": "One container, two actors, each with its own database connection (as two processes would have). The "
             "first queue starts and runs a worker; the second queue starts once the first worker is polling, and "
             "enqueues "
             "jobs. The first worker is stopped after the second queue's actor returns. A separate deterministic "
             "explorer orders every database statement.",
    "table_sets": {"default": {"name": "ds_q", "jobs_table": "saq_jobs", "stats_table": "saq_stats",
                               "versions_table": "saq_versions"},
                   "alt": {"name": "ds_q_alt", "jobs_table": "saq_alt_jobs", "stats_table": "saq_alt_stats",
                           "versions_table": "saq_alt_versions"}},
    "operations": {
        "first.run_worker": "PostgresQueue(pool, **table_set) connect(); Queue.enqueue('ds_task', key='<tables>-<n>',"
                            " n=n) for n in 0..jobs-1; then saq.Worker(queue, functions=[ds_task], concurrency=1, "
                            "poll_interval).start() until the run stops it.",
        "second.enqueue": "PostgresQueue(pool, **table_set) connect(); Queue.enqueue('ds_task', key='<tables>-<n>', "
                          "n=n) for n in 10..10+jobs-1."},
    "documented_semantics": {
        "PostgresQueue": {"versions_table": "name of the Postgres table SAQ will use to maintain migrations",
                          "jobs_table": "name of the Postgres table SAQ will write jobs to (default \"saq_jobs\")",
                          "stats_table": "name of the Postgres table SAQ will write stats to (default \"saq_stats\")"},
        "Queue.enqueue": "'Enqueue a job by instance or string.' 'Returns: If the job has already been enqueued, this "
                         "returns None, else Job'.",
        "Status": STATUS_DOC, "task": TASK},
    "argument_ranges": {"first.tables": TABLE_SETS, "first.jobs": "0..2", "second.tables": TABLE_SETS,
                        "second.jobs": "1..3", "poll_interval_s": TWO_POLLS},
    "constants": "both queues share one schema; every other PostgresQueue argument is its default; poll_interval > 0."}

# ------------------------------------------------------------------------- two queues, v2 (added after a live run)
# The same deployment as ``two_queues``, but the second queue only starts (its ``connect``) and issues no statement of
# its own afterwards, so the run can't depend on corroborating a statement the server rejects while parsing it. The
# property is decided on the state its ``connect`` left. ``two_queues`` above is unchanged (earlier attempts use it).

TWO_QUEUES_V2_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["first", "second", "poll_interval_s"],
    "properties": {
        "first": {"type": "object", "additionalProperties": False, "required": ["tables", "jobs"],
                  "properties": {"tables": {"type": "string", "enum": TABLE_SETS},
                                 "jobs": {"type": "integer", "minimum": 0, "maximum": 2}}},
        "second": {"type": "object", "additionalProperties": False, "required": ["tables"],
                   "properties": {"tables": {"type": "string", "enum": TABLE_SETS}}},
        "poll_interval_s": {"type": "number", "enum": TWO_POLLS}}}


def two_queues_v2_validate(w: Any) -> list[str]:
    if not isinstance(w, dict) or set(w) != {"first", "second", "poll_interval_s"}:
        return ["exactly first, second and poll_interval_s"]
    p = []
    f, s = w["first"], w["second"]
    if not isinstance(f, dict) or set(f) != {"tables", "jobs"}:
        p.append("first: exactly tables and jobs")
    else:
        if f["tables"] not in TABLE_SETS:
            p.append(f"first.tables: one of {TABLE_SETS}")
        if not _int(f["jobs"], 0, 2):
            p.append("first.jobs: an int in 0..2")
    if not isinstance(s, dict) or set(s) != {"tables"}:
        p.append("second: exactly tables")
    elif s["tables"] not in TABLE_SETS:
        p.append(f"second.tables: one of {TABLE_SETS}")
    if not _num_in(w["poll_interval_s"], TWO_POLLS):
        p.append(f"poll_interval_s: one of {TWO_POLLS}")
    return p


def two_queues_v2_spec(w: dict[str, Any]) -> dict[str, Any]:
    s = two_queues_spec({**w, "second": {"tables": w["second"]["tables"], "jobs": 1}})
    s["processes"]["A"][1]["args"]["ns"] = []  # the second queue only connects
    return s


TWO_QUEUES_V2_REGISTRY = {
    **TWO_QUEUES_REGISTRY,
    "operations": {**TWO_QUEUES_REGISTRY["operations"],
                   "second.enqueue": "PostgresQueue(pool, **table_set) connect(), then disconnect(); nothing else."},
    "argument_ranges": {"first.tables": TABLE_SETS, "first.jobs": "0..2", "second.tables": TABLE_SETS,
                        "poll_interval_s": TWO_POLLS}}
