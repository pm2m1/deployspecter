"""EXPERIMENTAL, NONBLIND: one parametrized run of the historical case chancy-2895c4a, for arms B1-B3 and the
invariant mutants. A thin wrapper over the existing runners (``research_tools/isolated/run_historical.py`` and
``run_coordinated.py``), which are imported and called unmodified:

* the same restricted containers, coordinator, gates, server-log corroboration and frozen case checker (H0-H2);
* the workload (jobs, cancels, max_attempts, one or two workers) is a parameter instead of the maintainers' fixed
  shape; the gates are the ``explore`` schedule's (no producer statement before every worker has polled; a cancel
  only after the first claim), and seeded policies decide the rest;
* a second worker runs in its own container (process ``B``), so each process's handler record is its own.

Application mutants for the invariant study are declared here and registered into the runner's mutant table
*in this process only* (``run_isolated.MUTANTS`` is not edited): each is one exact substring replacement in one file
of the fix commit's tree, applied to the isolated build copy, with the mutated file's sha256 declared to the
in-container adapter, which refuses any other difference. They are controlled experiments, not upstream defects.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "isolated"))
import run_coordinated as rc  # noqa: E402
import run_historical as rh  # noqa: E402
import run_isolated as ri  # noqa: E402

from deployspecter.experimental import historical_oracles as ho  # noqa: E402

PARENT, FIX = "chancy-git-80f570f.tar", "chancy-git-2895c4a.tar"
FIX_BASE = "git-2895c4a"
EXISTING_MUTANTS = ["abl_no_fence", "abl_cancel_keeps_claim", "abl_no_claim_assignment", "no_skip_locked_claim"]
NEW_MUTANTS = {
    "mc_claim_takes_failed": ("chancy/worker.py", "(state = 'pending' OR state = 'retrying')",
                              "(state = 'pending' OR state = 'retrying' OR state = 'failed')",
                              "the claim query no longer excludes failed (e.g. cancelled) jobs"),
    "mc_cancel_keeps_state": ("chancy/app.py", "SET state = 'failed', claim_id = NULL, completed_at = NOW()",
                              "SET claim_id = NULL, completed_at = NOW()",
                              "cancel_job no longer marks the job failed"),
    "mc_completion_drops_attempts": ("chancy/worker.py", "attempts = %(attempts)s,", "",
                                     "the completion update no longer records the attempt count"),
    "mc_claim_ignores_budget": ("chancy/worker.py", "attempts < max_attempts", "attempts <= max_attempts",
                                "the claim query allows one attempt beyond max_attempts"),
}
MUTANTS = EXISTING_MUTANTS + sorted(NEW_MUTANTS)


def register_mutants() -> None:
    for k, v in NEW_MUTANTS.items():
        if ri.MUTANTS.get(k, v) != v:
            raise RuntimeError(f"mutant id {k} already means something else")
        ri.MUTANTS[k] = v


def spec_for(w: dict[str, Any]) -> dict[str, Any]:
    workers = ["worker"] + (["worker2"] if w["workers"] == 2 else [])
    processes = {"A": [{"actor": "worker", "fn": "work", "args": {"timeout_s": 60}},
                       {"actor": "push", "fn": "push", "args": {"ns": w["ns"], "queue": "q_shared",
                                                                "max_attempts": w["max_attempts"],
                                                                "cancel": w["cancel"]}}]}
    if w["workers"] == 2:
        processes["B"] = [{"actor": "worker2", "fn": "work", "args": {"timeout_s": 60}}]
    gates = rc._window(["push"], workers) + [
        {"actor": "push", "match": "SET state = 'failed'", "until": [{"actor": "worker", "marker": "claim",
                                                                        "count": 1}]}]
    return {"processes": processes, "gates": gates, "covers": list(w["ns"]), "consumer_of": {}}


def build(source: Path, mutant: str | None = None) -> tuple[Any, dict[str, str]]:
    register_mutants()
    env = {"DS_CHANCY_MUTANT": ri.mutant_declaration(source, mutant, FIX_BASE)} if mutant else {}
    d = Path(tempfile.mkdtemp(prefix="ds_mc_build_"))
    try:
        return ri.build_image(d, chancy_sdist=source, mutant=mutant), env
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_one(target: Any, image: Any, env: dict[str, str], source: Path, w: dict[str, Any], seed: int, out: Path,
            *, mutant: str | None = None) -> dict[str, Any]:
    """As ``run_historical.one`` with ``spec_for(w)`` and a seeded policy; the report is written to ``out``."""
    out.mkdir(parents=True)
    s = {**spec_for(w), "expect_schema": rh.SCHEMA_OF[source.name]}
    policy = {"kind": "seeded", "seed": seed}
    pre = rh.setup(target, image, out, env or None)
    report: dict[str, Any] = {"case": rh.CASE, "nonblind": True, "source": source.name,
                              "role": rh.ROLE_OF[source.name], "mutant": mutant,
                              "mutant_why": ri.MUTANTS[mutant][3] if mutant else None, "workload": w,
                              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                              "git": ri.CHANCY_GIT.get(source.name), "policy": policy, "spec": s, "image": list(image),
                              "frozen_sha256": {k: hashlib.sha256((ri.REPO / v).read_bytes()).hexdigest()
                                                for k, v in rh.FROZEN.items()},
                              "setup": {"corroboration_ok": pre["corroboration"]["ok"],
                                        "isolation_ok": pre.get("isolation_ok"),
                                        "version": (pre.get("pinned_sources") or {}).get("version")}}
    if not (pre.get("isolation_ok") and pre["corroboration"]["ok"]):
        report["verdict"], report["reason"] = "REFUSED", "the setup wasn't isolated and corroborated"
    else:
        images = {"A": image, "env": {"A": env}}
        if w["workers"] == 2:
            images["B"], images["env"]["B"] = image, env
        sim = rc.simultaneous(target, images, s, policy, out, label=rh.CASE)
        report["run"] = sim
        report["summary"] = rc.summary({**sim, "spec": s})
        report["verdict"] = sim["verdict"]
        if sim["verdict"] == "SOUND":
            report["case_oracle"] = ho.chancy_2895c4a(sim["final_state"]["jobs_detail"], sim["raw_results"],
                                                      has_claim_column=rh.SCHEMA_OF[source.name] == "8")
            report["sql_check"] = rh.sql_check(target, sim["raw_results"])
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    for f in sorted(out.rglob("*.json")):  # compress the bulky per-run files; report.json stays plain
        if f.name != "report.json" or f.parent != out:
            with f.open("rb") as src, gzip.open(f.with_suffix(f.suffix + ".gz"), "wb") as dst:
                shutil.copyfileobj(src, dst)
            f.unlink()
    return report
