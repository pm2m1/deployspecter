"""EXPERIMENTAL, NONBLIND: the historical case chancy-2895c4a (docs/experimental_mvp/historical/chancy_2895c4a/CASE.md).

    python research_tools/isolated/run_historical.py --source SDISTS/chancy-git-80f570f.tar --schedule forced \
        --repeat 3 --out DIR
    python research_tools/isolated/run_historical.py --source SDISTS/chancy-git-2895c4a.tar --schedule explore \
        --seeds 0-9 --out DIR

One Chancy revision at a time (the documented single-version deployment). Each run:
1. A fresh schema, migrated by **that revision itself** (no other version touches it). The host checks the
   revision's own schema version and that no job exists.
2. One restricted container holding that revision, with two actors on their own sessions: a worker, and a producer
   that pushes one job through the public `push` and then cancels it through the public `cancel_job`. Every
   statement is granted by the coordinator (a second container, with no database credentials).
3. Schedules:
   * ``forced``: the order the maintainers' regression test constructs. The cancel waits for the worker's claim; the
     worker's completion flush waits for the cancel.
   * ``explore``: only the cancel-after-claim gate. Seeded policies decide whether the flush lands before or after
     the cancel.
4. The server log corroborates every statement, the host reads the final state independently, and the frozen case
   checker applies H0–H2 only to a SOUND run.
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
from deployspecter.experimental import historical_oracles as ho  # noqa: E402

CASE = "chancy-2895c4a"
FROZEN = {"case": "docs/experimental_mvp/historical/chancy_2895c4a/CASE.md",
          "checker": "src/deployspecter/experimental/historical_oracles.py"}
SCHEMA_OF = {"chancy-0.25.1.tar.gz": "6", "chancy-git-80f570f.tar": "7", "chancy-git-2895c4a.tar": "8",
             "chancy-0.26.0.tar.gz": "8"}
ROLE_OF = {"chancy-0.25.1.tar.gz": "buggy (last release before the fix)",
           "chancy-git-80f570f.tar": "buggy (fix parent)",
           "chancy-git-2895c4a.tar": "fixed (fix commit)", "chancy-0.26.0.tar.gz": "fixed (first release with it)"}


def spec(schedule: str, max_attempts: int) -> dict[str, Any]:
    worker = {"actor": "worker", "fn": "work", "args": {"timeout_s": 60}}
    push = {"actor": "push", "fn": "push", "args": {"ns": [0], "queue": "q_shared", "max_attempts": max_attempts,
                                                     "cancel": [0]}}
    gates = rc._window(["push"], ["worker"]) + [
        {"actor": "push", "match": "SET state = 'failed'", "until": [{"actor": "worker", "marker": "claim",
                                                                        "count": 1}]}]
    if schedule == "forced":
        gates.append({"actor": "worker", "match": rc.FLUSH, "from": [{"actor": "worker", "marker": "claim",
                                                                       "count": 1}],
                      "until": [{"actor": "push", "marker": "cancel", "count": 1}]})
    return {"processes": {"A": [worker, push]}, "gates": gates, "covers": [0], "consumer_of": {}}


def setup(target: hz.Target, image: Any, out: Path) -> dict[str, Any]:
    d = out / "0_setup"
    d.mkdir(parents=True)
    r = ri.run_smoke(target, variant="0_setup", schedule=[], out=d, image=image, mode="chancy-plan", fresh_schema=True,
                     keep_schema=True, extra_env={"DS_CHANCY_PLAN": json.dumps([{"actors": [
                         {"actor": "setup", "fn": "co_setup"}]}])})
    (d / "report.json").write_text(json.dumps(r, indent=1, default=str))
    return r


def one(target: hz.Target, image: Any, source: Path, schedule: str, policy: dict[str, Any], max_attempts: int,
        out: Path) -> dict[str, Any]:
    out.mkdir(parents=True)
    s = {**spec(schedule, max_attempts), "expect_schema": SCHEMA_OF[source.name]}
    pre = setup(target, image, out)
    report: dict[str, Any] = {"case": CASE, "nonblind": True, "source": source.name, "role": ROLE_OF[source.name],
                              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                              "git": ri.CHANCY_GIT.get(source.name), "schedule": schedule, "policy": policy,
                              "max_attempts": max_attempts, "spec": s, "image": list(image),
                              "frozen_sha256": {k: hashlib.sha256((ri.REPO / v).read_bytes()).hexdigest()
                                                for k, v in FROZEN.items()},
                              "setup": {"corroboration_ok": pre["corroboration"]["ok"],
                                        "isolation_ok": pre.get("isolation_ok"),
                                        "version": (pre.get("pinned_sources") or {}).get("version"),
                                        "statements": pre["corroboration"].get("matched")}}
    if not (pre.get("isolation_ok") and pre["corroboration"]["ok"]):
        report["verdict"] = "REFUSED"
        report["reason"] = "the setup wasn't isolated and corroborated"
    else:
        sim = rc.simultaneous(target, {"A": image}, s, policy, out, label=CASE)
        report["run"] = sim
        report["summary"] = rc.summary({**sim, "spec": s})
        report["verdict"] = sim["verdict"]
        if sim["verdict"] == "SOUND":
            jobs = sim["final_state"]["jobs_detail"]
            report["case_oracle"] = ho.chancy_2895c4a(jobs, sim["raw_results"],
                                                      has_claim_column=SCHEMA_OF[source.name] == "8")
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True, help="a pinned Chancy sdist or commit tree")
    ap.add_argument("--schedule", choices=["forced", "explore"], required=True)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--max-attempts", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists (evidence is never overwritten)")
    if a.source.name not in SCHEMA_OF:
        raise SystemExit(f"refused: {a.source.name} isn't a source of this case")
    secret = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    d = Path(tempfile.mkdtemp(prefix="ds_iso_build_"))
    try:
        image = ri.build_image(d, chancy_sdist=a.source)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if a.schedule == "forced":
        policies = [{"kind": "alternate"}] * a.repeat
    else:
        lo, hi = (int(x) for x in a.seeds.split("-"))
        policies = [{"kind": "seeded", "seed": s} for s in range(lo, hi + 1)]
    rows = []
    for i, policy in enumerate(policies):
        tag = f"{i:02d}_{policy['kind']}{policy.get('seed', '')}"
        r = one(target, image, a.source, a.schedule, policy, a.max_attempts, a.out / tag)
        co = r.get("case_oracle") or {}
        row = {"run": tag, "source": a.source.name, "role": r["role"], "verdict": r["verdict"],
               "case_verdict": co.get("verdict"), "H1": co.get("H1"), "H2": co.get("H2"), "H0": co.get("H0"),
               "final": [(j["state"], j["attempts"], j["has_claim_id"], j["completed"])
                         for j in ((r.get("run") or {}).get("final_state") or {}).get("jobs_detail", [])],
               "corroboration": (r.get("summary") or {}).get("corroboration"),
               "refusal_reasons": (r.get("run") or {}).get("refusal_reasons")}
        rows.append(row)
        print(json.dumps(row, default=str), flush=True)
    (a.out / "summary.json").write_text(json.dumps(rows, indent=1, default=str))
    return 0 if all(r["verdict"] == "SOUND" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
