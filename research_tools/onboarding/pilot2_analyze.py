"""onb-pilot/2 predeclared analysis (frozen with the manifests, before any session). Descriptive only.

    python research_tools/onboarding/pilot2_analyze.py --sessions DIR ... --out ANALYSIS.json

Per session: ``session.measure`` (unchanged), plus database-lock waiting time from the session's ``pg_lock.log``.
Each wait is the interval [acquired - waited, acquired]. A wait is subtracted from an elapsed time only for the part
of it that falls before that milestone. Unmet milestones are censored at the 5400 s budget, never imputed.

Per app and replicate: the paired difference M - D in lock-adjusted seconds to S1, S2 and S3, total seconds,
interventions, failed attempts, final and revised adapter lines, and rejected proposals. Per app: the median over
replicates. Reported only for operator_kind developer-ai sessions, labelled AI-OPERATOR; no inference.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from deployspecter.experimental.onboarding_pilot import session  # noqa: E402

BUDGET = 5400
WAIT = re.compile(r"^(\S+Z) pid=\d+ .* acquired waited=([\d.]+)s")


def waits(log: Path) -> list[tuple[float, float]]:
    out = []
    if log.exists():
        for line in log.read_text().splitlines():
            if m := WAIT.match(line):
                acq = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ"))
                out.append((acq - float(m.group(2)), float(acq)))
    return out


def adjusted(t0: float, rel: float | None, ws: list[tuple[float, float]]) -> float | None:
    if rel is None:
        return None
    end = t0 + rel
    lost = sum(max(0.0, min(b, end) - max(a, t0)) for a, b in ws)
    return round(rel - lost, 1)


def one(s: Path) -> dict:
    m = session.measure(s)
    t0 = session.events(s)[0]["t"]
    ws = waits(s / "pg_lock.log")
    snaps = [e for e in session.events(s) if e["kind"] == "snapshot"]
    revisions = sum(1 for a, b in zip(snaps, snaps[1:], strict=False) if a.get("files") != b.get("files"))
    rep = re.search(r"_r(\d+)", s.parent.name + "/" + s.name)
    out = {**m, "session": str(s), "replicate": int(rep.group(1)) if rep else None,
           "lock_wait_seconds": round(sum(b - a for a, b in ws), 1), "code_revisions": revisions}
    for k in ("seconds_to_S1", "seconds_to_S2", "seconds_to_S3", "seconds_total"):
        out[f"{k}_lock_adjusted"] = adjusted(t0, m[k], ws)
        out[f"{k}_censored"] = m[k] is None
    return out


def cens(v: float | None) -> float:
    return BUDGET if v is None else v


def analyze(rows: list[dict]) -> dict:
    keys = ["seconds_to_S1_lock_adjusted", "seconds_to_S2_lock_adjusted", "seconds_to_S3_lock_adjusted",
            "seconds_total_lock_adjusted", "interventions", "attempts_failed", "adapter_lines_final",
            "adapter_lines_revised", "proposals_rejected", "code_revisions"]
    pairs, per_app = [], {}
    for app in sorted({r["app"] for r in rows}):
        for rep in sorted({r["replicate"] for r in rows if r["app"] == app}):
            d = [r for r in rows if r["app"] == app and r["replicate"] == rep and r["condition"] == "D"]
            m = [r for r in rows if r["app"] == app and r["replicate"] == rep and r["condition"] == "M"]
            if len(d) != 1 or len(m) != 1:
                pairs.append({"app": app, "replicate": rep, "incomplete_pair": True})
                continue
            p = {"app": app, "replicate": rep, "D": {k: d[0][k] for k in keys}, "M": {k: m[0][k] for k in keys},
                 "D_reached_S3": d[0]["seconds_to_S3"] is not None, "M_reached_S3": m[0]["seconds_to_S3"] is not None,
                 "M_muse": {k: m[0][k] for k in ("model_calls", "tokens_in", "tokens_out", "est_usd",
                                                 "muse_proposals_rejected")}}
            p["M_minus_D"] = {k: round((cens(m[0][k]) if "seconds" in k else (m[0][k] or 0))
                                       - (cens(d[0][k]) if "seconds" in k else (d[0][k] or 0)), 1) for k in keys}
            pairs.append(p)
        ok = [p for p in pairs if p["app"] == app and not p.get("incomplete_pair")]
        if ok:
            per_app[app] = {k: statistics.median(p["M_minus_D"][k] for p in ok) for k in keys}
            per_app[app]["replicates"] = len(ok)
    return {"label": "AI-OPERATOR, NONBLIND EXPLORATORY (onb-pilot/2); not human participants; descriptive only",
            "sessions": rows, "pairs": pairs, "median_M_minus_D_per_app": per_app,
            "claim": "none possible: the plan's Q4 rule needs >= 3 applications; 2 were eligible"}


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--sessions", nargs="+", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    res = analyze([one(s) for s in o.sessions])
    o.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps(res["median_M_minus_D_per_app"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
