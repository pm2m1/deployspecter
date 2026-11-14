"""EXPERIMENTAL three-application onboarding study (onb-study/2) CLI. DRAFT; NOT RUN; nobody recruited.

    study3.py allocate --ids P1 P2 ... --seed SEED --design min18|pref24 --out allocation.json  # before recruitment
    study3.py bundle   --sessions DIR... --out BUNDLE --key KEY.json      # for the blind scorers; key kept apart
    study3.py analyze  --sessions DIR... --allocation allocation.json --budget 5400 --out analysis.json
    study3.py power    --out SAMPLE_SIZE.json                              # sample-size and sensitivity table

Sessions are recorded with ``pilot_v2.py`` (which logs Muse call durations), ``--operator-kind independent`` and
``--operator`` set to the participant's study id, never a name. The blind bundle is onb-study/1's, unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from deployspecter.experimental.onboarding_pilot import participant_study as ps1  # noqa: E402
from deployspecter.experimental.onboarding_pilot import participant_study_3app as ps  # noqa: E402

POWER_NS = [12, 18, 24, 30, 36]
POWER_PS = [0.60, 0.70, 0.75, 0.80, 0.85, 0.90]
ATTRITION = [0.10, 0.20, 0.30]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("allocate")
    a.add_argument("--ids", nargs="+", required=True)
    a.add_argument("--seed", required=True)
    a.add_argument("--design", choices=sorted(ps.DESIGNS), required=True)
    a.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("bundle")
    b.add_argument("--sessions", type=Path, nargs="+", required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--key", type=Path, required=True)
    n = sub.add_parser("analyze")
    n.add_argument("--sessions", type=Path, nargs="+", required=True)
    n.add_argument("--allocation", type=Path, required=True)
    n.add_argument("--budget", type=float, required=True)
    n.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("power")
    p.add_argument("--out", type=Path, required=True)
    x = ap.parse_args(argv)
    if x.cmd in ("allocate", "power") and x.out.exists():
        raise SystemExit(f"refused: {x.out} is never overwritten")
    if x.cmd == "allocate":
        x.out.write_text(json.dumps({"version": ps.VERSION, "design": x.design, "seed": x.seed,
                                     "allocation": ps.allocate(x.ids, x.seed, x.design),
                                     "sequences": ps.sequences(x.design)}, indent=1))
    elif x.cmd == "bundle":
        ps1.blind_bundle(x.sessions, x.out, x.key)
    elif x.cmd == "power":
        x.out.write_text(json.dumps(ps.sample_size_table(POWER_NS, POWER_PS, ATTRITION), indent=1) + "\n")
    else:
        alloc = json.loads(x.allocation.read_text())
        r = ps.analyze([ps.measure(s) for s in x.sessions], alloc["allocation"], x.budget, alloc["design"])
        x.out.write_text(json.dumps(r, indent=1, sort_keys=True))
        print(r["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
