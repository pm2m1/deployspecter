"""EXPERIMENTAL reviewed integration ``chancy-sv/1``: Chancy under its documented single-version contract.

**Each run, for one opaque revision archive** (R1 or R2 of a sealed case):
1. A fresh schema, migrated by **that revision itself** (``co_setup``: Chancy's own migrate and declare), with
   isolation and corroboration checked.
2. One restricted container of that revision: a worker (``co_work``) and a producer (``co_push``) that pushes the
   workload's jobs with Chancy's public ``push`` and cancels the listed ones with its public ``cancel_job``.
3. Every statement is granted by the coordinator under the seeded explorer (the given seed) and corroborated
   against the server log.
4. The **approved oracle** decides the verdict, but only on a SOUND run. It must have the approved sha256, and
   exposes ``check(final_state, raw_results) -> "HOLDS" | "VIOLATED" | "NO_VERDICT"``.

**Workload schema (frozen):** ``{"jobs": [{"n": 0..99, "cancel": bool}, ...], "max_attempts": 1 | 2}``, 1 or 2
jobs, distinct ``n``. A workload can name no SQL, Python, gate or schedule.

**Archives.** The archive must have the sha256 the frozen case manifest gives. Only then is it placed, under a
unique name, in the runner's pinned-tree catalog, for the build alone. The runner still re-checks the hash. The
expected schema version is read from the archive's own migrations.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))

NAME = "chancy-sv/1"
SECRET = Path.home() / ".ds_mvp_pg_password"


def validate(w: Any) -> list[str]:
    if not isinstance(w, dict) or set(w) != {"jobs", "max_attempts"}:
        return ['exactly {"jobs": [...], "max_attempts": 1|2}']
    p = []
    if w["max_attempts"] not in (1, 2) or isinstance(w["max_attempts"], bool):
        p.append("max_attempts: 1 or 2")
    jobs = w["jobs"]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 2:
        return p + ["jobs: 1 or 2"]
    seen = set()
    for i, j in enumerate(jobs):
        if not isinstance(j, dict) or set(j) != {"n", "cancel"}:
            p.append(f"jobs[{i}]: exactly n and cancel")
            continue
        if not isinstance(j["n"], int) or isinstance(j["n"], bool) or not 0 <= j["n"] <= 99 or j["n"] in seen:
            p.append(f"jobs[{i}].n: a distinct int in 0..99")
        seen.add(j["n"])
        if not isinstance(j["cancel"], bool):
            p.append(f"jobs[{i}].cancel: a bool")
    return p


def workloads(arm: str) -> list[dict[str, Any]]:
    """The deterministic arms' frozen workloads: RA-DET the systematic grid, RA-DET-C the literal translation."""
    if arm == "RA-DET":
        return [{"jobs": [{"n": 0, "cancel": True}], "max_attempts": 1},
                {"jobs": [{"n": 0, "cancel": True}], "max_attempts": 2},
                {"jobs": [{"n": 0, "cancel": False}, {"n": 1, "cancel": True}], "max_attempts": 1}]
    if arm == "RA-DET-C":
        return [{"jobs": [{"n": 0, "cancel": True}], "max_attempts": 1}]
    raise ValueError(f"{arm} isn't a deterministic arm")


def schema_version(archive: Path) -> str:
    with tarfile.open(archive) as t:
        vs = [int(m.group(1)) for n in t.getnames() if (m := re.fullmatch(r"chancy/migrations/v(\d+)\.py", n))]
    if not vs:
        raise ValueError("the archive has no chancy/migrations/vN.py")
    return str(max(vs))


def prepare(archive: Path, sha256: str, workdir: Path) -> dict[str, Any]:
    import run_isolated as ri
    got = hashlib.sha256(archive.read_bytes()).hexdigest()
    if got != sha256:
        raise ValueError(f"{archive.name}: sha256 {got[:12]} isn't the frozen manifest's {sha256[:12]}")
    workdir.mkdir(parents=True, exist_ok=True)
    name = f"dsconf-{got[:16]}.tar"
    staged = workdir / name
    shutil.copyfile(archive, staged)
    build = Path(tempfile.mkdtemp(prefix="ds_conf_build_"))
    ri.CHANCY_GIT[name] = (got, "curator-pinned", "")  # scoped to this build; the runner re-checks the hash
    try:
        image = ri.build_image(build, chancy_sdist=staged)
    finally:
        ri.CHANCY_GIT.pop(name, None)
        shutil.rmtree(build, ignore_errors=True)
    return {"image": image, "expect_schema": schema_version(archive), "archive_sha256": got, "integration": NAME}


def spec(w: dict[str, Any]) -> dict[str, Any]:
    import run_coordinated as rc
    ns = [j["n"] for j in w["jobs"]]
    cancel = [j["n"] for j in w["jobs"] if j["cancel"]]
    worker = {"actor": "worker", "fn": "work", "args": {"timeout_s": 60}}
    push = {"actor": "push", "fn": "push", "args": {"ns": ns, "queue": "q_shared", "max_attempts": w["max_attempts"],
                                                     "cancel": cancel}}
    gates = rc._window(["push"], ["worker"])
    if cancel:  # a cancel is meaningful only once the worker has claimed: the explorer decides everything else
        gates.append({"actor": "push", "match": "SET state = 'failed'",
                      "until": [{"actor": "worker", "marker": "claim", "count": 1}]})
    return {"processes": {"A": [worker, push]}, "gates": gates, "covers": ns, "consumer_of": {}}


def load_oracle(path: Path, sha256: str) -> Any:
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError("the oracle code isn't the approved one")
    s = importlib.util.spec_from_file_location(f"ds_conf_oracle_{sha256[:12]}", path)
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(m)
    return m


def run(handle: dict[str, Any], workload: dict[str, Any], seed: int, out: Path, *, oracle_code: Path,
        oracle_sha256: str) -> dict[str, Any]:
    import run_coordinated as rc
    import run_historical as rh

    from deployspecter.experimental import harness as hz
    if p := validate(workload):
        return {"verdict": "REFUSED", "seed": seed, "corroboration": {"ok": False}, "reason": p}
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", SECRET.read_text().strip())
    out.mkdir(parents=True)
    pre = rh.setup(target, handle["image"], out)
    # an errored setup actor can still leave an isolated, corroborated (short) log: require its own result too
    if not (pre.get("isolation_ok") and (pre.get("corroboration") or {}).get("ok")) or \
            not isinstance((pre.get("raw_results") or {}).get("setup"), dict):
        return {"verdict": "REFUSED", "seed": seed, "corroboration": pre.get("corroboration"),
                "reason": "the setup wasn't isolated and corroborated"}
    s = {**spec(workload), "expect_schema": handle["expect_schema"]}
    sim = rc.simultaneous(target, {"A": handle["image"], "env": {"A": {}}}, s, {"kind": "seeded", "seed": seed},
                          out, label="confirmatory")
    c = sim.get("corroboration") or {}
    corr = {k: c.get(k) for k in ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked")}
    if sim["verdict"] != "SOUND":
        return {"verdict": sim["verdict"], "seed": seed, "corroboration": corr,
                "reason": sim.get("refusal_reasons")}
    verdict = load_oracle(oracle_code, oracle_sha256).check(sim["final_state"], sim["raw_results"])
    return {"verdict": "SOUND", "oracle": verdict, "seed": seed, "corroboration": corr}
