"""EXPERIMENTAL isolated runner (host side): run a payload in a restricted container against the disposable PostgreSQL.

    python research_tools/isolated/run_isolated.py smoke --out DIR

What the container gets, and nothing else:

* an image built from a digest-pinned base, with Python dependencies installed only from hashes (``--require-hashes
  --no-deps``) taken from ``uv.lock``, and the payload copied in (no volume or bind mount of the host);
* a fresh ``--internal`` Docker network shared only with the disposable PostgreSQL container (no route to the
  internet or the host, so neither the protected 55432 nor anything else on the host);
* uid 65534, read-only root filesystem, a 64 MB noexec tmpfs, all capabilities dropped, ``no-new-privileges``,
  64 PIDs, 512 MB memory and no swap, 1 CPU, 256 open files, a wall-clock limit enforced by the host (``docker
  kill``);
* environment: only the disposable database's connection settings and the run's schedule. No model credentials,
  no SSH agent or keys, no production DSN.

The container reports what it actually observed (``probes``); the host refuses the run as isolation evidence unless
every probe matches. Reset, the initial-state check, the oracle and the server-log corroboration run on the host.

This is **not** a hostile-code sandbox. Containers share the Docker Desktop VM's kernel; a kernel or runtime escape
defeats every flag here; Docker's default seccomp profile is in force but no gVisor/Kata runtime is available. It is a
containment layer for trusted fixtures and for pinned, statically reviewed public code, with complete teardown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import oracle  # noqa: E402
from deployspecter.experimental.apps import reservations_mixed as app  # noqa: E402
from deployspecter.experimental.corroborate import corroborate  # noqa: E402

PG_CONTAINER = "deployspecter-mvp-pg"
PROTECTED_CONTAINER = "deployspecter-pg"
RUNTIME_FLAGS = ["--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m", "--memory-swap",
                 "512m", "--cpus", "1", "--ulimit", "nofile=256:256", "--user", "65534:65534"]
PAYLOAD = ["deployspecter/__init__.py", "deployspecter/experimental/__init__.py",
           "deployspecter/experimental/stepping.py", "deployspecter/experimental/stepping_async.py",
           "deployspecter/experimental/oracle.py", "deployspecter/experimental/apps/__init__.py",
           "deployspecter/experimental/apps/reservations_mixed.py",
           "deployspecter/experimental/apps/reservations_mixed_async.py",
           "deployspecter/experimental/thirdparty/__init__.py",
           "deployspecter/experimental/thirdparty/pgqueuer_adapter.py",
           "deployspecter/experimental/thirdparty/chancy_adapter.py",
           "deployspecter/experimental/coordinated.py"]
LOCKED = ["psycopg", "psycopg-binary", "typing-extensions"]
# The one third-party candidate whose migration path takes a caller-supplied connection (CORPUS_SHORTLIST.json v2).
CHANCY_SDIST = "chancy-0.26.0.tar.gz"
CHANCY_SDIST_SHA256 = "ac98b9c502d7bdb6f47d53fac1c22b4dc55e6cfcfec84b08b1a0cbaee45aef06"
CHANCY_SDISTS = {CHANCY_SDIST: CHANCY_SDIST_SHA256,  # the mixed-version pilot also needs the previous release
                 "chancy-0.25.1.tar.gz": "264ed4f732cb51c405b789e0727c4d045fdfa1dcee1e3eed6d762df6c44122d7"}
# Commit-pinned Chancy trees for the historical case chancy-2895c4a: ``git archive --format=tar <commit> chancy``,
# deterministic for a commit; name -> (tar sha256, commit, git tree)
CHANCY_GIT = {
    "chancy-git-80f570f.tar": ("8747cdc8258aa46d445c82babcdf513c6c8d8e44fae5c67d11c9caffc795deaa",
                               "80f570f0b8332212e46c3c27af5940946f6486a9", "a5a389c45c8db1f53275a6c121bb3bf520796adb"),
    "chancy-git-2895c4a.tar": ("cbc461d2e7cb5edcc48830470405c1dfea91fe5c68381afd6bade26a193ed626",
                               "2895c4a7fc173cec3d645221f63c4e51e2cf7e84", "bdf46b6b445bdc8519a3c96133a411610fc4a57a"),
}
CHANCY_EXTRA_REQUIREMENTS = ("psycopg-pool==3.3.3 "
                             "--hash=sha256:9b9cd6a4fcec47a410f7e82d408540e7f77b478509e91b44c1a5457a13e5ff37\n")
CAPTURE_SCHEMA = "chancy_capture"
# PGQueuer 1.6.0 (CORPUS_SHORTLIST.json v2): its psycopg Driver port takes a caller-supplied AsyncConnection.
PGQUEUER_SDIST = "pgqueuer-1.6.0.tar.gz"
PGQUEUER_SDIST_SHA256 = "ed1306404f96e25c84c951f434e8b06b091e04e0177af0d5441505a7944f156e"
PGQUEUER_REQUIREMENTS = Path(__file__).with_name("requirements.pgqueuer.txt")
SCHEMAS = {"smoke": CAPTURE_SCHEMA, "chancy": CAPTURE_SCHEMA, "pgqueuer": "pgq_capture",
           "chancy-runtime": CAPTURE_SCHEMA, "chancy-plan": CAPTURE_SCHEMA}
INSIDE_MODE = {"smoke": "smoke", "chancy": "chancy_migrate", "pgqueuer": "pgqueuer_lifecycle",
               "chancy-runtime": "chancy_runtime", "chancy-plan": "chancy_plan"}
CAPTURE_MODES = ("chancy", "pgqueuer", "chancy-runtime", "chancy-plan")
PGQ_DEFAULT_WORKLOAD = {"jobs": [{"payload": "alpha", "priority": 0}, {"payload": "beta", "priority": 0}],
                        "batch_size": 1}
EXPECTED_PROBES = {"uid": 65534, "root_writable": False, "app_writable": False, "tmp_writable": True,
                   "ssh_dir_present": False, "secret_like_env": []}


def docker(*args: str, timeout: float = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=check)


def requirements_from_lock(names: list[str]) -> str:
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    lines = []
    for name in names:
        pkg = next(p for p in lock["package"] if p["name"] == name)
        hashes = [w["hash"] for w in pkg.get("wheels", [])] + ([pkg["sdist"]["hash"]] if "sdist" in pkg else [])
        lines.append(f"{name}=={pkg['version']} " + " ".join(f"--hash={h}" for h in hashes))
    return "\n".join(lines) + "\n"


def copy_verified_package(sdist: Path, *, name: str, sha256: str, package: str, work: Path, dest: Path) -> None:
    """Unpack a pinned sdist (data only) after re-verifying its sha256, and copy its package directory."""
    import tarfile
    got = hashlib.sha256(sdist.read_bytes()).hexdigest()
    if sdist.name != name or got != sha256:
        raise RuntimeError(f"{sdist.name} isn't the pinned sdist (sha256 {got[:12]})")
    unpack = work / f"sdist_{package}"
    with tarfile.open(sdist) as tf:
        tf.extractall(unpack, filter="data")  # data only: no links out, no device files, no absolute paths
    shutil.copytree(unpack / name.removesuffix(".tar.gz") / package, dest / package)


# Controlled mutants of pinned public code, applied to the isolated build copy only (never in place): an exact
# substring of one file, replaced; the mutated file's sha256 is declared to the adapter.
MUTANTS = {"no_skip_locked_claim": ("chancy/worker.py", "FOR UPDATE OF {jobs} SKIP LOCKED", "",
                                    "the job claim's row lock is removed: two workers can claim one job"),
           # Ablations of the historical fix chancy-2895c4a (applied to the fix commit's tree, base git-2895c4a); each
           # removes one part of the fix: a controlled experiment, not a defect
           "abl_no_fence": ("chancy/worker.py", "AND claim_id IS NOT DISTINCT FROM %(claim_id)s", "",
                            "F2 removed: the completion update is no longer fenced by the claim"),
           "abl_cancel_keeps_claim": ("chancy/app.py", "SET state = 'failed', claim_id = NULL, completed_at = NOW()",
                                      "SET state = 'failed', completed_at = NOW()",
                                      "F3 removed: cancel_job_ex no longer invalidates the claim"),
           "abl_no_claim_assignment": ("chancy/worker.py", "claim_id = gen_random_uuid(),", "",
                                       "F1 removed: the claim query no longer assigns a claim id")}


def mutant_declaration(sdist: Path, mutant: str, base: str) -> str:
    """What the adapter must accept for this mutant: '<id>:<base>:<file>:<sha256 of the mutated file>'."""
    import tarfile
    rel, old, new, _why = MUTANTS[mutant]
    member = rel if sdist.name in CHANCY_GIT else f"{sdist.name.removesuffix('.tar.gz')}/{rel}"
    with tarfile.open(sdist) as tf:
        text = tf.extractfile(member).read().decode()
    if text.count(old) != 1:
        raise RuntimeError(f"mutant {mutant}: the substring doesn't occur exactly once in {rel}")
    return f"{mutant}:{base}:{rel}:{hashlib.sha256(text.replace(old, new).encode()).hexdigest()}"


def build_image(work: Path, *, chancy_sdist: Path | None = None, pgqueuer_sdist: Path | None = None,
                mutant: str | None = None) -> tuple[str, str]:
    ctx = work / "context"
    (ctx / "app" / "src").mkdir(parents=True)
    extra = ""
    base_requirements = requirements_from_lock(LOCKED)
    if pgqueuer_sdist is not None:
        copy_verified_package(pgqueuer_sdist, name=PGQUEUER_SDIST, sha256=PGQUEUER_SDIST_SHA256, package="pgqueuer",
                              work=work, dest=ctx / "app" / "src")
        base_requirements = PGQUEUER_REQUIREMENTS.read_text()  # includes psycopg and psycopg-binary 3.3.6
    if chancy_sdist is not None:
        import tarfile
        got = hashlib.sha256(chancy_sdist.read_bytes()).hexdigest()
        unpack = work / "sdist"
        if chancy_sdist.name in CHANCY_GIT:  # a commit-pinned tree (historical case), not a released sdist
            if CHANCY_GIT[chancy_sdist.name][0] != got:
                raise RuntimeError(f"{chancy_sdist.name} isn't the pinned tree (sha256 {got[:12]})")
            with tarfile.open(chancy_sdist) as tf:
                tf.extractall(unpack, filter="data")
            shutil.copytree(unpack / "chancy", ctx / "app" / "src" / "chancy")
        else:
            if CHANCY_SDISTS.get(chancy_sdist.name) != got:
                raise RuntimeError(f"{chancy_sdist.name} isn't a pinned sdist (sha256 {got[:12]})")
            with tarfile.open(chancy_sdist) as tf:
                tf.extractall(unpack, filter="data")  # data only: no links out, no device files, no absolute paths
            shutil.copytree(unpack / chancy_sdist.name.removesuffix(".tar.gz") / "chancy",
                            ctx / "app" / "src" / "chancy")
        if mutant is not None:
            rel, old, new, _why = MUTANTS[mutant]
            target_file = ctx / "app" / "src" / rel
            text = target_file.read_text()
            if text.count(old) != 1:
                raise RuntimeError(f"mutant {mutant}: the substring doesn't occur exactly once in {rel}")
            target_file.write_text(text.replace(old, new))
        extra = CHANCY_EXTRA_REQUIREMENTS
    for rel in PAYLOAD:
        dst = ctx / "app" / "src" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / "src" / rel, dst)
    shutil.copyfile(Path(__file__).with_name("smoke_inside.py"), ctx / "app" / "smoke_inside.py")
    shutil.copyfile(Path(__file__).with_name("Dockerfile.runner"), ctx / "Dockerfile")
    (ctx / "requirements.txt").write_text(base_requirements + extra)
    digest = hashlib.sha256()
    for f in sorted(p for p in ctx.rglob("*") if p.is_file()):
        digest.update(str(f.relative_to(ctx)).encode() + b"\0" + f.read_bytes())
    tag = f"ds-mvp-iso:{digest.hexdigest()[:16]}"
    docker("build", "-q", "-t", tag, str(ctx), timeout=900)
    image_id = docker("image", "inspect", "-f", "{{.Id}}", tag).stdout.strip()
    return tag, image_id


def protected_state() -> str:
    return docker("inspect", "-f", "{{.Id}} {{.State.StartedAt}} {{.State.Status}}", PROTECTED_CONTAINER,
                  check=False).stdout.strip()


def run_smoke(target: hz.Target, *, variant: str, schedule: list[str], out: Path, image: tuple[str, str],
              mode: str = "smoke", pgq_variant: str = "adapter", chancy_variant: str = "pool_only",
              pgq_workload: dict[str, Any] | None = None, fresh_schema: bool = True, keep_schema: bool = False,
              extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    before = protected_state()
    work = Path(tempfile.mkdtemp(prefix="ds_iso_"))
    nonce = secrets.token_hex(4)
    net, name = f"ds-mvp-iso-{nonce}", f"ds-mvp-iso-run-{nonce}"
    envfile = work / "env"
    result: dict[str, Any] = {"variant": variant, "schedule": schedule, "network": net}
    try:
        tag, image_id = image
        result.update(image=tag, image_id=image_id, runtime_flags=RUNTIME_FLAGS)
        docker("network", "create", "--internal", net)
        docker("network", "connect", "--alias", "ds-mvp-pg", net, PG_CONTAINER)
        result["network_internal"] = docker("network", "inspect", "-f", "{{.Internal}}", net).stdout.strip()
        seed = {"events": [{"id": 1, "capacity": 5, "reserved": 0}]}
        admin = target.admin()
        try:
            schema = SCHEMAS[mode]
            if mode in CAPTURE_MODES:
                if fresh_schema:
                    admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                    admin.execute(f"CREATE SCHEMA {schema}")
                initial = {"catalog": catalog(admin, schema)}
                if fresh_schema and initial["catalog"]:
                    raise RuntimeError("the capture schema isn't empty")
                if mode == "chancy-plan":
                    initial.update(chancy_state(admin, schema))
                digest = oracle.digest(initial)
            else:
                app.reset(admin, seed)
                initial = oracle.read_state(admin)
                digest = oracle.digest(initial)
                if digest != oracle.digest(oracle.expected_initial(seed)):
                    raise RuntimeError("the initial state isn't the verified one")
            from deployspecter.live.serverlog import ServerLog
            log = ServerLog(lambda q, prm=None: admin.execute(q, prm).fetchone())
            log.position()
            start, end = f"ds_mvp_mark_start_{nonce}", f"ds_mvp_mark_end_{nonce}"
            envfile.write_text("\n".join([
                "DS_PG_HOST=ds-mvp-pg", "DS_PG_PORT=5432", f"DS_PG_USER={target.user}",
                f"DS_PG_PASSWORD={target.password}", f"DS_PG_DB={target.dbname}",
                f"DS_SCHEDULE={json.dumps(schedule)}", f"DS_VARIANT={variant}",
                f"DS_MODE={INSIDE_MODE[mode]}", f"DS_SCHEMA={schema}", f"DS_PGQ_VARIANT={pgq_variant}",
                f"DS_CHANCY_VARIANT={chancy_variant}",
                f"DS_PGQ_WORKLOAD={json.dumps(pgq_workload or PGQ_DEFAULT_WORKLOAD)}",
                *[f"{k}={v}" for k, v in (extra_env or {}).items()]]) + "\n")
            envfile.chmod(0o600)
            admin.execute(f"SELECT '{start}'")
            started = time.monotonic()
            try:
                proc = docker("run", "--rm", "--name", name, "--network", net, *RUNTIME_FLAGS, "--env-file",
                              str(envfile), tag, "/app/smoke_inside.py", timeout=240, check=False)
            except subprocess.TimeoutExpired:
                docker("kill", name, check=False)
                raise
            result["container_seconds"] = round(time.monotonic() - started, 2)
            result["exit_code"] = proc.returncode
            if proc.returncode != 0:
                result["stderr_tail"] = proc.stderr[-2000:]
                raise RuntimeError(f"the container exited {proc.returncode}")
            inside = json.loads(proc.stdout.strip().splitlines()[-1])
            if "runs" in inside:  # one run per lifecycle phase, merged: grants stay ordered within each phase
                inside = merge_runs(inside)
            trace = inside["trace"]
            monitors = inside.get("monitor_pids") or [inside["monitor_pid"]]
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
            if mode == "chancy":
                final = {"catalog": catalog(admin, schema)}
            elif mode == "pgqueuer":
                final = {"catalog": catalog(admin, schema), **pgqueuer_state(admin, schema)}
            elif mode in ("chancy-runtime", "chancy-plan"):
                final = {"catalog": catalog(admin, schema), **chancy_state(admin, schema)}
            else:
                final = oracle.read_state(admin)
            if mode in CAPTURE_MODES and not keep_schema:
                admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            admin.close()
        done = any(isinstance(r, dict) and r.get("status") == "done" for r in inside["raw_results"].values())
        probes = inside["probes"]
        isolation = {k: (probes.get(k) == v) for k, v in EXPECTED_PROBES.items()}
        isolation.update(host_55432_unreachable=probes["host_55432"].startswith("refused"),
                         host_gateway_unreachable=probes["host_gateway_55441"].startswith("refused"),
                         internet_tcp_unreachable=probes["internet_tcp"].startswith("refused"),
                         internet_dns_unresolved=probes["internet_dns"].startswith("unresolved"),
                         # the server log saw the container's session(s): a gated record isn't required (a
                         # negative control may have none)
                         disposable_pg_reachable=bool(trace["records"]) or bool(corr.get("sessions")),
                         pids_limited=probes["nproc_limit"] == "64",
                         memory_limited=probes["memory_limit"] == str(512 * 1024 * 1024))
        result.update(probes=probes, isolation_checks=isolation, isolation_ok=all(isolation.values()),
                      initial_state_digest=digest, final_state=final, final_state_digest=oracle.digest(final),
                      oracle_violations=(None if mode == "chancy" else pgqueuer_check(
                                             final, inside["raw_results"], pgq_workload or PGQ_DEFAULT_WORKLOAD)
                                         if mode == "pgqueuer" else None if mode == "chancy-plan" else (
                                             chancy_concurrent_check if chancy_variant == "concurrent"
                                             else chancy_check)(final, inside["raw_results"])
                                         if mode == "chancy-runtime" else oracle.check(final, backfill_done=done)),
                      pinned_sources=inside.get("pinned_sources"),
                      corroboration=corr,
                      trace_sha256=hashlib.sha256(json.dumps(trace, sort_keys=True, default=str).encode()).hexdigest(),
                      raw_results=inside["raw_results"], code_revision=hz.code_revision(),
                      payload_sha256={rel: hashlib.sha256((REPO / "src" / rel).read_bytes()).hexdigest()
                                      for rel in PAYLOAD})
        (out / f"{variant}.trace.json").write_text(json.dumps(trace, indent=1, default=str))
    finally:
        docker("rm", "-f", name, check=False)
        docker("network", "disconnect", "-f", net, PG_CONTAINER, check=False)
        docker("network", "rm", net, check=False)
        shutil.rmtree(work, ignore_errors=True)
        leftovers = docker("ps", "-a", "--filter", f"name={name}", "-q", check=False).stdout.strip()
        nets = docker("network", "ls", "--filter", f"name={net}", "-q", check=False).stdout.strip()
        result["teardown"] = {"container_removed": not leftovers, "network_removed": not nets,
                              "workdir_removed": not work.exists(), "protected_unchanged": protected_state() == before}
    return result


def catalog(admin: Any, schema: str = CAPTURE_SCHEMA) -> list[dict[str, Any]]:
    return [dict(zip(("table", "column", "type", "nullable"), r, strict=True)) for r in admin.execute(
        "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = %s ORDER BY table_name, ordinal_position", (schema,)).fetchall()]


def merge_runs(inside: dict[str, Any]) -> dict[str, Any]:
    """Several sequential runs (one per lifecycle phase) as one trace for one log window. Actor names are distinct
    per phase; grants are offset per phase so the order check stays meaningful across them."""
    actors: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    for i, run in enumerate(inside["runs"]):
        actors.update(run["trace"]["actors"])
        raw.update(run["raw_results"])
        for r in run["trace"]["records"]:
            r = dict(r)
            if r.get("grant") is not None:
                r["grant"] = i * 1_000_000 + r["grant"]
            records.append(r)
    return {"probes": inside["probes"], "pinned_sources": inside.get("pinned_sources"),
            "monitor_pids": [run["monitor_pid"] for run in inside["runs"]],
            "phases": [{"phase": run["phase"], "outcome": run["trace"]["outcome"],
                        "exceptions": {n: a["exception"] for n, a in run["trace"]["actors"].items()}}
                       for run in inside["runs"]],
            "trace": {"actors": actors, "records": records}, "raw_results": raw}


def pgqueuer_state(admin: Any, schema: str) -> dict[str, Any]:
    def rows(q: str) -> list[dict[str, Any]]:
        cur = admin.execute(q)
        names = [d.name for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    def exists(table: str) -> bool:
        return admin.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{table}",)).fetchone()[0]
    present = {t: exists(t) for t in ("pgqueuer", "pgqueuer_log", "ds_pgq_results")}
    return {"tables": [{"table": t, "present": v} for t, v in present.items()],
            "queue": rows(f"SELECT id, entrypoint, status::text AS status FROM {schema}.pgqueuer ORDER BY id")
            if present["pgqueuer"] else [],
            "log": rows(f"SELECT job_id, status::text AS status, entrypoint FROM {schema}.pgqueuer_log ORDER BY id")
            if present["pgqueuer_log"] else [],
            "results": [{"job_id": r["job_id"], "payload": bytes(r["payload"]).decode()}
                        for r in rows(f"SELECT job_id, payload FROM {schema}.ds_pgq_results ORDER BY job_id")]
            if present["ds_pgq_results"] else []}


def chancy_state(admin: Any, schema: str) -> dict[str, Any]:
    if not admin.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.chancy_jobs",)).fetchone()[0]:
        return {"jobs": [], "schema_version": []}
    has_claim = admin.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = %s AND "
                              "table_name = 'chancy_jobs' AND column_name = 'claim_id'", (schema,)).fetchone()[0]
    claim = ", claim_id IS NOT NULL AS has_claim_id" if has_claim else ""
    cur = admin.execute(f"SELECT id::text AS id, queue, func, state, attempts, kwargs::text AS kwargs, "
                        f"completed_at IS NOT NULL AS completed{claim} FROM {schema}.chancy_jobs ORDER BY created_at")
    names = [d.name for d in cur.description]
    jobs = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    cur = admin.execute(f"SELECT * FROM {schema}.chancy_schema_version")
    names = [d.name for d in cur.description]
    return {"jobs": jobs, "schema_version": [{k: str(v) for k, v in zip(names, r, strict=True)}
                                             for r in cur.fetchall()]}


def chancy_mixed_check(final: dict[str, Any], phases: list[dict[str, Any]]) -> list[str]:
    """Independent of either version's code, from Chancy's documented job states and the workload:
    M1 exactly the pushed jobs exist; M2 each reached 'succeeded', with a completion time, after one attempt;
    M3 across all workers of both versions, each pushed job's handler ran exactly once; M4 the schema ends at the
    new release's version (8)."""
    out = []
    pushed, ns, handled = [], [], []
    for ph in phases:
        for name, res in (ph.get("raw_results") or {}).items():
            res = res or {}
            pushed += res.get("job_ids", [])
            if name.endswith("worker"):
                handled += res.get("handled", [])
    for ph in phases:
        ns += ph.get("ns", [])
    jobs = final["jobs"]
    if sorted(j["id"] for j in jobs) != sorted(pushed) or not pushed:
        out.append(f"M1 {len(jobs)} jobs in the database, {len(pushed)} pushed")
    bad = [(j["id"][:8], j["state"], j["attempts"]) for j in jobs
           if j["state"] != "succeeded" or j["attempts"] != 1 or not j["completed"]]
    if bad:
        out.append(f"M2 not succeeded once: {bad}")
    if sorted(handled) != sorted(ns):
        out.append(f"M3 handlers ran for {sorted(handled)}, pushed {sorted(ns)}")
    versions = [r.get("version") for r in final.get("schema_version", [])]
    if "8" not in versions:
        out.append(f"M4 schema version rows {final.get('schema_version')}")
    return out


