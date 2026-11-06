"""EXPERIMENTAL onboarding pilot: build an onboarded application's restricted runner image, and run the three adapter
steps in it against the disposable PostgreSQL, with server-log corroboration. App-independent: everything
app-specific comes from the spec and the adapter module it names.

The isolation is the existing runner's, reused rather than re-implemented:
* its Dockerfile (base pinned by digest, ``--require-hashes``), runtime flags and isolation probe;
* its internal network, teardown under deferred signals, and corroboration.

The app's package is copied from the pinned sdist (re-verified, data-only extraction). Its dependencies are exactly
the spec's hash-pinned wheels, plus the runner's locked psycopg. Untrusted code runs only in the container.
Disposable PostgreSQL only (55441); run it through ``research_tools/pg_lock.py``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))

import run_isolated as ri  # noqa: E402
from signal_safe import deferred_signals  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental.corroborate import corroborate  # noqa: E402

HARNESS_PAYLOAD = ["deployspecter/experimental/onboarding_pilot/__init__.py",
                   "deployspecter/experimental/onboarding_pilot/recorder.py",
                   "deployspecter/experimental/onboarding_pilot/adapters/__init__.py"]


def adapter_file(spec: dict[str, Any]) -> str:
    return spec["adapter"].replace(".", "/") + ".py"


def build_image(spec: dict[str, Any], sdists: Path, work: Path) -> tuple[str, str]:
    ctx = work / "context"
    src = ctx / "app" / "src"
    src.mkdir(parents=True)
    sdist = sdists / spec["sdist"]
    got = hashlib.sha256(sdist.read_bytes()).hexdigest()
    if got != spec["sdist_sha256"]:
        raise RuntimeError(f"{sdist.name} isn't the pinned sdist (sha256 {got[:12]})")
    unpack = work / "sdist"
    with tarfile.open(sdist) as tf:
        tf.extractall(unpack, filter="data")  # data only: no links out, no devices, no absolute paths
    root = next(unpack.iterdir())
    shutil.copytree(root / spec["package"], src / spec["package"])
    if spec["install_metadata"]:  # the package reads its own distribution metadata at import
        dist = src / f"{root.name}.dist-info"
        dist.mkdir()
        shutil.copyfile(root / "PKG-INFO", dist / "METADATA")
    for rel in ri.PAYLOAD + HARNESS_PAYLOAD + [adapter_file(spec)]:
        dst = src / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / "src" / rel, dst)
    here = Path(__file__).resolve().parent
    shutil.copyfile(here / "pilot_inside.py", ctx / "app" / "pilot_inside.py")
    shutil.copyfile(REPO / "research_tools/isolated/smoke_inside.py", ctx / "app" / "smoke_inside.py")
    shutil.copyfile(REPO / "research_tools/isolated/Dockerfile.runner", ctx / "Dockerfile")
    wheels = "".join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in spec["wheels"])
    (ctx / "requirements.txt").write_text(ri.requirements_from_lock(ri.LOCKED) + wheels)
    digest = hashlib.sha256()
    for f in sorted(p for p in ctx.rglob("*") if p.is_file()):
        digest.update(str(f.relative_to(ctx)).encode() + b"\0" + f.read_bytes())
    tag = f"ds-mvp-iso:onb-{digest.hexdigest()[:16]}"
    proc = ri.docker("build", "-q", "-t", tag, str(ctx), timeout=900, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"image build failed: {proc.stderr[-1500:]}")
    return tag, ri.docker("image", "inspect", "-f", "{{.Id}}", tag).stdout.strip()


def run(target: hz.Target, spec: dict[str, Any], workload: dict[str, Any], image: tuple[str, str], out: Path, *,
        timeout_s: float = 60) -> dict[str, Any]:
    from deployspecter.live.serverlog import ServerLog
    before = ri.protected_state()
    work = Path(tempfile.mkdtemp(prefix="ds_onb_"))
    nonce = secrets.token_hex(4)
    net, name = f"ds-mvp-iso-{nonce}", f"ds-mvp-iso-run-{nonce}"
    schema = spec["schema"]
    result: dict[str, Any] = {"app": spec["app"], "image": list(image), "schema": schema, "network": net}
    try:
        ri.docker("network", "create", "--internal", net)
        ri.docker("network", "connect", "--alias", "ds-mvp-pg", net, ri.PG_CONTAINER)
        admin = target.admin()
        try:
            if admin.execute("SELECT current_setting('cluster_name'), current_database()").fetchone() != \
                    ("ds_mvp_disposable", "ds_mvp"):
                raise RuntimeError("not the disposable database")
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.execute(f'CREATE SCHEMA "{schema}"')
            log = ServerLog(lambda q, prm=None: admin.execute(q, prm).fetchone())
            log.position()
            start, end = f"ds_mvp_mark_start_{nonce}", f"ds_mvp_mark_end_{nonce}"
            envfile = work / "env"
            envfile.write_text("\n".join([
                "DS_PG_HOST=ds-mvp-pg", "DS_PG_PORT=5432", f"DS_PG_USER={target.user}",
                f"DS_PG_PASSWORD={target.password}", f"DS_PG_DB={target.dbname}", f"DS_SCHEMA={schema}",
                f"DS_ADAPTER={spec['adapter']}", f"DS_WORKLOAD={json.dumps(workload)}",
                f"DS_TIMEOUT_S={timeout_s}"]) + "\n")
            envfile.chmod(0o600)
            admin.execute(f"SELECT '{start}'")
            t = time.monotonic()
            try:
                proc = ri.docker("run", "--rm", "--name", name, "--network", net, *ri.RUNTIME_FLAGS, "--env-file",
                                 str(envfile), image[0], "/app/pilot_inside.py", timeout=300, check=False)
            except Exception:
                ri.docker("kill", name, check=False)
                raise
            result["container_seconds"] = round(time.monotonic() - t, 2)
            result["exit_code"] = proc.returncode
            result["stderr_tail"] = proc.stderr[-3000:]
            if proc.returncode != 0:
                raise RuntimeError(f"the container exited {proc.returncode}")
            inside = json.loads(proc.stdout.strip().splitlines()[-1])
            merged = ri.merge_runs(inside)  # it carries probes and pinned sources through
            trace = merged["trace"]
            monitors = merged.get("monitor_pids") or [merged["monitor_pid"]]
            pids = [a["backend_pid"] for a in trace["actors"].values()] + monitors
            gone = hz._wait_gone(admin, pids)
            admin.execute(f"SELECT '{end}'")
            until = time.monotonic() + 5
            data = log.read()
            while not (f"SELECT '{end}'".encode() in data and data.endswith(b"\n")) and time.monotonic() < until:
                time.sleep(0.05)
                data = log.read()
            shaped = SimpleNamespace(actors=trace["actors"], records=[SimpleNamespace(**r) for r in trace["records"]])
            corr = corroborate(data, start=start, end=end, trace=shaped,
                               admin_pid={admin.info.backend_pid, *monitors}, user=target.user,
                               database=target.dbname).to_dict()
            if not gone:
                corr["ok"] = False
                corr["problems"].append("a container session outlived the run")
            final_tables = [r[0] for r in admin.execute(
                "SELECT tablename::text FROM pg_tables WHERE schemaname = %s ORDER BY 1", (schema,)).fetchall()]
        finally:
            admin.close()
        probes = inside["probes"]
        iso = {k: probes.get(k) == v for k, v in ri.EXPECTED_PROBES.items()}
        iso.update(host_55432_unreachable=probes["host_55432"].startswith("refused"),
                   host_gateway_unreachable=probes["host_gateway_55441"].startswith("refused"),
                   internet_tcp_unreachable=probes["internet_tcp"].startswith("refused"),
                   internet_dns_unresolved=probes["internet_dns"].startswith("unresolved"),
                   pids_limited=probes["nproc_limit"] == "64", memory_limited=probes["memory_limit"] == str(512 << 20))
        result.update(probes=probes, isolation_checks=iso, isolation_ok=all(iso.values()), corroboration=corr,
                      handled=inside["handled"], phases=inside["phases"], pinned_sources=inside["pinned_sources"],
                      final_tables=final_tables,
                      trace_sha256=hashlib.sha256(json.dumps(trace, sort_keys=True, default=str).encode()).hexdigest())
        (out / "trace.json").write_text(json.dumps(trace, indent=1, default=str))
    except Exception as exc:  # noqa: BLE001 - a failed attempt is a result; teardown still runs
        result["error"] = f"{type(exc).__name__}: {str(exc)[:1500]}"
    finally:
        with deferred_signals():
            try:  # the onboarding schema, whatever happened
                a2 = target.admin()
                try:
                    a2.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    result["schema_dropped"] = True
                finally:
                    a2.close()
            except Exception as exc:  # noqa: BLE001
                result["schema_dropped"] = f"failed: {exc}"
            ri.docker("rm", "-f", name, check=False)
            ri.docker("network", "disconnect", "-f", net, ri.PG_CONTAINER, check=False)
            ri.docker("network", "rm", net, check=False)
            shutil.rmtree(work, ignore_errors=True)
            left = ri.docker("ps", "-a", "--filter", f"name={name}", "-q", check=False).stdout.strip()
            nets = ri.docker("network", "ls", "--filter", f"name={net}", "-q", check=False).stdout.strip()
            result["teardown"] = {"container_removed": not left, "network_removed": not nets,
                                  "workdir_removed": not work.exists(),
                                  "protected_unchanged": ri.protected_state() == before}
    return result
