"""EXPERIMENTAL, NONBLIND exploratory comparison on the procrastinate 3.3.0 → 3.4.0 rolling-upgrade case: curated,
systematic-deterministic and Muse-generated overlap workloads, through the identical six-phase pipeline
(``research_tools/isolated/run_rolling_upgrade.py``), with equal budgets: K executed workloads per source.

    python research_tools/mvp_compare_procrastinate.py --k 2 --muse-n 4 --muse-usd 2 --out DIR \
        --old-sdist SDISTS/procrastinate-3.3.0.tar.gz --new-sdist SDISTS/procrastinate-3.4.0.tar.gz

Muse returns structured JSON only. The deterministic validator (``muse_procrastinate.validate``) accepts or rejects
each proposal, and the harness alone builds actor arguments from accepted ones. No model output is executed as code
or SQL. Every request, raw reply and rejection is written when it happens. Reads only the MODEL_* keys from the
main repository's ``.env``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from deployspecter.experimental import muse_procrastinate as mp  # noqa: E402

CURATED = [  # the frozen case's overlap workload, then a second hand-written one aimed at cross-version retries
    {"old_jobs": [{"kind": "ok"}, {"kind": "flaky", "fail_in": "3.3.0"}, {"kind": "flaky", "fail_in": "3.4.0"},
                  {"kind": "fail"}],
     "new_jobs": [{"kind": "ok"}, {"kind": "flaky", "fail_in": "3.3.0"}, {"kind": "flaky", "fail_in": "3.4.0"},
                  {"kind": "fail"}], "admin": [1]},
    {"old_jobs": [{"kind": "flaky", "fail_in": "3.4.0"}, {"kind": "flaky", "fail_in": "3.4.0"}],
     "new_jobs": [{"kind": "flaky", "fail_in": "3.3.0"}, {"kind": "flaky", "fail_in": "3.3.0"}], "admin": [1, 2]},
]


def coverage(run_dir: Path) -> dict[str, Any]:
    """From the corroborated overlap phase, and the final checker: the migration-touching and cross-version
    operations actually executed."""
    r = json.loads((run_dir / "report.json").read_text())
    ov = run_dir / "3_overlap" / "coordinator.json"
    c = {"v1_retries_by_3.3.0": 0, "v2_retries_by_3.4.0_worker": 0, "v2_admin_retries": 0}
    if ov.exists():
        coord = json.loads(ov.read_text())
        for d in coord["decisions"]:
            text = coord["statements"].get(d["statement_id"], d["sql"])
            if "procrastinate_retry_job_v1" in text:
                c["v1_retries_by_3.3.0"] += 1
            elif "procrastinate_retry_job_v2" in text:
                c["v2_admin_retries" if d["actor"] == "new_admin" else "v2_retries_by_3.4.0_worker"] += 1
    cc = r.get("case_checker") or {}
    pushed = {int(p["n"]): p["version"] for res in (r.get("results") or {}).values() if isinstance(res, dict)
              for p in res.get("pushed", [])}
    ex = {int(n): v for n, v in (cc.get("executions") or {}).items()}
    c["jobs_executed_by_other_release"] = sum(1 for n, vs in ex.items() if any(v != pushed.get(n) for v in vs))
    c["jobs_executed_by_both_releases"] = sum(1 for vs in ex.values() if len(set(vs)) == 2)
    viol = dict(cc.get("violations") or {})
    viol.pop("R2", None)  # erratum E1: the frozen R2 equality is an oracle error
    r5 = [x for x in viol.get("R5", []) if "'doing'" not in x]  # erratum E2: a non-atomic after-read
    if r5:
        viol["R5"] = r5
    else:
        viol.pop("R5", None)
    e1 = r.get("erratum_E1") or {}
    return {"verdict": r.get("verdict"), "phases_sound": sum(p["verdict"] == "SOUND" for p in r.get("phases", [])),
            "statements": sum((p["corroboration"] or {}).get("matched") or 0 for p in r.get("phases", [])),
            "mismatched": sum((p["corroboration"] or {}).get("params_mismatched") or 0 for p in r.get("phases", [])),
            "unchecked": sum((p["corroboration"] or {}).get("params_unchecked") or 0 for p in r.get("phases", [])),
            "coverage": c, "violations_after_errata": viol,
            "source_relation": f"{e1.get('relation_holds')}/{e1.get('jobs')}",
            "documented_bound": f"{e1.get('bound_holds')}/{e1.get('jobs')}"}


def run(workload: dict[str, Any], out: Path, a: Any) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "DATABASE_URL")}
    proc = subprocess.run([sys.executable, str(REPO / "research_tools/isolated/run_rolling_upgrade.py"),
                           "--old-sdist", str(a.old_sdist), "--new-sdist", str(a.new_sdist), "--overlap",
                           json.dumps(workload), "--out", str(out)], capture_output=True, text=True, timeout=1800,
                          env=env)
    (out.parent / f"{out.name}.log").write_text(proc.stdout[-20000:] + "\n--- stderr\n" + proc.stderr[-20000:])
    return {"exit": proc.returncode, **(coverage(out) if (out / "report.json").exists() else {"verdict": "CRASHED"})}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--muse-n", type=int, default=4)
    ap.add_argument("--muse-usd", type=float, default=2.0)
    ap.add_argument("--env-file", type=Path, default=Path.home() / "Desktop/deployspecter/.env")
    ap.add_argument("--old-sdist", type=Path, required=True)
    ap.add_argument("--new-sdist", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists (evidence is never overwritten)")
    a.out.mkdir(parents=True)
    rows: dict[str, Any] = {"curated": [], "systematic": [], "muse": []}
    proposals: dict[str, Any] = {"curated": CURATED[:a.k], "systematic": mp.systematic(a.k)}
    from deployspecter.experimental.muse import client_from_env_file
    client = client_from_env_file(a.env_file, max_requests=1, max_estimated_usd=a.muse_usd)
    raw = mp.propose(client, n=a.muse_n, ledger=a.out / "muse_ledger.jsonl", call_label="muse_procrastinate_0")
    validated = []
    for label, p in raw:
        w, problems = mp.validate(p)
        entry = {"label": label, "valid": w is not None, "problems": problems, "workload": w,
                 "rationale": p.get("rationale") if isinstance(p, dict) else None}
        validated.append(entry)
        with (a.out / "muse_validation.jsonl").open("a") as f:  # every acceptance and rejection, at once
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    proposals["muse"] = [e["workload"] for e in validated if e["valid"]][:a.k]
    summary: dict[str, Any] = {"k": a.k, "muse_requested": a.muse_n, "muse_returned": len(raw),
                               "muse_valid": sum(e["valid"] for e in validated),
                               "muse_estimated_usd": round(client.estimated_usd, 6),
                               "prompt_sha256": mp.prompt_hash(a.muse_n), "model": client.model}
    for source, ws in proposals.items():
        for i, w in enumerate(ws):
            res = run(w, a.out / f"{source}_{i}", a)
            rows[source].append({"workload": w, **res})
            print(json.dumps({"source": source, "i": i, **{k: v for k, v in res.items() if k != "workload"}},
                             default=str), flush=True)
    summary["rows"] = rows
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return 0 if all(r.get("verdict") == "SOUND" for rs in rows.values() for r in rs) else 1


if __name__ == "__main__":
    sys.exit(main())
