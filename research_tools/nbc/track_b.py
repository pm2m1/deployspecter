"""ds-nbc/1 Track B (RA-NONBLIND paired time-to-detection): the frozen manifest, case admission, and the
predeclared analysis.

    track_b.py freeze  --out docs/experimental_mvp/nonblind_corpus/track_b/MANIFEST.json
    track_b.py admit   --manifest M --spec CASE_SPEC.json --track-a-class REPRODUCED|NOT_REPRODUCED
    track_b.py order   --manifest M --case CASE_ID         # this case's predeclared arm order
    track_b.py analyze --manifest M --runs DIR ... --out DIR

**Admission:** every Track A case that is executable with a holding control, whether or not RA-DET reproduced
it (REPRODUCED or NOT REPRODUCED), is admitted, and its spec's hashes are logged **before** that case's first RA-MD
request. Cases that are REFUSED or CONTROL FAILED are reported but not compared.

**Analysis:**
- The ledgers are converted to ``paired_eval.py`` run records: the buggy slot's runs in replicate order, with each
  run's fixed-slot counterpart.
- ``paired_eval`` gives the comparison: exact McNemar and Gehan sign tests, KM medians, and the insufficient-sample
  rule (fewer than 10 paired defect cases).
- Added here: exact Clopper–Pearson 95% intervals for each arm's detection rate and for the discordant split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "research_tools"))
sys.path.insert(0, str(REPO / "research_tools" / "confirmatory"))

ARMS_RUN = ["RA-DET", "RA-MD"]
NOT_APPLICABLE = {
    "RA-MS": "draft v2 §2: RA-DET's drivers with Muse state goals through the deterministic constructor. These "
             "integrations have no state-goal stage: all initial state is created by the workload's own public API "
             "calls (push, enqueue, cancel) inside the run. A Muse-state arm would only relabel RA-DET.",
    "RA-MDS": "draft v2 §2: Muse proposals plus Muse state goals through the constructor. No state-goal stage "
              "(as RA-MS), so it would only relabel RA-MD.",
    "RA-DET-C": "draft v2 §2: RA-DET's goals with the constructor replaced by literal translation. There is no "
                "constructor in these integrations to replace, so the control has nothing to isolate.",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest() -> dict[str, Any]:
    import muse_workloads as mw
    import run as crun
    files = ["research_tools/nbc/run_nbc.py", "research_tools/nbc/muse_workloads.py", "research_tools/nbc/budget.py",
             "research_tools/nbc/track_b.py", "research_tools/confirmatory/real_executor.py",
             "research_tools/paired_eval.py", "docs/experimental_mvp/protocol/PROSPECTIVE_MUSE_PLAN.md",
             "docs/experimental_mvp/nonblind_corpus/SELECTION_RULE.md"]
    return {
        "experiment": "ds-nbc/1 Track B", "label": "RA-NONBLIND EXPLORATORY: developer-selected development cases, "
        "developer-written oracles; never confirmatory, never pooled with confirmatory results",
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "frozen_before": "any RA-MD request on any case",
        "arms_run": ARMS_RUN, "arms_not_applicable": NOT_APPLICABLE,
        "arm_definitions": "AMENDMENT_003_DRAFT_v2.md §2, unchanged: RA-DET = curated workloads and/or the "
                           "systematic grid (exactly 3 per case, frozen per case before its first live run); RA-MD = "
                           "validated Muse workload proposals",
        "budgets": {**crun.plan_budgets()["budgets"], "source": "PROSPECTIVE_MUSE_PLAN.md §5, unchanged"},
        "deviation_cost": "the plan's $10 per evaluation is replaced by the sprint's stricter $5 total Meta cap "
                          "(research_tools/nbc/budget.py), shared with onb-pilot/2",
        "seeds": "real_executor.seed_for(case_id, replicate, i): identical for both arms",
        "schedule_explorer": "coordinated.Policy 'seeded' via each integration's run(); unchanged for both arms",
        "muse": {"proposer": mw.VERSION, "model": "muse-spark-1.3 (DEFAULT_MODEL; MODEL_NAME as in .env, recorded "
                                                  "per call)", "reasoning_effort": "medium", "store": False,
                 "max_output_tokens": mw.MAX_OUTPUT_TOKENS, "R_requests": "plan R", "P_proposals": "plan P",
                 "context": "the integration's MUSE_CONTEXT (registry and R1 schema only) and its workload schema",
                 "instructions_sha256": hashlib.sha256(mw.INSTRUCTIONS.encode()).hexdigest()},
        "accounting": "every Muse proposal stays in the denominator (executor invalid_proposals); runs are never "
                      "retried; REFUSED and INCOMPLETE are final; transport failures count against R",
        "arm_order": "per case: sha256(case_id) first byte even -> RA-DET then RA-MD; odd -> RA-MD then RA-DET "
                     "(image builds are outside the timed region in both orders)",
        "admission": "every Track A case classified REPRODUCED or NOT REPRODUCED, logged before its first RA-MD "
                     "request; REFUSED and CONTROL FAILED cases are reported only",
        "analysis": {"primary": "case-level detection RA-MD vs RA-DET (exact McNemar), schedules to first verified "
                                "detection (paired Gehan sign test, censored at B), via paired_eval.py",
                     "verdict_rule": "paired_eval: INSUFFICIENT SAMPLE below 10 paired defect cases; no claim of "
                                     "significance otherwise unless p < 0.05",
                     "uncertainty": "exact Clopper-Pearson 95% intervals: per-arm detection rate; discordant split",
                     "unit": "the historical case; seeds and replicates are never cases",
                     "post_hoc": "anything not listed here is labelled POST-HOC"},
        "code_sha256": {f: sha(REPO / f) for f in files},
    }


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float] | None:
    """Exact (Clopper-Pearson) two-sided interval for k successes in n, by bisection on the binomial tails."""
    if n == 0:
        return None
    from math import comb

    def cdf(x: int, p: float) -> float:
        return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(x + 1)) if x >= 0 else 0.0

    def bisect(pred: Any) -> float:  # pred is True below the root, False above it
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if pred(mid) else (lo, mid)
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else bisect(lambda p: 1 - cdf(k - 1, p) < alpha / 2)
    upper = 1.0 if k == n else bisect(lambda p: cdf(k, p) > alpha / 2)
    return round(lower, 4), round(upper, 4)


def to_paired(ledgers: list[Path], buggy: dict[str, str]) -> list[dict[str, Any]]:
    import run_nbc
    rows = []
    for out in ledgers:
        runs = [e["run"] for e in run_nbc.Ledger(out / "RESULTS_LEDGER.jsonl").entries()]
        for r in sorted(runs, key=lambda r: (r["case_id"], r["arm"], r["replicate"])):
            b = buggy.get(r["case_id"], "R1")
            if r["slot"] != b:
                continue
            fixed = [x for x in runs if x["case_id"] == r["case_id"] and x["arm"] == r["arm"]
                     and x["replicate"] == r["replicate"] and x["slot"] != b]
            first = r["replicate"] == min(x["replicate"] for x in runs if x["case_id"] == r["case_id"]
                                          and x["arm"] == r["arm"])
            rows.append({"case": r["case_id"], "arm": r["arm"], "run_id": f"r{r['replicate']}",
                         "schedules_used": r["schedules_used"], "detected": r["verdict"] == "VIOLATED",
                         "first_detection_schedule": r["first_violation_schedule"],
                         "wall_seconds": r["wall_seconds"], "invalid_proposals": r["invalid_proposals"] if first else 0,
                         "refused": r["verdict"] in ("REFUSED", "INCOMPLETE"),
                         "fixed_version_violation": any(x["verdict"] == "VIOLATED" for x in fixed),
                         "tokens_in": r["tokens_in"] if first else 0, "tokens_out": r["tokens_out"] if first else 0,
                         "est_usd": r["est_usd"] if first else 0, "schedule_budget": 12})
    return rows


def main() -> int:
    a = argparse.ArgumentParser()
    sub = a.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--out", type=Path, required=True)
    ad = sub.add_parser("admit")
    ad.add_argument("--manifest", type=Path, required=True)
    ad.add_argument("--spec", type=Path, required=True)
    ad.add_argument("--track-a-class", required=True, choices=["REPRODUCED", "NOT_REPRODUCED"])
    od = sub.add_parser("order")
    od.add_argument("--manifest", type=Path, required=True)
    od.add_argument("--case", required=True)
    an = sub.add_parser("analyze")
    an.add_argument("--manifest", type=Path, required=True)
    an.add_argument("--runs", nargs="+", type=Path, required=True)
    an.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    if o.cmd == "freeze":
        if o.out.exists():
            raise SystemExit("refused: the manifest is already frozen")
        o.out.parent.mkdir(parents=True, exist_ok=True)
        o.out.write_text(json.dumps(manifest(), indent=1, sort_keys=True) + "\n")
        print(o.out, sha(o.out))
        return 0
    if o.cmd == "order":
        first = hashlib.sha256(o.case.encode()).digest()[0]
        print(" ".join(ARMS_RUN if first % 2 == 0 else list(reversed(ARMS_RUN))))
        return 0
    log = o.manifest.parent / "ADMISSIONS.jsonl"
    if o.cmd == "admit":
        s = json.loads(o.spec.read_text())
        e = {"case_id": s["case_id"], "spec": str(o.spec), "spec_sha256": sha(o.spec),
             "integration_sha256": sha(REPO / s["integration"]), "oracle_sha256": s["oracle"]["sha256"],
             "track_a_class": o.track_a_class, "manifest_sha256": sha(o.manifest),
             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with log.open("a") as fh:
            fh.write(json.dumps(e, sort_keys=True) + "\n")
        print(json.dumps(e))
        return 0
    import paired_eval as pe
    adm = [json.loads(x) for x in log.read_text().splitlines()]
    buggy = {}
    for e in adm:
        buggy[e["case_id"]] = json.loads(Path(e["spec"]).read_text()).get("buggy", "R1")
    rows = [r for r in to_paired(o.runs, buggy) if r["case"] in buggy]
    o.out.mkdir(parents=True, exist_ok=True)
    runs_path = o.out / "paired_runs.jsonl"
    runs_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    table, excluded = pe.paired_table(pe.load([runs_path]), "RA-MD", "RA-DET")
    res = pe.compare(table, "RA-MD", "RA-DET", excluded)
    cases = sorted({c for (c, _a) in table})
    det = {arm: sum(1 for c in cases if table[(c, arm)]["detected"]) for arm in ARMS_RUN}
    b = sum(1 for c in cases if table[(c, "RA-MD")]["detected"] and not table[(c, "RA-DET")]["detected"])
    c2 = sum(1 for c in cases if table[(c, "RA-DET")]["detected"] and not table[(c, "RA-MD")]["detected"])
    res["uncertainty"] = {"cases": len(cases),
                          "detection_rate_ci95": {arm: {"k": det[arm], "n": len(cases),
                                                        "ci": clopper_pearson(det[arm], len(cases))}
                                                  for arm in ARMS_RUN},
                          "discordant": {"only_RA-MD": b, "only_RA-DET": c2,
                                         "share_RA-MD_ci95": clopper_pearson(b, b + c2)}}
    res["cells"] = {f"{c}|{a}": v for (c, a), v in table.items()}
    res["label"] = "RA-NONBLIND EXPLORATORY"
    (o.out / "ANALYSIS.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: res[k] for k in ("verdict", "uncertainty") if k in res}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
