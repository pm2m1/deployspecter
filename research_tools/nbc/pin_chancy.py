"""ds-nbc/1, chancy (NONBLIND development): reproduce the pinned R1/R2 trees of every chancy case.

    .venv/bin/python research_tools/nbc/pin_chancy.py --repo MIRROR.git --out ARCHIVES/chancy [--check PINS.json]

For each case: R1 is the fix commit's first parent, R2 the fix commit. Each tree is ``git archive --format=tar
<commit> chancy`` (the package directory only, from the public mirror), then ``sealed_evaluation.normalize_archive``
(sorted members, no pax header, no mtimes or owners). Writes ``<out>/<case_id>/R1.tar`` and ``R2.tar`` and prints the
pins (commit, root tree, package tree, raw and normalized sha256). With ``--check``, it refuses unless every pin equals
the committed ``PINS.json``. Writes nothing into the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools"))
from sealed_evaluation import normalize_archive  # noqa: E402

# case_id -> (fix commit, its first parent): the parent is checked against the mirror, never assumed
CASES = {
    "chancy-2895c4a": ("2895c4a7fc173cec3d645221f63c4e51e2cf7e84", "80f570f0b8332212e46c3c27af5940946f6486a9"),
    "chancy-b8a77df": ("b8a77df3a514ce19ebcaf31453cff8da8af9f212", "6716e0db1924d3393e51cd18076415e340b7c508"),
}


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def pin(repo: Path, commit: str, dst: Path) -> dict[str, str]:
    raw = git(repo, "archive", "--format=tar", commit, "chancy")
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "raw.tar"
        src.write_bytes(raw)
        dst.parent.mkdir(parents=True, exist_ok=True)
        normalize_archive(src, dst)
    return {"commit": commit, "root_tree": git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip(),
            "package_tree": git(repo, "rev-parse", f"{commit}:chancy").decode().strip(),
            "git_archive_sha256": hashlib.sha256(raw).hexdigest(),
            "archive": str(dst), "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--case", action="append", choices=sorted(CASES))
    ap.add_argument("--check", type=Path)
    a = ap.parse_args()
    pins = {}
    for cid in a.case or sorted(CASES):
        fix, parent = CASES[cid]
        got = git(a.repo, "rev-list", "--parents", "-n", "1", fix).decode().split()
        if got[1] != parent:
            raise SystemExit(f"refused: {cid}: {fix[:7]}'s first parent is {got[1][:7]}, not {parent[:7]}")
        pins[cid] = {"R1": pin(a.repo, parent, a.out / cid / "R1.tar"), "R2": pin(a.repo, fix, a.out / cid / "R2.tar")}
    print(json.dumps(pins, indent=1, sort_keys=True))
    if a.check:
        want = json.loads(a.check.read_text())
        for cid, p in pins.items():
            for slot in ("R1", "R2"):
                w = {k: v for k, v in want[cid][slot].items() if k != "archive"}
                g = {k: v for k, v in p[slot].items() if k != "archive"}
                if w != g:
                    raise SystemExit(f"refused: {cid}/{slot} differs from {a.check}: {w} != {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
