"""EXPERIMENTAL, NONBLIND reviewed integration ``pgqueuer-cancel-sv/1`` (ds-nbc/1): PGQueuer under its documented
single-version contract, with producers that enqueue and cancel jobs and workers that run them.

**Each run, for one opaque revision archive** (R1 or R2 of a case):
1. A fresh, empty schema (``pgq_capture``), checked empty by the host.
2. One restricted container holding that revision, plus the coordinator (no credentials). Its actors, each on its
   own stepped connection: ``setup`` (PGQueuer's own ``Queries.install``), one or two workers (``QueueManager``
   with entrypoint ``ds_job`` and the workload's ``concurrency_limit``) and a producer (public ``Queries.enqueue``,
   then ``Queries.mark_job_as_cancelled`` for the listed jobs).
3. Every statement is granted by the coordinator under the seeded explorer (the given seed). The integration's gates
   only make the operations meaningful: nothing runs before the install; the producer waits until every worker has
   polled once; a cancel is issued only once the first worker has claimed a job, and the other workers start
   claiming only after that first claim (workers are identical, so this only names which one claims first). The
   explorer decides everything else.
4. The server log corroborates every statement; the host reads the final state independently; the **approved
   oracle** (sha256 checked) decides, and only on a SOUND run.

**Image.** Built here, not by ``run_isolated.build_image`` (whose PGQueuer path accepts only the 1.6.0 sdist): the
same ``Dockerfile.runner``, the same hash-locked ``requirements.pgqueuer.txt`` (the dependency specifiers of these
revisions are 1.6.0's), the runner's ``PAYLOAD`` plus the declared adapter variant ``pgqueuer_hist_adapter``, and the
pinned tree's ``pgqueuer/`` extracted data-only. ``/app/smoke_inside.py`` is ``pgqueuer_inside.py``, which keeps the
runner's ``smoke_inside.py`` byte-identical beside it and adds one mode. Containers run with the runner's own
``RUNTIME_FLAGS`` through ``run_coordinated.simultaneous`` (unchanged).

**Workload** (``WORKLOAD_JSON_SCHEMA``): 1 to 3 jobs ``{n, priority, cancel}`` (distinct ``n``), ``workers`` 1 or 2,
``concurrency_limit`` 0 to 2, ``handler_steps`` 0 to 2. A workload names no SQL, Python, gate or schedule.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
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

NAME = "pgqueuer-cancel-sv/1"
SECRET = Path.home() / ".ds_mvp_pg_password"
SCHEMA = "pgq_capture"
HERE = Path(__file__).resolve().parent
INSIDE = HERE / "pgqueuer_inside.py"
DDL_FILE = HERE / "pgqueuer_cancel_r1_schema.sql"
ADAPTER_REL = "deployspecter/experimental/thirdparty/pgqueuer_hist_adapter.py"
# statement anchors in the pinned trees' own SQL (checked in each archive's qb.py at prepare)
DEQUEUE_RX = r"Unpack per-entrypoint parameters|No entrypoint carries a concurrency limit"
LOG_JOB_RX = r"WITH job_status AS"
INSTALLED_RX = r"CREATE TABLE IF NOT EXISTS ds_pgq_runs"
ANCHORS = ("Unpack per-entrypoint parameters", "No entrypoint carries a concurrency limit", "WITH job_status AS (")
MARKERS = {"installed": {"regex": INSTALLED_RX}, "fetch": {"regex": DEQUEUE_RX},
           "claim": {"regex": DEQUEUE_RX, "rows": True}}
WORKERS = ("w1", "w2")

_N = list(range(10))
WORKLOAD_JSON_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["jobs", "workers", "concurrency_limit", "handler_steps"],
    "properties": {
        "jobs": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False, "required": ["n", "priority", "cancel"],
            "properties": {"n": {"type": "integer", "enum": _N}, "priority": {"type": "integer", "enum": _N},
                           "cancel": {"type": "boolean"}}}},
        "workers": {"type": "integer", "enum": [1, 2]},
        "concurrency_limit": {"type": "integer", "enum": [0, 1, 2]},
        "handler_steps": {"type": "integer", "enum": [0, 1, 2]},
    },
}


def _ddl() -> str:
    return DDL_FILE.read_text() if DDL_FILE.exists() else ""


MUSE_CONTEXT: dict[str, Any] = {
    "application": "PGQueuer (a PostgreSQL job queue for Python), one release at a time on a fresh schema",
    "operations": {
        "produce": {
            "what": "One producer. For each job in order: Queries.enqueue('ds_job', payload=str(n), priority). Then, "
                    "if any job has cancel=true, one Queries.mark_job_as_cancelled([ids of those jobs]) call. The "
                    "cancel call is issued after the first worker has claimed a job.",
            "arguments": {"jobs": "1 to 3 jobs, each {n: 0..9 (distinct), priority: 0..9, cancel: bool}"},
        },
        "work": {
            "what": "`workers` workers, each its own QueueManager and database session, entrypoint 'ds_job' "
                    "registered with `concurrency_limit`; dequeue batch size 1, continuous mode until every job is "
                    "resolved. The handler writes a 'start' row, then `handler_steps` 'step' rows, then an 'end' "
                    "row (one statement each) and does not check the cancellation scope.",
            "arguments": {"workers": "1 or 2", "concurrency_limit": "0, 1 or 2", "handler_steps": "0, 1 or 2"},
        },
    },
    "documented_semantics": {
        "QueueManager.entrypoint(name, concurrency_limit=...)": "concurrency_limit=0 means unlimited; 1 serialises "
            "dispatch. Limits are enforced at the database level across all workers.",
        "Queries.enqueue": "Insert one or many jobs; payload bytes, priority int (higher runs first).",
        "Queries.mark_job_as_cancelled(ids)": "Log ids as 'canceled' and emit a cancellation NOTIFY. Cancellations "
            "are best-effort and may not halt a job already underway. Jobs that have already completed are "
            "unaffected.",
        "job statuses": "queued, picked, successful, exception, failed, canceled, deleted",
    },
    "schema_ddl": _ddl(),
}


def validate(w: Any) -> list[str]:
    keys = {"jobs", "workers", "concurrency_limit", "handler_steps"}
    if not isinstance(w, dict) or set(w) != keys:
        return [f"exactly {sorted(keys)}"]
    p = []

    def small_int(v: Any, allowed: tuple[int, ...]) -> bool:
        return isinstance(v, int) and not isinstance(v, bool) and v in allowed

    if not small_int(w["workers"], (1, 2)):
        p.append("workers: 1 or 2")
    if not small_int(w["concurrency_limit"], (0, 1, 2)):
        p.append("concurrency_limit: 0, 1 or 2")
    if not small_int(w["handler_steps"], (0, 1, 2)):
        p.append("handler_steps: 0, 1 or 2")
    jobs = w["jobs"]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 3:
        return p + ["jobs: 1 to 3"]
    seen: set[int] = set()
    for i, j in enumerate(jobs):
        if not isinstance(j, dict) or set(j) != {"n", "priority", "cancel"}:
            p.append(f"jobs[{i}]: exactly n, priority and cancel")
            continue
        if not small_int(j["n"], tuple(_N)) or j["n"] in seen:
            p.append(f"jobs[{i}].n: a distinct int in 0..9")
        else:
            seen.add(j["n"])
        if not small_int(j["priority"], tuple(_N)):
            p.append(f"jobs[{i}].priority: an int in 0..9")
        if not isinstance(j["cancel"], bool):
            p.append(f"jobs[{i}].cancel: a bool")
    return p


def workloads(arm: str) -> list[dict[str, Any]]:
    """RA-DET: three curated workloads, fixed before any live run (CASE.md). The tightest limit (1), two workers,
    the first-claimed job cancelled; handler length 1 and 2 steps, and a third queued job."""
    if arm == "RA-DET":
        two = [{"n": 0, "priority": 0, "cancel": True}, {"n": 1, "priority": 0, "cancel": False}]
        return [{"jobs": two, "workers": 2, "concurrency_limit": 1, "handler_steps": 1},
                {"jobs": two, "workers": 2, "concurrency_limit": 1, "handler_steps": 2},
                {"jobs": two + [{"n": 2, "priority": 0, "cancel": False}], "workers": 2, "concurrency_limit": 1,
                 "handler_steps": 2}]
    raise ValueError(f"{arm} isn't a deterministic arm of {NAME}")


# ---------------------------------------------------------------------------------------------------- the image

def archive_members(archive: Path) -> dict[str, bytes]:
    """The archive's regular files; refuses anything outside ``pgqueuer/`` or that isn't a file or directory."""
    files = {}
    with tarfile.open(archive) as tf:
        for m in tf.getmembers():
            if not (m.isfile() or m.isdir()) or not (m.name == "pgqueuer" or m.name.startswith("pgqueuer/")) \
                    or ".." in Path(m.name).parts:
                raise ValueError(f"{archive.name}: unexpected member {m.name!r}")
            if m.isfile():
                f = tf.extractfile(m)
                assert f is not None
                files[m.name] = f.read()
    return files


def tree_digest(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()  # the same as pgqueuer_hist_adapter.tree_digest, over the archive
    for rel in sorted(files):
        h.update(rel.encode() + b"\0" + files[rel])
    return h.hexdigest()


def declaration(archive: Path, sha256: str) -> dict[str, str]:
    from deployspecter.experimental.thirdparty import pgqueuer_hist_adapter as ha
    got = hashlib.sha256(archive.read_bytes()).hexdigest()
    if got != sha256:
        raise ValueError(f"{archive.name}: sha256 {got[:12]} isn't the frozen manifest's {sha256[:12]}")
    files = archive_members(archive)
    drv = hashlib.sha256(files.get("pgqueuer/adapters/drivers/psycopg.py", b"")).hexdigest()
    if drv not in ha.REVIEWED_DRIVERS:
        raise ValueError(f"{archive.name}: its psycopg driver ({drv[:12]}) isn't a reviewed one")
    qb = files.get("pgqueuer/adapters/persistence/qb.py", b"").decode()
    if missing := [a for a in ANCHORS if a not in qb]:
        raise ValueError(f"{archive.name}: the integration's statement anchors are missing: {missing}")
    digest = tree_digest(files)
    return {"label": f"tree-{digest[:12]}", "archive_sha256": got, "tree_digest": digest}


def build_image(archive: Path, decl: dict[str, str]) -> tuple[str, str]:
    import run_isolated as ri
    work = Path(tempfile.mkdtemp(prefix="ds_nbc_pgq_build_"))
    try:
        ctx = work / "context"
        src = ctx / "app" / "src"
        src.mkdir(parents=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != decl["archive_sha256"]:
            raise ValueError("the archive changed after its declaration")
        archive_members(archive)  # refuses anything but pgqueuer/ files and directories
        with tarfile.open(archive) as tf:
            tf.extractall(src, filter="data")
        for rel in [*ri.PAYLOAD, ADAPTER_REL]:
            dst = src / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "src" / rel, dst)
        shutil.copyfile(REPO / "research_tools/isolated/smoke_inside.py", ctx / "app" / "ds_inside_base.py")
        shutil.copyfile(INSIDE, ctx / "app" / "smoke_inside.py")
        shutil.copyfile(REPO / "research_tools/isolated/Dockerfile.runner", ctx / "Dockerfile")
        (ctx / "requirements.txt").write_text(ri.PGQUEUER_REQUIREMENTS.read_text())
        digest = hashlib.sha256()
        for f in sorted(p for p in ctx.rglob("*") if p.is_file()):
            digest.update(str(f.relative_to(ctx)).encode() + b"\0" + f.read_bytes())
        tag = f"ds-mvp-iso:nbcpgq-{digest.hexdigest()[:16]}"
        ri.docker("build", "-q", "-t", tag, str(ctx), timeout=900)
        return tag, ri.docker("image", "inspect", "-f", "{{.Id}}", tag).stdout.strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def prepare(archive: Path, sha256: str, workdir: Path) -> dict[str, Any]:
    decl = declaration(archive, sha256)
    workdir.mkdir(parents=True, exist_ok=True)
    image = build_image(archive, decl)
    return {"image": image, "pinned": json.dumps(decl, sort_keys=True), "archive_sha256": decl["archive_sha256"],
            "label": decl["label"], "integration": NAME}


def render_ddl(handle: dict[str, Any]) -> dict[str, Any]:
    """The pinned tree's own install DDL, rendered inside its restricted container with no network at all."""
    import run_isolated as ri
    env = Path(tempfile.mkdtemp(prefix="ds_nbc_pgq_env_")) / "env"
    try:
        env.write_text(f"DS_MODE=pgq_render_ddl\nDS_PGQ_PINNED={handle['pinned']}\n")
        env.chmod(0o600)
        proc = ri.docker("run", "--rm", "--network", "none", *ri.RUNTIME_FLAGS, "--env-file", str(env),
                         handle["image"][0], "/app/smoke_inside.py", timeout=120, check=False)
    finally:
        shutil.rmtree(env.parent, ignore_errors=True)
    if proc.returncode != 0:
        raise RuntimeError(f"render failed ({proc.returncode}): {proc.stderr[-800:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------------------------------- a run

def spec(w: dict[str, Any]) -> dict[str, Any]:
    workers = list(WORKERS[: w["workers"]])
    ns = [j["n"] for j in w["jobs"]]
    cancel = [j["n"] for j in w["jobs"] if j["cancel"]]
    actors = [{"actor": "setup", "fn": "install"}]
    actors += [{"actor": a, "fn": "work", "args": {"concurrency_limit": w["concurrency_limit"],
                                                  "steps": w["handler_steps"], "timeout_s": 120}} for a in workers]
    actors.append({"actor": "prod", "fn": "produce",
                   "args": {"jobs": [{"n": j["n"], "priority": j["priority"]} for j in w["jobs"]], "cancel": cancel}})
    installed = [{"actor": "setup", "marker": "installed", "count": 1}]
    first_claim = [{"actor": "w1", "marker": "claim", "count": 1}]
    gates = [{"actor": a, "match": None, "until": installed} for a in [*workers, "prod"]]
    gates.append({"actor": "prod", "match": None,
                  "until": [{"actor": a, "marker": "fetch", "count": 1} for a in workers]})
    if cancel:
        gates.append({"actor": "prod", "match": LOG_JOB_RX, "until": first_claim})
    for a in workers[1:]:
        gates.append({"actor": a, "match": DEQUEUE_RX, "from": [{"actor": a, "marker": "fetch", "count": 1}],
                      "until": first_claim})
    return {"processes": {"A": actors}, "gates": gates, "covers": ns, "consumer_of": {}}


def state(admin: Any) -> dict[str, Any]:
    def rows(q: str) -> list[dict[str, Any]]:
        cur = admin.execute(q)
        names = [d.name for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def exists(t: str) -> bool:
        return bool(admin.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{SCHEMA}.{t}",)).fetchone()[0])

    tables = [r["table_name"] for r in rows(f"SELECT table_name FROM information_schema.tables WHERE table_schema = "
                                             f"'{SCHEMA}' ORDER BY table_name")]
    out: dict[str, Any] = {"tables": tables, "queue": [], "log": [], "runs": []}
    if exists("pgqueuer"):
        out["queue"] = rows(f"SELECT id, entrypoint, status::text AS status, priority, "
                            f"queue_manager_id IS NOT NULL AS owned FROM {SCHEMA}.pgqueuer ORDER BY id")
    if exists("pgqueuer_log"):
        out["log"] = rows(f"SELECT id, job_id, status::text AS status, entrypoint FROM {SCHEMA}.pgqueuer_log "
                          f"ORDER BY id")
    if exists("ds_pgq_runs"):
        out["runs"] = rows(f"SELECT seq, job_id, n, worker, exec, phase FROM {SCHEMA}.ds_pgq_runs ORDER BY seq")
    return out


class App:
    """``run_coordinated.simultaneous``'s application hooks for this integration."""
    schema = SCHEMA
    inside_mode = "pgq_hist_coordinated"
    markers = MARKERS

    @staticmethod
    def state(admin: Any) -> dict[str, Any]:
        return state(admin)

    @staticmethod
    def precondition(initial: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        if initial["tables"]:
            raise RuntimeError(f"the schema isn't empty before the run: {initial['tables']}")
        return {"tables_before": 0}

    @staticmethod
    def check(final: dict[str, Any], results: dict[str, Any], spec: dict[str, Any], quiescent: bool) -> dict:
        return {"note": "decided by the approved case oracle, not here", "quiescent": quiescent}


def fresh_schema(target: Any) -> None:
    admin = target.admin()
    try:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")
        if admin.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
                         (SCHEMA,)).fetchone()[0]:
            raise RuntimeError("the fresh schema isn't empty")
    finally:
        admin.close()


def load_oracle(path: Path, sha256: str) -> Any:
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError("the oracle code isn't the approved one")
    s = importlib.util.spec_from_file_location(f"ds_nbc_oracle_{sha256[:12]}", path)
    assert s is not None and s.loader is not None
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def run(handle: dict[str, Any], workload: dict[str, Any], seed: int, out: Path, *, oracle_code: Path,
        oracle_sha256: str) -> dict[str, Any]:
    import run_coordinated as rc

    from deployspecter.experimental import harness as hz
    if p := validate(workload):
        return {"verdict": "REFUSED", "seed": seed, "corroboration": {"ok": False}, "reason": p}
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", SECRET.read_text().strip())
    out.mkdir(parents=True)
    s = spec(workload)
    report: dict[str, Any] = {"RA-NONBLIND": True, "integration": NAME, "seed": seed, "workload": workload,
                              "spec": s, "image": list(handle["image"]), "declaration": json.loads(handle["pinned"]),
                              "integration_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        fresh_schema(target)
        sim = rc.simultaneous(target, {"A": handle["image"], "env": {"A": {"DS_PGQ_PINNED": handle["pinned"]}}}, s,
                              {"kind": "seeded", "seed": seed}, out, label=f"RA-NONBLIND {NAME}", app=App)
    except Exception as exc:  # a refused run, recorded; never retried
        report.update(verdict="REFUSED", reason=f"{type(exc).__name__}: {exc}")
        (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        return {"verdict": "REFUSED", "seed": seed, "corroboration": {"ok": False}, "reason": report["reason"]}
    c = sim.get("corroboration") or {}
    corr = {k: c.get(k) for k in ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked")}
    report["run"] = sim
    res: dict[str, Any] = {"verdict": sim["verdict"], "seed": seed, "corroboration": corr}
    if sim["verdict"] != "SOUND":
        res["reason"] = sim.get("refusal_reasons") or ("incomplete" if sim["verdict"] == "INCOMPLETE" else None)
    else:
        oracle = load_oracle(oracle_code, oracle_sha256)
        res["oracle"] = oracle.check(sim["final_state"], sim["raw_results"])
        if hasattr(oracle, "explain"):
            report["oracle_explain"] = oracle.explain(sim["final_state"], sim["raw_results"])
    report.update(result=res)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    return res


def anchors_present(text: str) -> bool:
    return all(re.search(rx, text) for rx in (DEQUEUE_RX, LOG_JOB_RX))
