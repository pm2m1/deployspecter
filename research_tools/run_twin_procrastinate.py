"""EXPERIMENTAL, NONBLIND: the supported digital twin of procrastinate 3.3.0 → 3.4.0's documented blue-green upgrade.

    python research_tools/run_twin_procrastinate.py --old-sdist SDISTS/procrastinate-3.3.0.tar.gz \
        --new-sdist SDISTS/procrastinate-3.4.0.tar.gz --policy '{"kind":"alternate"}' --out DIR

It reuses, unchanged, the rolling-upgrade case's phase specs and application hooks
(``research_tools/isolated/run_rolling_upgrade.py``) and the coordinated isolated runner (``run_coordinated.py``), and
adds the twin's own steps: a background-job interaction phase (T4), a host-verified quiescence gate (T5) before
``post``, and final-state verification with a fresh-install schema comparison (T7). The model is in
``deployspecter.experimental.twin.procrastinate_twin``.

Every step's entry condition is checked by the host before it starts and its exit condition after it ends. The first
step that isn't SOUND ends the run (REFUSED_ENTRY, REFUSED_RUN or REFUSED_EXIT); nothing is retried. Only the
disposable database is used, and its ``public`` schema is left empty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "isolated"))
import run_coordinated as rc  # noqa: E402
import run_isolated as ri  # noqa: E402
import run_rolling_upgrade as rur  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import procrastinate_properties as pp  # noqa: E402
from deployspecter.experimental.twin import procrastinate_twin as tw  # noqa: E402

STEPS = ["0a_old_schema", "0b_old_on_old_schema", "1_pre_migration", "2_old_on_pre_schema", "3_overlap",
         "T4_background_retry", "T5_quiescence", "4_post_migration", "5_new_only", "T7_final_verification",
         "R_fresh_3.4.0_schema"]
PHASE_OF = {s: p["id"] for p in tw.PHASES for s in p["runs"]}
PHASE_OF.update({"T5_quiescence": "T5", "T7_final_verification": "T7"})
RESERVED_FOR_PHASE_5 = {4}  # the rolling-upgrade case's phase 5 retries n=4 after post


def migration_text(sdist: Path, name: str) -> str:
    with tarfile.open(sdist) as tf:
        member = next(m for m in tf.getmembers() if m.name.endswith(f"procrastinate/sql/migrations/{name}"))
        return tf.extractfile(member).read().decode()


def fn_present(cat: dict[str, Any], name: str) -> bool:
    return any(r[0] == name for r in cat.get("functions", []))


def terminal(state: dict[str, Any]) -> bool:
    return all(j["status"] in ("succeeded", "failed") for j in state.get("jobs", []))


def no_doing(state: dict[str, Any]) -> bool:
    return not [j for j in state.get("jobs", []) if j["status"] == "doing"]


def background_spec(targets: list[dict[str, Any]]) -> dict[str, Any]:
    a = rur.actor
    return {"name": "T4_background_retry", "covers": [], "handled_all": [t["n"] for t in targets],
            "consumer_of": {}, "producers": ["new_admin"],
            "processes": {"A": [a("old_worker", "work", wait=True)],
                          "B": [a("new_worker", "work", wait=True), a("new_admin", "admin_retry", targets=targets)]},
            "gates": rc._window(["new_admin"], ["old_worker", "new_worker"]),
            "expect": {"retry_v2": True, "trigger_records_retried": False}}


def fresh_spec() -> dict[str, Any]:
    return {"name": "R_fresh_3.4.0_schema", "covers": [], "consumer_of": {},
            "processes": {"B": [rur.actor("new_schema", "schema")]}, "gates": [], "expect": {"schema_present": False}}


def exit_checks(step: str, entry: dict[str, Any], ex: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    """The declared exit condition of each container step (procrastinate_twin.PHASES), as failures."""
    f: list[str] = []
    diff = tw.catalog_diff(entry["catalog"], ex["catalog"])
    st = ex["state"]
    unchanged = not diff
    if st.get("workers", 0) != 0:
        f.append("a worker is still registered")
    if not no_doing(st):
        f.append("a job is still doing")
    if step == "0a_old_schema":
        if not (fn_present(ex["catalog"], "procrastinate_retry_job_v1") and
                not fn_present(ex["catalog"], "procrastinate_retry_job_v2")):
            f.append("not the 3.3.0 schema")
    elif step in ("0b_old_on_old_schema", "2_old_on_pre_schema", "5_new_only"):
        if not unchanged:
            f.append(f"catalog changed: {sorted(diff)}")
        if not terminal(st):
            f.append("a deferred job isn't terminal after the drain")
    elif step == "1_pre_migration":
        if not diff or not tw.is_additive(diff):
            f.append(f"pre isn't purely additive: {diff}")
        if tw.rows_digest(entry["state"]["jobs"]) != tw.rows_digest(st["jobs"]):
            f.append("pre modified job rows")
        if not fn_present(ex["catalog"], "procrastinate_retry_job_v2"):
            f.append("retry_job_v2 missing after pre")
    elif step in ("3_overlap", "T4_background_retry"):
        if not unchanged:
            f.append(f"catalog changed: {sorted(diff)}")
        if step == "3_overlap":
            il = ctx["interleaving"]
            if len(il["by_version"]) < 2 or il["switches"] < 1:
                f.append(f"the versions didn't interleave: {il}")
    elif step == "4_post_migration":
        fd = diff.get("functions", {})
        touched = {r[0] for r in fd.get("removed", []) + fd.get("added", [])}
        if set(diff) - {"functions"} or touched != {tw.TRIGGER_FN} or fd.get("changed") != [f"{tw.TRIGGER_FN}()"]:
            f.append(f"post changed more than the status-events trigger function: {diff}")
        if tw.rows_digest(entry["state"]["jobs"]) != tw.rows_digest(st["jobs"]):
            f.append("post modified job rows")
    if ctx["permitted_violations"]:
        f += ctx["permitted_violations"]
    if not ctx["provenance"]["consistent_with_server_log"]:
        f.append("trace sessions disagree with the server log's")
    return f


def read(target: hz.Target) -> dict[str, Any]:
    admin = target.admin()
    try:
        cat = tw.catalog(admin)
        st = tw.twin_state(admin)
        app_state = rur.Procrastinate.state(admin)
    finally:
        admin.close()
    d = tw.catalog_digest(cat)
    return {"catalog": cat, "catalog_digest": d, "state": st, "state_digest": tw.state_digest(st, d),
            "app_state": app_state}


def reset_public(target: hz.Target) -> None:
    admin = target.admin()
    try:
        if admin.execute("SELECT current_setting('cluster_name'), current_database()").fetchone() != \
                ("ds_mvp_disposable", "ds_mvp"):
            raise SystemExit("refused: not the disposable database")
        admin.execute("DROP SCHEMA public CASCADE")
        admin.execute("CREATE SCHEMA public")
    finally:
        admin.close()


def main() -> int:  # noqa: C901 - one linear lifecycle, kept in one place for review
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-sdist", type=Path, required=True)
    ap.add_argument("--new-sdist", type=Path, required=True)
    ap.add_argument("--policy", default='{"kind": "alternate"}')
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists (evidence is never overwritten)")
    a.out.mkdir(parents=True)
    policy = json.loads(a.policy)
    secret = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", secret.read_text().strip())
    images = {}
    for p, sdist in (("A", a.old_sdist), ("B", a.new_sdist)):
        d = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
        try:
            images[p] = ri.build_image(d, procrastinate_sdist=sdist)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    reset_public(target)
    migrations = {m: migration_text(a.new_sdist, m) for m in (tw.PRE, tw.POST)}
    report: dict[str, Any] = {
        "case": rur.CASE, "twin": True, "nonblind": True, "exploratory_development": True, "policy": policy,
        "code_revision": hz.code_revision(), "model": "src/deployspecter/experimental/twin/procrastinate_twin.py",
        "model_sha256": hashlib.sha256((ri.REPO / "src/deployspecter/experimental/twin/procrastinate_twin.py")
                                       .read_bytes()).hexdigest(),
        "frozen_sha256": {k: hashlib.sha256((ri.REPO / v).read_bytes()).hexdigest() for k, v in rur.FROZEN.items()},
        "images": {p: list(v) for p, v in images.items()},
        "version_identity": {p: {"version": v, "sdist": s.name, "sdist_sha256": hashlib.sha256(s.read_bytes())
                                 .hexdigest()} for p, v, s in (("A", rur.OLD, a.old_sdist), ("B", rur.NEW,
                                                                                            a.new_sdist))},
        "migrations": {m: {"sha256": hashlib.sha256(t.encode()).hexdigest(),
                           "row_modifying_statements_outside_function_bodies": tw.data_only_statements(t)}
                       for m, t in migrations.items()},
        "phase_model": tw.PHASES, "steps": []}
    report["backfill"] = ("NOT SUPPORTED: this upgrade pair has no data migration (neither pinned migration has a "
                          "row-modifying statement outside a function body)"
                          if not any(v["row_modifying_statements_outside_function_bodies"]
                                     for v in report["migrations"].values()) else "PRESENT (not modelled)")
    ids: dict[int, int] = {}
    results: dict[str, Any] = {}
    post_retries: set[int] = set()
    exits: dict[str, dict[str, Any]] = {}
    quiesced = False
    verdict = "SOUND"
    upgraded_catalog = None
    for step in STEPS:
        row: dict[str, Any] = {"step": step, "phase": PHASE_OF[step]}
        entry = read(target)
        row.update(entry_catalog_digest=entry["catalog_digest"], entry_state_digest=entry["state_digest"])
        prev = exits.get(STEPS[STEPS.index(step) - 1]) if STEPS.index(step) else None
        failures: list[str] = []
        # ---------------------------------------------------------------- entry conditions
        if step == "0a_old_schema" and any(entry["catalog"].values()):
            failures.append("public isn't empty")
        elif step not in ("0a_old_schema", "R_fresh_3.4.0_schema") and prev and \
                prev["catalog_digest"] != entry["catalog_digest"]:
            failures.append("catalog changed between steps")
        if step == "4_post_migration" and not quiesced:
            failures.append("T5 didn't verify quiescence")
        if step == "R_fresh_3.4.0_schema" and any(entry["catalog"].values()):
            failures.append("public isn't empty for the fresh reference install")
        if failures:
            row.update(outcome="REFUSED_ENTRY", failures=failures)
            report["steps"].append(row)
            print(json.dumps(row, default=str), flush=True)
            verdict = "REFUSED"
            break
        # ---------------------------------------------------------------- host-only steps
        if step == "T5_quiescence":
            old = ri.docker("ps", "-a", "--filter", f"ancestor={images['A'][0]}", "-q", check=False).stdout.split()
            q = tw.quiescence(entry["state"], old)
            row.update(quiescence=q, outcome="SOUND" if q["quiesced"] else "REFUSED_EXIT",
                       exit_catalog_digest=entry["catalog_digest"], exit_state_digest=entry["state_digest"])
            quiesced = q["quiesced"]
            exits[step] = entry
            report["steps"].append(row)
            print(json.dumps(row, default=str), flush=True)
            if not quiesced:
                verdict = "REFUSED"
                break
            continue
        if step == "T7_final_verification":
            final = entry["app_state"]
            workers = [r for r in results.values() if isinstance(r, dict) and "worker_stopped" in r]
            last = [r for k, r in results.items() if k.startswith("5_new_only/") and isinstance(r, dict)
                    and "worker_stopped" in r]
            quiescent = all(w["worker_stopped"] for w in workers) and bool(last) and \
                all(r["stopped_by"] == "drained" for r in last)
            checked = pp.check(final["jobs"], final["events"], results, post_applied_before=post_retries,
                               quiescent=quiescent)
            cls = tw.classify(checked)
            names = [s["step"] for s in report["steps"]]
            row.update(case_checker=checked, classification=cls,
                       erratum_E1=rur.erratum_e1(final, checked, results),
                       contract_order={"post_after_quiescence": names.index("4_post_migration") >
                                       names.index("T5_quiescence") > names.index("3_overlap")},
                       final_catalog_digest=entry["catalog_digest"], final_state_digest=entry["state_digest"],
                       outcome="SOUND" if not cls["genuine_violations"] else "VIOLATION")
            upgraded_catalog = entry["catalog"]
            exits[step] = entry
            report["steps"].append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "case_checker"}, default=str), flush=True)
            reset_public(target)  # the fresh reference install needs an empty public schema
            continue
        # ---------------------------------------------------------------- container steps
        if step == "T4_background_retry":
            ns = tw.background_targets(entry["state"], RESERVED_FOR_PHASE_5)
            if not ns:
                row.update(outcome="REFUSED_ENTRY", failures=["no failed job to retry"])
                report["steps"].append(row)
                verdict = "REFUSED"
                break
            spec = background_spec([{"n": n, "id": ids[n]} for n in ns])
        elif step == "R_fresh_3.4.0_schema":
            spec = fresh_spec()
        else:
            spec = next(s for s in rur.phases(ids, None) if s["name"] == step)
        (a.out / step).mkdir()
        sim = rc.simultaneous(target, images, spec, policy, a.out / step, label=step, app=rur.Procrastinate)
        (a.out / step / "report.json").write_text(json.dumps({"spec": spec, **sim}, indent=1, default=str))
        traces = {p: json.loads((a.out / step / f"{p}.trace.json").read_text())
                  for p in spec["processes"] if (a.out / step / f"{p}.trace.json").exists()}
        model = next(p for p in tw.PHASES if p["id"] == PHASE_OF[step])
        versions = [rur.NEW] if step in ("4_post_migration", "5_new_only", "R_fresh_3.4.0_schema") else \
            sorted({rur.OLD if p == "A" else rur.NEW for p in spec["processes"]})
        recs = tw.records(traces)
        ctx = {"permitted_violations": tw.permitted_violations(step, recs, ddl_allowed=step in model["ddl"],
                                                                versions=versions),
               "provenance": tw.provenance(traces, (sim.get("corroboration") or {}).get("sessions")),
               "interleaving": tw.interleaving(traces)}
        ex = read(target)
        row.update(verdict=sim["verdict"], refusal_reasons=sim.get("refusal_reasons"),
                   precondition=sim.get("precondition"), versions=sim.get("versions"),
                   pinned_sources=sim.get("pinned_sources"),
                   corroboration={k: (sim.get("corroboration") or {}).get(k) for k in
                                  ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked",
                                   "problems", "unchecked_kinds")},
                   window=rc.summary({**sim, "spec": spec}).get("window"), teardown=sim.get("teardown"),
                   permitted_operations={"ddl_allowed": step in model["ddl"], "versions": versions,
                                         "violations": ctx["permitted_violations"]},
                   provenance=ctx["provenance"], interleaving=ctx["interleaving"],
                   exit_catalog_digest=ex["catalog_digest"], exit_state_digest=ex["state_digest"],
                   catalog_diff=tw.catalog_diff(entry["catalog"], ex["catalog"]))
        for name, res in (sim.get("raw_results") or {}).items():
            results[f"{step}/{name}"] = res
            for p in (res or {}).get("pushed", []) if isinstance(res, dict) else []:
                ids[int(p["n"])] = int(p["id"])
            if step == "5_new_only" and isinstance(res, dict):
                post_retries |= {int(x["n"]) for x in res.get("retried", []) if x.get("ok")}
        if sim["verdict"] != "SOUND":
            row["outcome"] = "REFUSED_RUN"
        elif step == "R_fresh_3.4.0_schema":
            d = tw.catalog_diff(upgraded_catalog or {}, ex["catalog"])
            row.update(outcome="SOUND", schema_equivalence={"equal": not d, "upgraded_vs_fresh_diff": d,
                                                            "upgraded_digest": tw.catalog_digest(
                                                                upgraded_catalog or {}),
                                                            "fresh_digest": ex["catalog_digest"]})
        else:
            fails = exit_checks(step, entry, ex, ctx)
            row.update(outcome="SOUND" if not fails else "REFUSED_EXIT", failures=fails)
        exits[step] = ex
        report["steps"].append(row)
        print(json.dumps({k: v for k, v in row.items() if k not in ("pinned_sources", "catalog_diff")},
                         default=str), flush=True)
        if row["outcome"] != "SOUND":
            verdict = "REFUSED"
            break
    final_row = next((s for s in report["steps"] if s["step"] == "T7_final_verification"), None)
    report["verdict"] = verdict
    report["genuine_violations"] = (final_row or {}).get("classification", {}).get("genuine_violations")
    report["results"] = results
    reset_public(target)
    report["teardown_public_schema_reset"] = True
    (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"verdict": verdict, "genuine_violations": report["genuine_violations"],
                      "schema_equivalence": next((s.get("schema_equivalence", {}).get("equal") for s in
                                                  report["steps"] if s["step"] == "R_fresh_3.4.0_schema"), None)},
                     default=str))
    return 0 if verdict == "SOUND" and not report["genuine_violations"] else 1


if __name__ == "__main__":
    sys.exit(main())
