"""SIMULATION ONLY: a stand-in for the real arm runners, run inside an evaluator package by
``evaluator_sandbox.launch``.

    python -I sim_executor.py BUDGETS_JSON ARMS_CSV SCENARIO

It sees only the package: the frozen registry, ``cases/*/{case.json,R1.tar,R2.tar}`` and ``oracles/``. For every
case × arm × replicate × slot it emits one run outcome to ``RESULTS.jsonl`` in its working directory.

**The simulated application and oracle.** The synthetic oracle (``oracles/sim_oracle.json``) says a revision is
violable when no source under ``app/`` guards a completion with its claim. Detection then happens at a pseudo-random
schedule, seeded from (case, arm profile, replicate, scenario), with refusals and model usage simulated.

Scenarios:
* ``difference``: the Muse-assisted arms (RA-MD, RA-MDS) detect faster than the deterministic arms, and one
  injected fixed-version false positive appears (case 2, RA-MD, replicate 0).
* ``null``: every arm shares one profile and one seed, so the arms are indistinguishable: a null result.

Nothing here reads labels. It is not a model of any real application.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import tarfile
from pathlib import Path

PROFILES = {"difference": {"RA-DET": 0.02, "RA-DET-C": 0.02, "RA-MS": 0.10, "RA-MD": 0.35, "RA-MDS": 0.40},
            "null": {}}
MUSE = ("RA-MD", "RA-MS", "RA-MDS")


def violable(archive: Path, oracle: dict) -> bool:
    with tarfile.open(archive) as t:
        code = "".join(t.extractfile(m).read().decode() for m in t.getmembers()
                       if m.isfile() and m.name.startswith(oracle["scan"]) and m.name.endswith(".py"))
    return oracle["required"] not in code


def rng(*parts: object) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def main() -> int:
    budgets, arms, scenario = json.loads(sys.argv[1]), sys.argv[2].split(","), sys.argv[3]
    oracle = json.loads(Path("oracles/sim_oracle.json").read_text())
    per_rep = budgets["B"] // budgets["replicates"]
    out = []
    for i, case in enumerate(sorted(p for p in Path("cases").iterdir() if p.is_dir())):
        cid = json.loads((case / "case.json").read_text())["case_id"]
        for arm in arms:
            p = PROFILES[scenario].get(arm, 0.15)
            prof = arm if scenario == "difference" else "common"
            for rep in range(budgets["replicates"]):
                r_arm = rng(cid, prof, rep, scenario, "arm")
                usage = ({"tokens_in": 400 + r_arm.randint(0, 200), "tokens_out": 2500 + r_arm.randint(0, 900),
                          "est_usd": round(0.01 + r_arm.random() * 0.02, 6),
                          "invalid_proposals": r_arm.randint(0, 2)}
                         if arm in MUSE and scenario == "difference" else
                         {"tokens_in": 0, "tokens_out": 0, "est_usd": 0.0, "invalid_proposals": 0})
                for slot in ("R1", "R2"):
                    r = rng(cid, prof, rep, scenario, violable(case / f"{slot}.tar", oracle))
                    first, refused, used = None, False, 0
                    for s in range(1, per_rep + 1):
                        used = s
                        if r.random() < 0.03:
                            refused = True
                            break
                        if violable(case / f"{slot}.tar", oracle) and r.random() < p:
                            first = s
                            break
                    if scenario == "difference" and i == 2 and arm == "RA-MD" and rep == 0 and \
                            not violable(case / f"{slot}.tar", oracle):
                        first, refused, used = 2, False, 2  # the injected fixed-version false positive
                    out.append({"SIMULATION": True, "case_id": cid, "arm": arm, "replicate": rep, "slot": slot,
                                "schedules_used": used, "first_violation_schedule": first, "refused": refused,
                                "verdict": "REFUSED" if refused else ("VIOLATED" if first else "HOLDS"),
                                "corroboration": {"ok": not refused, "params_mismatched": 0, "params_unchecked": 0},
                                "wall_seconds": 20.0 * used, **usage})
    Path("RESULTS.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in out))
    print(json.dumps({"SIMULATION": True, "runs": len(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
