"""EXPERIMENTAL async demo: the async psycopg engine on three cases, each buggy vs its reference build.

    DS_MVP_PORT=55441 python -m deployspecter.experimental.demo_async --out DIR

* the migration backfill race on ``reservations_mixed_async`` (raw ``AsyncConnection``), buggy vs fixed;
* two withdrawals on ``ledger_orm_async`` (SQLAlchemy ``AsyncSession``), buggy vs fixed;
* a concurrent duplicate release on Gate 2A's synthetic reservations app (sync, planned from WorkloadSpec v2), the
  frozen ``fixed`` build vs the experimental ``corrected`` reference.

Every run starts from a verified initial digest, is corroborated against PostgreSQL's log and writes an evidence
bundle (revision, workload and source hashes, digests, trace hash, reconciliation, classification). Exit 0 only when
each case's first build violates its oracle, its reference holds, both are corroborated and both start from the same
state. Not certified Gate A2 evidence.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any

from deployspecter.research.track_a import scenario as sc

from . import harness as hz
from . import planning_adapter as pa
from . import workloads as wl
from .apps import ledger_orm_async, reservations_mixed_async

RACE = ["j", "j", "a", "a", "a", "a", "a", "j", "j"]
INTERLEAVE = ["a", "b"] * 6
BOTH_READ_FIRST = ["a", "b"] + ["a"] * 5 + ["b"] * 5


def duplicate_release() -> dict[str, Any]:
    d = copy.deepcopy(sc.DOCUMENT)
    d["name"] = "duplicate_release"
    d["actors"] = [{"name": n, "entrypoint": "release_seats", "application_version": "v1", "role": "request",
                    "arguments": [{"name": "holder", "value": {"type": "str", "value": "jack"}}]} for n in "ab"]
    d["state_goals"][0]["constraints"][1:] = [{"field": "capacity", "op": "eq", "value": {"type": "int", "value": 10}},
                                              {"field": "reserved", "op": "eq", "value": {"type": "int", "value": 4}}]
    d["state_goals"].append({"entity": "reservation", "label": "held", "count": 1, "constraints": [
        {"field": "event_id", "op": "eq", "value": {"type": "int", "value": 1}},
        {"field": "holder", "op": "eq", "value": {"type": "str", "value": "jack"}},
        {"field": "qty", "op": "eq", "value": {"type": "int", "value": 4}}]})
    return d


def show(case: str, build: str, r: hz.RunResult, out: Path) -> tuple[bool, bool, str]:
    c = r.corroboration or {}
    b = r.bundle()
    print(f"\n=== {case}, {build}: run {r.run_id}, {b['classification']}, initial {r.initial_digest[:16]}")
    for op in sorted(r.trace.records, key=lambda o: (o.grant is None, o.grant)):
        d = r.trace.decisions[op.decision].source if op.decision is not None else "-"
        if d == "setup":
            continue
        task = op.provenance.get("task") or op.provenance.get("thread")
        print(f"  grant {op.grant} {op.actor} {op.kind:<17} {op.sql[:50]:<50} params={op.params} rows={op.rows} "
              f"[{d}] {op.provenance['call_site'].rsplit('/', 1)[-1]} task={task}")
    print(f"  oracle: {r.violations or 'all invariants hold'}")
    print(f"  server log: {'corroborates' if c.get('ok') else 'REFUTES'} ({c.get('matched')} statements, "
          f"{c.get('params_compared')} values) {c.get('problems') or ''}")
    tag = f"{case}_{build}_{r.run_id}"
    (out / f"{tag}.bundle.json").write_text(json.dumps(b, indent=1, default=str))
    (out / f"{tag}.run.json").write_text(json.dumps(r.to_dict(), indent=1, default=str))
    return bool(r.violations), bool(c.get("ok")), r.initial_digest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    verdicts: dict[str, dict[str, Any]] = {}
    race = wl.validate(wl._prop([("j", "job", "backfill_v2", {"batch": 10}),
                                 ("a", "v1", "reserve", {"event_id": 1, "holder": "alice", "qty": 2})],
                                [{"id": 1, "capacity": 5, "reserved": 0}]), source="demo", label="backfill race")
    for build in ("buggy", "fixed"):
        r = hz.run_app(target, reservations_mixed_async, race.invocations, race.seed, variant=build, schedule=RACE,
                       label=race.label, corroborate=True)
        verdicts.setdefault("async_migration_race", {})[build] = show("async_migration_race", build, r, a.out)
    seed = {"accounts": [{"id": 1, "owner": "alice", "opening": 100, "balance": 100}]}
    inv = [{"actor": "a", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 70}},
           {"actor": "b", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 60}}]
    for build in ("buggy", "fixed"):
        r = hz.run_app(target, ledger_orm_async, inv, seed, variant=build, schedule=INTERLEAVE,
                       label="two withdrawals", corroborate=True)
        verdicts.setdefault("async_sqlalchemy_ledger", {})[build] = show("async_sqlalchemy_ledger", build, r, a.out)
    for build in ("fixed", "corrected"):
        _p, r, _d = pa.run_planned(target, duplicate_release(), variant=build, schedule=BOTH_READ_FIRST,
                                   corroborate=True)
        verdicts.setdefault("release_path", {})[build] = show("release_path", build, r, a.out)
    ok = True
    print()
    for case, v in verdicts.items():
        first, ref = list(v.values())
        good = first[0] and not ref[0] and first[1] and ref[1] and first[2] == ref[2]
        ok &= good
        names = list(v)
        print(f"{case}: {names[0]} violates {first[0]}, {names[1]} holds {not ref[0]}, both corroborated "
              f"{first[1] and ref[1]}, same initial state {first[2] == ref[2]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
