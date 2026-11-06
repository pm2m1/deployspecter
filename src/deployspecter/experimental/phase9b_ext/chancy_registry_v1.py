"""EXPERIMENTAL ds-p9x/1: the reviewed Chancy 0.26.0 workload registry (``chancy_registry_v1.json``) and its checks.

The registry supplies application semantics:
* operations and handlers;
* typed kwargs;
* goal states;
* jsonb field shapes;
* schema preconditions;
* the expected connection behaviour;
* the independent properties C1–C4.

It holds no defect labels and no oracle answers. A workload names only registered things. ``validate`` refuses
anything else, deterministically, and nothing from a workload is ever imported, executed or interpolated into SQL.

``verify_sources`` re-hashes the pinned sdist and the source files it names, plus the adapter file. A mismatch
makes the registry inapplicable.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import uuid
from pathlib import Path
from typing import Any

from deployspecter.experimental.state_construction.typed import TV

from . import jsonb_v1

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
REGISTRY = json.loads((HERE / "chancy_registry_v1.json").read_text())
NAMESPACE = uuid.UUID("6f1c2a4e-9d3b-5e7f-8a1b-2c3d4e5f6a7b")
EPOCH = "2026-01-01T00:00:00+00:00"
TABLE = REGISTRY["job_goal"]["entity"]


class RegistryRefused(ValueError):
    """Outside the registry, or the registry doesn't apply here."""


def registry_sha256() -> str:
    return hashlib.sha256((HERE / "chancy_registry_v1.json").read_bytes()).hexdigest()


def verify_sources(sdist: Path) -> dict[str, Any]:
    """Hashes of the sdist, the named sources inside it (read, not executed) and the adapter file, against the pins."""
    app = REGISTRY["application"]
    problems = []
    got = hashlib.sha256(sdist.read_bytes()).hexdigest()
    if got != app["sdist_sha256"]:
        problems.append(f"sdist sha256 {got[:12]} isn't the pinned {app['sdist_sha256'][:12]}")
    sources = {}
    with tarfile.open(sdist) as t:
        for rel, pin in app["sources_sha256"].items():
            f = t.extractfile(f"chancy-{app['version']}/{rel}")
            h = hashlib.sha256(f.read()).hexdigest() if f is not None else None
            sources[rel] = h
            if h != pin:
                problems.append(f"{rel}: {str(h)[:12]} isn't the pinned {pin[:12]}")
    adapter = hashlib.sha256((REPO / REGISTRY["adapter"]["file"]).read_bytes()).hexdigest()
    return {"ok": not problems, "problems": problems, "sdist_sha256": got, "sources_sha256": sources,
            "adapter_sha256": adapter, "registry_sha256": registry_sha256()}


def validate(workload: Any) -> list[str]:
    """Deterministic. ``{"registry": "chancy-0.26.0/registry-v1", "jobs": [{"handler", "kwargs", "queue", "state",
    "priority"}...]}``. Returns the problems (empty = valid)."""
    if not isinstance(workload, dict) or set(workload) != {"registry", "jobs"}:
        return ['shape: exactly {"registry": ..., "jobs": [...]}']
    if workload["registry"] != REGISTRY["registry"]:
        return [f"registry {workload['registry']!r} isn't {REGISTRY['registry']!r}"]
    jobs, out = workload["jobs"], []
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= REGISTRY["job_goal"]["max_jobs"]:
        return [f"jobs: a list of 1..{REGISTRY['job_goal']['max_jobs']}"]
    queues = REGISTRY["schema_preconditions"]["declared_queues"]
    seen = set()
    for i, j in enumerate(jobs):
        if not isinstance(j, dict) or set(j) != {"handler", "kwargs", "queue", "state", "priority"}:
            out.append(f"jobs[{i}]: exactly handler, kwargs, queue, state, priority")
            continue
        h = REGISTRY["handlers"].get(j["handler"]) if isinstance(j["handler"], str) else None
        if h is None:
            out.append(f"jobs[{i}].handler {j['handler']!r} isn't registered")
            continue
        kw = j["kwargs"]
        if not isinstance(kw, dict) or set(kw) != set(h["kwargs"]):
            out.append(f"jobs[{i}].kwargs: exactly {sorted(h['kwargs'])}")
        else:
            for k, spec in h["kwargs"].items():
                v = kw[k]
                if spec["type"] == "int" and (not isinstance(v, int) or isinstance(v, bool) or
                                              not spec["min"] <= v <= spec["max"]):
                    out.append(f"jobs[{i}].kwargs.{k}: an int in {spec['min']}..{spec['max']}")
        if j["queue"] not in queues:
            out.append(f"jobs[{i}].queue {j['queue']!r} isn't a declared queue {queues} (no such parent)")
        if j["state"] not in REGISTRY["job_goal"]["goal_states"]:
            out.append(f"jobs[{i}].state {j['state']!r} isn't a goal state {REGISTRY['job_goal']['goal_states']}")
        if not isinstance(j["priority"], int) or isinstance(j["priority"], bool) or not 0 <= j["priority"] <= 100:
            out.append(f"jobs[{i}].priority: an int in 0..100")
        n = kw.get("n") if isinstance(kw, dict) else None
        if n in seen:
            out.append(f"jobs[{i}]: duplicate job n={n}")
        seen.add(n)
    if not out and not any(j["state"] == "pending" for j in jobs):
        out.append("at least one pending job")
    return out


