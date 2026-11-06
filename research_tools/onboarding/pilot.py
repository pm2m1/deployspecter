"""EXPERIMENTAL onboarding pilot CLI (onb-pilot/1). Every operator action goes through it, so the session log records
events as they happen.

    pilot.py start      --session S --manifest M --app APP --operator ID --operator-kind developer-ai|independent
    pilot.py note       --session S KIND --detail TEXT          # intervention | unsupported | budget_stop
    pilot.py snapshot   --session S
    pilot.py pkginfo    --manifest M --app APP --sdists DIR      # the sdist's PKG-INFO (public docs), printed
    pilot.py wheels     --session S NAME==VERSION ...            # resolve hash-pinned wheels (linux aarch64 cp312)
    pilot.py validate   --session S --manifest M --spec F --workload F [--source operator|muse]
    pilot.py run        --session S --manifest M --spec F --workload F --sdists DIR --out DIR
    pilot.py muse       --session S --manifest M --sdists DIR --env-file F --label L
    pilot.py end        --session S
    pilot.py measure    --session S

``validate`` records a ``proposal`` event. The first spec and workload that pass record milestone S1.
``run`` records an ``attempt`` event (exit, S2 and S3 problems, evidence). The first runs meeting S2 and S3 record
those milestones. ``run`` refuses before S1, and also once the frozen attempt budget or time budget is spent; it
then records ``budget_stop``. ``muse`` refuses outside condition M or over the frozen call and dollar budget.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from deployspecter.experimental.onboarding_pilot import correctness, session, spec  # noqa: E402


def manifest_entry(manifest: Path, app: str) -> tuple[dict[str, Any], dict[str, Any]]:
    m = json.loads(manifest.read_text())
    return m, next(a for a in m["apps"] if a["app"] == app)


def _session_budget(s: Path, m: dict[str, Any]) -> list[str]:
    ev = session.events(s)
    st = ev[0]
    out = []
    if time.time() - st["t"] > m["budgets"]["wall_clock_seconds"]:
        out.append("wall-clock budget spent")
    if sum(1 for e in ev if e["kind"] == "attempt") >= m["budgets"]["max_attempts"]:
        out.append("attempt budget spent")
    return out


def cmd_validate(a: argparse.Namespace) -> int:
    st = session.events(a.session)[0]
    m, pins = manifest_entry(a.manifest, st["app"])
    sp, wl = json.loads(a.spec.read_text()), json.loads(a.workload.read_text())
    problems = spec.validate(sp, wl, pins)
    session.append(a.session, "proposal", source=a.source, spec_sha256=_sha(a.spec), workload_sha256=_sha(a.workload),
                   problems=problems)
    if not problems and not _has(a.session, "S1"):
        session.append(a.session, "milestone", name="S1", evidence=f"{a.spec.name}+{a.workload.name}")
    print(json.dumps({"valid": not problems, "problems": problems}, indent=1))
    return 0 if not problems else 1


def cmd_run(a: argparse.Namespace) -> int:
    sys.path.insert(0, str(REPO / "research_tools" / "onboarding"))
    import pilot_runner as pr

    from deployspecter.experimental import harness as hz
    st = session.events(a.session)[0]
    m, pins = manifest_entry(a.manifest, st["app"])
    if not _has(a.session, "S1"):
        print("refused: no valid spec and workload yet (S1)")
        return 2
    if over := _session_budget(a.session, m):
        session.append(a.session, "budget_stop", detail="; ".join(over))
        print("refused:", over)
        return 2
    sp, wl = json.loads(a.spec.read_text()), json.loads(a.workload.read_text())
    if p := spec.validate(sp, wl, pins):
        print("refused: invalid", p)
        return 2
    session.snapshot(a.session)
    a.out.mkdir(parents=True, exist_ok=False)
    secret = Path.home() / ".ds_mvp_pg_password"
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", secret.read_text().strip())
    import shutil
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="ds_onb_build_"))
    try:
        try:
            image = pr.build_image(sp, a.sdists, work)
            r = pr.run(target, sp, wl, image, a.out, timeout_s=m["budgets"]["work_timeout_s"])
        except Exception as exc:  # noqa: BLE001 - a failed build is a failed attempt, recorded
            r = {"error": f"{type(exc).__name__}: {str(exc)[:2000]}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    s2p = correctness.s2(r) if "corroboration" in r else [r.get("error", "no run")]
    s3p = correctness.s3(r, wl) if "corroboration" in r else s2p
    text = json.dumps(r, indent=1, default=str).replace(target.password, "<redacted>")
    (a.out / "run.json").write_text(text)
    ev = session.append(a.session, "attempt", exit=r.get("exit_code"), error=r.get("error"), s2_problems=s2p,
                        s3_problems=s3p, evidence=str(a.out), run_sha256=hashlib.sha256(text.encode()).hexdigest())
    if not s2p and not _has(a.session, "S2"):
        session.append(a.session, "milestone", name="S2", evidence=str(a.out))
    if not s3p and not _has(a.session, "S3"):
        session.append(a.session, "milestone", name="S3", evidence=str(a.out))
    print(json.dumps({"attempt": ev["seq"], "exit": r.get("exit_code"), "error": r.get("error"), "S2": s2p,
                      "S3": s3p}, indent=1, default=str)[:4000])
    return 0 if not s3p else 1


def cmd_muse(a: argparse.Namespace) -> int:
    st = session.events(a.session)[0]
    m, pins = manifest_entry(a.manifest, st["app"])
    if st["condition"] != "M":
        print("refused: Muse is condition M only")
        return 2
    ev = session.events(a.session)
    calls = [e for e in ev if e["kind"] == "model_call"]
    spent = sum(c.get("est_usd") or 0 for c in calls)
    b = m["budgets"]["muse"]
    if len(calls) >= b["max_calls"] or spent >= b["max_estimated_usd"]:
        session.append(a.session, "budget_stop", detail=f"Muse budget: {len(calls)} calls, ${spent:.4f}")
        print("refused: Muse budget spent")
        return 2
    from deployspecter.experimental import muse
    from deployspecter.experimental.onboarding_pilot import muse_assist
    client = muse.client_from_env_file(a.env_file, max_requests=1,
                                       max_estimated_usd=round(b["max_estimated_usd"] - spent, 6))
    info = _pkginfo(a.sdists / pins["sdist"])
    log = a.session / "muse_calls.jsonl"
    try:
        entry = muse_assist.ask(client, pkg_info=info, contract=spec.CONTRACT, log=log, label=a.label)
    except Exception as exc:  # noqa: BLE001
        session.append(a.session, "model_call", label=a.label, error=type(exc).__name__, tokens_in=0, tokens_out=0,
                       est_usd=0)
        print("Muse call failed:", type(exc).__name__)
        return 1
    session.append(a.session, "model_call", label=a.label, tokens_in=entry.get("tokens_in"),
                   tokens_out=entry.get("tokens_out"), est_usd=entry.get("est_usd"),
                   reply_sha256=entry.get("reply_sha256"))
    print(json.dumps(entry.get("proposal"), indent=1))
    return 0


def _pkginfo(sdist: Path) -> str:
    with tarfile.open(sdist) as tf:
        member = next(x for x in tf.getmembers() if x.name.count("/") == 1 and x.name.endswith("/PKG-INFO"))
        return tf.extractfile(member).read().decode(errors="replace")


def cmd_wheels(a: argparse.Namespace) -> int:
    """Resolve each NAME==VERSION to one wheel for CPython 3.12 on linux aarch64 (or a pure wheel), with PyPI's sha256.
    Data only: nothing is installed or run here."""
    out = []
    for spec_ in a.specs:
        name, _, version = spec_.partition("==")
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
            files = json.load(r)["urls"]
        whl = [f for f in files if f["packagetype"] == "bdist_wheel"]
        def linux_arm(f: dict[str, Any]) -> bool:
            return "manylinux" in f["filename"] and "aarch64" in f["filename"]
        pick = ([f for f in whl if "cp312" in f["filename"] and linux_arm(f)] or
                [f for f in whl if "abi3" in f["filename"] and linux_arm(f)] or
                [f for f in whl if f["filename"].endswith("none-any.whl")])
        if not pick:
            print(f"no usable wheel for {spec_}", file=sys.stderr)
            return 1
        out.append({"name": name, "version": version, "filename": pick[0]["filename"],
                    "sha256": pick[0]["digests"]["sha256"]})
    session.append(a.session, "intervention", detail=f"resolved wheels {a.specs}")
    print(json.dumps(out, indent=1))
    return 0


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _has(s: Path, name: str) -> bool:
    return any(e["kind"] == "milestone" and e.get("name") == name for e in session.events(s))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--session", type=Path, required=True)
    s.add_argument("--manifest", type=Path, required=True)
    s.add_argument("--app", required=True)
    s.add_argument("--operator", required=True)
    s.add_argument("--operator-kind", choices=("developer-ai", "independent"), required=True)
    n = sub.add_parser("note")
    n.add_argument("--session", type=Path, required=True)
    n.add_argument("kind", choices=("intervention", "unsupported", "budget_stop"))
    n.add_argument("--detail", required=True)
    for c in ("snapshot", "end", "measure"):
        sub.add_parser(c).add_argument("--session", type=Path, required=True)
    p = sub.add_parser("pkginfo")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--app", required=True)
    p.add_argument("--sdists", type=Path, required=True)
    w = sub.add_parser("wheels")
    w.add_argument("--session", type=Path, required=True)
    w.add_argument("specs", nargs="+")
    v = sub.add_parser("validate")
    for x in ("session", "manifest", "spec", "workload"):
        v.add_argument(f"--{x}", type=Path, required=True)
    v.add_argument("--source", choices=("operator", "muse"), default="operator")
    r = sub.add_parser("run")
    for x in ("session", "manifest", "spec", "workload", "sdists", "out"):
        r.add_argument(f"--{x}", type=Path, required=True)
    mu = sub.add_parser("muse")
    for x in ("session", "manifest", "sdists", "env_file"):
        mu.add_argument(f"--{x.replace('_', '-')}", dest=x, type=Path, required=True)
    mu.add_argument("--label", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "start":
            m, entry = manifest_entry(a.manifest, a.app)
            session.start(a.session, app=a.app, condition=entry["condition"], operator=a.operator,
                          operator_kind=a.operator_kind, repo=REPO, paths=entry["declared_paths"])
            return 0
        if a.cmd == "note":
            session.append(a.session, a.kind, detail=a.detail)
            return 0
        if a.cmd == "snapshot":
            session.snapshot(a.session)
            return 0
        if a.cmd == "end":
            session.append(a.session, "end")
            return 0
        if a.cmd == "measure":
            print(json.dumps(session.measure(a.session), indent=1))
            return 0
        if a.cmd == "pkginfo":
            _, pins = manifest_entry(a.manifest, a.app)
            print(_pkginfo(a.sdists / pins["sdist"]))
            return 0
        return {"wheels": cmd_wheels, "validate": cmd_validate, "run": cmd_run, "muse": cmd_muse}[a.cmd](a)
    except session.SessionRefused as exc:
        print("refused:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
