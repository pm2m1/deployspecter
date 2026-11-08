"""NONBLIND ds-nbc/1: reproduce the pinned pgqueuer trees of this application's cases (data only; nothing is run).

    .venv/bin/python research_tools/nbc/integrations/pgqueuer_pin.py --repo MIRROR.git --out DIR [--case ID]

For each case: ``git archive --format=tar <commit> pgqueuer`` (the package directory), normalized with
``research_tools/sealed_evaluation.normalize_archive`` (sorted members, no identity metadata), written to
``DIR/<case_id>/R1.tar`` (the fix commit's first parent, buggy) and ``R2.tar`` (the fix commit). The commit tree, the
package tree and the archive sha256 are verified against the pins below; any mismatch fails and removes the file.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "research_tools"))
from sealed_evaluation import normalize_archive  # noqa: E402

# case_id -> slot -> (commit, commit tree, package tree, normalized archive sha256)
PINS = {
    "pgqueuer-8dfadf4": {
        "R1": ("f258cbcafab840612b8c42b1db60ab553b67c093", "f9fbfbac88c491e0a918b5be3227c07ecb9d7614",
               "3c6247642ceb3ceea639cf60d78ce121b5d1b6e6",
               "378ee7593ca1396be0a0d0323562f0c0cd98b1e76e2db75849f8eac2eb6310e2"),
        "R2": ("8dfadf4ea3a6fb6d60ac970d799faf8338f9d03f", "d9d7b31b593ec9d55e2e2d05afe8451e61d0808b",
               "9b7989b302a3e481f8ec0978c470347e6e9a004c",
               "d985c4686106f040a7ed274613bf16a7d1379e3631c4059fc58cb45706fe260b"),
    },
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def pin(repo: Path, out: Path, case_id: str) -> dict[str, dict[str, str]]:
    res = {}
    for slot, (commit, tree, pkg, want) in PINS[case_id].items():
        if git(repo, "rev-parse", f"{commit}^{{tree}}") != tree or git(repo, "rev-parse", f"{commit}:pgqueuer") != pkg:
            raise SystemExit(f"{case_id}/{slot}: tree mismatch for {commit}")
        d = out / case_id
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{slot}.tar"
        with tempfile.TemporaryDirectory() as t:
            raw = Path(t) / "raw.tar"
            subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(raw), commit, "pgqueuer"],
                           check=True)
            normalize_archive(raw, dst)
        got = hashlib.sha256(dst.read_bytes()).hexdigest()
        if want is not None and got != want:
            dst.unlink()
            raise SystemExit(f"{case_id}/{slot}: archive sha256 {got} isn't the pinned {want}")
        res[slot] = {"commit": commit, "tree": tree, "package_tree": pkg, "archive": str(dst), "sha256": got}
        print(f"{case_id} {slot} {commit[:12]} tree {tree[:12]} pkg {pkg[:12]} sha256 {got}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--case", choices=sorted(PINS))
    a = ap.parse_args()
    for c in [a.case] if a.case else sorted(PINS):
        pin(a.repo, a.out, c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
