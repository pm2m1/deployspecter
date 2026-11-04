"""EXPERIMENTAL demo: re-execute the real handlers on the race schedule, buggy and fixed, and show the evidence.

    DS_MVP_PORT=55441 python -m deployspecter.experimental.demo --out DIR

Prints, for each build: the handlers that ran, the driver-level SQL trace with the scheduler's grants and decisions,
the rows each read returned (what the next Python computation used), the verified initial-state digest, the final
PostgreSQL state and the independent oracle's verdict. Every run re-executes the Python handlers; nothing is replayed.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path

from . import harness as hz
from . import workloads as wl
from .apps import reservations_mixed as app

RACE = ["j", "j", "a", "a", "a", "a", "a", "j", "j"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    w = wl.validate(wl._prop([("j", "job", "backfill_v2", {"batch": 10}),
                              ("a", "v1", "reserve", {"event_id": 1, "holder": "alice", "qty": 2})],
                             [{"id": 1, "capacity": 5, "reserved": 0}]), source="demo", label="backfill race")
    a.out.mkdir(parents=True, exist_ok=False)
    verdict = {}
    for variant in ("buggy", "fixed"):
        r = hz.run_once(target, w, variant=variant, schedule=RACE)
        handlers = app.registry(variant)
        print(f"\n=== {variant} build: run {r.run_id}, schedule {''.join(RACE)}, outcome {r.trace.outcome}")
        for inv in w.invocations:
            fn = handlers[(inv["app_version"], inv["handler"])].fn
            print(f"  actor {inv['actor']}: {inv['app_version']}.{inv['handler']} -> {fn.__module__}.{fn.__name__} "
                  f"({inspect.getsourcefile(fn).rsplit('/src/', 1)[-1]}:{inspect.getsourcelines(fn)[1]})")
        print(f"  verified initial state digest {r.initial_digest[:16]}")
        for op in sorted(r.trace.records, key=lambda o: (o.grant is None, o.grant)):
            d = r.trace.decisions[op.decision] if op.decision is not None else None
            how = f"{d.source}{' blocked=' + str(d.blocked) if d and d.blocked else ''}" if d else "-"
            print(f"  grant {op.grant} {op.actor} {op.kind:<17} {op.sql[:58]:<58} params={op.params} "
                  f"rows={op.rows} [{how}] {op.provenance['call_site'].rsplit('/', 1)[-1]}")
        print(f"  final state {r.final_state['events']}")
        print(f"  oracle: {r.violations or 'all invariants hold'}")
        (a.out / f"{variant}_{r.run_id}.json").write_text(json.dumps(r.to_dict(), indent=1, default=str))
        verdict[variant] = r.violations
    ok = bool(verdict["buggy"]) and not verdict["fixed"]
    print(f"\nbuggy build violates: {bool(verdict['buggy'])}; fixed build holds: {not verdict['fixed']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
