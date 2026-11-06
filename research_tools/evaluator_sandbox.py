"""EXPERIMENTAL: launch a sealed evaluator with only its permitted inputs, and verify what it can and cannot reach.

    python research_tools/evaluator_sandbox.py normalize SRC.tar DST.tar
    python research_tools/evaluator_sandbox.py launch --mode simulated|confirmatory --registry REG --cases DIR... \\
        --out PKG [--oracle FILE --approved-oracles APPROVED.json] [--sensitive PATH...] [--container] -- CMD...

This wraps ``sealed_evaluation.py`` and ``corpus_intake.py`` without changing them.

**normalize.** The curator's CLI for ``sealed_evaluation.normalize_archive``. ``git archive`` writes the commit id into
a pax header, and stamps commit times that order the two revisions. ``build_package`` refuses such archives, so
the curator normalizes each one *before* ``corpus_intake.py submit``.

**launch**, in order:

1. **Mode.** ``confirmatory`` is refused unless ``verify_amendment.py`` reports Amendment 003 "adopted, consistent"
   and the registration evidence file named by ``--registration`` exists. With none of the approvals in place
   today, only ``simulated`` runs. Simulated output is labelled as such in ``LAUNCH.json``.
2. **Package.** ``sealed_evaluation.build_package`` makes it in a fresh directory. It holds the frozen registry
   and each case's ``case.json``, ``R1.tar`` and ``R2.tar``, plus only **approved** oracle revisions: a file whose
   sha256 isn't in the reviewer-approved allowlist is refused, never copied.
3. **Byte and content checks.** No package file may be byte-identical to, or contain the text of, any
   ``--sensitive`` file: reveal files, curator submissions, a results ledger with a reveal authorization, raw
   archives. The package scan (40-hex commit ids, reveal paths, tar identity metadata) must be clean.
4. **Run.** The evaluator command runs:
   - with cwd at the package;
   - with an environment reduced to ``PATH``, ``LANG`` and a fresh empty ``HOME``, so no model key,
     ``DATABASE_URL`` or ``DS_*`` variable reaches it;
   - with ``--container``: in a throwaway container ``ds-mvp-evalbox-<nonce>``, from the runner's pinned Python
     base image (``--pull never``), with ``--network none``, a read-only root, all capabilities dropped,
     ``no-new-privileges``, uid 65534, and **only the package mounted, read-only**, at ``/pkg``. The container is
     removed afterwards.

**Limits (stated, not solved).**
- In host mode the evaluator runs as the same OS user, so it *can* open any file that user can, a 0600 reveal
  included. Host mode guarantees only what reaches it by design: cwd, environment and package contents.
- Filesystem separation needs ``--container``, or a separate account or machine.
- Container mode shares Docker Desktop's daemon and VM. It is restricted, not hostile-grade.
- Neither mode hides what the revisions' *contents* reveal (diffing R1 against R2 shows the fix location).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "research_tools"))
import sealed_evaluation as se  # noqa: E402
import verify_amendment as va  # noqa: E402

BASE_IMAGE = "python@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30"
ENV_KEEP = ("PATH", "LANG")
# fields a sensitive JSON file shares with the public case manifest by design (corpus_intake.leak_scan excludes
# the same ones): their values are not label material
PUBLIC_KEYS = frozenset({"case_id", "repository", "schema"})


class Refused(SystemExit):
    pass


def confirmatory_allowed(protocol_dir: Path, frozen_dir: Path, registration: Path | None) -> list[str]:
    """Why a confirmatory launch is not allowed (empty when it is)."""
    r = va.verify(protocol_dir, frozen_dir)
    why = []
    if r["integrity_problems"]:
        why.append(f"amendment integrity problems: {r['integrity_problems']}")
    if r["adoption"] != "adopted, consistent":
        why.append(f"Amendment 003 is {r['adoption']}")
    if registration is None or not registration.exists():
        why.append("no timestamped registration evidence")
    return why


def approved_oracles(oracles: list[Path], allowlist: Path | None) -> dict[str, str]:
    if not oracles:
        return {}
    if allowlist is None:
        raise Refused("refused: oracle files need a reviewer-approved allowlist (--approved-oracles)")
    allowed = json.loads(allowlist.read_text())  # {"file name": "sha256", ...}
    out = {}
    for f in oracles:
        h = se.sha(f)
        if allowed.get(f.name) != h:
            raise Refused(f"refused: {f.name} (sha256 {h[:12]}…) is not an approved oracle revision")
        out[f.name] = h
    return out


def sensitive_problems(package: Path, sensitive: list[Path]) -> list[str]:
    """Byte identity, or a sensitive file's text appearing inside a package file (not only which files exist)."""
    problems = []
    blobs = []
    for s in sensitive:
        for f in ([s] if s.is_file() else sorted(p for p in s.rglob("*") if p.is_file())):
            data = f.read_bytes()
            blobs.append((f, data, se.sha(f)))
    for p in sorted(x for x in package.rglob("*") if x.is_file()):
        pd = p.read_bytes()
        h = se.sha(p)
        for f, data, fh in blobs:
            if h == fh:
                problems.append(f"{p.relative_to(package)} is byte-identical to sensitive {f.name}")
            elif len(data) >= 32 and data.strip() and data.strip() in pd:
                problems.append(f"{p.relative_to(package)} contains the contents of sensitive {f.name}")
            # the sensitive file's JSON string values (commits, paths, keywords) inside a non-tar package file
            elif p.suffix != ".tar":
                for v in _json_strings(data):
                    if len(v) >= 12 and v.encode() in pd:
                        problems.append(f"{p.relative_to(package)} contains a value from sensitive {f.name}")
                        break
    return problems


