"""EXPERIMENTAL, NONBLIND: Chancy 0.25.1 and 0.26.0 workers running AT THE SAME TIME against one disposable
PostgreSQL, each in its own restricted container, every database statement granted by one coordinator (ds-coord/1).

    python research_tools/isolated/run_coordinated.py run --scenario compete --policy '{"kind":"seeded","seed":1}' \
        --chancy-old-sdist SDISTS/chancy-0.25.1.tar.gz --chancy-sdist SDISTS/chancy-0.26.0.tar.gz --out DIR
    python research_tools/isolated/run_coordinated.py explore --scenario compete --seeds 0-7 ... --out DIR

Each run:
1. Pre-phases, each one sequential container, corroborated:
   - 0.25.1 migrates to its own latest schema and declares q_old, q_new and q_shared;
   - 0.26.0 applies its migrations (v7, v8).
   The migration is complete before either version's worker starts. The host checks schema version 8.
2. The simultaneous phase. Three containers on one internal network:
   - the coordinator, with no database credentials;
   - A, holding 0.25.1;
   - B, holding 0.26.0.
   Both workers start and must each complete a claim query (``ready``) before any producer statement is granted
   (the window gate). Producers then push through the public API; workers claim, run and complete jobs. The
   coordinator sends ``stop`` when the expected jobs have succeeded, and workers stop at safe points (``quiesced``).
3. The host corroborates the merged trace of both processes against the server log, in one window:
   - every session, statement, typed parameter and error;
   - the global grant order.
   It then reads the final state independently, and checks the frozen properties
   (``docs/experimental_mvp/simultaneous/PROPERTIES.md``) only for a run that is isolated, coordinated to
   completion and corroborated. Anything else is REFUSED, with no property verdict, and is never retried.
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
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_isolated as ri  # noqa: E402
from signal_safe import deferred_signals  # noqa: E402

from deployspecter.experimental import chancy_properties as cp  # noqa: E402
from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import oracle  # noqa: E402
from deployspecter.experimental.coordinated import merge_traces  # noqa: E402
from deployspecter.experimental.corroborate import corroborate  # noqa: E402

REPO = ri.REPO
SCHEMA = ri.CAPTURE_SCHEMA
OLD, NEW = "0.25.1", "0.26.0"
MARKERS = {"fetch": {"regex": "WITH selected_jobs AS"}, "claim": {"regex": "WITH selected_jobs AS", "rows": True},
           "push": {"regex": 'INSERT INTO "chancy_jobs"'}, "cancel": {"regex": "SET state = 'failed'"}}
FLUSH = r'UPDATE "chancy_jobs" SET state = %\(state\)s'
FROZEN = {"properties": "docs/experimental_mvp/simultaneous/PROPERTIES.md",
          "checker": "src/deployspecter/experimental/chancy_properties.py"}


def _window(producers: list[str], workers: list[str]) -> list[dict[str, Any]]:
    """No producer statement before every worker has completed a claim query."""
    return [{"actor": p, "match": None, "until": [{"actor": w, "marker": "fetch", "count": 1} for w in workers]}
            for p in producers]


def scenario(name: str) -> dict[str, Any]:
    """The scenario catalogue (declared before any run). ``processes``: which containers run which actors."""
    A_W = {"actor": "old_worker", "fn": "work", "args": {"timeout_s": 60}}
    B_W = {"actor": "new_worker", "fn": "work", "args": {"timeout_s": 60}}
    both = ["old_worker", "new_worker"]
    if name == "old_to_new":  # old producer, new consumer (both workers running)
        return {"processes": {"A": [A_W, {"actor": "old_push", "fn": "push", "args": {"ns": [0, 1, 2],
                                                                                      "queue": "q_new"}}],
                              "B": [B_W]},
                "gates": _window(["old_push"], both), "covers": [0, 1, 2],
                "consumer_of": {"0": NEW, "1": NEW, "2": NEW}}
    if name == "new_to_old":  # new producer, old consumer
        return {"processes": {"A": [A_W], "B": [B_W, {"actor": "new_push", "fn": "push",
                                                      "args": {"ns": [0, 1, 2], "queue": "q_old"}}]},
                "gates": _window(["new_push"], both), "covers": [0, 1, 2],
                "consumer_of": {"0": OLD, "1": OLD, "2": OLD}}
    if name == "distinct":  # both workers, each on its own queue, both producers
        return {"processes": {"A": [A_W, {"actor": "old_push", "fn": "push", "args": {"ns": [0, 1],
                                                                                      "queue": "q_old"}}],
                              "B": [B_W, {"actor": "new_push", "fn": "push", "args": {"ns": [2, 3],
                                                                                      "queue": "q_new"}}]},
                "gates": _window(["old_push", "new_push"], both), "covers": [0, 1, 2, 3],
                "consumer_of": {"0": OLD, "1": OLD, "2": NEW, "3": NEW}}
    if name == "compete":  # both workers on one queue, jobs from both producers
        return {"processes": {"A": [A_W, {"actor": "old_push", "fn": "push", "args": {"ns": [0, 1],
                                                                                      "queue": "q_shared"}}],
                              "B": [B_W, {"actor": "new_push", "fn": "push", "args": {"ns": [2, 3],
                                                                                      "queue": "q_shared"}}]},
                "gates": _window(["old_push", "new_push"], both), "covers": [0, 1, 2, 3], "consumer_of": {}}
    if name == "retry_handoff":  # fails under 0.25.1; its first attempt is 0.25.1's (one short gate); the retry
        # goes to whichever worker claims it first (either outcome is valid under P8)
        return {"processes": {"A": [A_W], "B": [B_W, {"actor": "new_push", "fn": "push", "args": {
                    "ns": [0], "queue": "q_shared", "max_attempts": 2, "flaky_in": OLD}}]},
                "gates": _window(["new_push"], both) + [
                    {"actor": "new_worker", "match": "WITH selected_jobs AS",
                     "from": [{"actor": "new_push", "marker": "push", "count": 1}],
                     "until": [{"actor": "old_worker", "marker": "claim", "count": 1}]}],
                "covers": [0], "or_count": {"key": "handled", "n": 0, "at_least": 2}, "retry": [0],
                "consumer_of": {}}
    if name.startswith("cancel_"):  # cancel_<canceller>_<worker>: old|new each; the cancel lands between the
        _c, canceller, worker = name.split("_")  # worker's claim and its completion flush (gates)
        cv, wv = {"old": OLD, "new": NEW}[canceller], {"old": OLD, "new": NEW}[worker]
        w_actor = f"{worker}_worker"
        push = {"actor": f"{canceller}_push", "fn": "push", "args": {"ns": [0], "queue": f"q_{worker}",
                                                                      "cancel": [0]}}
        procs: dict[str, list[dict[str, Any]]] = {}
        procs.setdefault("A" if worker == "old" else "B", []).append(A_W if worker == "old" else B_W)
        procs.setdefault("A" if canceller == "old" else "B", []).append(push)
        return {"processes": procs,
                "gates": _window([push["actor"]], [w_actor]) + [
                    {"actor": push["actor"], "match": "SET state = 'failed'",
                     "until": [{"actor": w_actor, "marker": "claim", "count": 1}]},
                    {"actor": w_actor, "match": FLUSH,
                     "from": [{"actor": w_actor, "marker": "claim", "count": 1}],
                     "until": [{"actor": push["actor"], "marker": "cancel", "count": 1}]}],
                "covers": [0], "consumer_of": {"0": wv}, "cells": {"canceller": cv, "worker": wv}}
    raise SystemExit(f"unknown scenario {name!r}")


SCENARIOS = ["old_to_new", "new_to_old", "distinct", "compete", "retry_handoff",
             "cancel_new_new", "cancel_old_old", "cancel_new_old", "cancel_old_new"]


def jobs_detail(admin: Any) -> list[dict[str, Any]]:
    has_claim = admin.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = %s AND "
                              "table_name = 'chancy_jobs' AND column_name = 'claim_id'", (SCHEMA,)).fetchone()[0]
    cur = admin.execute(cp.JOBS_DETAIL_SQL.format(schema=SCHEMA, claim="claim_id IS NOT NULL" if has_claim
                                                  else "NULL::bool"))
    names = [d.name for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def sessions_left(admin: Any) -> list[Any]:
    return admin.execute("SELECT pid, application_name, state FROM pg_stat_activity WHERE datname = current_database()"
                         " AND pid <> pg_backend_pid() AND backend_type = 'client backend'").fetchall()


def prephases(target: hz.Target, images: dict[str, Any], out: Path) -> list[dict[str, Any]]:
    phases = []
    for i, (name, which, actor) in enumerate([("0_old_setup", "A", {"actor": "old_setup", "fn": "co_setup"}),
                                              ("0_new_migrate", "B", {"actor": "new_migrate", "fn": "migrate"})]):
        d = out / name
        d.mkdir(parents=True)
        r = ri.run_smoke(target, variant=name, schedule=[], out=d, image=images[which], mode="chancy-plan",
                         fresh_schema=(i == 0), keep_schema=True,
                         extra_env={"DS_CHANCY_PLAN": json.dumps([{"actors": [actor]}]),
                                    **images.get("env", {}).get(which, {})})
        phases.append(r)
        (d / "report.json").write_text(json.dumps(r, indent=1, default=str))
        if not (r.get("isolation_ok") and (r.get("corroboration") or {}).get("ok")):
            break
    return phases


def _digest(state: dict[str, Any], app: Any) -> str:
    if app is None:
        return oracle.digest(state)
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def simultaneous(target: hz.Target, images: dict[str, Any], spec: dict[str, Any], policy: dict[str, Any],
                 out: Path, *, label: str, app: Any = None) -> dict[str, Any]:
    """``app``: optional hooks for another application (``schema``, ``inside_mode``, ``state(admin)``,
    ``precondition(initial, spec)`` returning a record or raising, ``check(final, results, spec, quiescent)``).
    Without it, the Chancy behaviour, unchanged."""
    schema = app.schema if app else SCHEMA
    before = ri.protected_state()
    work = Path(tempfile.mkdtemp(prefix="ds_coord_"))
    nonce = secrets.token_hex(4)
    net = f"ds-mvp-coord-{nonce}"
    names = {"coordinator": f"ds-mvp-coord-c-{nonce}", **{p: f"ds-mvp-coord-{p.lower()}-{nonce}"
                                                          for p in spec["processes"]}}
    token = secrets.token_hex(16)
    result: dict[str, Any] = {"label": label, "policy": policy, "network": net, "containers": names,
                              "frozen_sha256": {k: hashlib.sha256((REPO / v).read_bytes()).hexdigest()
                                                for k, v in FROZEN.items()},
                              "code_revision": hz.code_revision(), "runtime_flags": ri.RUNTIME_FLAGS}
    procs: dict[str, subprocess.Popen[str]] = {}
    try:
        ri.docker("network", "create", "--internal", net)
        ri.docker("network", "connect", "--alias", "ds-mvp-pg", net, ri.PG_CONTAINER)
        result["network_internal"] = ri.docker("network", "inspect", "-f", "{{.Internal}}", net).stdout.strip()
        admin = target.admin()
        try:
            if app is not None:
                initial = app.state(admin)
                result["precondition"] = app.precondition(initial, spec)
            else:
                initial = {**ri.chancy_state(admin, SCHEMA), "jobs_detail": jobs_detail(admin)}
                result["precondition"] = {"schema_version": initial["schema_version"],
                                          "jobs_before": len(initial["jobs_detail"])}
                versions = {v.get("version_of"): v.get("version") for v in initial["schema_version"]}
                if versions.get("chancy") != spec.get("expect_schema", "8"):
                    raise RuntimeError(f"the migration isn't complete before the window: {initial['schema_version']}")
                if initial["jobs_detail"]:
                    raise RuntimeError("jobs exist before the simultaneous phase")
            result["initial_state_digest"] = _digest(initial, app)
            from deployspecter.live.serverlog import ServerLog
            log = ServerLog(lambda q, prm=None: admin.execute(q, prm).fetchone())
            log.position()
            start, end = f"ds_mvp_mark_start_{nonce}", f"ds_mvp_mark_end_{nonce}"
            admin.execute(f"SELECT '{start}'")
            envs = {"coordinator": [
                "DS_MODE=coordinator", f"DS_CO_NONCE={token}",
                "DS_CO_SPEC=" + json.dumps({"processes": sorted(spec["processes"]), "policy": policy,
                                            "gates": spec["gates"], "deadline_s": 170, "epoch_timeout_s": 60,
                                            "stall_s": 30,
                                            "stop": {"key": "succeeded", "covers": spec["covers"], "after_s": 45,
                                                     **({"or_count": spec["or_count"]} if "or_count" in spec
                                                        else {}),
                                                     **({"handled_all": spec["handled_all"]} if "handled_all" in spec
                                                        else {})}})]}
            for p, actors in spec["processes"].items():
                envs[p] = ["DS_PG_HOST=ds-mvp-pg", "DS_PG_PORT=5432", f"DS_PG_USER={target.user}",
                           f"DS_PG_PASSWORD={target.password}", f"DS_PG_DB={target.dbname}",
                           f"DS_MODE={app.inside_mode if app else 'chancy_coordinated'}", f"DS_SCHEMA={schema}",
                           f"DS_CO_NONCE={token}",
                           "DS_CO_PLAN=" + json.dumps({"process": p, "coordinator": ["ds-coord", 7777],
                                                       "markers": app.markers if app else MARKERS,
                                                       "actors": actors, "run_s": 160}),
                           *[f"{k}={v}" for k, v in images.get("env", {}).get(p, {}).items()]]
            started = time.monotonic()
            for who in ["coordinator", *sorted(spec["processes"])]:
                f = work / f"{who}.env"
                f.write_text("\n".join(envs[who]) + "\n")
                f.chmod(0o600)
                image = (images.get("B") or images["A"])[0] if who == "coordinator" else images[who][0]
                alias = ["--network-alias", "ds-coord"] if who == "coordinator" else []
                procs[who] = subprocess.Popen(
                    ["docker", "run", "--rm", "--name", names[who], "--network", net, *alias, *ri.RUNTIME_FLAGS,
                     "--env-file", str(f), image, "/app/smoke_inside.py"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            outputs: dict[str, Any] = {}
            for who, proc in procs.items():
                try:
                    so, se = proc.communicate(timeout=max(5.0, 240 - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    for n in names.values():
                        ri.docker("kill", n, check=False)
                    so, se = proc.communicate(timeout=30)
                    result.setdefault("refusals", []).append(f"{who} exceeded the wall-clock limit")
                outputs[who] = {"exit": proc.returncode, "stderr_tail": se[-1500:]}
                try:
                    outputs[who]["out"] = json.loads(so.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    result.setdefault("refusals", []).append(f"{who} printed no result (exit {proc.returncode})")
            result["container_seconds"] = round(time.monotonic() - started, 2)
            coord = (outputs["coordinator"].get("out") or {}).get("coordinator") or {}
            inside = {p: outputs[p].get("out") or {} for p in spec["processes"]}
            traces = {p: o["trace"] for p, o in inside.items() if "trace" in o}
            monitors = [o.get("monitor_pid") for o in inside.values() if o.get("monitor_pid")]
            pids = [a["backend_pid"] for t in traces.values() for a in t["actors"].values()] + monitors
            gone = hz._wait_gone(admin, pids)
            admin.execute(f"SELECT '{end}'")
            until = time.monotonic() + 5
            data = log.read()
            while not (f"SELECT '{end}'".encode() in data and data.endswith(b"\n")) and time.monotonic() < until:
                time.sleep(0.05)
                data = log.read()
            corr = {"ok": False, "problems": ["no process trace"]}
            if len(traces) == len(spec["processes"]):
                merged = merge_traces(traces)
                corr = corroborate(data, start=start, end=end, trace=merged, admin_pid={admin.info.backend_pid,
                                                                                        *monitors},
                                   user=target.user, database=target.dbname).to_dict()
            if not gone:
                corr["ok"] = False
                corr["problems"].append("a session outlived the run")
            final = app.state(admin) if app else {**ri.chancy_state(admin, SCHEMA), "jobs_detail": jobs_detail(admin)}
            result["sessions_left_after_run"] = [list(r) for r in sessions_left(admin)]
        finally:
            admin.close()
        probes = {p: o.get("probes", {}) for p, o in {**inside, "coordinator": outputs["coordinator"].get("out")
                                                     or {}}.items()}
        isolation = {}
        for p, pr in probes.items():
            checks = {k: (pr.get(k) == v) for k, v in ri.EXPECTED_PROBES.items()}
            checks.update(host_55432_unreachable=str(pr.get("host_55432", "")).startswith("refused"),
                          internet_tcp_unreachable=str(pr.get("internet_tcp", "")).startswith("refused"),
                          internet_dns_unresolved=str(pr.get("internet_dns", "")).startswith("unresolved"),
                          pids_limited=pr.get("nproc_limit") == "64",
                          memory_limited=pr.get("memory_limit") == str(512 * 1024 * 1024))
            isolation[p] = checks
        results = {name: res for o in inside.values() for name, res in (o.get("raw_results") or {}).items()}
        workers = [r for r in results.values() if isinstance(r, dict) and "worker_stopped" in r]
        stop_event = next((e for e in coord.get("events", []) if e.get("event") == "stop"), None)
        # complete: the coordinator stopped the workers because the expected jobs had succeeded, not on its timeout
        complete = bool(stop_event) and stop_event.get("why") == "covered"
        quiescent = complete and bool(workers) and all(w["worker_stopped"] and w["stopped_by"] == "signal"
                                                       for w in workers)
        failed = {"coordinator_completed": coord.get("outcome") == "completed",
                  "corroborated": bool(corr.get("ok")),
                  "isolation": all(all(c.values()) for c in isolation.values()),
                  "every_container_reported": not result.get("refusals"),
                  "every_run_completed": len(traces) == len(spec["processes"]) and
                  all(t["outcome"] == "completed" for t in traces.values()),
                  "no_actor_exception": not any(a["exception"] for t in traces.values()
                                                for a in t["actors"].values())}
        result["refusal_reasons"] = sorted(k for k, ok in failed.items() if not ok)
        sound = all(failed.values())
        result.update(
            coordinator=coord, corroboration=corr, isolation_checks=isolation, probes=probes,
            isolation_ok=all(all(c.values()) for c in isolation.values()),
            versions={p: o.get("version") for p, o in inside.items()},
            pinned_sources={p: o.get("pinned_sources") for p, o in inside.items()},
            outcomes={p: t["outcome"] for p, t in traces.items()},
            exceptions={n: a["exception"] for t in traces.values() for n, a in t["actors"].items() if a["exception"]},
            raw_results=results, final_state=final, final_state_digest=_digest(final, app),
            trace_sha256={p: hashlib.sha256(json.dumps(t, sort_keys=True, default=str).encode()).hexdigest()
                          for p, t in traces.items()},
            outputs={k: {kk: vv for kk, vv in v.items() if kk != "out"} for k, v in outputs.items()},
            sound=bool(sound), complete=complete,
            verdict="REFUSED" if not sound else "SOUND" if complete else "INCOMPLETE")
        if sound and complete:  # an incomplete run (stopped on the timeout) gets no property verdict
            result["properties"] = (app.check(final, results, spec, quiescent) if app else
                                    cp.check(final["jobs_detail"], results, spec, quiescent=quiescent))
        for p, t in traces.items():
            (out / f"{p}.trace.json").write_text(json.dumps(t, indent=1, default=str))
        (out / "coordinator.json").write_text(json.dumps(coord, indent=1, default=str))
    finally:
        with deferred_signals():  # an interrupt here is held until the teardown is complete (signal_safe.py)
            for n in names.values():
                ri.docker("rm", "-f", n, check=False)
            for proc in procs.values():
                if proc.poll() is None:
                    proc.kill()
            ri.docker("network", "disconnect", "-f", net, ri.PG_CONTAINER, check=False)
            ri.docker("network", "rm", net, check=False)
            shutil.rmtree(work, ignore_errors=True)
            left = [n for n in names.values() if ri.docker("ps", "-a", "--filter", f"name=^{n}$", "-q",
                                                           check=False).stdout.strip()]
            nets = ri.docker("network", "ls", "--filter", f"name={net}", "-q", check=False).stdout.strip()
            result["teardown"] = {"containers_removed": not left, "network_removed": not nets,
                                  "workdir_removed": not work.exists(),
                                  "protected_unchanged": ri.protected_state() == before}
    return result


def summary(r: dict[str, Any]) -> dict[str, Any]:
    """What the window actually contained: grants by version, and the first grant at which both versions had been
    granted a statement after both workers were ready."""
    ds = (r.get("coordinator") or {}).get("decisions", [])
    statements = (r.get("coordinator") or {}).get("statements", {})
    by_v: dict[str, int] = {}
    for d in ds:
        by_v[d["version"]] = by_v.get(d["version"], 0) + 1
    seen: set[str] = set()
    overlap_at = None
    for d in ds:
        seen.add(d["version"])
        if len(seen) == 2 and overlap_at is None:
            overlap_at = d["grant"]
    stop = next((e for e in (r.get("coordinator") or {}).get("events", []) if e["event"] == "stop"), None)
    txn_order = [(d["actor"], d["kind"]) for d in ds if d["kind"] in ("commit", "transaction_exit")]
    # the mixed-version window: from the first producer grant (every worker had completed a claim query: the gate)
    # to the stop signal
    producers = set((r.get("spec") or {}).get("producers") or
                    [a for acts in (r.get("spec") or {}).get("processes", {}).values() for a in
                     [x["actor"] for x in acts] if a.endswith("_push")])
    opened = next((d["grant"] for d in ds if d["actor"] in producers), None)
    closed = stop["at_grant"] if stop else len(ds)
    inside = [d for d in ds if opened is not None and opened <= d["grant"] < closed]
    window = {"opened_at_grant": opened, "closed_at_grant": closed, "grants": len(inside),
              "by_version": {}, "by_actor": {}, "version_switches": 0, "claims_by_version": {}}
    for i, d in enumerate(inside):
        window["by_version"][d["version"]] = window["by_version"].get(d["version"], 0) + 1
        window["by_actor"][d["actor"]] = window["by_actor"].get(d["actor"], 0) + 1
        if i and inside[i - 1]["version"] != d["version"]:
            window["version_switches"] += 1
        text = statements.get(d["statement_id"], d["sql"])
        if "WITH selected_jobs AS" in text or "procrastinate_fetch_job_v2" in text:
            window["claims_by_version"][d["version"]] = window["claims_by_version"].get(d["version"], 0) + 1
    return {"verdict": r.get("verdict"), "grants": len(ds), "grants_by_version": by_v, "window": window,
            "sessions": (r.get("corroboration") or {}).get("sessions"),
            "both_versions_granted_by": overlap_at, "stop_at_grant": stop and stop["at_grant"],
            "violations": (r.get("properties") or {}).get("violations"),
            "executions": (r.get("properties") or {}).get("executions"),
            "corroboration": {k: (r.get("corroboration") or {}).get(k) for k in
                              ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked",
                               "problems")},
            "final_state_digest": r.get("final_state_digest"),
            "txn_order_sha256": hashlib.sha256(json.dumps(txn_order).encode()).hexdigest()[:16],
            "teardown": r.get("teardown")}


def build_images(old_sdist: Path, new_sdist: Path, mutant: str | None) -> dict[str, Any]:
    """A: 0.25.1. B: 0.26.0, or the declared mutant of it (its declaration goes to every B container)."""
    images: dict[str, Any] = {"env": {"A": {}, "B": {}}}
    if mutant:
        images["env"]["B"] = {"DS_CHANCY_MUTANT": ri.mutant_declaration(new_sdist, mutant, "0.26.0")}
    for p, sdist, m in (("A", old_sdist, None), ("B", new_sdist, mutant)):
        d = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
        try:
            images[p] = ri.build_image(d, chancy_sdist=sdist, mutant=m)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return images


def one(target: hz.Target, images: dict[str, Any], name: str, policy: dict[str, Any], out: Path,
        mutant: str | None) -> dict[str, Any]:
    out.mkdir(parents=True)
    spec = scenario(name)
    pre = prephases(target, images, out)
    pre_ok = len(pre) == 2 and all(p.get("isolation_ok") and p["corroboration"]["ok"] for p in pre)
    report: dict[str, Any] = {"scenario": name, "spec": spec, "mutant": mutant, "nonblind": True,
                              "images": {p: list(v) for p, v in images.items() if p != "env"},
                              "image_env": images.get("env"),
                              "prephases": [{"variant": p["variant"], "corroboration_ok": p["corroboration"]["ok"],
                                             "isolation_ok": p.get("isolation_ok"),
                                             "version": (p.get("pinned_sources") or {}).get("version")}
                                            for p in pre]}
    if not pre_ok:
        report["verdict"] = "REFUSED"
        report["reason"] = "a pre-phase wasn't isolated and corroborated"
    else:
        sim = simultaneous(target, images, spec, policy, out, label=name)
        if mutant:
            sim["mutant_declaration"] = mutant
        report["simultaneous"] = sim
        report["summary"] = summary({**sim, "spec": spec})
        report["verdict"] = sim["verdict"]
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "explore"])
    ap.add_argument("--scenario", required=True, choices=SCENARIOS)
    ap.add_argument("--policy", default='{"kind": "alternate"}')  # "prefer" can starve a process
    ap.add_argument("--seeds", default="0-3")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--mutant", choices=sorted(ri.MUTANTS), help="applied to the 0.26.0 image only")
    ap.add_argument("--chancy-old-sdist", type=Path, required=True)
    ap.add_argument("--chancy-sdist", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists (evidence is never overwritten)")
    secret = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp",
                       secret.read_text().strip())
    images = build_images(a.chancy_old_sdist, a.chancy_sdist, a.mutant)
    if a.cmd == "run":
        policies = [json.loads(a.policy)] * a.repeat
    else:
        lo, hi = (int(x) for x in a.seeds.split("-"))
        policies = [{"kind": "seeded", "seed": s} for s in range(lo, hi + 1) for _ in range(a.repeat)]
    rows = []
    for i, policy in enumerate(policies):
        tag = f"{i:02d}_{policy['kind']}{policy.get('seed', '')}"
        r = one(target, images, a.scenario, policy, a.out / tag, a.mutant)
        row = {"run": tag, "policy": policy, **(r.get("summary") or {"verdict": r["verdict"]})}
        rows.append(row)
        print(json.dumps(row, default=str), flush=True)
        if r["verdict"] != "SOUND" and a.cmd == "run":
            break  # a run batch ends at its first refused or incomplete run
        # explore goes on to the next schedule and counts the refusal; no schedule is ever re-run after one
    (a.out / "summary.json").write_text(json.dumps(rows, indent=1, default=str))
    return 0 if all(r["verdict"] == "SOUND" for r in rows) and len(rows) == len(policies) else 1


if __name__ == "__main__":
    sys.exit(main())
