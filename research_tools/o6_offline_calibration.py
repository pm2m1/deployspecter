"""Development-only, offline O6 planner-budget calibration. Not an evaluation arm.

It runs the existing pure planner (``live/state_planning.py``, unchanged) over a hand-written synthetic corpus. No
database, network, model or Phase 9B code is involved.

    uv run python tools/o6_offline_calibration.py validate CORPUS
    uv run python tools/o6_offline_calibration.py run CORPUS DECLARATION OUTPUT

``validate`` checks only the corpus's own validity: the context, and stages 1 to 3 of the frozen v2 pipeline. It
never calls the planner, so it shows no calibration result. ``run`` refuses unless the declaration file names the
corpus digest, and records the declaration's digest in its output.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.live import state_planning as sp  # noqa: E402
from deployspecter.live import workload_spec as ws  # noqa: E402
from test_argument_validation import book, make_contracts, make_registry  # noqa: E402
from test_workload_identity import validated  # noqa: E402

# Declared before any planner run (see the declaration file). Never changed after results are seen.
PROBE = {"max_rows": 64, "max_expansions": 1_000_000, "max_constraint_checks": 10_000_000}
DEFAULT = sp.PlanningBudget()
LIMITS = ("expansions", "constraint_checks")
PROBE_KEY = {"expansions": "max_expansions", "constraint_checks": "max_constraint_checks"}
BINDING_SHARE = 0.05


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _no_network(*_args, **_kwargs):
    raise RuntimeError("network use is refused during offline calibration")


def load(corpus_path: Path):
    corpus = json.loads(corpus_path.read_text())
    registry = make_registry()
    contracts = make_contracts(registry)
    config = json.loads(json.dumps(corpus["context"]))
    config["registry_digest"] = registry.digest
    digest = sp.context_digest(sp.SchemaContextConfig.model_validate(config))
    trusted = sp.TrustedPlanning(reviewer="calibration_harness", pinned_digest=digest)
    return corpus, registry, contracts, config, trusted


def goals_of(case) -> list[dict]:
    goals = []
    for goal in case["goals"]:
        constraints = []
        for item in goal["c"]:
            field, op = item[0], item[1]
            entry = {"field": field, "op": op}
            if op not in {"is_null", "not_null"}:
                entry["value"] = ws._typed(item[2])
            constraints.append(entry)
        goals.append({"entity": goal["entity"], "label": goal["label"], "count": goal["count"],
                      "constraints": constraints})
    return goals


def workload_of(case, registry, contracts):
    return validated(book("a"), book("b"), goals=goals_of(case), registry=registry, contracts=contracts)


def rows_of(case) -> int:
    return sum(g["count"] for g in case["goals"])


def validate(corpus_path: Path) -> int:
    corpus, registry, contracts, _config, _trusted = load(corpus_path)
    problems = []
    for case in corpus["cases"]:
        try:
            workload_of(case, registry, contracts)
        except Exception as exc:  # noqa: BLE001 - a corpus defect, reported by case
            problems.append(f"{case['id']}: {type(exc).__name__}: {str(exc)[:200]}")
    ids = [c["id"] for c in corpus["cases"]]
    if len(set(ids)) != len(ids):
        problems.append("repeated case id")
    print(json.dumps({"corpus_sha256": sha256_file(corpus_path), "cases": len(ids), "problems": problems}, indent=2))
    return 1 if problems else 0


def plan_once(case, registry, contracts, config, trusted, budget):
    workload = workload_of(case, registry, contracts)
    started = time.perf_counter()
    result = sp.plan_state(workload, registry=registry, contracts=contracts, context=config, trusted=trusted,
                           budget=budget)
    seconds = time.perf_counter() - started
    counters = result.counters
    record = {
        "outcome": type(result).__name__,
        "code": getattr(result, "code", None),
        "expansions": counters.expansions,
        "constraint_checks": counters.constraint_checks,
        "rows_metered": counters.rows,
        "candidate_rows": len(result.request.rows) if isinstance(result, sp.CandidatePlan) else 0,
        "request_digest": result.request.request_digest if isinstance(result, sp.CandidatePlan) else None,
        "seconds": seconds,
    }
    return record


def choose(usages: list[int], probe: int) -> int:
    peak = max(usages)
    return min(probe, max(1, 2 ** math.ceil(math.log2(max(1, 2 * peak)))))


def rule(records: list[dict], limit: str) -> dict:
    probe = PROBE[PROBE_KEY[limit]]
    reached = [r["id"] for r in records if r["probe"][limit] >= probe]
    usable = [r["probe"][limit] for r in records if r["probe"][limit] < probe]
    share = len(reached) / len(records) if records else 1.0
    if not usable or share > BINDING_SHARE:
        return {"value": None, "reason": f"{len(reached)} of {len(records)} cases reached the probe",
                "reached": reached}
    return {"value": choose(usable, probe), "peak": max(usable), "reached": reached}


def run(corpus_path: Path, declaration_path: Path, output_path: Path) -> int:
    socket.socket = _no_network  # type: ignore[assignment]
    corpus_digest = sha256_file(corpus_path)
    declaration = declaration_path.read_text()
    if corpus_digest not in declaration:
        raise SystemExit("the declaration doesn't name this corpus digest; refusing to run")
    corpus, registry, contracts, config, trusted = load(corpus_path)
    probe_budget = sp.PlanningBudget(**PROBE)
    records = []
    for case in corpus["cases"]:
        first = plan_once(case, registry, contracts, config, trusted, probe_budget)
        second = plan_once(case, registry, contracts, config, trusted, probe_budget)
        stable = {k: v for k, v in first.items() if k != "seconds"} == \
                 {k: v for k, v in second.items() if k != "seconds"}
        default = plan_once(case, registry, contracts, config, trusted, DEFAULT)
        records.append({"id": case["id"], "category": case["category"], "rows": rows_of(case),
                        "probe": first, "repeat_identical": stable,
                        "repeat_seconds": second["seconds"], "default_budget": default})

    summary = {"deterministic": all(r["repeat_identical"] for r in records)}
    for limit in LIMITS:
        summary[limit] = {"full": rule(records, limit)}
        categories = sorted({r["category"] for r in records})
        summary[limit]["leave_one_category_out"] = {
            c: rule([r for r in records if r["category"] != c], limit) for c in categories}
        strata = sorted({r["rows"] for r in records})
        summary[limit]["leave_one_stratum_out"] = {
            str(s): rule([r for r in records if r["rows"] != s], limit) for s in strata}
        summary[limit]["by_category"] = {c: rule([r for r in records if r["category"] == c], limit)
                                         for c in categories}
        values = {v["value"] for v in summary[limit]["leave_one_category_out"].values()}
        summary[limit]["stable"] = values == {summary[limit]["full"]["value"]}
    summary["default_budget_indeterminate"] = [
        {"id": r["id"], "code": r["default_budget"]["code"]} for r in records
        if r["default_budget"]["outcome"] == "Indeterminate"]
    summary["default_budget_changed_outcome"] = [
        r["id"] for r in records
        if (r["default_budget"]["outcome"], r["default_budget"]["code"]) != (r["probe"]["outcome"], r["probe"]["code"])]

    output = {
        "status": "development-only offline calibration of planner budgets; not an evaluation arm",
        "corpus_sha256": corpus_digest, "declaration_sha256": sha256_file(declaration_path),
        "planner_version": sp.PLANNER_VERSION, "rules_version": sp.RULES_VERSION,
        "planner_sha256": sha256_file(ROOT / "src/deployspecter/live/state_planning.py"),
        "probe": PROBE, "default": DEFAULT.model_dump(), "binding_share": BINDING_SHARE,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "records": records, "summary": summary,
    }
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "validate":
        raise SystemExit(validate(Path(sys.argv[2])))
    if mode == "run":
        raise SystemExit(run(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])))
    raise SystemExit("usage: validate CORPUS | run CORPUS DECLARATION OUTPUT")
