"""EXPERIMENTAL ds-p9x/1 live run: Chancy 0.26.0 on an initial state constructed through the Phase 9B pipeline.

    python research_tools/run_chancy_phase9b.py --out DIR --sdists DIR

For each registered workload (``single``: one pending job; ``multi``: four pending and one already-succeeded job):
1. ``verify_sources``: the pinned sdist, its sources and the adapter match the registry;
2. Chancy's own ``setup`` (migrate and declare ``ds_q``, no job pushed) in the restricted container;
3. the registry's schema preconditions;
4. ``pipeline_v1.construct_on_target``: Phase 9B (extension instance, traced), locked typed writes, readback;
5. Chancy's unmodified worker drains the queue (the ``work`` operation), bound to the verified state's digest,
   with every statement corroborated against the server log;
6. the independent oracle C1–C4.

There is no fallback: a refusal at any stage is recorded as such and the workload goes no further. The old direct
constructor then runs the ``multi`` goals once, labelled ``direct_comparison``, only to compare final states on
the semantically compared fields (see ``COMPARED``).

Disposable PostgreSQL (127.0.0.1:55441) only; run it through ``research_tools/pg_lock.py``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools"))

import run_state_construction as rsc  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import oracle as mvp_oracle  # noqa: E402
from deployspecter.experimental.phase9b_ext import chancy_registry_v1 as reg  # noqa: E402
from deployspecter.experimental.phase9b_ext import pipeline_v1  # noqa: E402
from deployspecter.experimental.state_construction import chancy  # noqa: E402

H = "deployspecter.experimental.thirdparty.chancy_adapter.ds_job"
# Compared between the Phase 9B run and the direct run: what the goals determine and what Chancy's processing must
# produce. Not compared, because Chancy generates them per run: started_at and completed_at (clock), taken_by (the
# worker's random id), claim_id (a fresh uuid per claim). Their presence is compared (completed, has_claim_id).
COMPARED = ("id", "queue", "func", "state", "attempts", "kwargs", "completed", "has_claim_id")


def job(n: int, state: str = "pending") -> dict[str, Any]:
    return {"handler": H, "kwargs": {"n": n}, "queue": "ds_q", "state": state, "priority": 0}


WORKLOADS = {
    "single": {"registry": reg.REGISTRY["registry"], "jobs": [job(0)]},
    "multi": {"registry": reg.REGISTRY["registry"], "jobs": [job(0), job(1), job(2), job(3), job(7, "succeeded")]},
}
NEGATIVE = {"unregistered_handler": {"registry": reg.REGISTRY["registry"],
                                     "jobs": [job(0) | {"handler": H.replace("ds_job", "ds_missing")}]}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sdists", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    password = rsc.SECRET.read_text().strip()
    target = hz.Target("127.0.0.1", rsc.PORT, "ds_mvp", password)
    summary: dict[str, Any] = {"sources": reg.verify_sources(a.sdists / "chancy-0.26.0.tar.gz")}
    rsc.write(a.out, "sources.json", summary["sources"], password)
    if not summary["sources"]["ok"]:
        summary["verdict"] = "refused: sources don't match the registry"
        rsc.write(a.out, "SUMMARY.json", summary, password)
        return 1
    iso = rsc.load_isolated()
    schema = iso.CAPTURE_SCHEMA
    build = Path(tempfile.mkdtemp(prefix="ds_p9x_build_"))
    try:
        image = iso.build_image(build, chancy_sdist=a.sdists / "chancy-0.26.0.tar.gz")
    finally:
        shutil.rmtree(build, ignore_errors=True)

    def phase(name: str, plan: list[dict[str, Any]], *, fresh: bool, keep: bool) -> dict[str, Any]:
        d = a.out / name
        d.mkdir(exist_ok=True)
        r = iso.run_smoke(target, variant=name, schedule=[], out=d, image=image, mode="chancy-plan",
                          fresh_schema=fresh, keep_schema=keep, extra_env={"DS_CHANCY_PLAN": json.dumps(plan)})
        rsc.write(a.out, f"{name}.run.json", r, password)
        return r

    def sound(r: dict[str, Any]) -> bool:
        c = r.get("corroboration") or {}
        return bool(r.get("isolation_ok")) and c.get("ok") is True and c.get("params_mismatched", 0) == 0 and \
            all((r.get("teardown") or {}).values())

    results = {}
    for label, w in NEGATIVE.items():
        results[label] = {"status": "refused_workload", "detail": reg.validate(w)}
    for label, w in WORKLOADS.items():
        results[label] = attempt(target, a.out, password, iso, phase, sound, schema, label, w)
    direct = rsc.chancy_attempt(target, a.out, password, iso, phase, sound, schema, "direct_comparison",
                                reg.to_direct_workload(WORKLOADS["multi"]), None)
    results["direct_comparison"] = direct
    p9 = results["multi"].get("final_jobs")
    summary["comparison"] = compare(p9, direct_final(a.out))
    summary["sandbox_teardown"] = pipeline_v1.teardown(rsc.PORT, target.user, password)
    summary["results"] = {k: {x: v.get(x) for x in ("status", "stage", "classification", "detail", "worker_sound",
                                                     "bound_to_verified_state", "oracle_violations", "handled",
                                                     "corroboration", "as_expected", "sha256") if x in v}
                          for k, v in results.items()}
    ok = all(results[k].get("as_expected") for k in WORKLOADS) and results["unregistered_handler"]["detail"] and \
        summary["comparison"]["equal"] and all(summary["sandbox_teardown"].values())
    summary["verdict"] = "as expected" if ok else "NOT as expected"
    rsc.write(a.out, "SUMMARY.json", summary, password)
    print(json.dumps({"verdict": summary["verdict"], "comparison": summary["comparison"]["equal"],
                      **{k: v.get("status") for k, v in summary["results"].items()}}, indent=1))
    return 0 if ok else 1


def attempt(target: Any, out: Path, password: str, iso: Any, phase: Any, sound: Any, schema: str, label: str,
            w: dict[str, Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {"attempt": label, "workload": w, "constructor": "phase9b_ext ds-p9x/1"}
    problems = reg.validate(w)
    if problems:
        rec.update(status="refused_workload", detail=problems, as_expected=False)
        return rec | {"sha256": rsc.write(out, f"p9x_{label}.json", rec, password)}
    rows = reg.rows(w)
    r1 = phase(f"p9x_{label}_1_setup", [{"actors": [{"actor": "setup", "fn": "setup", "args": {"ns": []}}]}],
               fresh=True, keep=True)
    rec["setup_sound"] = sound(r1)
    admin = target.admin()
    try:
        if not rec["setup_sound"]:
            rec.update(status="refused_setup", as_expected=False)
            return rec | {"sha256": rsc.write(out, f"p9x_{label}.json", rec, password)}
        rec["preconditions"] = reg.preconditions(admin, schema)
        if rec["preconditions"]:
            rec.update(status="refused_preconditions", as_expected=False)
            return rec | {"sha256": rsc.write(out, f"p9x_{label}.json", rec, password)}
        sandbox = pipeline_v1.open_sandbox(rsc.PORT, target.user, password)
        try:
            o = pipeline_v1.construct_on_target(target, sandbox, schema, rows)
        finally:
            sandbox.close()
        rec["construction"] = o.to_dict()
        rec.update(status=o.status, stage=o.stage, classification=o.classification, detail=o.detail)
        if o.status != "constructed":
            rec["as_expected"] = False
            return rec | {"sha256": rsc.write(out, f"p9x_{label}.json", rec, password)}
        expected_digest = mvp_oracle.digest({"catalog": iso.catalog(admin, schema), **iso.chancy_state(admin, schema)})
        rec["verified_digest"] = expected_digest
        rec["constructed_rows"] = [r[0] for r in admin.execute(
            f"SELECT to_jsonb(j) FROM {schema}.chancy_jobs j ORDER BY id").fetchall()]
        pending = [reg.job_id(j["kwargs"]["n"]) for j in w["jobs"] if j["state"] == "pending"]
        r2 = phase(f"p9x_{label}_2_worker", [{"actors": [{"actor": "worker", "fn": "work", "args": {
            "handle_at_least": 10**6, "wait_for": pending, "timeout_s": 60}}]}], fresh=False, keep=True)
        rec["worker_sound"] = sound(r2)
        rec["bound_to_verified_state"] = r2.get("initial_state_digest") == expected_digest
        final = iso.chancy_state(admin, schema)["jobs"]
        handled = ((r2.get("raw_results") or {}).get("worker") or {}).get("handled", [])
        rec["final_jobs"], rec["handled"] = final, handled
        rec["final_rows"] = [r[0] for r in admin.execute(
            f"SELECT to_jsonb(j) FROM {schema}.chancy_jobs j ORDER BY id").fetchall()]
        rec["oracle_violations"] = chancy.oracle(reg.to_direct_workload(w), final, handled)
        c = r2.get("corroboration") or {}
        rec["corroboration"] = {k: c.get(k) for k in ("ok", "matched", "sessions", "params_compared",
                                                       "params_mismatched", "params_unchecked", "problems")}
        rec["as_expected"] = bool(rec["worker_sound"] and rec["bound_to_verified_state"] and
                                  not rec["oracle_violations"] and c.get("params_unchecked") == 0)
    finally:
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.close()
    rec["status"] = "ran" if rec.get("status") == "constructed" else rec.get("status")
    return rec | {"sha256": rsc.write(out, f"p9x_{label}.json", rec, password)}


def direct_final(out: Path) -> list[dict[str, Any]] | None:
    p = out / "chancy_direct_comparison.json"
    return json.loads(p.read_text()).get("final_jobs") if p.exists() else None


def compare(p9: list[dict[str, Any]] | None, direct: list[dict[str, Any]] | None) -> dict[str, Any]:
    if p9 is None or direct is None:
        return {"equal": False, "why": "a run produced no final state", "fields": list(COMPARED)}
    a = sorted(({k: j.get(k) for k in COMPARED} for j in p9), key=lambda j: j["id"])
    b = sorted(({k: j.get(k) for k in COMPARED} for j in direct), key=lambda j: j["id"])
    return {"equal": a == b, "fields": list(COMPARED), "jobs": len(a),
            "not_compared": ["started_at", "completed_at", "taken_by", "claim_id (value)"],
            "differences": [(x, y) for x, y in zip(a, b, strict=False) if x != y][:5]}


if __name__ == "__main__":
    sys.exit(main())
