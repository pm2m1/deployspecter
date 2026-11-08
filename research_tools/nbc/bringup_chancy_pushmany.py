"""ds-nbc/1, case chancy-b8a77df: bring-up of the instrument only (NONBLIND development). Run through pg_lock.py.

    .venv/bin/python research_tools/pg_lock.py -- .venv/bin/python research_tools/nbc/bringup_chancy_pushmany.py OUT

For R1 and R2: build the image (``chancy_pushmany.prepare``), run the setup phase, then one coordinated run with a
bring-up spec (the worker and ``seed`` [3] only, no ``repush``, alternate policy). It checks that the variant adapter
loads, accepts the pinned trees and corroborates. It uses none of the case's RA-DET workloads and no oracle. Refuses
an existing OUT.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "docs/experimental_mvp/nonblind_corpus/chancy/cases/chancy-b8a77df/case_spec.json"


def main() -> int:
    out = Path(sys.argv[1])
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    case = json.loads(SPEC.read_text())
    s = importlib.util.spec_from_file_location("nbc_pm_bringup", REPO / case["integration"])
    pm = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(pm)
    import run_coordinated as rc

    from deployspecter.experimental import harness as hz
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", pm.SECRET.read_text().strip())
    rows = []
    for slot in ("R1", "R2"):
        d = out / slot
        h = pm.prepare(Path(case[slot]["archive"]), case[slot]["sha256"], out / "build")
        d.mkdir(parents=True)
        pre = pm.setup(target, h["image"], d)
        row = {"slot": slot, "image": list(h["image"]),
               "setup": {"isolation_ok": pre.get("isolation_ok"), "corroboration_ok": (pre.get("corroboration") or
                                                                                      {}).get("ok"),
                         "version": (pre.get("pinned_sources") or {}).get("version"),
                         "raw": (pre.get("raw_results") or {}).get("setup")}}
        if row["setup"]["isolation_ok"] and row["setup"]["corroboration_ok"]:
            spec = {"processes": {"A": [{"actor": "worker", "fn": "nbc_work", "args": {"timeout_s": 60}},
                                        {"actor": "seed", "fn": "nbc_push_many", "args": {"ns": [3]}}]},
                    "gates": rc._window(["seed"], ["worker"]), "covers": [3], "consumer_of": {},
                    "expect_schema": h["expect_schema"]}
            (d / "coordinated").mkdir()
            sim = rc.simultaneous(target, {"A": h["image"], "env": {"A": dict(pm.ENV)}}, spec, {"kind": "alternate"},
                                  d / "coordinated", label="bringup")
            c = sim.get("corroboration") or {}
            row["coordinated"] = {"verdict": sim["verdict"], "refusal_reasons": sim.get("refusal_reasons"),
                                  "corroboration": {k: c.get(k) for k in ("ok", "matched", "params_compared",
                                                                          "params_mismatched", "params_unchecked",
                                                                          "problems")},
                                  "versions": sim.get("versions"), "exceptions": sim.get("exceptions"),
                                  "raw_results": sim.get("raw_results"),
                                  "jobs": (sim.get("final_state") or {}).get("jobs_detail")}
        rows.append(row)
        (d / "bringup.json").write_text(json.dumps(row, indent=1, default=str))
    print(json.dumps(rows, indent=1, default=str))
    ok = all(r.get("coordinated", {}).get("verdict") == "SOUND" for r in rows)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
