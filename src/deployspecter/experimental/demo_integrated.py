"""EXPERIMENTAL integrated demo: the SQLAlchemy ledger, the Django ledger (both binding modes) and the planned Gate 2A
reservations app, buggy and fixed.

    DS_MVP_PORT=55441 python -m deployspecter.experimental.demo_integrated --out DIR

For each case, both builds run on the same schedule from the same verified initial state. Each run's driver trace is
corroborated against PostgreSQL's log, and an evidence bundle is written. Exit 0 only when every buggy build violates
its oracle, every fixed build holds, every run is corroborated and the two builds of a case share one initial digest.
Not certified Gate A2 evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from deployspecter.research.track_a import scenario as sc

from . import harness as hz
from . import planning_adapter as pa
from .apps import ledger_orm as L

LEDGER_SEED = {"accounts": [{"id": 1, "owner": "alice", "opening": 100, "balance": 100}]}
LEDGER_INV = [{"actor": "a", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 70}},
              {"actor": "b", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 60}}]
INTERLEAVE = ["a", "b"] * 6


def show(case: str, variant: str, r: hz.RunResult, out: Path) -> tuple[bool, bool, str]:
    c = r.corroboration or {}
    print(f"\n=== {case}, {variant} build: run {r.run_id}, outcome {r.trace.outcome}, "
          f"initial digest {r.initial_digest[:16]}")
    for op in sorted(r.trace.records, key=lambda o: (o.grant is None, o.grant)):
        d = r.trace.decisions[op.decision].source if op.decision is not None else "-"
        if d == "setup":
            continue
        print(f"  grant {op.grant} {op.actor} {op.kind:<17} {op.sql[:52]:<52} params={op.params} rows={op.rows} "
              f"[{d}] {op.provenance['call_site'].rsplit('/', 1)[-1]} via {op.provenance['framework_entry'] or '-'}")
    setup = sum(1 for d in r.trace.decisions if d.source == "setup")
    print(f"  ({setup} framework setup operations granted without a decision point, all recorded)")
    print(f"  oracle: {r.violations or 'all invariants hold'}")
    print(f"  server log: {'corroborates' if c.get('ok') else 'REFUTES'} the trace ({c.get('matched')} statements, "
          f"{c.get('params_compared')} values compared) {c.get('problems') or ''}")
    tag = f"{case}_{variant}_{r.run_id}"
    (out / f"{tag}.json").write_text(json.dumps(r.to_dict(), indent=1, default=str))
    (out / f"{tag}.bundle.json").write_text(json.dumps(r.bundle(), indent=1, default=str))
    return bool(r.violations), bool(c.get("ok")), r.initial_digest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    secret = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
    target = hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())
    a.out.mkdir(parents=True, exist_ok=False)
    verdicts: dict[str, dict[str, Any]] = {}
    for variant in ("buggy", "fixed"):
        r = hz.run_app(target, L, LEDGER_INV, LEDGER_SEED, variant=variant, schedule=INTERLEAVE,
                       label="two withdrawals", corroborate=True)
        verdicts.setdefault("sqlalchemy_ledger", {})[variant] = show("sqlalchemy_ledger", variant, r, a.out)
    from .apps import django_fixture
    django_fixture.configure(target)
    mtrace, mcorr = django_fixture.migrate_through_stepping(target)
    print(f"\nDjango migrate through the gate: {mtrace.outcome}, server log "
          f"{'corroborates' if mcorr['ok'] else 'REFUTES'} ({mcorr['matched']} statements)")
    ok_migrate = mcorr["ok"] and mtrace.actors["migrate"]["exception"] is None
    for binding in ("server", "client"):
        for variant in ("buggy", "fixed"):
            r = hz.run_app(target, django_fixture, LEDGER_INV, LEDGER_SEED, variant=variant, schedule=INTERLEAVE,
                           label="django two withdrawals", corroborate=True, registry_kw={"binding": binding})
            verdicts.setdefault(f"django_ledger_{binding}_binding", {})[variant] = show(
                f"django_ledger_{binding}_binding", variant, r, a.out)
    p = pa.plan(sc.DOCUMENT)
    print(f"\nWorkloadSpec v2 {sc.DOCUMENT['name']!r}: {p.outcome} at {p.stage}; typed rows {pa.typed_rows(p)}")
    for variant in ("buggy", "fixed"):
        _p, r, _digest = pa.run_planned(target, sc.DOCUMENT, variant=variant, schedule=INTERLEAVE, corroborate=True)
        verdicts.setdefault("planned_reservations", {})[variant] = show("planned_reservations", variant, r, a.out)
    ok = True
    print()
    for case, v in verdicts.items():
        good = v["buggy"][0] and not v["fixed"][0] and v["buggy"][1] and v["fixed"][1] and v["buggy"][2] == \
            v["fixed"][2]
        ok &= good
        print(f"{case}: buggy violates {v['buggy'][0]}, fixed holds {not v['fixed'][0]}, both corroborated "
              f"{v['buggy'][1] and v['fixed'][1]}, same initial state {v['buggy'][2] == v['fixed'][2]}")
    return 0 if ok and ok_migrate else 1


if __name__ == "__main__":
    raise SystemExit(main())
