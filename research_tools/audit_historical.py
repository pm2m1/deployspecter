"""EXPERIMENTAL: audit the saved chancy-2895c4a record against itself, instead of trusting its summary.

    python research_tools/audit_historical.py docs/experimental_mvp/historical/chancy_2895c4a/runs

For every run directory under ``final_*``, it re-checks:
* the frozen case files' sha256, recorded in the report, against the committed files;
* the source archive's sha256 against the pins in ``run_isolated.py``;
* the version the container verified by source hash;
* the precondition schema version;
* the trace sha256, recomputed from the saved trace (the same canonical JSON);
* corroboration: ok, with 0 mismatched and 0 unchecked;
* the case verdict, recomputed by running the frozen checker on the saved final state;
* the statement positions of push, claim, cancel and flush, so that forced schedules can be compared across revisions.

It needs no database and no network.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))
import run_isolated as ri  # noqa: E402

from deployspecter.experimental import historical_oracles as ho  # noqa: E402

FROZEN = {"case": REPO / "docs/experimental_mvp/historical/chancy_2895c4a/CASE.md",
          "checker": REPO / "src/deployspecter/experimental/historical_oracles.py"}
EXPECT = {"chancy-git-80f570f.tar": ("git-80f570f", "7"), "chancy-git-2895c4a.tar": ("git-2895c4a", "8"),
          "chancy-0.25.1.tar.gz": ("0.25.1", "6"), "chancy-0.26.0.tar.gz": ("0.26.0", "8")}
PINS = {**{k: v[0] for k, v in ri.CHANCY_GIT.items()}, **ri.CHANCY_SDISTS}
KEY = {"claim": "WITH selected_jobs AS", "push": 'INSERT INTO "chancy_jobs"', "cancel": "SET state = 'failed'",
       "flush": 'UPDATE "chancy_jobs" SET state = %(state)s'}


def positions(run: dict) -> list[tuple[int, str]]:
    coord = run["coordinator"]
    out = []
    for d in coord["decisions"]:
        text = coord["statements"].get(d["statement_id"], d["sql"])
        what = next((k for k, n in KEY.items() if n in text), None)
        if what and (what != "claim" or d["actor"] == "worker"):
            out.append((d["grant"], what))
    return out


def audit(run_dir: Path) -> dict:
    r = json.loads((run_dir / "report.json").read_text())
    p = []  # problems
    for k, f in FROZEN.items():
        if r["frozen_sha256"][k] != hashlib.sha256(f.read_bytes()).hexdigest():
            p.append(f"frozen {k} hash differs from the committed file")
    if PINS.get(r["source"]) != r["source_sha256"]:
        p.append("source archive hash isn't the pinned one")
    version, schema = EXPECT[r["source"]]
    if r["setup"]["version"] != version:
        p.append(f"setup ran {r['setup']['version']}, not {version}")
    run = r["run"]
    if run["pinned_sources"]["A"]["version"] != version:
        p.append(f"container verified {run['pinned_sources']['A']['version']}, not {version}")
    if run["precondition"]["schema_version"] != [{"version_of": "chancy", "version": schema}]:
        p.append(f"precondition schema {run['precondition']['schema_version']}")
    trace = json.loads((run_dir / "A.trace.json").read_text())
    if hashlib.sha256(json.dumps(trace, sort_keys=True, default=str).encode()).hexdigest() != run["trace_sha256"]["A"]:
        p.append("trace sha256 doesn't match the saved trace")
    c = run["corroboration"]
    if not (c["ok"] and c["params_mismatched"] == 0 and c["params_unchecked"] == 0 and not c["problems"]):
        p.append(f"corroboration {c['ok']} {c['params_mismatched']} {c['params_unchecked']} {c['problems']}")
    recomputed = ho.chancy_2895c4a(run["final_state"]["jobs_detail"], run["raw_results"],
                                   has_claim_column=schema == "8")
    if recomputed["verdict"] != r["case_oracle"]["verdict"]:
        p.append(f"recomputed verdict {recomputed['verdict']} != recorded {r['case_oracle']['verdict']}")
    if not all(run["teardown"].values()):
        p.append(f"teardown {run['teardown']}")
    return {"run": f"{run_dir.parent.name}/{run_dir.name}", "source": r["source"], "verdict": r["verdict"],
            "case_verdict": recomputed["verdict"], "statements": c["matched"], "positions": positions(run),
            "problems": p}


def main() -> int:
    root = Path(sys.argv[1])
    rows = [audit(d) for d in sorted(root.glob("final_*/*/")) if (d / "report.json").exists()]
    forced = {tuple(x["positions"]) for x in rows if "/final_forced_" in "/" + x["run"]
              and "max3" not in x["run"]}
    summary = {"runs": len(rows), "with_problems": sum(1 for x in rows if x["problems"]),
               "sound": sum(1 for x in rows if x["verdict"] == "SOUND"),
               "statements": sum(x["statements"] for x in rows),
               "distinct_forced_positions": [list(t) for t in forced],
               "verdicts": {}}
    for x in rows:
        k = f"{x['run'].split('/')[0]}"
        summary["verdicts"].setdefault(k, {}).setdefault(x["case_verdict"], 0)
        summary["verdicts"][k][x["case_verdict"]] += 1
    print(json.dumps({"summary": summary, "runs": rows}, indent=1))
    return 0 if summary["with_problems"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
