"""EXPERIMENTAL engineering release-candidate manifest: write it, or verify a checkout against it.

    python research_tools/rc_manifest.py write  --out docs/experimental_mvp/RELEASE_CANDIDATE.json --label rc1
    python research_tools/rc_manifest.py verify --manifest docs/experimental_mvp/RELEASE_CANDIDATE.json

The manifest pins:
* the code revision;
* sha256 of every tracked file under the experimental source, test and tool trees;
* the lock file and project file;
* the pinned container images and third-party sdists.

``verify`` recomputes those hashes and reports every difference. It uses no network and no database. An engineering
release candidate is not research evidence, and nothing here is Gate A2 evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
TREES = ("src/deployspecter/experimental", "tests/experimental", "tests/thirdparty_isolated", "research_tools")
SINGLE = ("pyproject.toml", "uv.lock")
SCHEMA = "ds-mvp-release-candidate/1"


def tracked(paths: tuple[str, ...]) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", *paths], cwd=REPO, capture_output=True, text=True, check=True)
    return sorted(p for p in out.stdout.splitlines() if "__pycache__" not in p)


def sha(rel: str) -> str:
    return hashlib.sha256((REPO / rel).read_bytes()).hexdigest()


def pins() -> dict[str, Any]:
    pg = re.search(r"IMAGE=(\S+)", (REPO / "research_tools/mvp_pg.sh").read_text())
    base = re.search(r"^FROM (\S+)", (REPO / "research_tools/isolated/Dockerfile.runner").read_text(), re.M)
    sdists = dict(re.findall(r"fetch (\S+\.tar\.gz) \S+ \\\n\s+([0-9a-f]{64})",
                             (REPO / "research_tools/isolated/fetch_pinned.sh").read_text()))
    return {"postgres_image": pg.group(1) if pg else None, "runner_base_image": base.group(1) if base else None,
            "sdists_sha256": sdists}


def build(label: str) -> dict[str, Any]:
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                         check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *TREES, *SINGLE], cwd=REPO, capture_output=True,
                           text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit(f"refused: uncommitted changes in the pinned trees:\n{dirty}")
    files = tracked(TREES)
    return {"schema": SCHEMA, "label": label, "code_revision": rev,
            "status": "ENGINEERING RELEASE CANDIDATE (not research evidence, not Gate A2 evidence)",
            "project": {rel: sha(rel) for rel in SINGLE}, "pins": pins(),
            "files": {rel: sha(rel) for rel in files}, "file_count": len(files)}


def verify(manifest: dict[str, Any]) -> list[str]:
    problems = []
    if manifest.get("schema") != SCHEMA:
        return [f"unknown schema {manifest.get('schema')!r}"]
    for rel, want in {**manifest["project"], **manifest["files"]}.items():
        path = REPO / rel
        if not path.exists():
            problems.append(f"missing: {rel}")
        elif sha(rel) != want:
            problems.append(f"changed: {rel}")
    extra = sorted(set(tracked(TREES)) - set(manifest["files"]))
    problems += [f"not in the manifest: {rel}" for rel in extra]
    if pins() != manifest["pins"]:
        problems.append("the pinned images or sdists differ")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--out", type=Path, required=True)
    w.add_argument("--label", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--manifest", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "write":
        if a.out.exists():
            raise SystemExit(f"refused: {a.out} exists (evidence is never overwritten)")
        m = build(a.label)
        a.out.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
        print(f"{a.label}: {m['code_revision'][:12]}, {m['file_count']} files")
        return 0
    problems = verify(json.loads(a.manifest.read_text()))
    print("\n".join(problems) if problems else "verified: every pinned file, image and sdist matches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
