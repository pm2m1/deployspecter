"""EXPERIMENTAL label sealing and leakage checks for an independent evaluation (curator-side tool).

    python research_tools/seal_labels.py seal --labels LABELS.json --seal SEAL.json --reveal /private/path/REVEAL.json
    python research_tools/seal_labels.py verify --seal SEAL.json --reveal REVEAL.json
    python research_tools/seal_labels.py leak-check --reveal REVEAL.json --bundle RUNNER_INPUT_DIR

* ``seal``: a salted SHA-256 commitment over the canonical labels. ``SEAL.json`` (commitment, label count, schema,
  time) is safe to commit and to give the evaluation runner. ``REVEAL.json`` (salt and labels) stays with the
  curator: the tool refuses to write it inside this repository's working tree, so it can't be committed by accident.
* ``verify``: at authorized scoring, recompute the commitment from a reveal file.
* ``leak-check``: before any arm runs, the curator scans the runner's frozen inputs (prompt contexts, registries,
  workload sources) for label material: buggy/fixed commits (full and 7-12-character prefixes), fix paths, case
  identifiers and the curator's declared keywords. Any hit blocks the run.

The tool doesn't make anyone independent. It only makes a curator's commitment checkable and a leak detectable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

SCHEMA = "ds-sealed-labels/1"
REQUIRED = {"case_id", "repository", "buggy_commit", "fixed_commit", "fix_paths", "defect_summary", "keywords",
            "exposure"}
REPO = Path(__file__).resolve().parents[1]


def canonical(labels: list[dict[str, Any]]) -> bytes:
    return json.dumps(sorted(labels, key=lambda x: x["case_id"]), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def validate(labels: Any) -> list[str]:
    if not isinstance(labels, list) or not labels:
        return ["labels: a non-empty list"]
    out = []
    for i, lab in enumerate(labels):
        if not isinstance(lab, dict) or set(lab) != REQUIRED:
            out.append(f"label {i}: exactly {sorted(REQUIRED)}")
    ids = [lab.get("case_id") for lab in labels if isinstance(lab, dict)]
    if len(set(ids)) != len(ids):
        out.append("case_id values must be unique")
    return out


def commitment(salt: str, labels: list[dict[str, Any]]) -> str:
    return hashlib.sha256(bytes.fromhex(salt) + canonical(labels)).hexdigest()


def seal(labels_path: Path, seal_path: Path, reveal_path: Path) -> dict[str, Any]:
    if reveal_path.resolve().is_relative_to(REPO):
        raise SystemExit("refused: the reveal file must be written outside the repository's working tree")
    labels = json.loads(labels_path.read_text())
    problems = validate(labels)
    if problems:
        raise SystemExit(f"refused: {problems}")
    salt = secrets.token_hex(32)
    public = {"schema": SCHEMA, "commitment": commitment(salt, labels), "labels": len(labels),
              "sealed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    reveal_path.write_text(json.dumps({"schema": SCHEMA, "salt": salt, "labels": labels}, indent=1))
    reveal_path.chmod(0o600)
    seal_path.write_text(json.dumps(public, indent=1) + "\n")
    return public


def verify(seal_path: Path, reveal_path: Path) -> bool:
    public, reveal = json.loads(seal_path.read_text()), json.loads(reveal_path.read_text())
    return commitment(reveal["salt"], reveal["labels"]) == public["commitment"] and \
        len(reveal["labels"]) == public["labels"]


def sensitive_tokens(labels: list[dict[str, Any]]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for lab in labels:
        for field in ("buggy_commit", "fixed_commit"):
            sha = str(lab[field]).lower()
            tokens[sha] = f"{lab['case_id']}:{field}"
            for n in (7, 8, 10, 12):
                tokens[sha[:n]] = f"{lab['case_id']}:{field}[:{n}]"
        for path in lab["fix_paths"]:
            tokens[str(path).lower()] = f"{lab['case_id']}:fix_path"
        tokens[str(lab["case_id"]).lower()] = f"{lab['case_id']}:case_id"
        for word in lab["keywords"]:
            if len(str(word)) >= 4:
                tokens[str(word).lower()] = f"{lab['case_id']}:keyword"
    return tokens


def leak_check(reveal_path: Path, bundle: Path) -> list[dict[str, str]]:
    labels = json.loads(reveal_path.read_text())["labels"]
    tokens = sensitive_tokens(labels)
    hits = []
    for f in sorted(p for p in bundle.rglob("*") if p.is_file()):
        text = f.read_text(errors="replace").lower()
        for token, what in tokens.items():
            if token in text:
                hits.append({"file": str(f.relative_to(bundle)), "label_field": what})
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seal")
    s.add_argument("--labels", type=Path, required=True)
    s.add_argument("--seal", type=Path, required=True)
    s.add_argument("--reveal", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--seal", type=Path, required=True)
    v.add_argument("--reveal", type=Path, required=True)
    lc = sub.add_parser("leak-check")
    lc.add_argument("--reveal", type=Path, required=True)
    lc.add_argument("--bundle", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "seal":
        print(json.dumps(seal(a.labels, a.seal, a.reveal)))
        return 0
    if a.cmd == "verify":
        ok = verify(a.seal, a.reveal)
        print("verified" if ok else "MISMATCH")
        return 0 if ok else 1
    hits = leak_check(a.reveal, a.bundle)
    print(json.dumps({"hits": hits}, indent=1))
    return 1 if hits else 0  # leak-free hands the runner its inputs; any hit blocks the run (values never printed)


if __name__ == "__main__":
    sys.exit(main())
