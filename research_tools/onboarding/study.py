"""EXPERIMENTAL onboarding study (onb-study/1) CLI, for the independent-participant study. NOT RUN.

    study.py allocate --ids P1 P2 ... --seed SEED --out allocation.json   # before recruitment completes
    study.py bundle   --sessions DIR... --out BUNDLE --key KEY.json      # for the blind scorer; key kept apart
    study.py analyze  --sessions DIR... --allocation allocation.json --budget 5400 --out analysis.json

Each participant session is recorded with ``pilot.py`` (``--operator-kind independent``, ``--operator`` = the
participant's study id, never a name).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from deployspecter.experimental.onboarding_pilot import participant_study as ps  # noqa: E402
from deployspecter.experimental.onboarding_pilot import session  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("allocate")
    a.add_argument("--ids", nargs="+", required=True)
    a.add_argument("--seed", required=True)
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
    x = ap.parse_args(argv)
    if x.cmd == "allocate":
        if x.out.exists():
            raise SystemExit("refused: an allocation is never overwritten")
        x.out.write_text(json.dumps({"seed": x.seed, "allocation": ps.allocate(x.ids, x.seed),
                                     "sequences": ps.SEQUENCES}, indent=1))
    elif x.cmd == "bundle":
        ps.blind_bundle(x.sessions, x.out, x.key)
    else:
        for s in x.sessions:
            if session.verify(s):
                raise SystemExit(f"refused: broken session log {s}")
        r = ps.analyze([session.measure(s) for s in x.sessions],
                       json.loads(x.allocation.read_text())["allocation"], x.budget)
        x.out.write_text(json.dumps(r, indent=1, sort_keys=True))
        print(r["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
