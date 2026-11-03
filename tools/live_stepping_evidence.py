"""Regenerates the proof-of-concept evidence for driver-level stepping. Synthetic data only, and no model requests.

Run on a disposable database:  uv run python tools/live_stepping_evidence.py OUTPUT.json [--psql-container NAME]

With ``--psql-container``, the overselling interleaving is also driven through two separate psql processes, so no
Python, psycopg or DeploySpecter code takes part in that one check.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.config import load_settings  # noqa: E402
from deployspecter.database import Sandbox  # noqa: E402
from deployspecter.live.explore import LiveBudget, explore_live  # noqa: E402
from deployspecter.live.runner import (  # noqa: E402
    ExplicitSchedule,
    LiveActor,
    LiveRunner,
    LiveScenario,
    run_uninstrumented_serial,
)
from deployspecter.models import ColumnValue, SeedRow  # noqa: E402
from live_app import properties as props  # noqa: E402
from live_app import reservations as app  # noqa: E402

REPLAYS_PER_PATH = 3
CHAIN_REPLAYS = 100
BOTH_READ_FIRST = ["a", "b", "a", "b", "a", "a", "a", "b", "b", "b"]


def row(table: str, **values) -> SeedRow:
    return SeedRow(table=table, values=[ColumnValue(column=k, value=v) for k, v in values.items()])


def two_reservations(lock_row: bool) -> LiveScenario:
    seed = [row("events", id=1, capacity=3, reserved=0)]
    return LiveScenario(f"reserve+reserve lock_row={lock_row}", app.SETUP_SQL, seed, [
        LiveActor("a", app.reserve_seats, dict(event_id=1, holder="alice", qty=2, lock_row=lock_row)),
        LiveActor("b", app.reserve_seats, dict(event_id=1, holder="bob", qty=2, lock_row=lock_row)),
    ])


def reserve_and_release(lock_row: bool) -> LiveScenario:
    seed = [row("events", id=1, capacity=5, reserved=2), row("reservations", event_id=1, holder="carol", qty=2)]
    return LiveScenario(f"reserve+release lock_row={lock_row}", app.SETUP_SQL, seed, [
        LiveActor("a", app.reserve_seats, dict(event_id=1, holder="alice", qty=2, lock_row=lock_row)),
        LiveActor("b", app.release_seats, dict(holder="carol", lock_row=lock_row)),
    ])


SCENARIOS = [
    (two_reservations, set(), {"a": "alice", "b": "bob"}),
    (reserve_and_release, {"carol"}, {"a": "alice", "b": "carol"}),
]


def explore_scenario(box, runner, build, lock_row, seeded, holders) -> dict:
    scen = build(lock_row)
    plain = {
        order: run_uninstrumented_serial(box, scen, list(order))
        for order in (("a", "b"), ("b", "a"))
    }
    known = [props.canonical(p["final_state"]) for p in plain.values()]

    def check(trace):
        return (
            props.check_invariants(trace.final_state)
            + props.check_results(trace.final_state, seeded, trace.results(), holders)
            + props.check_serializable(trace.final_state, known)
        )

    result = explore_live(runner, scen, LiveBudget(max_runs=500, max_seconds=240), check=check)
    replays = matched = 0
    for trace in result.traces:
        for _ in range(REPLAYS_PER_PATH):
            again = runner.run(scen, ExplicitSchedule(trace.chosen_schedule, strict=True))
            replays += 1
            matched += again.fingerprint() == trace.fingerprint()
    lock_waits = sum(any(r.blocked_by for r in t.records) for t in result.traces)
    first_bad = next((i for i, v in enumerate(result.verdicts) if v), None)
    serial_rows = []
    for order, control in plain.items():
        traced = runner.run(scen, ExplicitSchedule([order[0]] * 16 + [order[1]] * 16))
        serial_rows.append({
            "order": list(order),
            "instrumented_steps": len(traced.records),
            "same_returned_values": traced.results() == control["results"],
            "same_final_state": traced.final_state == control["final_state"],
            "requirements_hold": check(traced) == [],
            "returned": control["results"],
        })
    example = None
    if first_bad is not None:
        trace = result.traces[first_bad]
        example = {"schedule": trace.chosen_schedule, "violations": result.verdicts[first_bad],
                   "returned": trace.results(), "events": trace.final_state["events"],
                   "reservations": trace.final_state["reservations"]}
    return {
        "scenario": scen.name,
        "interleavings_run": result.runs,
        "tree_exhausted": result.exhausted,
        "distinct_paths": result.distinct_paths,
        "distinct_behaviours": result.distinct_behaviours,
        "distinct_final_states": result.distinct_final_states,
        "outcomes": dict(result.outcomes),
        "divergences": result.divergences,
        "runs_with_a_lock_wait": lock_waits,
        "runs_violating_a_requirement": sum(bool(v) for v in result.verdicts),
        "first_violation_at_run": first_bad,
        "statement_counts_seen": sorted({len(t.records) for t in result.traces}),
        "replays": replays,
        "replays_matching_fingerprint": matched,
        "serial_controls": serial_rows,
        "example_violation": example,
    }


def chain_stability(runner) -> dict:
    """Three actors wait on one row, so PostgreSQL picks who wakes. This is the known limit of reproducibility."""

    def bump(conn):
        with conn.transaction():
            conn.execute("UPDATE counters SET n = n + 1 WHERE id = 1")

    scen = LiveScenario("three_actor_chain", "CREATE TABLE counters (id integer PRIMARY KEY, n integer NOT NULL);",
                        [row("counters", id=1, n=0)], [LiveActor(n, bump) for n in "abc"])
    first = runner.run(scen, ExplicitSchedule(["a", "a", "b", "b", "c", "c", "a", "b", "c"]))
    same = sum(
        runner.run(scen, ExplicitSchedule(first.chosen_schedule, strict=True)).fingerprint() == first.fingerprint()
        for _ in range(CHAIN_REPLAYS)
    )
    return {"schedule": first.chosen_schedule, "replays": CHAIN_REPLAYS, "matching": same, "outcome": first.outcome}


def deadlock_stability(runner) -> dict:
    def update(conn, first, second):
        with conn.transaction():
            conn.execute("UPDATE counters SET n = n + 1 WHERE id = %s", (first,))
            conn.execute("UPDATE counters SET n = n + 1 WHERE id = %s", (second,))

    scen = LiveScenario(
        "deadlock", "CREATE TABLE counters (id integer PRIMARY KEY, n integer NOT NULL);",
        [row("counters", id=1, n=0), row("counters", id=2, n=0)],
        [LiveActor("a", update, {"first": 1, "second": 2}), LiveActor("b", update, {"first": 2, "second": 1})],
    )
    victims: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    for _ in range(40):
        trace = runner.run(scen, ExplicitSchedule(["a", "b", "a", "b", "a", "b"]))
        outcomes[trace.outcome] = outcomes.get(trace.outcome, 0) + 1
        for record in trace.records:
            if record.sqlstate == "40P01":
                victims[record.actor] = victims.get(record.actor, 0) + 1
    return {"runs": 40, "outcomes": outcomes, "victims": victims}


def psql_control(box, container: str) -> dict:
    """Two psql processes with no shared code. Each statement waits for its own echoed marker before the next."""
    scen = two_reservations(False)
    box.reset(scen.setup_sql, scen.seed)
    expected = box.snapshot()
    sessions = {}
    command = ["docker", "exec", "-i", container, "psql", "-U", "deployspecter", "-d", "deployspecter_test",
               "-qAtX", "-v", "ON_ERROR_STOP=1"]
    for name in ("a", "b"):
        sessions[name] = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def send(name: str, statement: str) -> list[str]:
        proc = sessions[name]
        marker = f"__done_{name}__"
        proc.stdin.write(f"{statement}\n\\echo {marker}\n")
        proc.stdin.flush()
        lines = []
        for line in proc.stdout:
            if line.strip() == marker:
                return lines
            lines.append(line.strip())
        raise RuntimeError("psql ended early")

    try:
        for name in ("a", "b"):
            send(name, "SET search_path = ds_run;")
            send(name, "BEGIN;")
        read = "SELECT capacity, reserved FROM events WHERE id = 1;"
        reads = {"a": send("a", read), "b": send("b", read)}
        send("a", "UPDATE events SET reserved = 2 WHERE id = 1;")
        send("a", "INSERT INTO reservations (event_id, holder, qty) VALUES (1, 'alice', 2);")
        send("a", "COMMIT;")
        send("b", "UPDATE events SET reserved = 2 WHERE id = 1;")
        send("b", "INSERT INTO reservations (event_id, holder, qty) VALUES (1, 'bob', 2);")
        send("b", "COMMIT;")
    finally:
        for proc in sessions.values():
            proc.stdin.close()
            proc.wait(timeout=10)
    return {"reads": reads, "initial_events": expected["events"], "final_state": box.snapshot()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--psql-container")
    args = parser.parse_args()
    settings = load_settings()
    if ":55432/" in settings.admin_dsn:
        print("refusing: point DATABASE_URL at a disposable database, not the project's usual container")
        return 2
    with Sandbox(settings) as box:
        runner = LiveRunner(box)
        report = {"model_requests": 0, "scenarios": []}
        for build, seeded, holders in SCENARIOS:
            for lock_row in (False, True):
                report["scenarios"].append(explore_scenario(box, runner, build, lock_row, seeded, holders))
        report["three_actor_chain"] = chain_stability(runner)
        report["deadlock"] = deadlock_stability(runner)
        overlapped = runner.run(two_reservations(False), ExplicitSchedule(BOTH_READ_FIRST))
        report["overselling_interleaving"] = {"schedule": overlapped.chosen_schedule, "returned": overlapped.results(),
                                              "final_state": overlapped.final_state}
        if args.psql_container:
            control = psql_control(box, args.psql_container)
            report["psql_control"] = {**control, "same_final_state": control["final_state"] == overlapped.final_state}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({
        "scenarios": [
            {k: s[k] for k in ("scenario", "interleavings_run", "tree_exhausted", "distinct_paths",
                               "distinct_behaviours", "distinct_final_states", "outcomes", "runs_with_a_lock_wait",
                               "runs_violating_a_requirement", "first_violation_at_run", "statement_counts_seen",
                               "replays", "replays_matching_fingerprint", "divergences")}
            for s in report["scenarios"]
        ],
        "serial": [[s["scenario"], r] for s in report["scenarios"] for r in s["serial_controls"]],
        "example": report["scenarios"][0]["example_violation"],
        "chain": report["three_actor_chain"], "deadlock": report["deadlock"],
        "psql_same": report.get("psql_control", {}).get("same_final_state"),
        "psql_reads": report.get("psql_control", {}).get("reads"),
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
