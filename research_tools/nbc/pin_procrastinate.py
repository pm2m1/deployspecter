"""ds-nbc/1, procrastinate (NONBLIND development): pin the screened fix commits and their first parents as
deterministic package trees, and verify the recorded pins.

    pin_procrastinate.py pin --mirror REPO.git --out-root DIR COMMIT ...
    pin_procrastinate.py verify --mirror REPO.git \
        --screening docs/experimental_mvp/nonblind_corpus/procrastinate/SCREENING.json

For each fix commit F (R2) and its first parent P (R1): ``git archive --format=tar <rev> procrastinate`` (the package
directory only), normalized by ``research_tools/sealed_evaluation.py``'s ``normalize_archive``, written to
``DIR/procrastinate-<F[:7]>/{R1,R2}.tar``. The record is the commit, its ``procrastinate`` git tree hash and the
normalized archive's sha256. ``pin`` prints the records as JSON. ``verify`` rebuilds them in a temporary directory,
compares them with SCREENING.json, and checks the sha256 of any recorded archive file that exists. No third-party
code is run: this only reads git objects and writes tar files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools"))
from sealed_evaluation import normalize_archive  # noqa: E402

PACKAGE = "procrastinate"


def git(mirror: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(mirror), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def pin_one(mirror: Path, rev: str, dst: Path) -> dict[str, Any]:
    commit = git(mirror, "rev-parse", f"{rev}^{{commit}}")
    tree = git(mirror, "rev-parse", f"{commit}:{PACKAGE}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        raw = Path(d) / "raw.tar"
        subprocess.run(["git", "--git-dir", str(mirror), "archive", "--format=tar", "-o", str(raw), commit, PACKAGE],
                       check=True)
        normalize_archive(raw, dst)
    return {"commit": commit, "tree": tree, "archive": str(dst),
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()}


def pin_pair(mirror: Path, fix: str, out_root: Path) -> dict[str, Any]:
    fix_c = git(mirror, "rev-parse", f"{fix}^{{commit}}")
    parent = git(mirror, "rev-parse", f"{fix_c}^1")
    d = out_root / f"procrastinate-{fix_c[:7]}"
    return {"fix": fix_c, "R1": pin_one(mirror, parent, d / "R1.tar"), "R2": pin_one(mirror, fix_c, d / "R2.tar")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pin", "verify"])
    ap.add_argument("--mirror", type=Path, required=True)
    ap.add_argument("--out-root", type=Path)
    ap.add_argument("--screening", type=Path)
    ap.add_argument("commits", nargs="*")
    a = ap.parse_args()
    if a.cmd == "pin":
        if a.out_root is None:
            raise SystemExit("pin needs --out-root")
        print(json.dumps([pin_pair(a.mirror, c, a.out_root) for c in a.commits], indent=1))
        return 0
    if a.screening is None:
        raise SystemExit("verify needs --screening")
    rec = json.loads(a.screening.read_text())
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for c in rec["candidates"]:
            if "pins" not in c:
                continue
            got = pin_pair(a.mirror, c["commit"], Path(tmp))
            for slot in ("R1", "R2"):
                want = c["pins"][slot]
                ok = all(got[slot][k] == want[k] for k in ("commit", "tree", "sha256"))
                kept = Path(want.get("archive", ""))
                if want.get("archive") and kept.exists():
                    ok = ok and hashlib.sha256(kept.read_bytes()).hexdigest() == want["sha256"]
                bad += not ok
                print(f"{c['commit'][:7]} {slot} {got[slot]['sha256'][:16]} {'OK' if ok else 'MISMATCH'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
