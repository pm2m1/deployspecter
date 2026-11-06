"""EXPERIMENTAL onboarding pilot: the hash-chained session log, and its measures.

One session = one operator × one app × one condition. ``events.jsonl`` is append-only. Each event carries the
recorder's clock (UTC seconds), its sequence number, the previous event's hash and its own. The log is written at the
moment things happen; times are never reconstructed later.

Event kinds:
* ``start``: app, condition (D or M), operator, base commit, declared adapter paths;
* ``intervention``: one manual operator action the tooling didn't do (reading source or docs, writing or editing
  the adapter or spec, diagnosing a failure);
* ``proposal``: a spec or workload checked by the validator, with source (operator or muse) and verdict;
* ``attempt``: a container run, with exit status, its S2 and S3 problems, and the evidence path;
* ``snapshot``: the adapter and spec files at that moment (copied into the session), for revision counting;
* ``model_call``: tokens and estimated dollars;
* ``milestone``: S1, S2 or S3, the first time each is met (an evidence path is required);
* ``unsupported``: something the harness can't handle;
* ``budget_stop``: a frozen budget was exhausted;
* ``end``.

``measure`` computes:
* times from start to S1, S2 and S3, and the total;
* interventions;
* proposals: total and rejected, by source;
* attempts: total and failed;
* adapter lines at the end, and lines revised after first written (summed over consecutive snapshots);
* model calls, tokens and dollars;
* the S3 pass rate over attempts that met S2.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

KINDS = ("start", "intervention", "proposal", "attempt", "snapshot", "model_call", "milestone", "unsupported",
         "budget_stop", "end")


class SessionRefused(ValueError):
    pass


def _h(e: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in e.items() if k != "hash"}, sort_keys=True).encode()).hexdigest()


def events(session: Path) -> list[dict[str, Any]]:
    f = session / "events.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def verify(session: Path) -> list[str]:
    p, prev = [], "0" * 64
    for i, e in enumerate(events(session)):
        if e.get("seq") != i or e.get("prev") != prev or e.get("hash") != _h(e):
            p.append(f"event {i}: chain broken")
        prev = e.get("hash", "")
    return p


def append(session: Path, kind: str, *, now: float | None = None, **fields: Any) -> dict[str, Any]:
    if kind not in KINDS:
        raise SessionRefused(f"unknown event kind {kind!r}")
    ev = events(session)
    if verify(session):
        raise SessionRefused("the session log is broken")
    if kind != "start" and (not ev or ev[0]["kind"] != "start"):
        raise SessionRefused("start the session first")
    if ev and ev[-1]["kind"] == "end":
        raise SessionRefused("the session has ended")
    if kind == "milestone":
        if fields.get("name") not in ("S1", "S2", "S3") or not fields.get("evidence"):
            raise SessionRefused("a milestone is S1, S2 or S3, with evidence")
        if any(e["kind"] == "milestone" and e["name"] == fields["name"] for e in ev):
            raise SessionRefused(f"{fields['name']} is already recorded")
    e = {"seq": len(ev), "kind": kind, "t": time.time() if now is None else now,
         "prev": ev[-1]["hash"] if ev else "0" * 64, **fields}
    e["hash"] = _h(e)
    session.mkdir(parents=True, exist_ok=True)
    with (session / "events.jsonl").open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def start(session: Path, *, app: str, condition: str, operator: str, repo: Path, paths: list[str],
          operator_kind: str, now: float | None = None) -> dict[str, Any]:
    if condition not in ("D", "M"):
        raise SessionRefused("condition D or M")
    if events(session):
        raise SessionRefused("the session already exists")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    return append(session, "start", now=now, app=app, condition=condition, operator=operator,
                  operator_kind=operator_kind, repo=str(repo), paths=paths, base_commit=base)


def snapshot(session: Path, *, now: float | None = None) -> dict[str, Any]:
    st = events(session)[0]
    n = sum(1 for e in events(session) if e["kind"] == "snapshot")
    d = session / "snapshots" / f"{n:03d}"
    files = {}
    for rel in st["paths"]:
        src = Path(st["repo"]) / rel
        if src.exists():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, d / rel)
            files[rel] = hashlib.sha256(src.read_bytes()).hexdigest()
    return append(session, "snapshot", now=now, index=n, files=files)


def _lines(session: Path, index: int, rel: str) -> list[str]:
    f = session / "snapshots" / f"{index:03d}" / rel
    return f.read_text().splitlines() if f.exists() else []


def measure(session: Path) -> dict[str, Any]:
    ev = events(session)
    if verify(session):
        raise SessionRefused("the session log is broken")
    st = ev[0]
    t0 = st["t"]
    end = next((e["t"] for e in ev if e["kind"] == "end"), None)
    ms = {e["name"]: e["t"] - t0 for e in ev if e["kind"] == "milestone"}
    snaps = [e for e in ev if e["kind"] == "snapshot"]
    written, revised = 0, 0
    for rel in st["paths"]:
        prev = None
        for s in snaps:
            cur = _lines(session, s["index"], rel)
            if prev is not None and cur != prev:
                revised += sum(1 for d in difflib.ndiff(prev, cur) if d[:1] in "+-")
            prev = cur if cur else prev
        written += len(prev or [])
    attempts = [e for e in ev if e["kind"] == "attempt"]
    s2ok = [a for a in attempts if not a.get("s2_problems")]
    props = [e for e in ev if e["kind"] == "proposal"]
    calls = [e for e in ev if e["kind"] == "model_call"]
    return {"app": st["app"], "condition": st["condition"], "operator": st["operator"],
            "operator_kind": st["operator_kind"], "ended": end is not None,
            "seconds_total": (end - t0) if end else None,
            "seconds_to_S1": ms.get("S1"), "seconds_to_S2": ms.get("S2"), "seconds_to_S3": ms.get("S3"),
            "interventions": sum(1 for e in ev if e["kind"] == "intervention"),
            "proposals": len(props), "proposals_rejected": sum(1 for e in props if e.get("problems")),
            "proposals_by_source": {s: sum(1 for e in props if e.get("source") == s) for s in ("operator", "muse")},
            "muse_proposals_rejected": sum(1 for e in props if e.get("source") == "muse" and e.get("problems")),
            "attempts": len(attempts), "attempts_failed": sum(1 for a in attempts if a.get("s3_problems")),
            "adapter_lines_final": written, "adapter_lines_revised": revised,
            "model_calls": len(calls), "tokens_in": sum(c.get("tokens_in") or 0 for c in calls),
            "tokens_out": sum(c.get("tokens_out") or 0 for c in calls),
            "est_usd": round(sum(c.get("est_usd") or 0 for c in calls), 6),
            "s3_pass_rate_on_s2_runs": (sum(1 for a in s2ok if not a.get("s3_problems")) / len(s2ok)) if s2ok
            else None,
            "unsupported": [e.get("detail") for e in ev if e["kind"] == "unsupported"],
            "budget_stops": [e.get("detail") for e in ev if e["kind"] == "budget_stop"]}