def job_id(n: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"ds_sc:chancy:{n}"))


def rows(workload: dict[str, Any]) -> list[tuple[str, dict[str, TV]]]:
    """Deterministic candidate rows for a valid workload: every column explicit (no clock, no random default)."""
    out = []
    for j in workload["jobs"]:
        finished = j["state"] == "succeeded"
        kwargs = jsonb_v1.accept(json.dumps(j["kwargs"]))
        out.append((TABLE, {
            "id": TV("uuid", job_id(j["kwargs"]["n"])), "queue": TV("str", j["queue"]),
            "func": TV("str", j["handler"]), "kwargs": TV("jsonb", kwargs), "limits": TV("jsonb", "[]"),
            "meta": TV("jsonb", "{}"), "state": TV("str", j["state"]), "priority": TV("int", j["priority"]),
            "attempts": TV("int", 1 if finished else 0), "max_attempts": TV("int", 1),
            "taken_by": TV("null", None), "created_at": TV("timestamptz", EPOCH),
            "started_at": TV("timestamptz", EPOCH) if finished else TV("null", None),
            "completed_at": TV("timestamptz", EPOCH) if finished else TV("null", None),
            "scheduled_at": TV("timestamptz", EPOCH), "unique_key": TV("null", None), "errors": TV("jsonb", "[]"),
            "claim_id": TV("null", None)}))
    return out


def preconditions(conn: Any, schema: str) -> list[str]:
    """The registry's schema preconditions on the live target, before construction."""
    p = REGISTRY["schema_preconditions"]
    out = []
    cols = dict(conn.execute("SELECT column_name::text, data_type::text FROM information_schema.columns WHERE "
                             "table_schema = %s AND table_name = %s", (schema, TABLE)).fetchall())
    if cols != p["chancy_jobs_columns"]:
        out.append(f"{TABLE} columns aren't the registered Chancy 0.26.0 v8 ones")
    sv = conn.execute(f'SELECT version_of::text, version FROM "{schema}".chancy_schema_version').fetchall() \
        if conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.chancy_schema_version",)).fetchone()[0] \
        else []
    if [list(r) for r in sv] != [[p["chancy_schema_version"]["version_of"], p["chancy_schema_version"]["version"]]]:
        out.append(f"chancy_schema_version is {sv}, not v8")
    declared = sorted(r[0] for r in conn.execute(f'SELECT name::text FROM "{schema}".chancy_queues').fetchall()) \
        if conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.chancy_queues",)).fetchone()[0] else []
    if declared != p["declared_queues"]:
        out.append(f"declared queues are {declared}, the registry needs {p['declared_queues']}")
    return out


def to_direct_workload(workload: dict[str, Any]) -> dict[str, Any]:
    """The same goals in the direct constructor's format, for the comparison run (defaults: priority 0, ds_q)."""
    return {"jobs": [{"n": j["kwargs"]["n"], "state": j["state"]} for j in workload["jobs"]]}
