"""EXPERIMENTAL, NONBLIND exploratory development case: procrastinate 3.3.0 → 3.4.0 under its documented blue-green
procedure (docs/experimental_mvp/rolling_upgrade/procrastinate_3.3_3.4/CASE.md).

    python research_tools/isolated/run_rolling_upgrade.py --old-sdist SDISTS/procrastinate-3.3.0.tar.gz \
        --new-sdist SDISTS/procrastinate-3.4.0.tar.gz --policy '{"kind":"alternate"}' --out DIR

The six phases run in order. Each is an isolated, coordinated run, corroborated against the server log, with its
precondition checked by the host from the catalog. The first phase that isn't SOUND ends the case; nothing is retried.
The frozen case checker runs once, on the final state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_coordinated as rc  # noqa: E402
import run_isolated as ri  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import procrastinate_properties as pp  # noqa: E402

CASE = "procrastinate-3.3.0-3.4.0"
FROZEN = {"case": "docs/experimental_mvp/rolling_upgrade/procrastinate_3.3_3.4/CASE.md",
          "checker": "src/deployspecter/experimental/procrastinate_properties.py"}
PRE, POST = "03.04.00_01_pre_add_retry_failed_job_procedure.sql", "03.04.00_50_post_add_retry_failed_job_procedure.sql"
OLD, NEW = "3.3.0", "3.4.0"


class Procrastinate:
    schema = "public"
    inside_mode = "procr_coordinated"
    markers = {"fetch": {"regex": "procrastinate_fetch_job_v2"}, "push": {"regex": "procrastinate_defer_jobs_v1"},
               "admin": {"regex": "procrastinate_retry_job_v2"}}

    @staticmethod
    def state(admin: Any) -> dict[str, Any]:
        def fn(name: str) -> bool:
            return bool(admin.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                                      "WHERE n.nspname = 'public' AND p.proname = %s", (name,)).fetchone()[0])
        tables = admin.execute("SELECT to_regclass('public.procrastinate_jobs') IS NOT NULL").fetchone()[0]
        out: dict[str, Any] = {"schema_present": tables, "retry_v1": fn("procrastinate_retry_job_v1"),
                               "retry_v2": fn("procrastinate_retry_job_v2"), "jobs": [], "events": []}
        trig = admin.execute("SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = "
                             "p.pronamespace WHERE n.nspname = 'public' AND p.proname = "
                             "'procrastinate_trigger_function_status_events_update_v1'").fetchone()
        out["trigger_records_retried"] = bool(trig) and "'retried'" in trig[0]
        if tables:
            for key, q in pp.STATE_SQL.items():
                cur = admin.execute(q)
                names = [d.name for d in cur.description]
                out[key] = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        return out

    @staticmethod
    def precondition(initial: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        want = spec["expect"]
        got = {k: initial.get(k) for k in want if k != "jobs_before"}
        if "jobs_before" in want:
            got["jobs_before"] = len(initial["jobs"])
        if got != want:
            raise RuntimeError(f"phase precondition not met: expected {want}, found {got}")
        return got

    @staticmethod
    def check(final: dict[str, Any], results: dict[str, Any], spec: dict[str, Any], quiescent: bool) -> dict:
        return {"phase_only": True}  # the case checker runs once, on the final state (main)


def actor(name: str, fn: str, **args: Any) -> dict[str, Any]:
    return {"actor": name, "fn": fn, "args": args}


CURATED_OVERLAP = {"old_jobs": [{"kind": "ok"}, {"kind": "flaky", "fail_in": OLD}, {"kind": "flaky", "fail_in": NEW},
                                {"kind": "fail"}],
                   "new_jobs": [{"kind": "ok"}, {"kind": "flaky", "fail_in": OLD}, {"kind": "flaky", "fail_in": NEW},
                                {"kind": "fail"}],
                   "admin": [1]}  # the frozen case's overlap workload (CASE.md, phase 3)


def phases(ids: dict[int, int], overlap: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The contract's six phases. ``ids`` maps n -> job id for jobs from earlier phases (the admin retries).
    ``overlap``: the overlap workload (``old_jobs``, ``new_jobs``, ``admin``); default, the frozen curated one."""
    ov = overlap or CURATED_OVERLAP
    def drain(worker: str, deferrer: str, count: int, extra: list[dict[str, Any]] | None = None) -> list[dict]:
        until = [{"actor": deferrer, "marker": "push", "count": count}] + (extra or [])
        return [{"actor": worker, "match": None, "until": until}]
    p0 = [{"n": 0, "kind": "ok"}, {"n": 1, "kind": "fail"}, {"n": 2, "kind": "flaky", "fail_in": OLD}]
    p2 = [{"n": 3, "kind": "ok"}, {"n": 4, "kind": "fail"}]
    nxt = iter(range(5, 100))
    p3_old = [{"n": next(nxt), **j} for j in ov["old_jobs"]]
    p3_new = [{"n": next(nxt), **j} for j in ov["new_jobs"]]
    first5 = max([j["n"] for j in p3_old + p3_new], default=4) + 1
    p5 = [{"n": first5, "kind": "ok"}, {"n": first5 + 1, "kind": "fail"}]
    overlap_ns = [j["n"] for j in p3_old + p3_new]
    ok_ns = [j["n"] for j in p3_old + p3_new if j["kind"] == "ok"]
    admin = [{"n": n, "id": ids.get(n)} for n in ov["admin"]]
    overlap_actors = {"A": ([actor("old_defer", "defer", jobs=p3_old)] if p3_old else []) +
                           [actor("old_worker", "work", wait=True)],
                      "B": ([actor("new_defer", "defer", jobs=p3_new)] if p3_new else []) +
                           [actor("new_worker", "work", wait=True)] +
                           ([actor("new_admin", "admin_retry", targets=admin)] if admin else [])}
    producers = [a["actor"] for acts in overlap_actors.values() for a in acts if a["fn"] in ("defer", "admin_retry")]
    base = {"covers": [], "consumer_of": {}}
    return [
        {"name": "0a_old_schema", **base, "processes": {"A": [actor("old_schema", "schema")]}, "gates": [],
         "expect": {"schema_present": False}},
        {"name": "0b_old_on_old_schema", **base, "processes": {"A": [actor("old_defer", "defer", jobs=p0),
                                                                     actor("old_worker", "work", wait=False)]},
         "gates": drain("old_worker", "old_defer", len(p0)),
         "expect": {"schema_present": True, "retry_v2": False, "jobs_before": 0}},
        {"name": "1_pre_migration", **base, "processes": {"B": [actor("operator", "migrate", migration=PRE)]},
         "gates": [], "expect": {"retry_v1": True, "retry_v2": False, "trigger_records_retried": False}},
        {"name": "2_old_on_pre_schema", **base, "processes": {"A": [actor("old_defer", "defer", jobs=p2),
                                                                    actor("old_worker", "work", wait=False)]},
         "gates": drain("old_worker", "old_defer", len(p2)),
         "expect": {"retry_v2": True, "trigger_records_retried": False}},
        {"name": "3_overlap", "covers": ok_ns, "handled_all": sorted(set(overlap_ns) | set(ov["admin"])),
         "consumer_of": {}, "producers": producers, "processes": overlap_actors,
         "gates": rc._window(producers, ["old_worker", "new_worker"]),
         "expect": {"retry_v2": True, "trigger_records_retried": False}},
        {"name": "4_post_migration", **base, "processes": {"B": [actor("operator", "migrate", migration=POST)]},
         "gates": [], "expect": {"retry_v2": True, "trigger_records_retried": False}},
        {"name": "5_new_only", **base,
         "processes": {"B": [actor("new_defer", "defer", jobs=p5),
                             actor("new_admin", "admin_retry", targets=[{"n": 4, "id": ids.get(4)}]),
                             actor("new_worker", "work", wait=False)]},
         "gates": drain("new_worker", "new_defer", len(p5), [{"actor": "new_admin", "marker": "admin", "count": 1}]),
         "expect": {"retry_v2": True, "trigger_records_retried": True}},
    ]


