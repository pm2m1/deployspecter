"""Build reproducible evidence bundles (``.tar.zst``) from a git tag, for GitHub Release assets or a Zenodo deposit.

    python tools/package_evidence.py build  --tag research-2026-11-15 --out DIR [--min-mb 5] [--level 3]
    python tools/package_evidence.py verify --tag research-2026-11-15 --dir DIR

**Source:** blobs are read from the tag's tree through git, never from the working tree, so local edits can't leak
into a bundle. Nothing in git is moved or deleted.

**Bundles:** one per directory under ``docs/experimental_mvp/`` whose size at the tag is at least ``--min-mb``. Each
nested bundle's files are excluded from its parent's, so every file belongs to exactly one bundle. Smaller
directories go into one ``rest`` bundle.

**Reproducible:**
- members are sorted, with mtime 0, uid and gid 0, empty owner names and mode 0644;
- ustar format;
- ``zstd`` runs single-threaded at a fixed level.

The same tag, the same zstd version and the same options give byte-identical bundles. ``SHA256SUMS`` records the
bundle hashes, the zstd version and the options.

**Inside each bundle:** ``FILES.tsv`` lists every member's path, size, sha256 and **git blob id**. ``verify``
recomputes each blob id from the bundle and compares it with the blob id that git has committed at the tag. That is
the hash link to the repository. It also checks ``SHA256SUMS``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = "docs/experimental_mvp/"


def git(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), *args], input=data, capture_output=True, check=True).stdout


def tree(tag: str) -> list[tuple[str, str, int]]:
    """(path, blob id, size) of every regular file under ``ROOT`` at ``tag``."""
    out = []
    for line in git("ls-tree", "-r", "-l", "-z", tag, "--", ROOT).split(b"\0"):
        if not line:
            continue
        meta, path = line.decode().split("\t", 1)
        mode, kind, oid, size = meta.split()
        if kind == "blob" and mode in ("100644", "100755"):
            out.append((path, oid, int(size)))
    return sorted(out)


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def plan(files: list[tuple[str, str, int]], min_bytes: int) -> dict[str, list[tuple[str, str, int]]]:
    """bundle name -> files. A directory at depth 1-2 below ROOT becomes a bundle when it is at least min_bytes."""
    size: dict[str, int] = {}
    for path, _, n in files:
        parts = path[len(ROOT):].split("/")[:-1]
        for depth in (1, 2):
            if len(parts) >= depth:
                d = "/".join(parts[:depth])
                size[d] = size.get(d, 0) + n
    bundles = sorted((d for d, n in size.items() if n >= min_bytes), key=lambda d: (-d.count("/"), d))
    groups: dict[str, list[tuple[str, str, int]]] = {}
    for f in files:
        rel = f[0][len(ROOT):]
        owner = next((d for d in bundles if rel.startswith(d + "/")), "rest")  # deepest first
        groups.setdefault(owner, []).append(f)
    return groups


def bundle_name(owner: str) -> str:
    return "evidence-" + owner.replace("/", "__") + ".tar.zst"


def build_tar(tag: str, files: list[tuple[str, str, int]]) -> bytes:
    buf = io.BytesIO()
    rows = ["path\tsize\tsha256\tgit_blob"]
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path, oid, _ in files:
            data = git("cat-file", "blob", oid)
            if blob_id(data) != oid:
                raise SystemExit(f"refused: {path}: blob id mismatch")
            rows.append(f"{path}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}\t{oid}")
            add(tar, path, data)
        add(tar, "FILES.tsv", ("\n".join(rows) + "\n").encode())
    return buf.getvalue()


def add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname = len(data), 0, 0o644, 0, 0, "", ""
    tar.addfile(info, io.BytesIO(data))


def zstd(data: bytes, level: int) -> bytes:
    return subprocess.run(["zstd", "-q", f"-{level}", "-T1", "-c"], input=data, capture_output=True,
                          check=True).stdout


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_build(a: argparse.Namespace) -> int:
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists")
    commit = git("rev-parse", f"{a.tag}^{{commit}}").decode().strip()
    files = tree(a.tag)
    groups = plan(files, int(a.min_mb * 1e6))
    a.out.mkdir(parents=True)
    zver = subprocess.run(["zstd", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    sums, index = [], []
    for owner in sorted(groups):
        name = bundle_name(owner)
        (a.out / name).write_bytes(zstd(build_tar(a.tag, groups[owner]), a.level))
        sums.append(f"{sha256(a.out / name)}  {name}")
        index.append({"bundle": name, "directory": ROOT + owner if owner != "rest" else ROOT + "(rest)",
                      "files": len(groups[owner]), "bytes_uncompressed": sum(n for *_, n in groups[owner]),
                      "bytes_compressed": (a.out / name).stat().st_size})
        print(json.dumps(index[-1]), flush=True)
    header = [f"# tag {a.tag} commit {commit}", f"# {zver}; options -{a.level} -T1; min-mb {a.min_mb}",
              f"# {len(files)} files under {ROOT}, each in exactly one bundle"]
    (a.out / "SHA256SUMS").write_text("\n".join(header + sums) + "\n")
    (a.out / "INDEX.json").write_text(json.dumps({"tag": a.tag, "commit": commit, "bundles": index}, indent=1))
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    expected = {p: oid for p, oid, _ in tree(a.tag)}
    problems, seen = [], set()
    for line in (a.dir / "SHA256SUMS").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        digest, name = line.split("  ", 1)
        if sha256(a.dir / name) != digest:
            problems.append(f"{name}: sha256 differs from SHA256SUMS")
            continue
        raw = subprocess.run(["zstd", "-q", "-d", "-c", str(a.dir / name)], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            for m in tar.getmembers():
                if m.name == "FILES.tsv":
                    continue
                f = tar.extractfile(m)
                data = f.read() if f else b""
                if expected.get(m.name) != blob_id(data):
                    problems.append(f"{name}: {m.name}: not the blob committed at {a.tag}")
                seen.add(m.name)
    missing = sorted(set(expected) - seen)
    problems += [f"missing from every bundle: {p}" for p in missing[:20]]
    print(json.dumps({"tag": a.tag, "files_checked": len(seen), "files_at_tag": len(expected),
                      "problems": problems[:50], "ok": not problems}, indent=1))
    return 0 if not problems else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--tag", required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--min-mb", type=float, default=5.0)
    b.add_argument("--level", type=int, default=3)
    v = sub.add_parser("verify")
    v.add_argument("--tag", required=True)
    v.add_argument("--dir", type=Path, required=True)
    a = ap.parse_args()
    return cmd_build(a) if a.cmd == "build" else cmd_verify(a)


if __name__ == "__main__":
    sys.exit(main())
