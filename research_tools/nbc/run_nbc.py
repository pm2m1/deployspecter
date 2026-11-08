"""RA-NONBLIND orchestrator for ds-nbc/1. It runs the confirmatory arm executor (``ds-confirm-exec/1``, unchanged) on
NONBLIND development cases, outside the real-mode gates. The cases are developer-selected, the oracles are
developer-written, and every record is stamped ``RA-NONBLIND``. Nothing here is confirmatory or may be pooled with
confirmatory results.

    run_nbc.py run --cases SPEC.json ... --arms RA-DET [RA-MD] --out DIR [--replicates 0 1 2] [--env-file F]
    run_nbc.py summarize --out DIR

A case spec (JSON):
    {"case_id", "application", "integration": "<path to the integration module>",
     "R1": {"archive", "sha256"}, "R2": {"archive", "sha256"}, "buggy": "R1",
     "oracle": {"path", "sha256"}, "upstream": {...}, "contract": {...}, "exposure": [...]}

**Budgets:** the prospective plan's §5, read from ``PROSPECTIVE_MUSE_PLAN.md`` (B, T, 3 replicates, R, P, the cost
caps). **Seeds:** ``real_executor.seed_for``, the same for every arm. **Model:** RA-MD uses ``muse_workloads``
under the sprint ledger. Deterministic arms have a model budget of zero, enforced by the executor.
**Runs:** never retried. **Ledger:** hash-chained, with each run appended as it finishes. A rerun into the same
``--out`` resumes after the committed runs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "research_tools" / "confirmatory"))
import real_executor as rx  # noqa: E402
import run as crun  # noqa: E402

LABEL = "RA-NONBLIND"
VERSION = "ds-nbc-run/1"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_integration(path: Path) -> Any:
    s = importlib.util.spec_from_file_location(f"nbc_integ_{sha(path)[:12]}", path)
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    sys.modules[s.name] = m
    s.loader.exec_module(m)
    return m


def write_json(path: Path, obj: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, dict):
        obj = {LABEL: True, **obj}
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")
    return sha(path)


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, Any]]:
        return [json.loads(x) for x in self.path.read_text().splitlines()] if self.path.exists() else []

    def append(self, run: dict[str, Any]) -> None:
        es = self.entries()
        prev = es[-1]["hash"] if es else None
        e = {"seq": len(es), "kind": "result", LABEL: True, "run": run, "prev": prev,
             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        e["hash"] = hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(e, sort_keys=True, default=str) + "\n")

    def verify(self) -> list[str]:
        p, prev = [], None
        for i, e in enumerate(self.entries()):
            h = e.pop("hash")
            if e.get("prev") != prev or e.get("seq") != i:
                p.append(f"entry {i}: chain broken")
            if hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest() != h:
                p.append(f"entry {i}: hash mismatch")
            prev = h
        return p


def build_package(specs: list[dict[str, Any]], pkg: Path) -> None:
    for s in specs:
        d = pkg / "cases" / s["case_id"]
        d.mkdir(parents=True, exist_ok=True)
        revs = {}
        for slot in ("R1", "R2"):
            src = Path(s[slot]["archive"])
            if sha(src) != s[slot]["sha256"]:
                raise SystemExit(f"refused: {s['case_id']}/{slot}: the archive isn't the spec's sha256")
            if not (d / f"{slot}.tar").exists():
                shutil.copyfile(src, d / f"{slot}.tar")
            revs[slot] = {"sha256": s[slot]["sha256"]}
        write_json(d / "case.json", {"case_id": s["case_id"], "application": s["application"], "revisions": revs})


def cmd_run(o: argparse.Namespace) -> int:
    specs = [json.loads(Path(p).read_text()) for p in o.cases]
    o.out.mkdir(parents=True, exist_ok=True)
    integrations, oracles = {}, {}
    for s in specs:
        ip = REPO / s["integration"]
        integrations[s["application"]] = load_integration(ip)
        op = REPO / s["oracle"]["path"]
        if sha(op) != s["oracle"]["sha256"]:
            raise SystemExit(f"refused: {s['case_id']}: the oracle isn't the spec's sha256")
        oracles[s["case_id"]] = (op, s["oracle"]["sha256"])
    pkg = o.out / "package"
    build_package(specs, pkg)
    lock = crun.plan_budgets()
    write_json(o.out / "BUDGETS.json", {**lock, "source": "PROSPECTIVE_MUSE_PLAN.md §5 (unchanged)"})
    ledger = Ledger(o.out / "RESULTS_LEDGER.jsonl")
    if why := ledger.verify():
        raise SystemExit("refused: the results ledger is broken: " + "; ".join(why))
    model = manifest = None
    if any(a in rx.MUSE for a in o.arms):
        if len(integrations) != 1:
            raise SystemExit("refused: run Muse arms one application at a time (one proposer per integration)")
        import muse_workloads as mw
        integ = next(iter(integrations.values()))
        model = mw.Proposer(integ, o.out / "muse", p=lock["budgets"]["P"], env_file=o.env_file)
        manifest = {"frozen_at_adoption": f"{LABEL}: frozen in the experiment manifest, not at adoption",
                    "proposer": mw.VERSION, "prompt_sha256": mw.prompt_sha256(integ, lock["budgets"]["P"])}
    write_json(o.out / "RUN_CONFIG.json", {
        "runner": VERSION, "executor": rx.VERSION, "arms": o.arms, "replicates": o.replicates,
        "cases": {s["case_id"]: {"spec_sha256": sha(Path(p)), "integration_sha256": sha(REPO / s["integration"]),
                                 "oracle_sha256": s["oracle"]["sha256"]} for s, p in zip(specs, o.cases, strict=True)},
        "muse_manifest": manifest})
    ex = rx.Executor(package=pkg, lock=lock, arms=o.arms, oracles=oracles, integrations=integrations,
                     append=lambda r: ledger.append({**r, LABEL: True}),
                     committed=ledger.entries, workdir=o.out / "work", write_json=write_json, model=model,
                     model_manifest=manifest, replicates=o.replicates)
    result = ex.run()
    write_json(o.out / "EXECUTION.json", result)
    print(json.dumps(result))
    return 0


def summarize(out: Path, buggy: dict[str, str] | None = None) -> dict[str, Any]:
    """Per case and arm, at the case level: the first verified detection on the buggy slot, in replicate order."""
    ledger = Ledger(out / "RESULTS_LEDGER.jsonl")
    if why := ledger.verify():
        raise SystemExit("refused: the results ledger is broken: " + "; ".join(why))
    runs = [e["run"] for e in ledger.entries()]
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for r in sorted(runs, key=lambda r: (r["case_id"], r["arm"], r["replicate"], r["slot"])):
        k = (r["case_id"], r["arm"])
        b = (buggy or {}).get(r["case_id"], "R1")
        row = rows.setdefault(k, {"case_id": k[0], "arm": k[1], "detected": False, "false_positive": False,
                                  "schedules_to_detection": None, "seconds_to_detection": None,
                                  "buggy_schedules": 0, "buggy_seconds": 0.0, "fixed_schedules": 0, "runs": {},
                                  "invalid_proposals": r.get("invalid_proposals", 0), "tokens_in": r.get("tokens_in"),
                                  "tokens_out": r.get("tokens_out"), "est_usd": r.get("est_usd"),
                                  "statements_matched": 0, "params_compared": 0, "params_mismatched": 0,
                                  "params_unchecked": 0, "censored_by_T": False})
        row["runs"][r["verdict"]] = row["runs"].get(r["verdict"], 0) + 1
        row["censored_by_T"] |= r.get("censored_by") == "T"
        for s in r.get("schedules", []):
            c = s.get("corroboration") or {}
            row["statements_matched"] += c.get("matched") or 0
            row["params_compared"] += c.get("params_compared") or 0
            row["params_mismatched"] += c.get("params_mismatched") or 0
            row["params_unchecked"] += c.get("params_unchecked") or 0
        if r["slot"] == b:
            if r["verdict"] == "VIOLATED" and not row["detected"]:
                row["detected"] = True
                row["schedules_to_detection"] = row["buggy_schedules"] + r["first_violation_schedule"]
                row["seconds_to_detection"] = round(row["buggy_seconds"] + r["wall_seconds"], 1)
            row["buggy_schedules"] += r["schedules_used"]
            row["buggy_seconds"] += r["wall_seconds"]
        else:
            row["fixed_schedules"] += r["schedules_used"]
            row["false_positive"] |= r["verdict"] == "VIOLATED"
    for row in rows.values():
        row["buggy_seconds"] = round(row["buggy_seconds"], 1)
        row["verified_detection"] = row["detected"] and not row["false_positive"]
    return {LABEL: True, "summarizer": VERSION, "rows": list(rows.values()), "ledger_entries": len(runs)}


def main() -> int:
    a = argparse.ArgumentParser()
    sub = a.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cases", nargs="+", required=True)
    r.add_argument("--arms", nargs="+", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--replicates", nargs="+", type=int, default=[0, 1, 2])
    r.add_argument("--env-file", type=Path, default=Path.home() / "Desktop/deployspecter/.env")
    s = sub.add_parser("summarize")
    s.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    if o.cmd == "run":
        return cmd_run(o)
    res = summarize(o.out)
    write_json(o.out / "SUMMARY.json", res)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