def _json_strings(data: bytes) -> list[str]:
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return []
    out: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k not in PUBLIC_KEYS:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    return out


def launch(*, mode: str, registry: Path, cases: list[Path], out: Path, cmd: list[str],
           oracles: list[Path] = (), allowlist: Path | None = None, sensitive: list[Path] = (),
           reveal_dirs: list[Path] = (), container: bool = False, registration: Path | None = None,
           protocol_dir: Path = REPO / "docs/experimental_mvp/protocol",
           frozen_dir: Path = REPO / "docs/research/live_stepping", timeout: float = 300) -> dict[str, Any]:
    if mode not in ("simulated", "confirmatory"):
        raise Refused("refused: --mode is simulated or confirmatory")
    if mode == "confirmatory":
        why = confirmatory_allowed(protocol_dir, frozen_dir, registration)
        if why:
            raise Refused(f"refused: confirmatory evaluation is not authorized: {why}")
    approved = approved_oracles(list(oracles), allowlist)
    se.build_package(registry, list(cases), out, reveal_dirs=list(reveal_dirs))
    if approved:
        (out / "oracles").mkdir()
        for f in oracles:
            shutil.copyfile(f, out / "oracles" / f.name)
    problems = se.scan_package(out, reveal_dirs=list(reveal_dirs)) + sensitive_problems(out, list(sensitive))
    if problems:
        shutil.rmtree(out)
        raise Refused(f"refused: the evaluator package fails its checks: {problems}")
    home = Path(tempfile.mkdtemp(prefix="ds_evalhome_"))
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ} | {"HOME": str(home)}
    started = time.time()
    name = None
    try:
        if container:
            name = f"ds-mvp-evalbox-{secrets.token_hex(4)}"
            full = ["docker", "run", "--rm", "--name", name, "--pull", "never", "--network", "none", "--read-only",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
                    "--tmpfs", "/tmp:rw,size=16m", "-e", "HOME=/tmp", "-w", "/pkg",
                    "-v", f"{out.resolve()}:/pkg:ro", BASE_IMAGE, *cmd]
            p = subprocess.run(full, capture_output=True, text=True, timeout=timeout, env=dict(os.environ))
        else:
            p = subprocess.run(cmd, cwd=out, env=env, capture_output=True, text=True, timeout=timeout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        if name:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
    leftover = bool(name) and bool(subprocess.run(["docker", "ps", "-a", "-q", "--filter", f"name={name}"],
                                                  capture_output=True, text=True).stdout.strip())
    record = {"mode": mode, "label": "SIMULATED (not a confirmatory evaluation)" if mode == "simulated"
              else "CONFIRMATORY", "container": container, "container_name": name,
              "container_left_behind": leftover, "image": BASE_IMAGE if container else None,
              "exit": p.returncode, "seconds": round(time.time() - started, 3), "approved_oracles": approved,
              "package": str(out), "env_keys": sorted(env) if not container else ["HOME"],
              "stdout_tail": p.stdout[-4000:], "stderr_tail": p.stderr[-4000:]}
    (out.parent / f"{out.name}.LAUNCH.json").write_text(json.dumps(record, indent=1))
    return record


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("normalize")
    n.add_argument("src", type=Path)
    n.add_argument("dst", type=Path)
    la = sub.add_parser("launch")
    la.add_argument("--mode", required=True)
    la.add_argument("--registry", type=Path, required=True)
    la.add_argument("--cases", type=Path, nargs="+", required=True)
    la.add_argument("--out", type=Path, required=True)
    la.add_argument("--oracle", type=Path, nargs="*", default=[])
    la.add_argument("--approved-oracles", type=Path)
    la.add_argument("--sensitive", type=Path, nargs="*", default=[])
    la.add_argument("--reveal-dir", type=Path, nargs="*", default=[])
    la.add_argument("--registration", type=Path)
    la.add_argument("--container", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "normalize":
            se.normalize_archive(a.src, a.dst)
            leaks = se.archive_metadata_leaks(a.dst)
            print(json.dumps({"normalized": str(a.dst), "remaining_metadata_leaks": leaks, "sha256": se.sha(a.dst)}))
            return 1 if leaks else 0
        if not cmd:
            ap.error("launch needs an evaluator command after --")
        r = launch(mode=a.mode, registry=a.registry, cases=a.cases, out=a.out, cmd=cmd, oracles=a.oracle,
                   allowlist=a.approved_oracles, sensitive=a.sensitive, reveal_dirs=a.reveal_dir,
                   container=a.container, registration=a.registration)
        print(json.dumps({k: r[k] for k in ("mode", "label", "container", "exit", "container_left_behind")}))
        return 0 if r["exit"] == 0 and not r["container_left_behind"] else 1
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
