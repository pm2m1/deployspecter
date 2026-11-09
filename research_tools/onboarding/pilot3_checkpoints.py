"""onb-pilot/3: the predeclared checkpoint record per session (frozen with the manifests). It reads the
CHECKPOINT_A and MUSE_N notes, the model calls and the Muse-sourced proposals from each session's event log.

    python research_tools/onboarding/pilot3_checkpoints.py --sessions DIR ... --out CHECKPOINTS.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from deployspecter.experimental.onboarding_pilot import session  # noqa: E402

DECISION = re.compile(r"^(CHECKPOINT_A|MUSE_\d+)\s+(?:decision=(accepted|modified|rejected)|plan:)\s*:?\s*(.*)$",
                      re.S)


def one(s: Path) -> dict:
    ev = session.events(s)
    st = ev[0]
    notes = []
    for e in ev:
        if e["kind"] == "intervention" and (m := DECISION.match(e.get("detail") or "")):
            notes.append({"point": m.group(1), "decision": m.group(2) or "plan", "text": m.group(3)[:300],
                          "t": round(e["t"] - st["t"], 1)})
    calls = [e for e in ev if e["kind"] == "model_call"]
    first_call = round(calls[0]["t"] - st["t"], 1) if calls else None
    cp = next((n for n in notes if n["point"] == "CHECKPOINT_A"), None)
    return {"session": s.name, "app": st["app"], "condition": st["condition"],
            "checkpoint_a": cp, "checkpoint_a_recorded": cp is not None,
            "mandatory_muse_call_made": st["condition"] != "M" or any(c.get("label") == "checkpoint_a" for c in calls),
            "muse_calls": len(calls), "first_muse_call_s": first_call,
            "muse_usd": round(sum(c.get("est_usd") or 0 for c in calls), 6),
            "muse_errors": sum(1 for c in calls if c.get("error")),
            "proposals_from_muse": sum(1 for e in ev if e["kind"] == "proposal" and e.get("source") == "muse"),
            "proposals_from_muse_rejected": sum(1 for e in ev if e["kind"] == "proposal" and e.get("source") == "muse"
                                                and e.get("problems")),
            "decisions": notes}


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--sessions", nargs="+", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    rows = [one(s) for s in o.sessions]
    o.out.write_text(json.dumps({"label": "AI-OPERATOR, NONBLIND EXPLORATORY (onb-pilot/3)", "sessions": rows},
                                indent=1, sort_keys=True) + "\n")
    for r in rows:
        print(r["session"], r["condition"], (r["checkpoint_a"] or {}).get("decision"), r["muse_calls"], r["muse_usd"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
