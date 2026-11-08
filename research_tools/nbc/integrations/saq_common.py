"""EXPERIMENTAL, NONBLIND (ds-nbc/1): shared host-side machinery for the saq integrations (``saq_*.py`` here).
Not an integration itself. It holds no workload, oracle or case knowledge.

**Image.** One restricted runner image per pinned tree:
* the archive (a normalized ``git archive`` of ``saq/``) is re-verified by sha256 and unpacked data-only;
* ``saq/`` is copied in, with the runner's payload (``run_isolated.PAYLOAD``) and the variant adapter
  ``thirdparty/saq_coord_adapter.py``;
* dependencies are the runner's locked psycopg plus the onboarding pilot's hash-pinned wheels (croniter,
  python-dateutil, six, psycopg-pool; ``onboarding/pilot_1/saq/spec.json``).

The host derives the sha256 of the exercised source files from the archive (data only, nothing imported) and declares
them to every container (``DS_SAQ_PINNED``), which refuses to run any other tree.

**A run** (one schedule):
1. A fresh schema ``saq_nbc`` (dropped and created by the host). The tree's own ``connect``/``init_db`` creates its
   tables inside the run: each revision migrates only its own fresh schema (single-version).
2. ``run_coordinated.simultaneous`` with this application's hooks: one restricted container holding the tree's
   actors, plus the coordinator; every statement granted under the seeded explorer; the merged trace corroborated
   against the server log; the final state read by the host.
3. The schema is dropped. The verdict is the approved oracle's, only on a SOUND run.

Every actor function reports, under the coordinator's stop key ``succeeded``, the actor names whose function has
returned. ``covers`` names the actors that must return before the coordinator's ``stop`` signal.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))

SECRET = Path.home() / ".ds_mvp_pg_password"
SCHEMA = "saq_nbc"
ADAPTER = "deployspecter/experimental/thirdparty/saq_coord_adapter.py"
EXERCISED = ("saq/__init__.py", "saq/job.py", "saq/utils.py", "saq/worker.py", "saq/queue/__init__.py",
             "saq/queue/base.py", "saq/queue/postgres.py", "saq/queue/postgres_migrations.py", "saq/multiplexer.py")
# the onboarding pilot's resolved, hash-pinned saq dependencies (pilot_1/saq/spec.json); the same install_requires
# (croniter) and postgres extra (psycopg[pool]) hold at every pinned revision
WHEELS = [("croniter", "6.2.4", "8ef3d544107a5c05a150a2d78f8bf5a8eb9c5c4d93405a736b824109574e3f4d"),
          ("python-dateutil", "2.9.0.post0", "a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427"),
          ("six", "1.17.0", "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274"),
          ("psycopg-pool", "3.3.3", "9b9cd6a4fcec47a410f7e82d408540e7f77b478509e91b44c1a5457a13e5ff37")]
MARKERS = {"fetch": {"regex": "WITH locked_job AS"}, "claim": {"regex": "WITH locked_job AS", "rows": True},
           "insert": {"regex": r"INSERT INTO \"saq(_alt)?_jobs\""}, "init": {"regex": "pg_try_advisory"}}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def archive_sources(archive: Path) -> dict[str, str]:
    """sha256 of the exercised files, read from the archive as data."""
    out = {}
    with tarfile.open(archive) as t:
        for rel in EXERCISED:
            f = t.extractfile(rel)
            if f is None:
                raise ValueError(f"{archive.name} has no {rel}")
            out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def requirements() -> str:
    import run_isolated as ri
    return ri.requirements_from_lock(ri.LOCKED) + "".join(f"{n}=={v} --hash=sha256:{h}\n" for n, v, h in WHEELS)


def build_image(archive: Path, sha256: str, work: Path) -> tuple[str, str]:
    import run_isolated as ri
    got = sha(archive)
    if got != sha256:
        raise ValueError(f"{archive.name}: sha256 {got[:12]} isn't the frozen manifest's {sha256[:12]}")
    ctx = work / "context"
    src = ctx / "app" / "src"
    src.mkdir(parents=True)
    unpack = work / "tree"
    with tarfile.open(fileobj=io.BytesIO(archive.read_bytes())) as tf:
        names = tf.getnames()
        if not names or any(not (n == "saq" or n.startswith("saq/")) for n in names):
            raise ValueError(f"{archive.name} holds something besides saq/")
        tf.extractall(unpack, filter="data")  # data only: no links out, no devices, no absolute paths
    shutil.copytree(unpack / "saq", src / "saq")
    for rel in [*ri.PAYLOAD, ADAPTER]:
        dst = src / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / "src" / rel, dst)
    shutil.copyfile(REPO / "research_tools/isolated/smoke_inside.py", ctx / "app" / "smoke_inside.py")
    shutil.copyfile(REPO / "research_tools/isolated/Dockerfile.runner", ctx / "Dockerfile")
    (ctx / "requirements.txt").write_text(requirements())
    digest = hashlib.sha256()
    for f in sorted(p for p in ctx.rglob("*") if p.is_file()):
        digest.update(str(f.relative_to(ctx)).encode() + b"\0" + f.read_bytes())
    tag = f"ds-mvp-iso:saq-{digest.hexdigest()[:16]}"
    # Erratum E1 (see the cases' ERRATA.md): a normalized archive gives every file mtime 0, and Docker's build-context
    # sync reused a stale same-size file from an earlier build. Every context file gets this build's own mtime, and
    # the built image's saq/ tree is verified file by file against the archive before it is used.
    stamp = time.time_ns()
    for f in ctx.rglob("*"):
        os.utime(f, ns=(stamp, stamp))
    proc = ri.docker("build", "-q", "-t", tag, str(ctx), timeout=900, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"image build failed: {proc.stderr[-1500:]}")
    image_id = ri.docker("image", "inspect", "-f", "{{.Id}}", tag).stdout.strip()
    if problems := verify_image_tree(image_id, archive, work / "verify"):
        raise RuntimeError(f"the built image's saq/ isn't the archive's: {problems[:5]}")
    return tag, image_id


def tar_files(data: bytes, strip: str) -> dict[str, str]:
    """sha256 of every regular file in a tar (read as data), named relative to its ``strip`` directory."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for m in tf.getmembers():
            name = m.name.removeprefix("./")
            if m.isfile() and "__pycache__" not in name.split("/"):
                f = tf.extractfile(m)
                assert f is not None
                out[name.removeprefix(strip + "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


def verify_image_tree(image_id: str, archive: Path, work: Path) -> list[str]:
    """Streams /app/src/saq out of a created (never started) container as a tar and compares every file with the
    archive's."""
    import subprocess

    import run_isolated as ri
    cid = ri.docker("create", image_id).stdout.strip()
    try:
        proc = subprocess.run(["docker", "cp", f"{cid}:/app/src/saq", "-"], capture_output=True, timeout=120,
                              check=True)
    finally:
        ri.docker("rm", cid, check=False)
    got, want = tar_files(proc.stdout, "saq"), tar_files(archive.read_bytes(), "saq")
    return sorted(f"{k}: image {got.get(k, 'missing')[:12]}, archive {want.get(k, 'missing')[:12]}"
                  for k in set(want) | set(got) if want.get(k) != got.get(k))


def prepare(archive: Path, sha256: str, workdir: Path, *, integration: str, rev: str) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix="ds_saq_build_"))
    try:
        image = build_image(archive, sha256, build)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    return {"image": image, "archive_sha256": sha256, "integration": integration, "rev": rev,
            "pinned": archive_sources(archive)}


