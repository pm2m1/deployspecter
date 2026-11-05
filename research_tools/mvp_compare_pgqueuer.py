"""EXPERIMENTAL measurement: curated vs systematic vs Muse structured workloads on PGQueuer 1.6.0's real lifecycle,
each run in the isolated runner through the stepped Driver adapter.

There is no defective or corrected PGQueuer version here, so this measures validity, coverage and oracle outcomes,
not detection. Equal budget: the first K valid workloads per source, one isolated run each.

    DS_MVP_PORT=55441 python research_tools/mvp_compare_pgqueuer.py --pgqueuer-sdist PATH --workloads 4 \\
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
    return [("curated#0", ri.PGQ_DEFAULT_WORKLOAD),
            ("curated#1", {"jobs": [{"payload": "solo", "priority": 0}], "batch_size": 1}),
            ("curated#2", {"jobs": [{"payload": "low", "priority": 1}, {"payload": "high", "priority": 9},
                                    {"payload": "mid", "priority": 5}], "batch_size": 1}),
            ("curated#3", {"jobs": [{"payload": f"b{i}", "priority": 0} for i in range(4)], "batch_size": 2})]


def systematic(limit: int) -> list[tuple[str, Any]]:
    out = []
    for n in (1, 3, 5):
        for pattern in ("flat", "descending"):
            for batch in (1, 3):
                jobs = [{"payload": f"j{i}", "priority": 0 if pattern == "flat" else max(0, 9 - i)} for i in range(n)]
                out.append((f"systematic#{len(out)}", {"jobs": jobs, "batch_size": batch}))
    return out[:limit]


def measure(target: hz.Target, source: str, items: list[tuple[str, Any]], *, k: int, image: Any, out: Path) -> dict:
    started = time.monotonic()
    valid, rejected = [], []
    for label, w in items:
        reasons = ri.validate_pgq_workload(w) if w is not None else ["shape: not a proposal"]
        (rejected.append({"label": label, "reasons": reasons}) if reasons else valid.append((label, w)))
    statements: set[str] = set()
    rows = []
    for label, w in valid[:k]:
        run_dir = out / label.replace("#", "_")
        run_dir.mkdir()
        r = ri.run_smoke(target, variant="pgqueuer_lifecycle", schedule=[], out=run_dir, image=image,
                         mode="pgqueuer", pgq_workload=w)
        trace = json.loads((run_dir / "pgqueuer_lifecycle.trace.json").read_text())
        ids = {statement_id(rec["sql"]) for rec in trace["records"] if rec["kind"] in ("execute", "executemany")}
        statements |= ids
        c = r.get("corroboration") or {}
        rows.append({"label": label, "workload": w, "corroborated": c.get("ok"), "statements": c.get("matched"),
                     "oracle_violations": r.get("oracle_violations"), "isolation_ok": r.get("isolation_ok"),
                     "teardown_ok": all(r["teardown"].values()), "distinct_statements": len(ids),
                     "trace_sha256": r.get("trace_sha256")})
        (run_dir / "result.json").write_text(json.dumps(r, indent=1, default=str))
    return {"source": source, "proposals": len(items), "valid": len(valid), "executed": len(rows),
            "rejected": rejected, "runs_corroborated": sum(bool(x["corroborated"]) for x in rows),
            "runs_oracle_holds": sum(x["oracle_violations"] == [] for x in rows),
            "distinct_statements_reached": len(statements), "runs": rows,
            "seconds": round(time.monotonic() - started, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pgqueuer-sdist", type=Path, required=True)
    ap.add_argument("--workloads", type=int, default=4)
    ap.add_argument("--muse-calls", type=int, default=0)
    ap.add_argument("--muse-n", type=int, default=6)
    ap.add_argument("--muse-usd", type=float, default=1.0)
    ap.add_argument("--muse-timeout", type=float, default=900.0)
    ap.add_argument("--env-file", type=Path, default=Path(os.path.expanduser("~/Desktop/deployspecter/.env")))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    muse_items: list[tuple[str, Any]] = []
    cost = None
    if a.muse_calls:
        from deployspecter.experimental.muse import client_from_env_file
        client = client_from_env_file(a.env_file, max_requests=a.muse_calls, max_estimated_usd=a.muse_usd,
                                      timeout=a.muse_timeout)
        failed = []
        for i in range(a.muse_calls):
            try:
                raw = ma.propose(client, "pgqueuer", n=a.muse_n, ledger=a.out / "muse_ledger.jsonl",
                                 call_label=f"muse_pgqueuer_{i}")
            except Exception as exc:  # recorded by propose; the deterministic sources still run
                failed.append({"call": f"muse_pgqueuer_{i}", "error": type(exc).__name__})
                continue
            muse_items += [(label, ma.pgq_proposal_to_workload(p)) for label, p in raw]
        cost = {"requests": client.requests_sent, "estimated_usd": round(client.estimated_usd, 6),
                "failed_calls": failed}
    build_dir = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
    try:
        image = ri.build_image(build_dir, pgqueuer_sdist=a.pgqueuer_sdist)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)
    k = a.workloads
    sources = [measure(target, "curated", curated(), k=k, image=image, out=a.out),
               measure(target, "systematic", systematic(12), k=k, image=image, out=a.out)]
    if a.muse_calls:
        sources.append(measure(target, "muse", muse_items, k=k, image=image, out=a.out))
    report = {"kind": "EXPERIMENTAL measurement on PGQueuer 1.6.0's lifecycle (no defect, no corrected version: "
                      "validity, coverage and oracle outcomes only); not a benchmark",
              "code_revision": hz.code_revision(), "image": {"tag": image[0], "id": image[1]},
              "budget": {"workloads_per_source": k, "runs_per_workload": 1, "muse_calls": a.muse_calls,
                         "muse_proposals_per_call": a.muse_n}, "sources": sources, "muse_cost": cost}
    (a.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    for s in sources:
        print(s["source"], {x: s[x] for x in ("proposals", "valid", "executed", "runs_corroborated",
                                              "runs_oracle_holds", "distinct_statements_reached", "seconds")},
              "rejected", [r["reasons"] for r in s["rejected"]][:3])
    print("muse", cost)
    ok = all(x["corroborated"] and x["oracle_violations"] == [] and x["teardown_ok"] for s in sources
             for x in s["runs"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
