"""EXPERIMENTAL: a repeatable onboarding measurement, for bringing a new application under DeploySpecter.

    python research_tools/onboarding/onboard.py start   --session DIR --app NAME --arm deterministic|muse \\
                                                         --participant ID --repo REPO --paths P... [--declaration F]
    python research_tools/onboarding/onboard.py event   --session DIR KIND [--detail TEXT] [--path F] [--ok|--failed]
                                                         [--tokens-in N --tokens-out N --usd X] [--evidence PATH]
    python research_tools/onboarding/onboard.py end     --session DIR
    python research_tools/onboarding/onboard.py measure --session DIR
    python research_tools/onboarding/onboard.py compare --sessions DIR... --out FILE

Use one session per fresh checkout, so that its line counts are its own. A session is an append-only,
hash-chained ``events.jsonl``, written by the recorder at the moment things happen.
Time is the recorder's clock, never reconstructed later.

Event kinds:
* ``start`` and ``end``;
* ``intervention``: a manual action the participant took that the tool didn't do (an edit, a lookup, a restart);
* ``attempt``: a run attempt, ``--ok`` or ``--failed``;
* ``generated``: a file produced by a tool or by Muse. A copy is kept, so later corrections can be measured;
* ``model_call``: tokens and estimated dollars;
* ``unsupported``: a feature the tool could not handle;
* ``first_corroborated_run``: the first run whose server-log corroboration passed. Its evidence path is required.

``measure`` computes:
- wall-clock setup time (start → end), and time to the first corroborated run;
- the manual intervention count, failed attempts, model tokens and dollars;
- the unsupported features;
- handwritten lines: lines added under the declared paths since the session's base commit, in files that were not
  generated, plus lines changed in generated files after generation ("generated code that required correction");
- the generated files that required correction.

``compare`` groups sessions by arm and reports medians per metric. With fewer than ``MIN_PARTICIPANTS`` independent
participants per arm, the verdict is ``INSUFFICIENT SAMPLE``. A session whose participant declared that they
contributed to DeploySpecter (``--developer``) never counts as independent: it is a labelled engineering baseline.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MIN_PARTICIPANTS = 5
KINDS = ("start", "intervention", "attempt", "generated", "model_call", "unsupported", "first_corroborated_run", "end")
ARMS = ("deterministic", "muse")


class Refused(ValueError):
    pass


def _canon(e: dict[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in e.items() if k != "hash"}, sort_keys=True, separators=(",", ":")).encode()


def events(session: Path) -> list[dict[str, Any]]:
    f = session / "events.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def verify(session: Path) -> list[str]:
    problems, prev, last_t = [], "0" * 64, None
    for i, e in enumerate(events(session)):
        if e.get("seq") != i or e.get("prev") != prev or e.get("hash") != hashlib.sha256(_canon(e)).hexdigest():
            problems.append(f"event {i}: the chain is broken")
        if last_t is not None and e.get("t", 0) < last_t:
            problems.append(f"event {i}: time goes backwards")
        prev, last_t = e.get("hash", ""), e.get("t", 0)
    return problems


def append(session: Path, kind: str, *, now: float | None = None, **fields: Any) -> dict[str, Any]:
    if kind not in KINDS:
        raise Refused(f"unknown event kind {kind!r}")
    ev = events(session)
    if verify(session):
        raise Refused("the session log is broken")
    if kind == "start" and ev:
        raise Refused("the session has already started")
    if kind != "start" and not ev:
        raise Refused("start the session first")
    if ev and ev[-1]["kind"] == "end":
        raise Refused("the session has ended")
    if kind == "first_corroborated_run":
        if any(e["kind"] == kind for e in ev):
            raise Refused("the first corroborated run is already recorded")
        if not fields.get("evidence"):
            raise Refused("a first corroborated run needs its evidence path")
    e = {"seq": len(ev), "kind": kind, "t": round(time.time() if now is None else now, 3),
         "prev": ev[-1]["hash"] if ev else "0" * 64, **fields}
    if ev and e["t"] < ev[-1]["t"]:
        raise Refused("time goes backwards")
    e["hash"] = hashlib.sha256(_canon(e)).hexdigest()
    with (session / "events.jsonl").open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def start(session: Path, *, app: str, arm: str, participant: str, repo: Path, paths: list[str],
          developer: bool = False, declaration: str | None = None, now: float | None = None) -> dict[str, Any]:
    if arm not in ARMS:
        raise Refused(f"arm must be one of {ARMS}")
    session.mkdir(parents=True, exist_ok=True)
    (session / "generated").mkdir(exist_ok=True)
    base = git(repo, "rev-parse", "HEAD").strip()
    return append(session, "start", now=now, app=app, arm=arm, participant=participant, repo=str(repo),
                  paths=paths, base_commit=base, developer=developer, declaration=declaration)


def record_generated(session: Path, path: str, *, now: float | None = None, tool: str = "") -> dict[str, Any]:
    """Call right after a tool writes ``path`` (relative to the repo). A copy is kept for correction measurement."""
    st = events(session)[0]
    src = Path(st["repo"]) / path
    data = src.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    (session / "generated" / sha).write_bytes(data)
    return append(session, "generated", now=now, path=path, sha256=sha, lines=data.decode(errors="replace").count(
        "\n"), tool=tool)


def _changed_lines(a: str, b: str) -> int:
    return sum(1 for x in difflib.ndiff(a.splitlines(), b.splitlines()) if x[:2] in ("+ ", "- "))


def measure(session: Path) -> dict[str, Any]:
    if verify(session):
        raise Refused(f"the session log is broken: {verify(session)}")
    ev = events(session)
    st = ev[0]
    end = next((e for e in ev if e["kind"] == "end"), None)
    repo = Path(st["repo"])
    first = next((e for e in ev if e["kind"] == "first_corroborated_run"), None)
    gen = {}
    for e in ev:
        if e["kind"] == "generated":
            gen[e["path"]] = e  # the latest generation of each path is the reference
    # handwritten: lines added since the base commit under the declared paths, outside generated files, plus the
    # working tree's uncommitted additions; corrections: lines changed in generated files after generation
    numstat = git(repo, "diff", "--numstat", st["base_commit"], "--", *st["paths"])
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "--", *st["paths"]).split()
    handwritten = 0
    per_file = {}
    for line in numstat.splitlines():
        added, _deleted, name = line.split("\t", 2)
        if name in gen or added == "-":
            continue
        per_file[name] = int(added)
        handwritten += int(added)
    for name in untracked:
        if name in gen:
            continue
        n = (repo / name).read_text(errors="replace").count("\n")
        per_file[name] = n
        handwritten += n
    corrected = {}
    for path, e in gen.items():
        original = (session / "generated" / e["sha256"]).read_text(errors="replace")
        now_text = (repo / path).read_text(errors="replace") if (repo / path).exists() else ""
        n = _changed_lines(original, now_text)
        if n:
            corrected[path] = n
    t0 = st["t"]
    return {"app": st["app"], "arm": st["arm"], "participant": st["participant"], "developer": st["developer"],
            "independent": not st["developer"], "declaration": st.get("declaration"),
            "ended": end is not None,
            "wall_clock_setup_seconds": round(end["t"] - t0, 3) if end else None,
            "seconds_to_first_corroborated_run": round(first["t"] - t0, 3) if first else None,
            "first_corroborated_run_evidence": first["evidence"] if first else None,
            "manual_interventions": sum(e["kind"] == "intervention" for e in ev),
            "failed_attempts": sum(e["kind"] == "attempt" and not e.get("ok") for e in ev),
            "attempts": sum(e["kind"] == "attempt" for e in ev),
            "handwritten_lines": handwritten + sum(corrected.values()), "handwritten_by_file": per_file,
            "generated_files": len(gen), "generated_files_corrected": len(corrected),
            "generated_lines_corrected": sum(corrected.values()), "corrected_by_file": corrected,
            "model_calls": sum(e["kind"] == "model_call" for e in ev),
            "tokens_in": sum(e.get("tokens_in", 0) for e in ev if e["kind"] == "model_call"),
            "tokens_out": sum(e.get("tokens_out", 0) for e in ev if e["kind"] == "model_call"),
            "est_usd": round(sum(e.get("usd", 0.0) for e in ev if e["kind"] == "model_call"), 6),
            "unsupported_features": [e.get("detail", "") for e in ev if e["kind"] == "unsupported"]}


METRICS = ("wall_clock_setup_seconds", "seconds_to_first_corroborated_run", "manual_interventions", "failed_attempts",
           "handwritten_lines", "generated_lines_corrected", "est_usd")


def compare(measures: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"arms": {}, "min_participants": MIN_PARTICIPANTS}
    for arm in ARMS:
        ms = [m for m in measures if m["arm"] == arm]
        ind = [m for m in ms if m["independent"] and m["ended"]]
        row: dict[str, Any] = {"sessions": len(ms), "independent_completed": len(ind),
                               "developer_baseline_sessions": sum(m["developer"] for m in ms),
                               "participants": sorted({m["participant"] for m in ind})}
        for k in METRICS:
            vals = [m[k] for m in ind if m[k] is not None]
            row[k] = {"median": statistics.median(vals) if vals else None, "n": len(vals)}
        row["never_reached_corroborated_run"] = sum(m["seconds_to_first_corroborated_run"] is None for m in ind)
        row["unsupported_features"] = sorted({f for m in ms for f in m["unsupported_features"]})
        out["arms"][arm] = row
    n = min(len(out["arms"][a]["participants"]) for a in ARMS)
    out["verdict"] = (f"INSUFFICIENT SAMPLE ({n} independent participants in the smaller arm < {MIN_PARTICIPANTS}):"
                      " descriptive only" if n < MIN_PARTICIPANTS else "DESCRIPTIVE COMPARISON (no test predeclared)")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--session", type=Path, required=True)
    s.add_argument("--app", required=True)
    s.add_argument("--arm", required=True)
    s.add_argument("--participant", required=True)
    s.add_argument("--repo", type=Path, required=True)
    s.add_argument("--paths", nargs="+", required=True)
    s.add_argument("--developer", action="store_true")
    s.add_argument("--declaration")
    e = sub.add_parser("event")
    e.add_argument("--session", type=Path, required=True)
    e.add_argument("kind")
    e.add_argument("--detail", default="")
    e.add_argument("--path")
    e.add_argument("--ok", action="store_true")
    e.add_argument("--failed", action="store_true")
    e.add_argument("--tokens-in", type=int, default=0)
    e.add_argument("--tokens-out", type=int, default=0)
    e.add_argument("--usd", type=float, default=0.0)
    e.add_argument("--evidence")
    for name in ("end", "measure"):
        x = sub.add_parser(name)
        x.add_argument("--session", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("--sessions", type=Path, nargs="+", required=True)
    c.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "start":
            start(a.session, app=a.app, arm=a.arm, participant=a.participant, repo=a.repo, paths=a.paths,
                  developer=a.developer, declaration=a.declaration)
        elif a.cmd == "event":
            if a.kind == "generated":
                record_generated(a.session, a.path, tool=a.detail)
            elif a.kind == "attempt":
                if a.ok == a.failed:
                    raise Refused("an attempt is --ok or --failed")
                append(a.session, "attempt", ok=a.ok, detail=a.detail)
            elif a.kind == "model_call":
                append(a.session, "model_call", tokens_in=a.tokens_in, tokens_out=a.tokens_out, usd=a.usd,
                       detail=a.detail)
            elif a.kind == "first_corroborated_run":
                append(a.session, a.kind, evidence=a.evidence, detail=a.detail)
            elif a.kind in ("start", "end"):
                raise Refused(f"use the {a.kind} command")
            else:
                append(a.session, a.kind, detail=a.detail)
        elif a.cmd == "end":
            append(a.session, "end")
        elif a.cmd == "measure":
            m = measure(a.session)
            (a.session / "measure.json").write_text(json.dumps(m, indent=1))
            print(json.dumps(m, indent=1))
        elif a.cmd == "compare":
            r = compare([measure(p) for p in a.sessions])
            a.out.write_text(json.dumps(r, indent=1))
            print(json.dumps({"verdict": r["verdict"]}))
    except (Refused, subprocess.CalledProcessError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    shutil.which("git") or sys.exit("git is required")
    sys.exit(main())
