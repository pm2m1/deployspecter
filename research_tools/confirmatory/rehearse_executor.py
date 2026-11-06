"""SIMULATION rehearsal of the **real** arm executor with the real Chancy integration (chancy-sv/1), live on the
disposable PostgreSQL.

    python research_tools/confirmatory/rehearse_executor.py --out DIR --private DIR --sdists DIR

The rehearsal case is NOT blind and NOT confirmatory. It is Chancy's known development case: the pinned parent and
fix trees of 2895c4a, normalized and sealed through the real intake into the **development** split as
``sim-chancy-pair``. Every artifact is stamped SIMULATION.
* **Approvals:** none exist; the rehearsal records that the real gates refuse this setup.
* **The oracle:** written by the developer (SIMULATION, not independent), from Chancy's documented cancel semantics:
  a job whose cancel updated its row stays failed.
* **The Muse arm:** uses a stub model, with no Meta call.
* **Scope, reduced and recorded:** arms RA-DET and RA-MD, replicate 0 only. The schedule budget is the plan's locked
  B/replicates, unchanged.

The path: the real intake (normalize, seal, register, freeze) → the evaluator package (leak-scanned) → the plan's
locked budgets → ``real_executor.Executor`` with chancy-sv/1 → results committed run by run → SIMULATION
authorization (and a proof that ``authorize_real`` refuses it) → scoring with the reveal → the frozen analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import approvals as ap  # noqa: E402
import corpus_intake as ci  # noqa: E402
import real_executor as rx  # noqa: E402
import run  # noqa: E402
import sealed_evaluation as se  # noqa: E402
from integrations import chancy_sv  # noqa: E402

ORACLE = '''"""SIMULATION oracle (developer-written, NOT independent): Chancy's documented cancel semantics. A job whose
cancel updated its row (rowcount 1) ends in state 'failed'. No verdict when no cancel updated a row."""


def check(final_state, raw_results):
    cancelled = {int(c["n"]) for r in raw_results.values() if isinstance(r, dict) for c in r.get("cancelled", [])
                 if c.get("rowcount") == 1}
    if not cancelled:
        return "NO_VERDICT"
    final = {int(j["n"]): j for j in final_state.get("jobs_detail", [])}
    return "VIOLATED" if any(final.get(n, {}).get("state") != "failed" for n in cancelled) else "HOLDS"
'''


def stub_model(case, arm, i):  # SIMULATION: no Meta call; one valid and one invalid proposal
    return json.dumps([{"jobs": [{"n": 3, "cancel": True}], "max_attempts": 1}, {"jobs": [], "max_attempts": 1}]), \
        {"tokens_in": 0, "tokens_out": 0, "est_usd": 0.0}


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--private", type=Path, required=True)
    a.add_argument("--sdists", type=Path, required=True)
    o = a.parse_args()
    o.out.mkdir(parents=True, exist_ok=False)
    o.private.mkdir(parents=True, exist_ok=False)
    w = run.Writer([o.out, o.private], simulation=True)
    w.text(o.out / "SIMULATION.md", "# SIMULATION rehearsal of the real arm executor (not blind, not confirmatory; "
                                    "a known development case; developer-written oracle; stub model)\n")
    # 1. the real intake: normalize, seal, register, freeze (development split)
    raw = o.private / "raw"
    raw.mkdir()
    for name in ("chancy-git-80f570f.tar", "chancy-git-2895c4a.tar"):
        se.normalize_archive(o.sdists / name, raw / f"norm-{name}")
    oracle = o.out / "oracle" / "sim_chancy_cancel_oracle.py"
    w.text(oracle, ORACLE)
    sub = {"case_id": "sim-chancy-pair", "application": "chancy", "repository": "https://github.com/TkTech/chancy",
           "split": "development", "revisions": [
               {"archive": str(raw / "norm-chancy-git-80f570f.tar"),
                "commit": "80f570f0b8332212e46c3c27af5940946f6486a9"},
               {"archive": str(raw / "norm-chancy-git-2895c4a.tar"),
                "commit": "2895c4a7fc173cec3d645221f63c4e51e2cf7e84"}],
           "buggy": 0, "fix_paths": ["chancy/worker.py"], "defect_summary": "SIMULATION: a late completion overwrote a "
           "cancel", "keywords": ["claim"], "contract": {"kind": "single-version", "citation": "CHANGELOG.md"},
           "oracle_spec_sha256": ci.sha(oracle), "exposure": {"curator": ["fix diff (developer)"], "public": ["git"]}}
    subp = o.private / "submission.json"
    subp.write_text(json.dumps(sub))
    corpus = o.out / "corpus"
    case_dir = corpus / "cases" / "sim-chancy-pair"
    ci.submit(subp, case_dir, o.private / "reveals" / "sim-chancy-pair.json")
    registry = corpus / "REGISTRY_development.jsonl"
    ci.register(case_dir, registry)
    ci.freeze(registry)
    summary: dict = {"SIMULATION": True, "case": "sim-chancy-pair", "split": "development",
                     "real_gates_refuse": run.real_refusals(registry=registry, cases_root=corpus / "cases",
                                                            oracle_ledger=None, registration=None, policy=None,
                                                            results_dir=o.out / "results")}
    # 2. the evaluator package, leak-scanned; 3. the plan's locked budgets
    pkg = o.out / "evaluator_package"
    se.build_package(registry, [case_dir], pkg, reveal_dirs=[o.private])
    lock = run.lock_budgets(w, o.out / "BUDGET_LOCK.json")
    oracle_set = o.out / "ORACLE_SET.json"
    oracle_sha = w.json(oracle_set, {"oracles": {"sim-chancy-pair": ci.sha(oracle)}})
    ledger = o.out / "results" / "RESULTS_LEDGER.jsonl"
    ex = rx.Executor(package=pkg, lock=lock, arms=["RA-DET", "RA-MD"],
                     oracles={"sim-chancy-pair": (oracle, ci.sha(oracle))}, integrations={"chancy": chancy_sv},
                     append=lambda r: run._append_result(w, ledger, r, oracle_sha=oracle_sha, lock=lock),
                     committed=lambda: run._chain(ledger), workdir=o.out / "work", write_json=w.json,
                     model=stub_model, model_manifest={"SIMULATION": True, "frozen_at_adoption": "SIMULATION stub"},
                     replicates=[0], simulation=True)
    t0 = time.monotonic()
    summary["execution"] = ex.run()
    summary["execution_seconds"] = round(time.monotonic() - t0, 1)
    entries = run._chain(ledger)
    summary["runs"] = [{k: e["run"][k] for k in ("arm", "replicate", "slot", "verdict", "schedules_used",
                                                 "first_violation_schedule", "corroboration")} for e in entries]
    # 4. authorization: the real check refuses a SIMULATION record; the rehearsal then authorizes as SIMULATION
    rec = o.private / "SIM_AUTHORIZATION.json"
    rec.write_text(json.dumps({"SIMULATION": True, "role": "owner", "authorizer": "SIM-owner"}))
    try:
        rx.authorize_real(entries, ex.expected(), rec, ap.role_signers(ap.forms())["owner"])
        summary["authorize_real_on_simulation_record"] = "ACCEPTED (a defect)"
    except SystemExit as exc:
        summary["authorize_real_on_simulation_record"] = f"refused: {exc}"
    run._authorize(w, ledger, authorizer="SIM-owner", oracle_sha=oracle_sha, lock=lock)
    # 5. scoring with the reveal, then the frozen analysis
    rows, voided, triage = run.score(ledger, [case_dir], o.private / "reveals", oracle_set,
                                     lock_path=o.out / "BUDGET_LOCK.json")
    w.json(o.out / "results" / "SCORED.json", {"rows": rows, "voided": voided, "triage": triage})
    analysis = run.analyse(rows, voided, lock)
    w.json(o.out / "results" / "ANALYSIS.json", analysis)
    summary["scored"] = [{k: r[k] for k in ("arm", "detected", "first_detection_schedule", "fixed_version_violation",
                                            "refused")} for r in rows]
    summary["analysis_verdicts"] = {k: v["verdict"] for k, v in analysis["comparisons"].items()}
    w.json(o.out / "SUMMARY.json", summary)
    print(json.dumps({k: summary[k] for k in ("execution", "scored", "authorize_real_on_simulation_record",
                                              "analysis_verdicts")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
