"""EXPERIMENTAL onboarding pilot: the app spec, the workload, and the deterministic validator (criterion S1).

**App spec** (JSON, written per app during onboarding; it may be proposed by Muse in condition M):

    {"app": "<name>", "version": "<v>", "sdist": "<file>", "sdist_sha256": "<64 hex>",
     "package": "<top-level package directory in the sdist>", "install_metadata": bool,
     "adapter": "<module under deployspecter.experimental.onboarding_pilot.adapters>",
     "schema": "onb_<name>", "seam": "connection" | "pool",
     "wheels": [{"name": ..., "version": ..., "filename": ..., "sha256": ...}],
     "tasks": {"<task name>": {"args": {"<arg>": "int"}}}}

**Workload** (JSON): ``{"jobs": [{"key": "<unique str>", "task": "<declared task>", "args": {...}}]}``, 1..8
jobs, each arg as its task declares it (only ``int``, 0..999, in onb-pilot/1).

``validate`` returns the problems; empty means valid. **S1** is the first moment a spec and a workload both validate
against the pins in the frozen pilot manifest. Nothing in a spec or a workload is executed or interpolated into SQL.
The adapter is an operator-written, reviewed Python module named by the spec, not by a proposal: the spec can only
choose among adapter modules that exist in the repository.
"""

from __future__ import annotations

import re
from typing import Any

CONTRACT = {
    "adapter_module_prefix": "deployspecter.experimental.onboarding_pilot.adapters.",
    "coroutines": {"install": "(conn) -> dict", "enqueue": "(conn, *, jobs) -> dict",
                   "work": "(conn, *, expect, timeout_s) -> dict"},
    "verify_pinned": "() -> dict: re-hash the library sources the adapter depends on",
    "recorder": "the trusted task function calls deployspecter.experimental.onboarding_pilot.recorder.handled(key)",
    "conn": "the harness's AsyncSteppedConnection; all of the library's database I/O must go through it",
}
NAME = re.compile(r"[a-z][a-z0-9_]{0,30}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SPEC_KEYS = {"app", "version", "sdist", "sdist_sha256", "package", "install_metadata", "adapter", "schema", "seam",
             "wheels", "tasks"}
MAX_JOBS = 8


def validate_spec(spec: Any, pins: dict[str, Any]) -> list[str]:
    """``pins``: the frozen manifest's entry for this app (sdist, sdist_sha256, version)."""
    if not isinstance(spec, dict) or set(spec) != SPEC_KEYS:
        return [f"spec: exactly the keys {sorted(SPEC_KEYS)}"]
    p = []
    for k in ("sdist", "sdist_sha256", "version"):
        if spec[k] != pins.get(k):
            p.append(f"{k} {spec[k]!r} isn't the pinned {pins.get(k)!r}")
    if not isinstance(spec["app"], str) or not NAME.match(spec["app"]):
        p.append("app: a lowercase identifier")
    if not isinstance(spec["package"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", spec["package"]):
        p.append("package: a Python package directory name")
    if not isinstance(spec["install_metadata"], bool):
        p.append("install_metadata: a bool")
    if not isinstance(spec["adapter"], str) or not spec["adapter"].startswith(CONTRACT["adapter_module_prefix"]) or \
            not NAME.match(spec["adapter"].removeprefix(CONTRACT["adapter_module_prefix"])):
        p.append(f"adapter: a module {CONTRACT['adapter_module_prefix']}<name>")
    if not isinstance(spec["schema"], str) or not re.fullmatch(r"onb_[a-z0-9_]{1,40}", spec["schema"]):
        p.append("schema: onb_<name>")
    if spec["seam"] not in ("connection", "pool"):
        p.append("seam: connection or pool")
    wheels = spec["wheels"]
    if not isinstance(wheels, list) or len(wheels) > 40:
        p.append("wheels: a list of at most 40")
    else:
        for i, w in enumerate(wheels):
            if not isinstance(w, dict) or set(w) != {"name", "version", "filename", "sha256"} or \
                    not all(isinstance(v, str) for v in w.values()) or not HEX64.match(w.get("sha256", "")) or \
                    not w.get("filename", "").endswith(".whl"):
                p.append(f"wheels[{i}]: name, version, filename (.whl), sha256 (64 hex)")
    tasks = spec["tasks"]
    if not isinstance(tasks, dict) or not 1 <= len(tasks) <= 4:
        p.append("tasks: 1..4 declared tasks")
    else:
        for t, d in tasks.items():
            if not isinstance(t, str) or not NAME.match(t) or not isinstance(d, dict) or set(d) != {"args"} or \
                    not isinstance(d["args"], dict) or not all(NAME.match(a) and v == "int"
                                                                for a, v in d["args"].items()):
                p.append(f"tasks.{t}: {{'args': {{name: 'int'}}}}")
    return p


def validate_workload(workload: Any, spec: dict[str, Any]) -> list[str]:
    if not isinstance(workload, dict) or set(workload) != {"jobs"} or not isinstance(workload["jobs"], list):
        return ['workload: exactly {"jobs": [...]}']
    jobs = workload["jobs"]
    if not 1 <= len(jobs) <= MAX_JOBS:
        return [f"jobs: 1..{MAX_JOBS}"]
    p, keys = [], set()
    for i, j in enumerate(jobs):
        if not isinstance(j, dict) or set(j) != {"key", "task", "args"}:
            p.append(f"jobs[{i}]: exactly key, task, args")
            continue
        if not isinstance(j["key"], str) or not NAME.match(j["key"]) or j["key"] in keys:
            p.append(f"jobs[{i}].key: a unique identifier")
        keys.add(j["key"])
        decl = spec["tasks"].get(j["task"]) if isinstance(j["task"], str) else None
        if decl is None:
            p.append(f"jobs[{i}].task {j['task']!r} isn't declared")
            continue
        if not isinstance(j["args"], dict) or set(j["args"]) != set(decl["args"]):
            p.append(f"jobs[{i}].args: exactly {sorted(decl['args'])}")
            continue
        for a, v in j["args"].items():
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 999:
                p.append(f"jobs[{i}].args.{a}: an int 0..999")
    return p


def validate(spec: Any, workload: Any, pins: dict[str, Any]) -> list[str]:
    p = validate_spec(spec, pins)
    return p or validate_workload(workload, spec)
