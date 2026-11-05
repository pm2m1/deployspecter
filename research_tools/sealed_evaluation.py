"""EXPERIMENTAL: the evaluator-side half of the sealed workflow, after ``corpus_intake.py`` (rehearsed on synthetic
cases only; see ``tests/experimental/test_sealed_workflow_offline.py``).

    curator submission → eligibility → seal labels and source identity → freeze the registry
    → evaluator package (permitted inputs only) → offline evaluation → blinded results (committed)
    → reveal authorization (bound to the results' hash) → scoring → integrity verification

Pieces:
* ``normalize_archive``: rebuilds a revision tar deterministically. It removes the pax global header (``git
  archive <commit>`` writes the commit id there) and every member's mtime (the commit time, which would tell the later,
  fixed revision from the earlier one), uid, gid and owner names, and sorts the members.
  ``archive_metadata_leaks`` finds what normalization removes.
* ``build_package``: the evaluator's bundle holds only the frozen registry and the case directories (``case.json``,
  ``R1.tar``, ``R2.tar``). It is scanned for label material (``seal_labels.sensitive_tokens``), 40-hex strings and
  reveal paths, and its manifest pins every file's sha256.
* ``commit_results``: blinded results (verdicts per R1 and R2, per run id) go into an append-only, hash-chained results
  ledger **before** any reveal.
* ``authorize_reveal``: the owner's authorization, bound to the results ledger's head hash and the frozen oracle hash.
  Scoring refuses without it, or if the ledger changed afterwards.
* ``score``: maps R1/R2 to buggy/fixed through the reveal, after verifying the reveal against the sealed commitment.
* ``verify_all``: every hash, chain and binding, end to end.

**What this cannot do (documented limits, never claimed away):**
* The *content* of the two revisions differs exactly where the fix is. An evaluator that diffs R1 against R2 sees
  the fix's location.
* A version string, a changelog entry or a test added with the fix can reveal which revision is newer.
* A public repository's history can be searched for that diff.

Blindness rests on the process: the runner and Muse never diff, and their inputs are leak-checked. File permissions
on one machine with one user don't separate the curator from the evaluator; separate accounts or machines do.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "research_tools"))
import corpus_intake as ci  # noqa: E402
import seal_labels as sl  # noqa: E402

HEX40 = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ------------------------------------------------------------------------------------------------ archive identity

def archive_metadata_leaks(path: Path) -> list[str]:
    """Identity in a tar's *metadata*: pax headers (git puts the commit id there), non-zero mtimes, owners, and
    member order that isn't sorted. The content isn't inspected here (see the module's limits)."""
    out = []
    with tarfile.open(path) as tf:
        if tf.pax_headers:
            out.append(f"pax global header: {sorted(tf.pax_headers)}")
        members = tf.getmembers()
        if any(m.mtime for m in members):
            out.append("member mtimes are set (they can order the two revisions)")
        if any(m.uname or m.gname or m.uid or m.gid for m in members):
            out.append("member owners are set")
        if any(m.pax_headers for m in members):
            out.append("per-member pax headers")
        if [m.name for m in members] != sorted(m.name for m in members):
            out.append("members aren't sorted")
    return out


def normalize_archive(src: Path, dst: Path) -> None:
    """A deterministic tar with the same files and modes, and no identity metadata. Links and devices are refused."""
    with tarfile.open(src) as tf:
        members = sorted(tf.getmembers(), key=lambda m: m.name)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as out:
            for m in members:
                if not (m.isfile() or m.isdir()):
                    raise SystemExit(f"refused: {m.name} isn't a regular file or directory")
                n = tarfile.TarInfo(m.name)
                n.type, n.mode, n.size = m.type, m.mode & 0o755, m.size if m.isfile() else 0
                n.mtime, n.uid, n.gid, n.uname, n.gname = 0, 0, 0, "", ""
                out.addfile(n, tf.extractfile(m) if m.isfile() else None)
    dst.write_bytes(buf.getvalue())


# ------------------------------------------------------------------------------------------------ evaluator package

def build_package(registry: Path, case_dirs: list[Path], out: Path, *, reveal_dirs: list[Path] = ()) -> dict:
    """The evaluator's bundle: the frozen registry, plus each registered case's public files. Refuses anything else,
    any label-shaped material, and any reference to a reveal location."""
    entries = [json.loads(x) for x in registry.read_text().splitlines()]
    if ci.verify_registry(registry) or not any(e["kind"] == "freeze" for e in entries):
        raise SystemExit("refused: the registry must be intact and frozen before an evaluator package is made")
    registered = {e["manifest_sha256"] for e in entries if e["kind"] == "case"}
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    (out / "cases").mkdir(parents=True)
    (out / registry.name).write_bytes(registry.read_bytes())
    for d in case_dirs:
        if ci.verify_case(d) or sha(d / "case.json") not in registered:
            raise SystemExit(f"refused: {d.name} isn't a verified, registered case")
        for f in ("R1.tar", "R2.tar"):
            leaks = archive_metadata_leaks(d / f)
            if leaks:
                raise SystemExit(f"refused: {d.name}/{f} carries identity metadata: {leaks}")
        (out / "cases" / d.name).mkdir()
        for f in ("case.json", "R1.tar", "R2.tar"):
            (out / "cases" / d.name / f).write_bytes((d / f).read_bytes())
    problems = scan_package(out, reveal_dirs=reveal_dirs)
    if problems:
        raise SystemExit(f"refused: {problems}")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {"files": {str(p.relative_to(out)): sha(p) for p in files},
                "registry_head": entries[-1]["hash"], "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out / "PACKAGE.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return manifest


def scan_package(root: Path, *, reveal_dirs: list[Path] = (), labels: list[dict] | None = None) -> list[str]:
    """Metadata and content scan of the evaluator's inputs (not only which files exist):
    * reveal paths named anywhere;
    * 40-hex strings in any JSON (commit ids);
    * label tokens, when the curator runs it with the reveal's labels (``labels``);
    * tar metadata identity (pax headers, mtimes, owners)."""
    problems = []
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        rel = str(p.relative_to(root))
        if p.suffix == ".tar":
            problems += [f"{rel}: {x}" for x in archive_metadata_leaks(p)]
            continue
        text = p.read_text(errors="replace")
        for rd in reveal_dirs:
            if str(rd) in text:
                problems.append(f"{rel}: names a reveal location")
        scrub = re.sub(r'"(commitment|hash|prev|manifest_sha256|sha256|registry_head|oracle_spec_sha256)":\s*'
                       r'"[0-9a-f]{64}"', "", text)
        if HEX40.search(scrub):
            problems.append(f"{rel}: contains a 40-hex string (a commit id?)")
        if labels:
            for tok, what in sl.sensitive_tokens(labels).items():
                if not what.endswith(":case_id") and tok and tok in text.lower():
                    problems.append(f"{rel}: label material ({what})")
    return problems


def prompt_leaks(prompt_text: str, labels: list[dict]) -> list[str]:
    """For the curator: does a model prompt built for a case contain solution metadata (the commits, prefixes, fix
    paths, keywords)? Run on every frozen prompt before any Muse request."""
    low = prompt_text.lower()
    return sorted({what for tok, what in sl.sensitive_tokens(labels).items()
                   if not what.endswith(":case_id") and tok and tok in low})


# ------------------------------------------------------------------------------------------------ results and scoring

def _chain(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _h(e: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in e.items() if k != "hash"})).hexdigest()


def commit_results(ledger: Path, *, case_id: str, run_id: str, verdicts: dict[str, str], oracle_sha256: str) -> dict:
    """Blinded results: per run, the verdict for R1 and R2 only, and the oracle code's hash. Append-only, chained."""
    if set(verdicts) != {"R1", "R2"}:
        raise SystemExit("refused: blinded results name R1 and R2, nothing else")
    entries = _chain(ledger)
    if verify_chain(ledger) or any(e["kind"] == "authorization" for e in entries):
        raise SystemExit("refused: the results ledger is broken or already authorized for reveal")
    e = {"seq": len(entries), "kind": "result", "case_id": case_id, "run_id": run_id, "verdicts": verdicts,
         "oracle_sha256": oracle_sha256, "prev": entries[-1]["hash"] if entries else "0" * 64}
    e["hash"] = _h(e)
    with ledger.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def verify_chain(path: Path) -> list[str]:
    p, prev = [], "0" * 64
    for i, e in enumerate(_chain(path)):
        if e.get("seq") != i or e.get("prev") != prev or e.get("hash") != _h(e):
            p.append(f"entry {i}: the chain is broken")
        prev = e.get("hash", "")
    return p


def authorize_reveal(ledger: Path, *, authorizer: str, oracle_sha256: str) -> dict:
    """The owner's authorization to reveal, bound to the results ledger's head and the frozen oracle's hash. After it,
    the ledger takes no more results."""
    entries = _chain(ledger)
    if verify_chain(ledger) or not entries or entries[-1]["kind"] != "result":
        raise SystemExit("refused: results must be committed (and intact) before a reveal is authorized")
    if any(e["oracle_sha256"] != oracle_sha256 for e in entries if e["kind"] == "result"):
        raise SystemExit("refused: a result was produced with a different oracle than the frozen one")
    e = {"seq": len(entries), "kind": "authorization", "authorizer": authorizer, "results_head": entries[-1]["hash"],
         "oracle_sha256": oracle_sha256, "prev": entries[-1]["hash"],
         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    e["hash"] = _h(e)
    with ledger.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def score(ledger: Path, case_dir: Path, reveal: Path, *, oracle_file: Path) -> dict:
    """Detection per run: a violation on the buggy revision with none on the fixed one. Refused without an intact,
    authorized ledger, with a reveal that doesn't match the seal, or if the oracle code changed since the results."""
    entries = _chain(ledger)
    if verify_chain(ledger):
        raise SystemExit("refused: the results ledger is broken")
    auth = [e for e in entries if e["kind"] == "authorization"]
    if not auth:
        raise SystemExit("refused: no reveal authorization")
    if sha(oracle_file) != auth[-1]["oracle_sha256"]:
        raise SystemExit("refused: the oracle code changed after the results were committed")
    roles = ci.score(case_dir, reveal)["revision_roles"]
    case_id = json.loads((case_dir / "case.json").read_text())["case_id"]
    out = []
    for e in entries:
        if e["kind"] == "result" and e["case_id"] == case_id:
            by = {roles[k]: v for k, v in e["verdicts"].items()}
            out.append({"run_id": e["run_id"], "buggy": by["buggy"], "fixed": by["fixed"],
                        "detected": by["buggy"] == "VIOLATED" and by["fixed"] == "HOLDS",
                        "corrected_version_violation": by["fixed"] == "VIOLATED"})
    return {"case_id": case_id, "runs": out}


def verify_all(*, registry: Path, package: Path, ledger: Path, oracle_file: Path) -> list[str]:
    """End-to-end integrity: the registry chain, the package manifest's file hashes and registry head, the results
    chain and its authorization, and the oracle hash."""
    p = [f"registry: {x}" for x in ci.verify_registry(registry)]
    pk = json.loads((package / "PACKAGE.json").read_text())
    for rel, h in pk["files"].items():
        if not (package / rel).exists() or sha(package / rel) != h:
            p.append(f"package: {rel} changed or missing")
    reg = [json.loads(x) for x in registry.read_text().splitlines()]
    if pk["registry_head"] != reg[-1]["hash"]:
        p.append("package: built from a different registry state")
    p += [f"results: {x}" for x in verify_chain(ledger)]
    auth = [e for e in _chain(ledger) if e["kind"] == "authorization"]
    if auth and sha(oracle_file) != auth[-1]["oracle_sha256"]:
        p.append("oracle: changed after authorization")
    return p