class SaqApp:
    """``run_coordinated.simultaneous`` hooks for saq on the fresh ``saq_nbc`` schema."""
    schema = SCHEMA
    inside_mode = "saq_coordinated"
    markers = MARKERS

    @staticmethod
    def state(admin: Any) -> dict[str, Any]:
        tables = [r[0] for r in admin.execute("SELECT tablename::text FROM pg_tables WHERE schemaname = %s "
                                              "ORDER BY 1", (SCHEMA,)).fetchall()]
        out: dict[str, Any] = {"tables": tables, "jobs": {}}
        for t in ("saq_jobs", "saq_alt_jobs"):
            if t not in tables:
                continue
            rows = []
            for key, queue, status, blob in admin.execute(
                    f'SELECT key, queue, status, job FROM {SCHEMA}."{t}" ORDER BY key').fetchall():
                try:
                    j = json.loads(bytes(blob))
                except ValueError:
                    j = {}
                rows.append({"key": key, "queue": queue, "status": status, "attempts": j.get("attempts"),
                             "result": j.get("result"), "error": (j.get("error") or "")[:200] or None})
            out["jobs"][t] = rows
        return out

    @staticmethod
    def precondition(initial: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        if initial["tables"]:
            raise RuntimeError(f"the schema isn't fresh: {initial['tables']}")
        return {"tables_before": 0}

    @staticmethod
    def check(final: dict[str, Any], results: dict[str, Any], spec: dict[str, Any], quiescent: bool) -> dict:
        return {"case_oracle_only": True}  # the case's approved oracle decides (integration ``run``)


def load_oracle(path: Path, sha256: str) -> Any:
    if sha(path) != sha256:
        raise ValueError("the oracle code isn't the approved one")
    s = importlib.util.spec_from_file_location(f"ds_nbc_saq_oracle_{sha256[:12]}", path)
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(m)
    return m


def fresh_schema(target: Any) -> None:
    admin = target.admin()
    try:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")
    finally:
        admin.close()


def drop_schema(target: Any) -> None:
    admin = target.admin()
    try:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    finally:
        admin.close()


def run_spec(handle: dict[str, Any], spec: dict[str, Any], seed: int, out: Path, *, label: str) -> dict[str, Any]:
    """One coordinated, corroborated run of ``spec`` (``processes``/``gates``/``covers``) on a fresh schema."""
    import run_coordinated as rc

    from deployspecter.experimental import harness as hz
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", SECRET.read_text().strip())
    out.mkdir(parents=True)
    env = {"DS_SAQ_PINNED": json.dumps(handle["pinned"], sort_keys=True, separators=(",", ":")),
           "DS_SAQ_REV": handle["rev"]}
    fresh_schema(target)
    try:
        sim = rc.simultaneous(target, {"A": handle["image"], "env": {"A": env}}, spec, {"kind": "seeded", "seed": seed},
                              out, label=label, app=SaqApp)
    finally:
        drop_schema(target)
    (out / "report.json").write_text(json.dumps(sim, indent=1, default=str))
    return sim


def judge(sim: dict[str, Any], seed: int, oracle_code: Path, oracle_sha256: str) -> dict[str, Any]:
    c = sim.get("corroboration") or {}
    corr = {k: c.get(k) for k in ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked")}
    if sim["verdict"] != "SOUND":
        return {"verdict": sim["verdict"], "seed": seed, "corroboration": corr, "reason": sim.get("refusal_reasons")}
    verdict = load_oracle(oracle_code, oracle_sha256).check(sim["final_state"], sim["raw_results"])
    return {"verdict": "SOUND", "oracle": verdict, "seed": seed, "corroboration": corr}


def strict(schema: dict[str, Any]) -> list[str]:
    """Problems with a schema for strict structured output (every object closed, every property required, no
    oneOf/anyOf)."""
    p: list[str] = []

    def walk(s: Any, at: str) -> None:
        if isinstance(s, dict):
            for bad in ("oneOf", "anyOf", "allOf"):
                if bad in s:
                    p.append(f"{at}: {bad}")
            if s.get("type") == "object":
                if s.get("additionalProperties") is not False:
                    p.append(f"{at}: additionalProperties must be false")
                if sorted(s.get("required", [])) != sorted(s.get("properties", {})):
                    p.append(f"{at}: every property must be required")
            for k, v in s.items():
                walk(v, f"{at}.{k}")
        elif isinstance(s, list):
            for i, v in enumerate(s):
                walk(v, f"{at}[{i}]")

    walk(schema, "$")
    return p
