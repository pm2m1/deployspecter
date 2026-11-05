"""EXPERIMENTAL sealed corpus intake for independently curated historical cases (Amendment 003, ruling #6).

The curator's side:

    python research_tools/corpus_intake.py submit --submission SUBMISSION.json --case-dir CASES/<id> \
        --reveal ~/private/<id>.reveal.json

* ``SUBMISSION.json`` (private; the curator's): case_id, application, repository, split (``development`` or
  ``confirmatory``), two revision archives with their commits, which one is buggy, fix paths, the defect summary,
  keywords, the documented upgrade contract, the oracle spec's sha256, and exposure.
* ``case-dir`` (handed to the runner) gets the two archives as ``R1.tar`` and ``R2.tar``, in a **random** order, and a
  public ``case.json`` holding only:
  - their sha256;
  - the application and repository;
  - the contract;
  - the oracle spec hash;
  - the public part of the exposure;
  - the seal (a salted commitment over the private labels).

  It holds no commit id, no buggy/fixed mapping, no fix paths and no keywords.
* ``reveal`` (private, held by the curator; refused inside this repository) gets the salt and the labels.

The runner's side:

    python research_tools/corpus_intake.py verify --case-dir CASES/<id>     # hashes, schema, no label material
    python research_tools/corpus_intake.py register --case-dir CASES/<id> --registry corpus/REGISTRY_<split>.jsonl
    python research_tools/corpus_intake.py freeze --registry corpus/REGISTRY_<split>.jsonl
    python research_tools/corpus_intake.py verify-registry --registry corpus/REGISTRY_<split>.jsonl
    python research_tools/corpus_intake.py run-id --case-dir CASES/<id> --protocol-sha256 S --code-revision C \
        --policy JSON

At authorized scoring only:

    python research_tools/corpus_intake.py score --case-dir CASES/<id> --reveal REVEAL.json

**The registry.** It is append-only and hash-chained, one per split. A registered case can't be changed, removed or
re-registered: any edit breaks the chain. After ``freeze``, nothing is added. A case, or a revision archive, that
appears in the other split's registry is refused, so the development and confirmatory sets stay disjoint. Frozen
development and held-out applications are refused for ``confirmatory``.

**Run identifiers** are immutable: the sha256 of the canonical tuple (case manifest sha256, registry chain head,
protocol sha256, code revision, policy).

The tool makes no one independent. It makes the curator's choices checkable, and the runner's blindness testable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "research_tools"))
import seal_labels as sl  # noqa: E402

SCHEMA = "ds-sealed-case/1"
SPLITS = ("development", "confirmatory")
# never confirmatory: the frozen protocol's held-out and development applications, and the prototype's targets
NOT_CONFIRMATORY = {"saleor", "healthchecks", "netbox", "authentik", "inventree", "paperless-ngx",
                    "langgraph-checkpoint-postgres", "procrastinate", "pgqueuer", "chancy"}
CONTRACT_KINDS = ("single-version", "rolling", "blue-green")
SUBMISSION_KEYS = {"case_id", "application", "repository", "split", "revisions", "buggy", "fix_paths",
                   "defect_summary", "keywords", "contract", "oracle_spec_sha256", "exposure"}
HEX40 = re.compile(r"\b[0-9a-f]{40}\b")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def validate_submission(s: Any) -> list[str]:
    if not isinstance(s, dict) or set(s) != SUBMISSION_KEYS:
        return [f"submission: exactly {sorted(SUBMISSION_KEYS)}"]
    p = []
    if s["split"] not in SPLITS:
        p.append(f"split must be one of {SPLITS}")
    if s["split"] == "confirmatory" and s["application"].lower() in NOT_CONFIRMATORY:
        p.append(f"{s['application']} is a frozen development or held-out application: never confirmatory")
    revs = s["revisions"]
    if not (isinstance(revs, list) and len(revs) == 2 and all(set(r) == {"archive", "commit"} for r in revs)):
        p.append("revisions: exactly two {archive, commit}")
    elif revs[0]["commit"] == revs[1]["commit"]:
        p.append("the two revisions are the same commit")
    if s["buggy"] not in (0, 1):
        p.append("buggy: 0 or 1 (the index of the buggy revision)")
    c = s["contract"]
    if not (isinstance(c, dict) and c.get("kind") in CONTRACT_KINDS and c.get("citation")):
        p.append(f"contract: {{kind in {CONTRACT_KINDS}, citation}} (ruling #19)")
    if not re.fullmatch(r"[0-9a-f]{64}", str(s["oracle_spec_sha256"])):
        p.append("oracle_spec_sha256: the independent oracle spec's sha256 (ruling #7)")
    e = s["exposure"]
    if not (isinstance(e, dict) and {"curator", "public"} <= set(e)):
        p.append("exposure: {curator: what you read, public: what may be shown to the runner}")
    return p


def submit(submission: Path, case_dir: Path, reveal: Path) -> dict[str, Any]:
    if reveal.resolve().is_relative_to(REPO):
        raise SystemExit("refused: the reveal file must be written outside the repository's working tree")
    if case_dir.exists():
        raise SystemExit(f"refused: {case_dir} exists")
    s = json.loads(submission.read_text())
    problems = validate_submission(s)
    if problems:
        raise SystemExit(f"refused: {problems}")
    order = [0, 1]
    random.SystemRandom().shuffle(order)  # R1/R2 carry no information about which revision is buggy
    case_dir.mkdir(parents=True)
    revisions = {}
    mapping = {}
    for slot, idx in zip(("R1", "R2"), order, strict=True):
        src = Path(s["revisions"][idx]["archive"])
        shutil.copyfile(src, case_dir / f"{slot}.tar")
        revisions[slot] = {"sha256": sha(case_dir / f"{slot}.tar")}
        mapping[slot] = {"commit": s["revisions"][idx]["commit"], "role": "buggy" if idx == s["buggy"] else "fixed"}
    labels = [{"case_id": s["case_id"], "repository": s["repository"],
               "buggy_commit": next(m["commit"] for m in mapping.values() if m["role"] == "buggy"),
               "fixed_commit": next(m["commit"] for m in mapping.values() if m["role"] == "fixed"),
               "fix_paths": s["fix_paths"], "defect_summary": s["defect_summary"], "keywords": s["keywords"],
               "exposure": s["exposure"]["curator"],
               "revision_roles": {k: v["role"] for k, v in mapping.items()}}]
    salt = secrets.token_hex(32)
    manifest = {"schema": SCHEMA, "case_id": s["case_id"], "split": s["split"], "application": s["application"],
                "repository": s["repository"], "revisions": revisions, "contract": s["contract"],
                "oracle_spec_sha256": s["oracle_spec_sha256"], "exposure_public": s["exposure"]["public"],
                "seal": {"commitment": sl.commitment(salt, labels), "labels": 1},
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    leaks = leak_scan(manifest, labels)
    if leaks:
        shutil.rmtree(case_dir)
        raise SystemExit(f"refused: the public manifest would contain label material: {leaks}")
    (case_dir / "case.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    reveal.parent.mkdir(parents=True, exist_ok=True)
    reveal.write_text(json.dumps({"schema": SCHEMA, "salt": salt, "labels": labels}, indent=1))
    reveal.chmod(0o600)
    return manifest


def leak_scan(manifest: dict[str, Any], labels: list[dict[str, Any]]) -> list[str]:
    """Label material in the public manifest: the commits (in full, or as 7–12-character prefixes), fix paths, and
    keywords. The manifest's own fields that legitimately repeat a label (case_id, repository) are excluded."""
    public = {k: v for k, v in manifest.items() if k not in ("case_id", "repository")}
    text = json.dumps(public).lower()
    tokens = {t: w for t, w in sl.sensitive_tokens(labels).items() if not w.endswith(":case_id")}
    return sorted({w for t, w in tokens.items() if t and t in text})


