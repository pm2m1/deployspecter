"""EXPERIMENTAL NONBLIND pilot: curated vs systematic vs Muse rolling-deploy workloads on Chancy 0.25.1 -> 0.26.0.

Each workload is four bounded integers (validated deterministically); each runs as the four-container mixed-version
pilot (research_tools/isolated/run_isolated.py, ``run_chancy_mixed``), corroborated per phase, with oracle M1-M4.
Equal budget: the first K valid workloads per source. Nothing here is blind or confirmatory.

    python research_tools/mvp_compare_chancy_mixed.py --old-sdist P --new-sdist P --workloads 2 \
        [--muse-calls 1] --out DIR
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "isolated"))

import run_isolated as ri  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import muse_apps as ma  # noqa: E402
from deployspecter.experimental.stepping import statement_id  # noqa: E402


def curated() -> list[tuple[str, Any]]:
    return [("curated#0", ri.MIXED_DEFAULT),
            ("curated#1", {"setup_jobs": 1, "old_push_jobs": 0, "old_worker_handles": 0, "new_push_jobs": 1})]


def systematic(limit: int) -> list[tuple[str, Any]]:
    out = []
    for setup in (2, 5):
        for old_push in (0, 3):
            for handles in (0, 8):
                for new_push in (0, 2):
                    out.append((f"systematic#{len(out)}", {"setup_jobs": setup, "old_push_jobs": old_push,
                                                           "old_worker_handles": handles, "new_push_jobs": new_push}))
    return out[:limit]


def measure(target: hz.Target, source: str, items: list[tuple[str, Any]], *, k: int, images: dict, sdists: tuple,
            out: Path) -> dict[str, Any]:
    started = time.monotonic()
    valid, rejected = [], []
    for label, w in items:
        reasons = ri.validate_mixed_workload(w) if w is not None else ["shape: not a proposal"]
        (rejected.append({"label": label, "reasons": reasons}) if reasons else valid.append((label, w)))
    rows, touching, statements = [], set(), set()
    for label, w in valid[:k]:
        run_dir = out / label.replace("#", "_")
        run_dir.mkdir()
        result = ri.run_chancy_mixed(target, old_sdist=sdists[0], new_sdist=sdists[1], out=run_dir, workload=w,
                                     images=images)
        sound = all(ph.get("isolation_ok") and (ph.get("corroboration") or {}).get("ok")
                    and all(ph["teardown"].values()) for ph in result["phases"]) and len(result["phases"]) == 4
        for ph in result["phases"]:
            trace = json.loads((run_dir / ph["variant"] / f"{ph['variant']}.trace.json").read_text())
            for rec in trace["records"]:
                if rec["kind"] in ("execute", "executemany"):
                    statements.add(statement_id(rec["sql"]))
                    if "claim_id" in rec["sql"]:
                        touching.add(statement_id(rec["sql"]))
        (run_dir / "result.json").write_text(json.dumps(result, indent=1, default=str))
        rows.append({"label": label, "workload": w, "sound": sound, "oracle_violations": result["oracle_violations"],
                     "jobs": [(j["state"], j["attempts"], j.get("has_claim_id")) for j in
                              result["final_state"].get("jobs", [])]})
    return {"source": source, "proposals": len(items), "valid": len(valid), "executed": len(rows),
            "rejected": rejected, "sound_runs": sum(r["sound"] for r in rows),
            "runs_with_oracle_violation": sum(bool(r["oracle_violations"]) for r in rows),
            "distinct_statements": len(statements), "claim_id_statements": len(touching), "runs": rows,
            "seconds": round(time.monotonic() - started, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-sdist", type=Path, required=True)
    ap.add_argument("--new-sdist", type=Path, required=True)
    ap.add_argument("--workloads", type=int, default=2)
    ap.add_argument("--muse-calls", type=int, default=0)
    ap.add_argument("--muse-n", type=int, default=4)
    ap.add_argument("--muse-usd", type=float, default=1.0)
    ap.add_argument("--env-file", type=Path, default=Path(os.path.expanduser("~/Desktop/deployspecter/.env")))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    muse_items, cost = [], None
    if a.muse_calls:
        from deployspecter.experimental.muse import client_from_env_file
        client = client_from_env_file(a.env_file, max_requests=a.muse_calls, max_estimated_usd=a.muse_usd,
                                      timeout=900)
        failed = []
        for i in range(a.muse_calls):
            try:
                raw = ma.propose(client, "chancy_mixed", n=a.muse_n, ledger=a.out / "muse_ledger.jsonl",
                                 call_label=f"muse_chancy_mixed_{i}")
            except Exception as exc:  # recorded by propose; the deterministic sources still run
                failed.append({"call": f"muse_chancy_mixed_{i}", "error": type(exc).__name__})
                continue
            muse_items += [(label, ma.mixed_proposal_to_workload(p)) for label, p in raw]
        cost = {"requests": client.requests_sent, "estimated_usd": round(client.estimated_usd, 6), "failed": failed}
    images = {}
    for label, sdist in (("old", a.old_sdist), ("new", a.new_sdist)):
        d = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
        try:
            images[label] = ri.build_image(d, chancy_sdist=sdist)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    k, sdists = a.workloads, (a.old_sdist, a.new_sdist)
    sources = [measure(target, "curated", curated(), k=k, images=images, sdists=sdists, out=a.out),
               measure(target, "systematic", systematic(16), k=k, images=images, sdists=sdists, out=a.out)]
    if a.muse_calls:
        sources.append(measure(target, "muse", muse_items, k=k, images=images, sdists=sdists, out=a.out))
    report = {"kind": "EXPERIMENTAL NONBLIND pilot on a real mixed-version deploy (Chancy 0.25.1 -> 0.26.0); no known "
                      "defect; validity, coverage and oracle outcomes; not confirmatory",
              "code_revision": hz.code_revision(), "images": images,
              "budget": {"workloads_per_source": k, "muse_calls": a.muse_calls, "muse_proposals_per_call": a.muse_n},
              "sources": sources, "muse_cost": cost}
    (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    for s in sources:
        print(s["source"], {x: s[x] for x in ("proposals", "valid", "executed", "sound_runs",
                                              "runs_with_oracle_violation", "distinct_statements",
                                              "claim_id_statements", "seconds")},
              "workloads", [r["workload"] for r in s["runs"]], "rejected", [r["reasons"] for r in s["rejected"]][:2])
    print("muse", cost)
    return 0 if all(r["sound"] for s in sources for r in s["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