def erratum_e1(final: dict[str, Any], checked: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    """Erratum E1 (ERRATA.md, written after the first bring-up): the frozen R2 *equality* assumed that ``attempts``
    counts retries. procrastinate's own SQL increments it on every finish of a ``doing`` job and on every retry,
    including an admin retry of a ``failed`` job, so ``attempts`` = executions + successful admin retries. The frozen
    checker is unchanged, so its R2 equality entries are an oracle error. This reports, separately, the relation the
    source defines and the documented bound (at most ``max_attempts + 1`` executions per retry cycle)."""
    admin: dict[int, int] = {}
    for a in checked.get("admin_retries", []):
        if a.get("ok"):
            admin[int(a["n"])] = admin.get(int(a["n"]), 0) + 1
    pushed = {int(p["n"]): p for r in results.values() if isinstance(r, dict) for p in r.get("pushed", [])}
    rows = []
    for j in final["jobs"]:
        n = int(j["n"])
        ex = len(checked["executions"].get(str(n), []))
        mx = int(pushed[n]["max_attempts"])
        rows.append({"n": n, "attempts": j["attempts"], "executions": ex, "admin_retries": admin.get(n, 0),
                     "relation_holds": j["attempts"] == ex + admin.get(n, 0),
                     "documented_bound_holds": ex <= (mx + 1) * (1 + admin.get(n, 0))})
    return {"relation_holds": sum(r["relation_holds"] for r in rows), "bound_holds":
            sum(r["documented_bound_holds"] for r in rows), "jobs": len(rows), "rows": rows,
            "r2_entries_in_frozen_checker": len(checked["violations"].get("R2", []))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-sdist", type=Path, required=True)
    ap.add_argument("--new-sdist", type=Path, required=True)
    ap.add_argument("--policy", default='{"kind": "alternate"}')
    ap.add_argument("--overlap", default=None, help="the overlap workload as JSON (default: the frozen curated one)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists (evidence is never overwritten)")
    a.out.mkdir(parents=True)
    policy = json.loads(a.policy)
    secret = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    images = {}
    for p, sdist in (("A", a.old_sdist), ("B", a.new_sdist)):
        d = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
        try:
            images[p] = ri.build_image(d, procrastinate_sdist=sdist)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    admin = target.admin()
    try:  # the disposable database only: a fresh public schema (procrastinate's post migration names `public.`)
        name = admin.execute("SELECT current_setting('cluster_name'), current_database()").fetchone()
        if name != ("ds_mvp_disposable", "ds_mvp"):
            raise SystemExit(f"refused: not the disposable database ({name})")
        admin.execute("DROP SCHEMA public CASCADE")
        admin.execute("CREATE SCHEMA public")
    finally:
        admin.close()
    report: dict[str, Any] = {"case": CASE, "nonblind": True, "exploratory_development": True, "policy": policy,
                              "images": {p: list(v) for p, v in images.items()},
                              "sdists": {p: {"name": s.name, "sha256": hashlib.sha256(s.read_bytes()).hexdigest()}
                                         for p, s in (("A", a.old_sdist), ("B", a.new_sdist))},
                              "frozen_sha256": {k: hashlib.sha256((ri.REPO / v).read_bytes()).hexdigest()
                                                for k, v in FROZEN.items()},
                              "phases": []}
    ids: dict[int, int] = {}
    results: dict[str, Any] = {}
    post_retries: set[int] = set()
    verdict = "SOUND"
    overlap = json.loads(a.overlap) if a.overlap else None
    report["overlap_workload"] = overlap or CURATED_OVERLAP
    for i, spec in enumerate(phases(ids, overlap)):
        spec = phases(ids, overlap)[i]  # rebuilt: later phases need job ids from earlier ones
        (a.out / spec["name"]).mkdir()
        sim = rc.simultaneous(target, images, spec, policy, a.out / spec["name"], label=spec["name"],
                              app=Procrastinate)
        row = {"phase": spec["name"], "verdict": sim["verdict"], "refusal_reasons": sim.get("refusal_reasons"),
               "precondition": sim.get("precondition"), "versions": sim.get("versions"),
               "corroboration": {k: (sim.get("corroboration") or {}).get(k) for k in
                                 ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked",
                                  "problems", "unchecked_kinds")},
               "window": rc.summary({**sim, "spec": spec}).get("window"), "teardown": sim.get("teardown"),
               "sessions": (sim.get("corroboration") or {}).get("sessions")}
        (a.out / spec["name"] / "report.json").write_text(json.dumps({"spec": spec, **sim}, indent=1, default=str))
        report["phases"].append(row)
        print(json.dumps(row, default=str), flush=True)
        for name, res in (sim.get("raw_results") or {}).items():
            results[f"{spec['name']}/{name}"] = res
            for p in (res or {}).get("pushed", []) if isinstance(res, dict) else []:
                ids[int(p["n"])] = int(p["id"])
            if spec["name"] == "5_new_only" and isinstance(res, dict):
                post_retries |= {int(x["n"]) for x in res.get("retried", []) if x.get("ok")}
        if sim["verdict"] != "SOUND":
            verdict = "REFUSED"
            break
    report["verdict"] = verdict
    if verdict == "SOUND":
        admin = target.admin()
        try:
            final = Procrastinate.state(admin)
        finally:
            admin.close()
        workers = [r for r in results.values() if isinstance(r, dict) and "worker_stopped" in r]
        last = [r for k, r in results.items() if k.startswith("5_new_only/") and isinstance(r, dict)
                and "worker_stopped" in r]
        quiescent = all(w["worker_stopped"] for w in workers) and bool(last) and \
            all(r["stopped_by"] == "drained" for r in last)
        report["final_state"] = final
        report["case_checker"] = pp.check(final["jobs"], final["events"], results, post_applied_before=post_retries,
                                          quiescent=quiescent)
        report["erratum_E1"] = erratum_e1(final, report["case_checker"], results)
        report["contract_order"] = {"post_after_overlap_ended": [p["phase"] for p in report["phases"]].index(
            "4_post_migration") > [p["phase"] for p in report["phases"]].index("3_overlap")}
    report["results"] = results
    (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"verdict": verdict, "violations": (report.get("case_checker") or {}).get("violations")},
                     default=str))
    return 0 if verdict == "SOUND" else 1


if __name__ == "__main__":
    sys.exit(main())