MIXED_DEFAULT = {"setup_jobs": 4, "old_push_jobs": 2, "old_worker_handles": 3, "new_push_jobs": 2}


def validate_mixed_workload(w: Any) -> list[str]:
    """Deterministic. A structured rolling-deploy workload: four bounded integers, nothing else."""
    bounds = {"setup_jobs": (1, 5), "old_push_jobs": (0, 3), "old_worker_handles": (0, 8), "new_push_jobs": (0, 3)}
    if not isinstance(w, dict) or set(w) != set(bounds):
        return [f"shape: exactly {sorted(bounds)}"]
    out = []
    for k, (lo, hi) in bounds.items():
        if not isinstance(w[k], int) or isinstance(w[k], bool) or not lo <= w[k] <= hi:
            out.append(f"{k}: an int in {lo}..{hi}")
    return out


def run_chancy_mixed(target: hz.Target, *, old_sdist: Path, new_sdist: Path, out: Path,
                     workload: dict[str, Any] | None = None, images: dict[str, Any] | None = None) -> dict[str, Any]:
    """The NONBLIND mixed-version pilot: Chancy 0.25.1 (old) and 0.26.0 (new) on one schema, as a rolling deploy:
    1. old: migrate to v6, declare the queue, push jobs 0..3;
    2. new: apply v7 and v8 (the deploy's migration step);
    3. old, on the new schema: a producer pushes jobs 4..5 while an old worker handles jobs (stopping once it has
       handled 3), the two scheduled against each other;
    4. new: push jobs 6..7, then a new worker drains every remaining job.
    Each phase is one isolated container, one stepped run per step, corroborated against the server log."""
    w = workload or MIXED_DEFAULT
    if validate_mixed_workload(w):
        raise RuntimeError(f"invalid workload: {validate_mixed_workload(w)}")
    images = dict(images or {})
    for label, sdist in (("old", old_sdist), ("new", new_sdist)) if not images else ():
        build_dir = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
        try:
            images[label] = build_image(build_dir, chancy_sdist=sdist)
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)
    a_n, b_n, c_n = w["setup_jobs"], w["old_push_jobs"], w["new_push_jobs"]
    setup_ns, old_ns, new_ns = list(range(a_n)), list(range(a_n, a_n + b_n)), list(range(a_n + b_n, a_n + b_n + c_n))
    old_actors = ([{"actor": "old_push", "fn": "push", "args": {"ns": old_ns}}] if old_ns else []) + [
        {"actor": "old_worker", "fn": "work",  # capped at the jobs that exist, so it never idles to its timeout
         "args": {"handle_at_least": min(w["old_worker_handles"], a_n + b_n)}}]
    plans = [("1_old_setup", "old", [{"actors": [{"actor": "old_setup", "fn": "setup", "args": {"ns": setup_ns}}]}],
              setup_ns),
             ("2_new_migrate", "new", [{"actors": [{"actor": "new_migrate", "fn": "migrate"}]}], []),
             ("3_old_on_new_schema", "old", [{"actors": old_actors,
                                              "schedule": [a["actor"] for a in old_actors] * 200}], old_ns),
             ("4_new", "new", [{"actors": [{"actor": "new_push", "fn": "push", "args": {"ns": new_ns}}]}]
              if new_ns else [], new_ns)]
    phases = []
    pushed: list[str] = []
    for i, (name, which, plan, ns) in enumerate(plans):
        if name == "4_new":  # the new worker waits for every job pushed so far, and for the new pushes before it
            plan = plan + [{"actors": [{"actor": "new_worker", "fn": "work",
                                        "args": {"handle_at_least": 10 ** 6, "wait_for": list(pushed),
                                                 "include_earlier_runs": True}}]}]
        plan_json = json.dumps(plan)
        phase_dir = out / name
        phase_dir.mkdir()
        r = run_smoke(target, variant=name, schedule=[], out=phase_dir, image=images[which], mode="chancy-plan",
                      fresh_schema=(i == 0), keep_schema=(i < len(plans) - 1),
                      extra_env={"DS_CHANCY_PLAN": plan_json})
        r["image"], r["ns"] = images[which], ns
        phases.append(r)
        for res in (r.get("raw_results") or {}).values():
            pushed += (res or {}).get("job_ids", [])
        if not (r.get("isolation_ok") and (r.get("corroboration") or {}).get("ok")):
            break
    final = phases[-1].get("final_state", {"jobs": []})
    return {"phases": phases, "oracle_violations": chancy_mixed_check(final, phases), "final_state": final}