def verify_case(case_dir: Path) -> list[str]:
    """The runner's check: the schema, the archive hashes, and no label-shaped material (no 40-hex commit ids)."""
    p = []
    try:
        m = json.loads((case_dir / "case.json").read_text())
    except (OSError, ValueError) as exc:
        return [f"case.json: {exc}"]
    if m.get("schema") != SCHEMA:
        p.append(f"schema {m.get('schema')!r}")
    for slot in ("R1", "R2"):
        f = case_dir / f"{slot}.tar"
        if not f.exists():
            p.append(f"{slot}.tar missing")
        elif sha(f) != m["revisions"][slot]["sha256"]:
            p.append(f"{slot}.tar doesn't match its sha256")
    if HEX40.search(json.dumps({k: v for k, v in m.items() if k != "seal"})):
        p.append("the manifest contains a 40-hex string (a commit id?)")
    extra = sorted(set(f.name for f in case_dir.iterdir()) - {"case.json", "R1.tar", "R2.tar"})
    if extra:
        p.append(f"unexpected files in the case directory: {extra}")
    return p


def _entries(registry: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in registry.read_text().splitlines()] if registry.exists() else []


def _entry_hash(e: dict[str, Any]) -> str:
    return hashlib.sha256(canonical({k: v for k, v in e.items() if k != "hash"})).hexdigest()


def verify_registry(registry: Path) -> list[str]:
    p, prev = [], "0" * 64
    for i, e in enumerate(_entries(registry)):
        if e.get("seq") != i or e.get("prev") != prev or e.get("hash") != _entry_hash(e):
            p.append(f"entry {i}: the chain is broken (edited, removed or reordered)")
        prev = e.get("hash", "")
    return p


