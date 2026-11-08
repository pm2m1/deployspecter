"""ds-nbc/1 integration for chancy, shape "push_many with unique keys, beside one worker" (NONBLIND development).

**Each run, for one pinned revision archive** (R1 or R2 of the case spec):
1. A fresh schema, migrated by **that revision itself** (``nbc_setup``: Chancy's own migrate, then queue ``q_nbc`` at
   concurrency 2), isolated and corroborated.
2. One restricted container of that revision with three actors on their own sessions: a worker (``nbc_work``) and two
   producers, ``seed`` and ``repush``, each making one ``Chancy.push_many`` call (one transaction) of the trusted
   ``ds_job`` with ``unique_key`` ``u<n>``, in the workload's order.
3. Every statement is granted by the coordinator under the seeded explorer (the given seed); gates: both producers
   wait until the worker has completed a fetch (the window), and ``repush`` waits until the worker's fetch has
   returned at least one job. The explorer decides everything else. Corroborated against the server log.
4. The approved oracle (its sha256 checked) decides the verdict, only on a SOUND run.

The container uses the declared variant adapter ``chancy_adapter_nbc`` (``DS_CHANCY_ADAPTER``): ``chancy_adapter``'s
seam and worker, plus the pins of the case's two trees and the phase functions above. Its file is added to the
runner's payload at import of this module (``run_isolated.PAYLOAD``), as ``chancy-sv/1`` registers its trees.

**Workload (one):** ``{"seed": [n, ...], "repush": [n, ...]}``: ``seed`` 1..3 and ``repush`` 1..6 distinct ints 0..9
each. A workload names no SQL, code, gate, timing or schedule.

Each run also writes ``report.json`` (final state, actors' results, the oracle's detail, corroboration) in its
directory.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))

NAME = "chancy-pushmany/1"
SECRET = Path.home() / ".ds_mvp_pg_password"
ADAPTER = "chancy_adapter_nbc"
ADAPTER_PAYLOAD = "deployspecter/experimental/thirdparty/chancy_adapter_nbc.py"
ENV = {"DS_CHANCY_ADAPTER": ADAPTER}
EXPECT_SCHEMA = "7"


def validate(w: Any) -> list[str]:
    if not isinstance(w, dict) or set(w) != {"seed", "repush"}:
        return ['exactly {"seed": [...], "repush": [...]}']
    p = []
    for key, hi in (("seed", 3), ("repush", 6)):
        ns = w[key]
        if not isinstance(ns, list) or not 1 <= len(ns) <= hi:
            p.append(f"{key}: 1..{hi} items")
            continue
        if not all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 9 for n in ns) or \
                len(set(ns)) != len(ns):
            p.append(f"{key}: distinct ints 0..9")
    return p


def workloads(arm: str) -> list[dict[str, Any]]:
    """RA-DET: curated, frozen before the case's first live run (CASE.md gives the reasons)."""
    if arm == "RA-DET":
        return [{"seed": [0, 1], "repush": [1, 0]},
                {"seed": [1, 0], "repush": [0, 1]},
                {"seed": [0, 1], "repush": [1, 5, 6, 7, 8, 0]}]
    raise ValueError(f"{arm} isn't a deterministic arm of {NAME}")


WORKLOAD_JSON_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["seed", "repush"],
    "properties": {
        "seed": {"type": "array", "minItems": 1, "maxItems": 3,
                 "items": {"type": "integer", "minimum": 0, "maximum": 9}},
        "repush": {"type": "array", "minItems": 1, "maxItems": 6,
                   "items": {"type": "integer", "minimum": 0, "maximum": 9}}}}


def _payload() -> None:
    import run_isolated as ri
    if ADAPTER_PAYLOAD not in ri.PAYLOAD:
        ri.PAYLOAD.append(ADAPTER_PAYLOAD)


def prepare(archive: Path, sha256: str, workdir: Path) -> dict[str, Any]:
    import run_isolated as ri
    got = hashlib.sha256(archive.read_bytes()).hexdigest()
    if got != sha256:
        raise ValueError(f"{archive.name}: sha256 {got[:12]} isn't the case spec's {sha256[:12]}")
    _payload()
    workdir.mkdir(parents=True, exist_ok=True)
    name = f"dsnbc-{got[:16]}.tar"
    staged = workdir / name
    shutil.copyfile(archive, staged)
    build = Path(tempfile.mkdtemp(prefix="ds_nbc_build_"))
    ri.CHANCY_GIT[name] = (got, "ds-nbc/1 pinned", "")  # scoped to this build; the runner re-checks the hash
    try:
        image = ri.build_image(build, chancy_sdist=staged)
    finally:
        ri.CHANCY_GIT.pop(name, None)
        shutil.rmtree(build, ignore_errors=True)
    return {"image": image, "expect_schema": EXPECT_SCHEMA, "archive_sha256": got, "integration": NAME}


def spec(w: dict[str, Any]) -> dict[str, Any]:
    import run_coordinated as rc
    worker = {"actor": "worker", "fn": "nbc_work", "args": {"timeout_s": 60}}
    seed = {"actor": "seed", "fn": "nbc_push_many", "args": {"ns": list(w["seed"])}}
    repush = {"actor": "repush", "fn": "nbc_push_many", "args": {"ns": list(w["repush"])}}
    gates = rc._window(["seed", "repush"], ["worker"]) + [
        {"actor": "repush", "match": None, "until": [{"actor": "worker", "marker": "claim", "count": 1}]}]
    return {"processes": {"A": [worker, seed, repush]}, "gates": gates, "covers": list(w["seed"]),
            "consumer_of": {}}


