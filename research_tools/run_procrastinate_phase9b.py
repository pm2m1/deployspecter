"""EXPERIMENTAL ds-p9x/1 compatibility probe: can procrastinate 3.4.0's schema take a Phase 9B-constructed state?

    python research_tools/run_procrastinate_phase9b.py --out DIR --sdists DIR

The run is bounded:
1. procrastinate 3.4.0's own ``apply_schema_async`` (the ``schema`` operation) runs in the restricted container,
   coordinated and corroborated, into a fresh ``public`` of the disposable database.
2. ``catalog.screen_triggers`` and ``catalog.export`` describe what that schema contains.
3. ``pipeline_v1.construct_on_target`` constructs one ``ds_ok`` job row: queue ``ds_q``, args ``{"n": 0}``, the
   other columns left to their defaults.
4. Only if that construction is verified does procrastinate's worker drain the queue, corroborated, and then the
   job must have succeeded.

Every refusal is recorded with its exact stage and reason. Nothing upstream is rewritten, and no trigger is
disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools"))
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))

import run_coordinated as rc  # noqa: E402
import run_isolated as ri  # noqa: E402
import run_rolling_upgrade as rru  # noqa: E402
import run_state_construction as rsc  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental.phase9b_ext import catalog, pipeline_v1  # noqa: E402
from deployspecter.experimental.state_construction.typed import TV  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sdists", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    password = rsc.SECRET.read_text().strip()
    target = hz.Target("127.0.0.1", rsc.PORT, "ds_mvp", password)
    sdist = a.sdists / "procrastinate-3.4.0.tar.gz"
    rec: dict[str, Any] = {"sdist": {"name": sdist.name, "sha256": hashlib.sha256(sdist.read_bytes()).hexdigest()}}
    d = Path(tempfile.mkdtemp(prefix="ds_p9x_build_"))
    try:
        images = {"B": ri.build_image(d, procrastinate_sdist=sdist)}
    finally:
        shutil.rmtree(d, ignore_errors=True)
    admin = target.admin()
    try:
        if admin.execute("SELECT current_setting('cluster_name'), current_database()").fetchone() != \
                ("ds_mvp_disposable", "ds_mvp"):
            raise SystemExit("refused: not the disposable database")
        admin.execute("DROP SCHEMA public CASCADE")
        admin.execute("CREATE SCHEMA public")
    finally:
        admin.close()
    spec = {"name": "p9x_schema", "covers": [], "consumer_of": {}, "gates": [], "expect": {"schema_present": False},
            "processes": {"B": [rru.actor("schema", "schema")]}}
    (a.out / spec["name"]).mkdir()
    sim = rc.simultaneous(target, images, spec, {"kind": "alternate"}, a.out / spec["name"], label=spec["name"],
                          app=rru.Procrastinate)
    rec["schema_phase"] = {"verdict": sim["verdict"], "corroboration": {k: (sim.get("corroboration") or {}).get(k) for k
                                                                        in ("ok", "matched", "params_mismatched",
                                                                            "params_unchecked")}}
    rsc.write(a.out, "schema_phase.json", {"spec": spec, **sim}, password)
    admin = target.admin()
    try:
        rec["triggers"] = catalog.screen_triggers(admin, "public")
        try:
            rec["export"] = {k: v for k, v in catalog.export(admin, "public").items() if k != "setup_sql"}
        except catalog.CatalogRefused as exc:
            rec["export"] = {"refused": str(exc)}
        rec["procrastinate_jobs_columns"] = [list(r) for r in admin.execute(
            "SELECT column_name::text, data_type::text, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'procrastinate_jobs' ORDER BY ordinal_position")]
    finally:
        admin.close()
    rows = [("procrastinate_jobs", {"queue_name": TV("str", "ds_q"), "task_name": TV("str", "ds_ok"),
                                    "args": TV("jsonb", '{"n": 0}')})]
    sandbox = pipeline_v1.open_sandbox(rsc.PORT, target.user, password)
    try:
        o = pipeline_v1.construct_on_target(target, sandbox, "public", rows)
    finally:
        sandbox.close()
    rec["construction"] = o.to_dict()
    rec["result"] = {"status": o.status, "stage": o.stage, "classification": o.classification, "detail": o.detail}
    if o.status == "constructed":
        w = {"name": "p9x_work", "covers": [], "consumer_of": {}, "gates": [], "expect": {"schema_present": True},
             "processes": {"B": [rru.actor("worker", "work", wait=False)]}}
        (a.out / w["name"]).mkdir()
        ws = rc.simultaneous(target, images, w, {"kind": "alternate"}, a.out / w["name"], label=w["name"],
                             app=rru.Procrastinate)
        rsc.write(a.out, "work_phase.json", {"spec": w, **ws}, password)
        admin = target.admin()
        try:
            rec["final_jobs"] = [list(r) for r in admin.execute(
                "SELECT id, task_name, status::text, attempts FROM procrastinate_jobs ORDER BY id").fetchall()]
        finally:
            admin.close()
        rec["work_phase"] = {"verdict": ws["verdict"], "corroboration": (ws.get("corroboration") or {}).get("ok")}
    rec["sandbox_teardown"] = pipeline_v1.teardown(rsc.PORT, target.user, password)
    admin = target.admin()
    try:  # leave the disposable public schema empty, as the other runners do
        admin.execute("DROP SCHEMA public CASCADE")
        admin.execute("CREATE SCHEMA public")
    finally:
        admin.close()
    rsc.write(a.out, "SUMMARY.json", rec, password)
    print(json.dumps({"schema_phase": rec["schema_phase"]["verdict"], **rec["result"]}, indent=1, default=str)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