def register(case_dir: Path, registry: Path) -> dict[str, Any]:
    problems = verify_case(case_dir) + verify_registry(registry)
    if problems:
        raise SystemExit(f"refused: {problems}")
    m = json.loads((case_dir / "case.json").read_text())
    if registry.name != f"REGISTRY_{m['split']}.jsonl":
        raise SystemExit(f"refused: a {m['split']} case goes in REGISTRY_{m['split']}.jsonl")
    entries = _entries(registry)
    if any(e["kind"] == "freeze" for e in entries):
        raise SystemExit("refused: this registry is frozen; nothing is added after the freeze")
    if any(e.get("case_id") == m["case_id"] for e in entries):
        raise SystemExit(f"refused: {m['case_id']} is already registered (a registered case is never replaced)")
    other = registry.with_name(f"REGISTRY_{[s for s in SPLITS if s != m['split']][0]}.jsonl")
    archives = {r["sha256"] for r in m["revisions"].values()}
    for e in _entries(other):
        if e.get("case_id") == m["case_id"] or archives & set(e.get("archives", [])):
            raise SystemExit("refused: this case or one of its revisions is in the other split (the sets stay "
                             "disjoint)")
    prev = entries[-1]["hash"] if entries else "0" * 64
    e = {"seq": len(entries), "kind": "case", "case_id": m["case_id"], "split": m["split"],
         "manifest_sha256": sha(case_dir / "case.json"), "archives": sorted(archives), "prev": prev,
         "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    e["hash"] = _entry_hash(e)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def freeze(registry: Path) -> dict[str, Any]:
    problems = verify_registry(registry)
    entries = _entries(registry)
    if problems or not entries or any(e["kind"] == "freeze" for e in entries):
        raise SystemExit(f"refused: {problems or 'nothing to freeze, or already frozen'}")
    e = {"seq": len(entries), "kind": "freeze", "cases": sum(1 for x in entries if x["kind"] == "case"),
         "prev": entries[-1]["hash"], "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    e["hash"] = _entry_hash(e)
    with registry.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def run_id(case_dir: Path, registry: Path, *, protocol_sha256: str, code_revision: str, policy: Any) -> str:
    problems = verify_case(case_dir) + verify_registry(registry)
    entries = _entries(registry)
    manifest = sha(case_dir / "case.json")
    if not any(e.get("manifest_sha256") == manifest for e in entries):
        problems.append("the case isn't registered with this manifest")
    if not any(e["kind"] == "freeze" for e in entries):
        problems.append("the registry isn't frozen: runs need a frozen corpus")
    if problems:
        raise SystemExit(f"refused: {problems}")
    return hashlib.sha256(canonical({"manifest": manifest, "registry_head": entries[-1]["hash"],
                                     "protocol": protocol_sha256, "code": code_revision,
                                     "policy": policy})).hexdigest()


def score(case_dir: Path, reveal: Path) -> dict[str, Any]:
    m = json.loads((case_dir / "case.json").read_text())
    r = json.loads(reveal.read_text())
    if sl.commitment(r["salt"], r["labels"]) != m["seal"]["commitment"]:
        raise SystemExit("refused: the reveal doesn't match the sealed commitment")
    return {"case_id": m["case_id"], "revision_roles": r["labels"][0]["revision_roles"], "verified": True}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("submit")
    a.add_argument("--submission", type=Path, required=True)
    a.add_argument("--case-dir", type=Path, required=True)
    a.add_argument("--reveal", type=Path, required=True)
    for name in ("verify", "register", "run-id", "score"):
        x = sub.add_parser(name)
        x.add_argument("--case-dir", type=Path, required=True)
        if name in ("register", "run-id"):
            x.add_argument("--registry", type=Path, required=True)
        if name == "run-id":
            x.add_argument("--protocol-sha256", required=True)
            x.add_argument("--code-revision", required=True)
            x.add_argument("--policy", required=True)
        if name == "score":
            x.add_argument("--reveal", type=Path, required=True)
    for name in ("freeze", "verify-registry"):
        sub.add_parser(name).add_argument("--registry", type=Path, required=True)
    o = ap.parse_args()
    if o.cmd == "submit":
        print(json.dumps(submit(o.submission, o.case_dir, o.reveal), indent=1))
    elif o.cmd == "verify":
        p = verify_case(o.case_dir)
        print("\n".join(p) or "verified")
        return 1 if p else 0
    elif o.cmd == "register":
        print(json.dumps(register(o.case_dir, o.registry)))
    elif o.cmd == "freeze":
        print(json.dumps(freeze(o.registry)))
    elif o.cmd == "verify-registry":
        p = verify_registry(o.registry)
        print("\n".join(p) or "chain intact")
        return 1 if p else 0
    elif o.cmd == "run-id":
        print(run_id(o.case_dir, o.registry, protocol_sha256=o.protocol_sha256, code_revision=o.code_revision,
                     policy=json.loads(o.policy)))
    elif o.cmd == "score":
        print(json.dumps(score(o.case_dir, o.reveal)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