def chancy_concurrent_check(final: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    """Independent of the adapter: K1 exactly the pushed jobs exist and every one 'succeeded' after one attempt;
    K2 the workers together handled every job exactly once (no job ran twice, none was lost)."""
    out = []
    pushed = sorted((raw.get("produce") or {}).get("job_ids", []))
    jobs = final["jobs"]
    if sorted(j["id"] for j in jobs) != pushed or not pushed:
        out.append(f"K1 jobs {sorted(j['id'] for j in jobs)}, pushed {pushed}")
    bad = [(j["id"], j["state"], j["attempts"]) for j in jobs if j["state"] != "succeeded" or j["attempts"] != 1
           or not j["completed"]]
    if bad:
        out.append(f"K1 not succeeded once: {bad}")
    handled = sorted(n for w in ("w1", "w2") for n in (raw.get(w) or {}).get("handled_here", []))
    if handled != list(range(len(pushed))):
        out.append(f"K2 the workers handled {handled}, expected each of 0..{len(pushed) - 1} once")
    return out


def chancy_check(final: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    """Independent of the adapter and of Chancy's code, from its documented job states and the workload:
    C1 exactly one job exists, the one pushed, on the declared queue; C2 it reached 'succeeded' with a completion time
    after one attempt; C3 the trusted handler ran exactly once, with the pushed argument."""
    out = []
    result = raw.get("chancy") or {}
    jobs = final["jobs"]
    if len(jobs) != 1 or jobs[0]["id"] != result.get("job_id") or jobs[0]["queue"] != "ds_q":
        out.append(f"C1 jobs {[(j['id'], j['queue']) for j in jobs]}, pushed {result.get('job_id')}")
    elif jobs[0]["state"] != "succeeded" or not jobs[0]["completed"] or jobs[0]["attempts"] != 1:
        out.append(f"C2 job state {jobs[0]['state']}, completed {jobs[0]['completed']}, "
                   f"attempts {jobs[0]['attempts']}")
    if result.get("handled") != [21]:
        out.append(f"C3 handler ran with {result.get('handled')}, expected [21] once")
    return out


def validate_pgq_workload(w: Any) -> list[str]:
    """Deterministic. A structured PGQueuer workload: 1 to 5 jobs, each a short lowercase payload and a priority in
    0..9, and a worker batch size in 1..3. Nothing else is accepted; nothing is repaired."""
    import re
    if not isinstance(w, dict) or set(w) != {"jobs", "batch_size"}:
        return ["shape: exactly 'jobs' and 'batch_size'"]
    out = []
    if not isinstance(w["batch_size"], int) or isinstance(w["batch_size"], bool) or not 1 <= w["batch_size"] <= 3:
        out.append("batch_size: an int in 1..3")
    jobs = w["jobs"]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 5:
        return out + ["jobs: 1 to 5"]
    for j in jobs:
        if not isinstance(j, dict) or set(j) != {"payload", "priority"}:
            out.append("job: exactly 'payload' and 'priority'")
            continue
        if not isinstance(j["payload"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", j["payload"]):
            out.append(f"payload {j['payload']!r}: ^[a-z][a-z0-9_]{{0,15}}$")
        if not isinstance(j["priority"], int) or isinstance(j["priority"], bool) or not 0 <= j["priority"] <= 9:
            out.append(f"priority {j['priority']!r}: an int in 0..9")
    return out


def pgqueuer_check(final: dict[str, Any], raw: dict[str, Any], workload: dict[str, Any]) -> list[str]:
    """Independent of the adapter and of PGQueuer's code, from its documented lifecycle and the workload:
    Q1 every enqueued payload was handled exactly once, under its own job id (duplicates counted);
    Q2 no job for the entrypoint is left in
    the queue; Q3 each job's log has exactly one 'successful' and no 'exception' or 'failed'."""
    out = []
    missing = [t["table"] for t in final["tables"] if not t["present"]]
    if missing:
        return [f"Q0 the lifecycle's tables don't all exist: {missing}"]
    enqueued = (raw.get("enqueue") or {}).get("job_ids", [])
    want = sorted(zip(enqueued, [j["payload"] for j in workload["jobs"]], strict=False))
    got = sorted((r["job_id"], r["payload"]) for r in final["results"])
    if got != want:
        out.append(f"Q1 handled {got}, but enqueued {want}")
    left = [r for r in final["queue"] if r["entrypoint"] == "ds_job"]
    if left:
        out.append(f"Q2 {len(left)} job(s) still in the queue: {left}")
    for job in enqueued:
        statuses = [r["status"] for r in final["log"] if r["job_id"] == job]
        if statuses.count("successful") != 1 or {"exception", "failed"} & set(statuses):
            out.append(f"Q3 job {job} logged {statuses}")
    if not enqueued:
        out.append("Q1 nothing was enqueued")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["smoke", "chancy", "pgqueuer", "chancy-runtime", "chancy-mixed"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--chancy-sdist", type=Path, help="the downloaded chancy-0.26.0.tar.gz (sha256 is re-verified)")
    ap.add_argument("--chancy-old-sdist", type=Path, help="chancy-mixed: the downloaded chancy-0.25.1.tar.gz")
    ap.add_argument("--pgqueuer-sdist", type=Path, help="the downloaded pgqueuer-1.6.0.tar.gz (sha256 re-verified)")
    ap.add_argument("--public-factory", action="store_true",
                    help="pgqueuer negative control: PGQueuer's own driver, which the server log must refute")
    ap.add_argument("--mutant", choices=sorted(MUTANTS), help="chancy-runtime --concurrent-workers: a declared mutant")
    ap.add_argument("--concurrent-workers", action="store_true",
                    help="chancy-runtime: a producer, then two workers, each its own actor and stepped connection")
    ap.add_argument("--with-notifications", action="store_true",
                    help="chancy-runtime negative control: the worker's own LISTEN connection must be refuted")
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    target.conninfo()  # refuses anything but the disposable database
    a.out.mkdir(parents=True, exist_ok=False)
    if a.mode == "chancy-mixed":
        result = run_chancy_mixed(target, old_sdist=a.chancy_old_sdist, new_sdist=a.chancy_sdist, out=a.out)
        report = {"kind": "EXPERIMENTAL NONBLIND development pilot: Chancy 0.25.1 and 0.26.0 on one schema as a "
                         "rolling deploy, in the isolated runner. Not a blind or confirmatory result",
                  "library": {"old": {"version": "0.25.1", "sdist_sha256": CHANCY_SDISTS["chancy-0.25.1.tar.gz"],
                                      "tag_commit": "b0e3e94fd12a567e47568d75f9d2d1d8fd996ea1"},
                              "new": {"version": "0.26.0", "sdist_sha256": CHANCY_SDIST_SHA256,
                                      "tag_commit": "73fd582c0e5d2f8655316e09f189ba0c57d76ca7"}},
                  "migration_area": "chancy/migrations/v8.py (frozen rule), with v7; the new release adds both",
                  "exposure": "NONBLIND: v7/v8 bodies, app/worker/migrate/executor modules read; no issue, "
                              "changelog or fix commit read",
                  **result}
        (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        sound = True
        for ph in result["phases"]:
            c = ph.get("corroboration") or {}
            good = bool(ph.get("isolation_ok") and c.get("ok") and all(ph["teardown"].values()))
            sound &= good
            print(ph["variant"], "isolation", ph.get("isolation_ok"), "corroborated", c.get("ok"), "statements",
                  c.get("matched"), "compared", c.get("params_compared"), "mismatched", c.get("params_mismatched"),
                  "unchecked", c.get("params_unchecked"), "results", {k: {x: y for x, y in (v or {}).items()
                                                                          if x != "job_ids"} if isinstance(v, dict)
                                                                      else v for k, v in
                                                                      (ph.get("raw_results") or {}).items()},
                  "problems", (c.get("problems") or [])[:3], "stderr", (ph.get("stderr_tail") or "")[-400:])
        print("oracle", result["oracle_violations"], "jobs", [(j["state"], j["attempts"], j.get("has_claim_id"))
                                                              for j in result["final_state"].get("jobs", [])])
        return 0 if sound and len(result["phases"]) == 4 else 1
    race = ["j", "j", "a", "a", "a", "a", "a", "j", "j"]
    build_dir = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
    try:
        image = build_image(build_dir, chancy_sdist=a.chancy_sdist if a.mode in ("chancy", "chancy-runtime") else None,
                            mutant=a.mutant,
                            pgqueuer_sdist=a.pgqueuer_sdist if a.mode == "pgqueuer" else None)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)
    if a.mode == "chancy-runtime" and a.concurrent_workers:
        env = {"DS_CHANCY_MUTANT": mutant_declaration(a.chancy_sdist, a.mutant, "0.26.0")} if a.mutant else {}
        r = run_smoke(target, variant="chancy_concurrent" + (f"_mutant_{a.mutant}" if a.mutant else ""), schedule=[],
                      out=a.out, image=image, mode="chancy-runtime", chancy_variant="concurrent", extra_env=env)
        r["mutant"] = {"id": a.mutant, "declaration": env.get("DS_CHANCY_MUTANT"),
                       "description": MUTANTS[a.mutant][3]} if a.mutant else None
        c = r.get("corroboration", {})
        report = {"kind": "EXPERIMENTAL: Chancy 0.26.0, two workers as two actors (each its own Chancy instance and "
                         "stepped connection) draining four jobs concurrently, in the isolated runner",
                  "image": {"tag": image[0], "id": image[1]}, "run": r}
        (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        raw = r.get("raw_results") or {}
        print("isolation", r.get("isolation_ok"), "corroborated", c.get("ok"), "statements", c.get("matched"),
              "compared", c.get("params_compared"), "mismatched", c.get("params_mismatched"), "unchecked",
              c.get("params_unchecked"), "oracle", r.get("oracle_violations"), "w1", (raw.get("w1") or {}).get(
                  "handled_here"), "w2", (raw.get("w2") or {}).get("handled_here"), "sessions", c.get("sessions"),
              "teardown", r["teardown"], "problems", (c.get("problems") or [])[:4],
              "stderr", (r.get("stderr_tail") or "")[-500:])
        if a.mutant:  # for a mutant the verdict is the oracle's; the run itself must still be sound
            return 0 if (r.get("isolation_ok") and c.get("ok") and all(r["teardown"].values())) else 1
        return 0 if (r.get("isolation_ok") and c.get("ok") and r.get("oracle_violations") == []
                     and all(r["teardown"].values())) else 1
    if a.mode == "chancy-runtime" and a.with_notifications:
        r = run_smoke(target, variant="chancy_runtime_notifications", schedule=[], out=a.out, image=image,
                      mode="chancy-runtime", chancy_variant="notifications")
        c = r.get("corroboration", {})
        report = {"kind": "EXPERIMENTAL negative control: Chancy 0.26.0 with notifications on; its worker opens its "
                         "own LISTEN connection outside the stepped pool",
                  "image": {"tag": image[0], "id": image[1]}, "run": r}
        (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        refuted = c.get("ok") is False and any("unexplained session" in p for p in c.get("problems", []))
        print("isolation", r.get("isolation_ok"), "refuted by the server log", refuted, "problems",
              (c.get("problems") or [])[:3], "teardown", r["teardown"])
        return 0 if (r.get("isolation_ok") and refuted and all(r["teardown"].values())) else 1
    if a.mode == "chancy-runtime":
        r = run_smoke(target, variant="chancy_runtime", schedule=[], out=a.out, image=image, mode="chancy-runtime")
        report = {"kind": "EXPERIMENTAL: Chancy 0.26.0's job runtime through a version-pinned one-connection pool "
                         "adapter on the async stepping engine, in the isolated runner; a compatibility test, not a "
                         "blind evaluation",
                  "library": {"name": "chancy", "version": "0.26.0", "sdist_sha256": CHANCY_SDIST_SHA256,
                              "tag_commit": "73fd582c0e5d2f8655316e09f189ba0c57d76ca7",
                              "adapter": "deployspecter.experimental.thirdparty.chancy_adapter",
                              "options": {"notifications": False, "no_default_plugins": True,
                                          "executor": "AsyncExecutor", "pool": "one stepped connection"}},
                  "image": {"tag": image[0], "id": image[1]}, "run": r}
        (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        c = r.get("corroboration", {})
        print("isolation", r.get("isolation_ok"), "corroborated", c.get("ok"), "statements", c.get("matched"),
              "values", c.get("params_compared"), "unchecked", c.get("params_unchecked"), "oracle",
              r.get("oracle_violations"), "result", {k: v for k, v in (r.get("raw_results") or {}).get(
                  "chancy", {}).items() if k != "pinned_sources"} if r.get("raw_results") else None,
              "teardown", r["teardown"], "problems", (c.get("problems") or [])[:5],
              "stderr", (r.get("stderr_tail") or "")[-600:])
        return 0 if (r.get("isolation_ok") and c.get("ok") and r.get("oracle_violations") == []
                     and all(r["teardown"].values())) else 1
    if a.mode == "pgqueuer" and a.public_factory:
        r = run_smoke(target, variant="pgqueuer_public_factory", schedule=[], out=a.out, image=image,
                      mode="pgqueuer", pgq_variant="public_factory")
        c = r.get("corroboration", {})
        report = {"kind": "EXPERIMENTAL negative control: PGQueuer 1.6.0's own Queries.from_psycopg_connection on a "
                         "stepped connection; its driver's directly built AsyncRawCursor bypasses the gate",
                  "image": {"tag": image[0], "id": image[1]}, "run": r}
        (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        refuted = c.get("ok") is False and any("doesn't record" in p for p in c.get("problems", []))
        print("isolation", r.get("isolation_ok"), "refuted by the server log", refuted, "problems",
              (c.get("problems") or [])[:3], "teardown", r["teardown"])
        return 0 if (r.get("isolation_ok") and refuted and all(r["teardown"].values())) else 1
    if a.mode == "pgqueuer":
        r = run_smoke(target, variant="pgqueuer_lifecycle", schedule=[], out=a.out, image=image, mode="pgqueuer")
        report = {"kind": "EXPERIMENTAL: PGQueuer 1.6.0's queue lifecycle through a version-pinned Driver adapter on "
                         "the async stepping engine, in the isolated runner; a compatibility test, not a blind "
                         "evaluation",
                  "library": {"name": "pgqueuer", "version": "1.6.0", "sdist_sha256": PGQUEUER_SDIST_SHA256,
                              "tag_commit": "efcd65689a49a067e68662702f4c5fbee1235df0",
                              "entrypoints": ["Queries(driver).install()", "Queries(driver).enqueue(...)",
                                              "QueueManager(connection=driver).run(mode=drain)"],
                              "adapter": "deployspecter.experimental.thirdparty.pgqueuer_adapter",
                              "requirements_sha256": hashlib.sha256(PGQUEUER_REQUIREMENTS.read_bytes()).hexdigest()},
                  "image": {"tag": image[0], "id": image[1]}, "run": r}
        (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        c = r.get("corroboration", {})
        print("isolation", r.get("isolation_ok"), "corroborated", c.get("ok"), "statements", c.get("matched"),
              "values", c.get("params_compared"), "unchecked", c.get("params_unchecked"), "oracle",
              r.get("oracle_violations"), "result", r.get("raw_results"), "teardown", r["teardown"],
              "problems", (c.get("problems") or [])[:5])
        return 0 if (r.get("isolation_ok") and c.get("ok") and r.get("oracle_violations") == []
                     and all(r["teardown"].values())) else 1
    if a.mode == "chancy":
        r = run_smoke(target, variant="chancy_migrate", schedule=[], out=a.out, image=image, mode="chancy")
        report = {"kind": "EXPERIMENTAL capture of one pinned third-party library's migration path in the isolated "
                         "runner; not a blind evaluation, no defect hypothesis, no oracle",
                  "library": {"name": "chancy", "version": "0.26.0", "sdist_sha256": CHANCY_SDIST_SHA256,
                              "tag_commit": "73fd582c0e5d2f8655316e09f189ba0c57d76ca7",
                              "entrypoint": "chancy.migrate.Migrator('chancy', 'chancy.migrations', "
                                            "prefix='chancy_').migrate(conn)"},
                  "image": {"tag": image[0], "id": image[1]}, "run": r}
        (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        print("isolation", r.get("isolation_ok"), "corroborated", r.get("corroboration", {}).get("ok"),
              "statements", r.get("corroboration", {}).get("matched"), "tables",
              len({row["table"] for row in r.get("final_state", {}).get("catalog", [])}), "result",
              r.get("raw_results"), "teardown", r["teardown"])
        return 0 if (r.get("isolation_ok") and r.get("corroboration", {}).get("ok")
                     and all(r["teardown"].values())) else 1
    report = {"kind": "EXPERIMENTAL isolated-runner smoke test on a trusted fixture; not a hostile-code sandbox",
              "image": {"tag": image[0], "id": image[1]},
              "runs": {v: run_smoke(target, variant=v, schedule=race, out=a.out, image=image)
                       for v in ("buggy", "fixed")}}
    (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    ok = True
    for v, r in report["runs"].items():
        good = (r.get("isolation_ok") and r.get("corroboration", {}).get("ok") and all(r["teardown"].values())
                and bool(r.get("oracle_violations")) == (v == "buggy"))
        ok &= bool(good)
        print(v, "isolation", r.get("isolation_ok"), "corroborated", r.get("corroboration", {}).get("ok"),
              "oracle", r.get("oracle_violations"), "teardown", r["teardown"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
