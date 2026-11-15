"""Solo r3 design: automatic oracles for mutants, needing no human-written property.

    python research_tools/solo/auto_oracles.py classify --pairs PAIRS.jsonl [--volatile FIELD ...]

Two oracles, both comparing a mutant build with the **unmutated** build of the same revision:

1. **Serial reachability.** In the serial run (one actor at a time, the integration's ``serial`` schedule), was the
   mutated code reached? Evidence, in order: the integration's own ``reached`` flag for the mutant (from a probe at
   the mutated site); otherwise, whether the mutant's corroborated statement trace differs from the unmutated build's
   trace under the same serial schedule. A mutant whose serial run already changes the final state is **serial-killed**:
   it needs no concurrency, and is reported separately.
2. **Differential.** For each schedule seed, the mutant and the unmutated build run under the **identical** schedule.
   The schedule's verdict is ``VIOLATED`` when the normalized final state or the actors' results differ, ``HOLDS``
   when they are equal, and ``UNREACHABLE`` when the mutated code wasn't reached on that schedule. A run that isn't
   SOUND (on either side) gives no verdict for that schedule and is counted, never retried.

A mutant's verdict is ``VIOLATED`` if any schedule is ``VIOLATED``; ``EQUIVALENT-SUSPECT`` if it was reached and every
reached schedule ``HOLDS`` (two reviewers would classify these; under the solo design they stay "suspect");
``UNREACHABLE`` if no run reached it.

Each line of PAIRS.jsonl: ``{"mutant_id", "schedule": "serial"|<seed>, "base": report, "mutant": report}``, where a
report has ``verdict`` ("SOUND" or not), ``final_state``, ``raw_results``, ``trace`` (list of statement texts) and
optionally ``reached`` (bool).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MUTANT_VERDICTS = ("VIOLATED", "EQUIVALENT-SUSPECT", "UNREACHABLE")
RUN_VERDICTS = ("VIOLATED", "HOLDS", "UNREACHABLE", "NO_VERDICT")
DEFAULT_VOLATILE = re.compile(r"^(.*_at|.*_time|.*timestamp.*|created|updated|started|completed|heartbeat.*|pid|"
                              r"backend_pid|txid|xmin|ctid|lsn)$", re.I)


def normalize(x: Any, volatile: set[str] | None = None) -> Any:
    """Drop volatile fields (timestamps, process ids) recursively, so two runs compare on content only."""
    if isinstance(x, dict):
        return {k: normalize(v, volatile) for k, v in sorted(x.items())
                if not (DEFAULT_VOLATILE.match(k) or (volatile and k in volatile))}
    if isinstance(x, list):
        return [normalize(v, volatile) for v in x]
    return x


def digest(x: Any, volatile: set[str] | None = None) -> str:
    return hashlib.sha256(json.dumps(normalize(x, volatile), sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def _reached(base: dict[str, Any], mutant: dict[str, Any]) -> bool:
    if "reached" in mutant:
        return bool(mutant["reached"])
    return list(mutant.get("trace") or []) != list(base.get("trace") or [])


def run_verdict(base: dict[str, Any], mutant: dict[str, Any], volatile: set[str] | None = None) -> str:
    """One schedule, mutant against the unmutated build under the same schedule."""
    if base.get("verdict") != "SOUND" or mutant.get("verdict") != "SOUND":
        return "NO_VERDICT"
    if not _reached(base, mutant):
        return "UNREACHABLE"
    same_state = digest(base.get("final_state"), volatile) == digest(mutant.get("final_state"), volatile)
    same_results = digest(base.get("raw_results"), volatile) == digest(mutant.get("raw_results"), volatile)
    return "HOLDS" if same_state and same_results else "VIOLATED"


def classify(pairs: Iterable[dict[str, Any]], volatile: set[str] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in pairs:
        m = out.setdefault(p["mutant_id"], {"runs": {}, "serial": None})
        v = run_verdict(p["base"], p["mutant"], volatile)
        if p["schedule"] == "serial":
            m["serial"] = v
        else:
            m["runs"][str(p["schedule"])] = v
    for m in out.values():
        vs = list(m["runs"].values()) + ([m["serial"]] if m["serial"] else [])
        if "VIOLATED" in vs:
            m["verdict"] = "VIOLATED"
        elif "HOLDS" in vs:
            m["verdict"] = "EQUIVALENT-SUSPECT"
        else:
            m["verdict"] = "UNREACHABLE"
        m["serial_reachable"] = m["serial"] in ("HOLDS", "VIOLATED")
        m["serial_killed"] = m["serial"] == "VIOLATED"
        m["no_verdict_runs"] = sum(1 for v in m["runs"].values() if v == "NO_VERDICT")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--pairs", type=Path, required=True)
    c.add_argument("--volatile", nargs="*", default=[])
    a = ap.parse_args(argv)
    pairs = [json.loads(line) for line in a.pairs.read_text().splitlines() if line.strip()]
    print(json.dumps(classify(pairs, set(a.volatile)), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