def setup(target: Any, image: Any, out: Path) -> dict[str, Any]:
    import run_isolated as ri
    d = out / "0_setup"
    d.mkdir(parents=True)
    r = ri.run_smoke(target, variant="0_setup", schedule=[], out=d, image=image, mode="chancy-plan", fresh_schema=True,
                     keep_schema=True, extra_env={"DS_CHANCY_PLAN": json.dumps([{"actors": [
                         {"actor": "setup", "fn": "nbc_setup"}]}]), **ENV})
    (d / "report.json").write_text(json.dumps(r, indent=1, default=str))
    return r


def load_oracle(path: Path, sha256: str) -> Any:
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError("the oracle code isn't the approved one")
    s = importlib.util.spec_from_file_location(f"ds_nbc_oracle_{sha256[:12]}", path)
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(m)
    return m


def run(handle: dict[str, Any], workload: dict[str, Any], seed: int, out: Path, *, oracle_code: Path,
        oracle_sha256: str) -> dict[str, Any]:
    import run_coordinated as rc

    from deployspecter.experimental import harness as hz
    if p := validate(workload):
        return {"verdict": "REFUSED", "seed": seed, "corroboration": {"ok": False}, "reason": p}
    _payload()
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", SECRET.read_text().strip())
    out.mkdir(parents=True)
    pre = setup(target, handle["image"], out)
    if not (pre.get("isolation_ok") and (pre.get("corroboration") or {}).get("ok")) or \
            not isinstance((pre.get("raw_results") or {}).get("setup"), dict):
        return {"verdict": "REFUSED", "seed": seed, "corroboration": pre.get("corroboration"),
                "reason": "the setup wasn't isolated and corroborated"}
    s = {**spec(workload), "expect_schema": handle["expect_schema"]}
    sim = rc.simultaneous(target, {"A": handle["image"], "env": {"A": dict(ENV)}}, s,
                          {"kind": "seeded", "seed": seed}, out, label=NAME)
    c = sim.get("corroboration") or {}
    corr = {k: c.get(k) for k in ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked")}
    report: dict[str, Any] = {"integration": NAME, "nonblind": True, "seed": seed, "workload": workload,
                              "archive_sha256": handle["archive_sha256"], "image": list(handle["image"]),
                              "verdict": sim["verdict"], "refusal_reasons": sim.get("refusal_reasons"),
                              "corroboration": corr, "corroboration_problems": c.get("problems"),
                              "exceptions": sim.get("exceptions"), "versions": sim.get("versions"),
                              "final_state": sim.get("final_state"), "raw_results": sim.get("raw_results"),
                              "teardown": sim.get("teardown"), "summary": rc.summary({**sim, "spec": s})}
    result: dict[str, Any]
    if sim["verdict"] != "SOUND":
        result = {"verdict": sim["verdict"], "seed": seed, "corroboration": corr,
                  "reason": sim.get("refusal_reasons")}
    else:
        o = load_oracle(oracle_code, oracle_sha256)
        verdict = o.check(sim["final_state"], sim["raw_results"])
        report["oracle"] = verdict
        if hasattr(o, "evaluate"):
            report["oracle_detail"] = o.evaluate(sim["final_state"], sim["raw_results"])
        result = {"verdict": "SOUND", "oracle": verdict, "seed": seed, "corroboration": corr}
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    return result


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
        "workload": "A workload gives two lists of job numbers n (ints 0..9): seed (1..3, distinct) and repush "
                    "(1..6, distinct). Job n always carries unique_key 'u<n>' and the same handler, which receives "
                    "n and returns n * 2 (it never raises); max_attempts is 1.",
        "seed": {"args": {"seed": "the list, in order"},
                 "semantics": "Once the worker is polling, one producer makes one Chancy.push_many(jobs) call with "
                              "the seed jobs in list order, on queue q_nbc. Chancy.push_many: 'Push multiple jobs "
                              "onto the queue. This method will push multiple jobs onto the queue, making them "
                              "available for processing by workers. Jobs are committed in batches, with the size "
                              "of each batch controlled by the batch_size parameter. Each batch is committed "
                              "within its own transaction to limit peak memory usage.' (here: one batch). It "
                              "returns references to the jobs, in the order given."},
        "repush": {"args": {"repush": "the list, in order"},
                   "semantics": "A second producer, on its own database session, makes one Chancy.push_many(jobs) "
                                "call with the repush jobs in list order, once the worker has fetched at least one "
                                "job to run. A number that also appears in seed names a job with the same "
                                "unique_key. Job.unique_key: 'An optional, globally unique identifier for this "
                                "job. If provided, only 1 copy of a job with this key will be allowed to run or be "
                                "scheduled at a time.'"},
        "worker": {"args": {},
                   "semantics": "One Chancy Worker polls q_nbc (concurrency 2, in-process async executor) for the "
                                "whole run, and stops once the handler of every seed job has returned, or after a "
                                "time limit. Worker._maintain_updates: 'We maintain a queue of updates to job "
                                "instances, and process them in batches to significantly reduce the number of "
                                "overall transactions that need to be made.' The worker sends its queued updates "
                                "about once a second. Job states: pending, running, failed, retrying, succeeded."},
        "environment": "Notifications are disabled (the worker polls). A deterministic explorer chooses the order "
                       "of every database statement; a workload never names SQL, code, timing or schedules."},
    "schema_ddl": R1_SCHEMA_DDL,
    "schema_note": "The DDL the release's own migrations v1..v7 apply on a fresh database, prefix chancy_ (v7's "
                   "rename only runs when an unprefixed legacy constraint exists). Migrations also keep a "
                   "chancy_schema_version table.",
}
