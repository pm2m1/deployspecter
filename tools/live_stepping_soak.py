"""Clean synthetic runs through the full supervised path, to measure false flags. No model requests.

Usage: uv run python tools/live_stepping_soak.py OUTPUT.json --runs 1200

Every run here is ordinary application behaviour, so any evidence flag at all is a false flag. Deadlocks, retries,
savepoints, rollbacks, application errors and opaque batches are all included, because they're the likely false alarms.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.config import load_settings  # noqa: E402
from deployspecter.database import Sandbox  # noqa: E402
from deployspecter.live.runner import ExplicitSchedule, LiveActor, LiveScenario  # noqa: E402
from deployspecter.live.supervisor import SupervisedRunner  # noqa: E402
from deployspecter.models import ColumnValue, SeedRow  # noqa: E402
from live_app import adversarial as adv  # noqa: E402
from live_app import compat  # noqa: E402
from live_app import reservations as app  # noqa: E402

ADV = "live_app.adversarial:"
RES = "live_app.reservations:"
COMPAT = "live_app.compat:"
# Cells whose expected outcome is effect_unknown are inconclusive by design, so they aren't clean-run material.
CELL_CASES = [c for c in compat.CASES if c not in {
    "unadaptable_parameter_in_middle", "wrong_arity_in_middle", "generator_raises_after_first",
}]


def seats() -> list[SeedRow]:
    return [SeedRow(table="events", values=[
        ColumnValue(column="id", value=1), ColumnValue(column="capacity", value=3),
        ColumnValue(column="reserved", value=0),
    ])]


def reservations(lock: bool) -> LiveScenario:
    return LiveScenario(f"reservations lock={lock}", app.SETUP_SQL, seats(), [
        LiveActor("a", f"{RES}reserve_seats", dict(event_id=1, holder="alice", qty=2, lock_row=lock)),
        LiveActor("b", f"{RES}reserve_seats", dict(event_id=1, holder="bob", qty=2, lock_row=lock)),
    ])


def release(lock: bool) -> LiveScenario:
    seed = [*seats(), SeedRow(table="reservations", values=[
        ColumnValue(column="event_id", value=1), ColumnValue(column="holder", value="carol"),
        ColumnValue(column="qty", value=2),
    ])]
    return LiveScenario(f"release lock={lock}", app.SETUP_SQL, seed, [
        LiveActor("a", f"{RES}reserve_seats", dict(event_id=1, holder="alice", qty=1, lock_row=lock)),
        LiveActor("b", f"{RES}release_seats", dict(holder="carol", lock_row=lock)),
    ])


def single(function: str, **options) -> LiveScenario:
    return LiveScenario(function, adv.SETUP, [], [LiveActor("a", f"{ADV}{function}", **options)])


def pair(function: str, **kwargs) -> LiveScenario:
    return LiveScenario(function, adv.SETUP, [], [
        LiveActor("a", f"{ADV}{function}", {"first": 1, "second": 2, "value": 10}, **kwargs),
        LiveActor("b", f"{ADV}{function}", {"first": 2, "second": 1, "value": 20}, **kwargs),
    ])


def cell(mode: str, case: str) -> LiveScenario:
    return LiveScenario(f"cell {mode} {case}", compat.SETUP, [], [
        LiveActor("a", f"{COMPAT}cell", {"mode": mode, "case": case},
                  autocommit=mode != "not_autocommit_commit_anyway"),
    ])


def scenarios() -> list[LiveScenario]:
    built = [reservations(False), reservations(True), release(False), release(True)]
    built += [single(f) for f in ("clean", "savepoints", "multi_line_and_quotes", "named_params", "many_types",
                                  "reads_with_factories", "forgets_commit", "sql_error", "app_error",
                                  "prints_to_stdout", "bump")]
    built += [single("text_transaction", autocommit=True), pair("set_pair"), pair("retry_pair")]
    built += [cell(m, c) for m in compat.MODES[:3] for c in CELL_CASES]
    return built


def schedule_for(scenario: LiveScenario, rng: random.Random) -> ExplicitSchedule:
    names = [a.name for a in scenario.actors]
    return ExplicitSchedule([rng.choice(names) for _ in range(rng.randint(0, 14))])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--runs", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=20261103)
    args = parser.parse_args()
    settings = load_settings()
    rng = random.Random(args.seed)
    built = scenarios()
    outcomes: Counter = Counter()
    by_scenario: Counter = Counter()
    false_flags: list[dict] = []
    not_scoreable: list[dict] = []
    logged = matched = opaque = lock_waits = 0
    started = time.monotonic()
    with Sandbox(settings) as box:
        runner = SupervisedRunner(box)
        if runner.preflight_problems():
            print("refusing: needs the disposable logging cluster")
            return 2
        roles_before = box.admin.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ds_run_%'").fetchone()[0]
        for index in range(args.runs):
            scenario = built[index % len(built)]
            trace = runner.run(scenario, schedule_for(scenario, rng))
            outcomes[trace.outcome] += 1
            by_scenario[scenario.name] += 1
            summary = trace.evidence.reconciliation
            logged += summary.get("statements_logged", 0)
            matched += summary.get("statements_matched", 0)
            opaque += trace.opaque_steps
            lock_waits += any(r.blocked_by for r in trace.records)
            if trace.evidence.flags:
                false_flags.append({"run": index, "scenario": scenario.name, "outcome": trace.outcome,
                                    "flags": [f.model_dump() for f in trace.evidence.flags][:3]})
            if not trace.scoreable:
                not_scoreable.append({"run": index, "scenario": scenario.name, "outcome": trace.outcome})
        roles = box.admin.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ds_run_%'").fetchone()[0]
    report = {
        "runs": args.runs, "seed": args.seed, "model_requests": 0, "scenarios": len(built),
        "seconds": round(time.monotonic() - started, 1), "outcomes": dict(outcomes),
        "false_flags": len(false_flags), "false_flag_samples": false_flags[:20],
        "not_scoreable": len(not_scoreable), "not_scoreable_samples": not_scoreable[:20],
        "statements_logged": logged, "statements_matched": matched, "opaque_steps": opaque,
        "runs_with_a_lock_wait": lock_waits, "run_roles_before": roles_before, "run_roles_left": roles,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("samples")}))
    print(json.dumps(report["false_flag_samples"][:3])[:1500])
    return 0 if not false_flags and not not_scoreable else 1


if __name__ == "__main__":
    raise SystemExit(main())
