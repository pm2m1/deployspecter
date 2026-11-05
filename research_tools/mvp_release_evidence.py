"""EXPERIMENTAL: the release_seats evidence, buggy vs lock_row-fixed (frozen) vs corrected (experimental reference).

For each case: bounded exploration on every build, and one race run per build corroborated against the server log,
with its evidence bundle. Writes OUT/summary.json and OUT/<case>_<build>.bundle.json. Never overwrites: OUT must be
new.

    DS_MVP_PORT=55441 python research_tools/mvp_release_evidence.py --out DIR
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any

from deployspecter.experimental import harness as hz
from deployspecter.experimental import planning_adapter as pa
from deployspecter.research.track_a import scenario as sc


def I(v: int) -> dict[str, Any]:  # noqa: N802, E743
    return {"type": "int", "value": v}


def S(v: str) -> dict[str, Any]:  # noqa: N802
    return {"type": "str", "value": v}


def release(name: str, holder: str) -> dict[str, Any]:
    return {"name": name, "entrypoint": "release_seats", "application_version": "v1", "role": "request",
            "arguments": [{"name": "holder", "value": S(holder)}]}


def reserve(name: str, holder: str, qty: int) -> dict[str, Any]:
    return {"name": name, "entrypoint": "reserve_seats", "application_version": "v1", "role": "request",
            "arguments": [{"name": "event_id", "value": I(1)}, {"name": "holder", "value": S(holder)},
                          {"name": "qty", "value": I(qty)}]}


def document(name: str, actors: list[dict[str, Any]], held: dict[str, list[int]]) -> dict[str, Any]:
    d = copy.deepcopy(sc.DOCUMENT)
    d["name"], d["actors"] = name, actors
    d["provenance"] = {"origin": "hand_written", "author": "mvp_release_evidence"}
    total = sum(q for qs in held.values() for q in qs)
    d["state_goals"][0]["constraints"][1:] = [{"field": "capacity", "op": "eq", "value": I(10)},
                                              {"field": "reserved", "op": "eq", "value": I(total)}]
    for holder, qtys in held.items():
        for i, q in enumerate(qtys):
            d["state_goals"].append({"entity": "reservation", "label": f"{holder}_{i}", "count": 1, "constraints": [
                {"field": "event_id", "op": "eq", "value": I(1)}, {"field": "holder", "op": "eq", "value": S(holder)},
                {"field": "qty", "op": "eq", "value": I(q)}]})
    return d


CASES = {
    "duplicate_release_one_held": document("dup_one", [release("a", "jack"), release("b", "jack")], {"jack": [4]}),
    "duplicate_release_two_held": document("dup_two", [release("a", "jack"), release("b", "jack")], {"jack": [4, 4]}),
    "independent_releases": document("indep", [release("a", "jack"), release("b", "jill")], {"jack": [3], "jill": [2]}),
    "reserve_vs_release": document("res_rel", [reserve("a", "jack", 2), release("b", "jack")], {}),
    "unknown_holder": document("unknown", [release("a", "nobody"), release("b", "jack")], {"jack": [1]}),
}
RACE = ["a", "b"] + ["a"] * 5 + ["b"] * 5
BUILDS = ("buggy", "fixed", "corrected")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--schedules", type=int, default=200)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {"kind": "EXPERIMENTAL release_seats evidence; trusted synthetic fixture, not blind",
                               "code_revision": hz.code_revision(), "schedule_budget": a.schedules, "cases": {}}
    for case, doc in CASES.items():
        p = pa.plan(doc)
        if p.outcome != "bridged":
            summary["cases"][case] = {"plan": p.to_dict()}
            continue
        row: dict[str, Any] = {"spec_digest": p.digests["spec"], "bridge_typed": p.digests["bridge_typed"]}
        for build in BUILDS:
            fx = pa.PlannedReservations(p)
            digest = fx.verify_initial(target)
            results, exhausted = hz.explore_app(target, fx, pa.invocations(p), None, variant=build,
                                                max_runs=a.schedules, label=case, expected_digest=digest)
            race = hz.run_app(target, fx, pa.invocations(p), None, variant=build, schedule=RACE, label=case,
                              corroborate=True, expected_digest=digest)
            bundle = race.bundle()
            (a.out / f"{case}_{build}.bundle.json").write_text(json.dumps(bundle, indent=1, default=str))
            (a.out / f"{case}_{build}.run.json").write_text(json.dumps(race.to_dict(), indent=1, default=str))
            row[build] = {"initial_digest": digest, "explored": len(results), "exhausted": exhausted,
                          "violating": sum(bool(r.violations) for r in results),
                          "violation_kinds": sorted({v.split(" ")[0] for r in results for v in r.violations}),
                          "race_run": {"run_id": race.run_id, "classification": bundle["classification"],
                                       "violations": race.violations, "corroborated": race.corroboration["ok"],
                                       "results": race.trace.raw_results, "trace_sha256": bundle["trace_sha256"],
                                       "handler_sources": race.handler_sources}}
            print(case, build, {k: row[build][k] for k in ("explored", "exhausted", "violating", "violation_kinds")},
                  row[build]["race_run"]["classification"], "corroborated", race.corroboration["ok"])
        summary["cases"][case] = row
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
