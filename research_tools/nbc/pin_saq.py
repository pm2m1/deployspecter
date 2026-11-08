"""ds-nbc/1, NONBLIND: reproduce the pinned saq trees of the saq cases from a public mirror of
https://github.com/tobymao/saq, verified by commit tree and by archive sha256.

    python -I research_tools/nbc/pin_saq.py --repo SAQ.git --out ARCHIVES_DIR [--print]

Each tree is ``git archive --format=tar <commit> saq`` (the package directory only), normalized by
``research_tools/sealed_evaluation.py``'s ``normalize_archive`` (sorted members, no identity metadata), written to
``ARCHIVES_DIR/<case_id>/<slot>.tar``. Nothing is unpacked, imported or run. A mismatch exits 1 and leaves the file
as ``<slot>.tar.mismatch``. ``--print`` reports the hashes without checking them (used once, to fill PINS).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# case_id -> slot -> (commit, git tree of the commit, sha256 of the normalized archive of saq/)
PINS: dict[str, dict[str, tuple[str, str, str]]] = {
    "saq-addee31": {
        "R1": ("f827b529d48557e1a156f0207121aaf638a1035d", "3c5a2543e935089ca8e301283a262142f6b30ef8",
               "32212a41573c1521610f1efa0d5de89ff275a2811053bd4a190384d1821e06e8"),
        "R2": ("addee31a697c42d292c5fd05aa107c157b0ee8e0", "a3716f1d42d1716ab09a3ccb802a4fc14a4fb029",
               "d6ac6adacf68999abe26b32fe00f82e2e25b0114fca8a8f29563c9c943c08c41")},
    "saq-ad8d1c8": {
        "R1": ("525acc9251c95395b14090ebe481dac6a79062a5", "c7ecb1cc79b9cbcd2e8e6bcf1c2d48a153e1fdd6",
               "a1c757de7ccfe744ebc1dfb770936893179d5dfa63f0ff7092e4ed28855c93d9"),
        "R2": ("ad8d1c8d3cfdf57d6800098ff1680862a964f9fc", "1b9c5f3daa0646966cf86724fb3145efb708795a",
               "047b052dc11eff0946bf8b1463cded16b3f5fc149f4ea0436f2201f833e39acb")},
    "saq-c570ba4": {
        "R1": ("59f32fce50428e5fb0073204f31cbe7ffc84c293", "c7e084c9e3530c7adaab09e0809fb3a902bdc876",
               "5adf904ddbb9360865e59bf8d2adc06fedd50eb7c438447fdb2622927203dd65"),
        "R2": ("c570ba4ee8366404db97e21074390361328f0d8b", "2816d46a401cc85dd30d04c8ec8912353b7f14c1",
               "d5de08ee0daa62500ded34fdf8224d853774c30b4bedc3ce65e71b7e8d850a42")},
    # not a case tree: the release the onboarding pilot already exposed, used only to bring the integration up
    "bringup": {
        "v0.26.5": ("4a9ecc242f04861552955a5a40f6232889f94354", "b12d47c981f816671ed5a2f1aa7588ac79de1f04",
                    "39fd069884c1840a14a173242bda00058f27423e9c458403e78d3f470e6f5066")},
}


def normalize_archive():  # noqa: ANN201 - the shared tool's function, loaded by path
    s = importlib.util.spec_from_file_location("ds_sealed_evaluation", REPO / "research_tools/sealed_evaluation.py")
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(m)
    return m.normalize_archive


def git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "--git-dir", str(repo), *a], capture_output=True, text=True,
                          check=True).stdout.strip()


def pin(repo: Path, commit: str, dst: Path) -> tuple[str, str]:
    norm = normalize_archive()
    with tempfile.TemporaryDirectory() as d:
        raw = Path(d) / "raw.tar"
        subprocess.run(["git", "--git-dir", str(repo), "archive", "--format=tar", "-o", str(raw), commit, "saq"],
                       check=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        norm(raw, dst)
    return git(repo, "rev-parse", f"{commit}^{{tree}}"), hashlib.sha256(dst.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--print", action="store_true", dest="just_print")
    ap.add_argument("--extra", nargs=3, action="append", default=[], metavar=("CASE", "SLOT", "COMMIT"),
                    help="with --print: an extra tree to pin (e.g. a bring-up tree)")
    a = ap.parse_args()
    rc = 0
    todo = [(c, s, v[0]) for c, slots in PINS.items() for s, v in slots.items()] + [tuple(x) for x in a.extra]
    for case, slot, commit in todo:
        dst = a.out / case / f"{slot}.tar"
        tree, digest = pin(a.repo, commit, dst)
        want = PINS.get(case, {}).get(slot)
        if a.just_print or want is None:
            print(f"{case} {slot} {commit} tree {tree} sha256 {digest}")
            continue
        ok = (git(a.repo, "rev-parse", f"{commit}^{{commit}}") == want[0]) and tree == want[1] and digest == want[2]
        if not ok:
            dst.rename(dst.with_suffix(".tar.mismatch"))
            rc = 1
        print(f"{'verified' if ok else 'MISMATCH'} {case}/{slot} {commit[:7]} tree {tree} sha256 {digest}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
