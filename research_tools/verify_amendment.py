"""EXPERIMENTAL: verify the Amendment 003 adoption candidate's integrity, and the state of its approvals.

    python research_tools/verify_amendment.py [--protocol-dir docs/experimental_mvp/protocol]

Checks:
* the candidate's exact sha256 against ``approval/CANDIDATE_SHA256``;
* every approval form names that same hash, and no other 64-hex hash;
* each form's signature table: an empty name or signature means the decision is unresolved;
* the frozen protocol directory (``docs/research/live_stepping``): if an ``EVALUATION_PROTOCOL_AMENDMENT_003.md``
  exists, it must be byte-identical to the candidate, and every form must be signed. Otherwise it is an
  unauthorized or silently edited adoption.

Exit 0 only when the hashes are consistent. Unsigned forms are reported, not treated as an integrity failure: they
mean "not adopted", which is the current, correct state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORMS = ("OWNER_APPROVAL.md", "CURATOR_ACCEPTANCE.md", "ORACLE_REVIEWER_ACCEPTANCE.md",
         "TRIAGE_REVIEWERS_ACCEPTANCE.md")
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def verify(protocol_dir: Path, frozen_dir: Path) -> dict:
    cand = protocol_dir / "AMENDMENT_003_ADOPTION_CANDIDATE.md"
    actual = hashlib.sha256(cand.read_bytes()).hexdigest()
    recorded = (protocol_dir / "approval" / "CANDIDATE_SHA256").read_text().split()[0]
    integrity = []
    if actual != recorded:
        integrity.append(f"the candidate's sha256 {actual[:12]}… isn't the recorded {recorded[:12]}…")
    forms = {}
    for name in FORMS:
        text = (protocol_dir / "approval" / name).read_text()
        hashes = set(HEX64.findall(text))
        if hashes != {recorded}:
            integrity.append(f"{name} names {sorted(h[:12] for h in hashes)}, not exactly the recorded hash")
        decisions = re.findall(r"^\| #(\w+) \| (.*?) \|$", text, re.M)
        ticked = {d: v for d, v in decisions if "[x]" in v.lower()}
        sig_rows = [r for r in re.findall(r"^\|(.*)\|$", text, re.M)
                    if r.count("|") == 3 and "Name" not in r and "---" not in r and not r.strip().startswith("#")]
        signed = bool(sig_rows) and all(all(c.strip() for c in r.split("|")[1:3]) for r in sig_rows)
        forms[name] = {"decisions": [d for d, _ in decisions], "ticked": sorted(ticked), "signed": signed}
    adopted = frozen_dir / "EVALUATION_PROTOCOL_AMENDMENT_003.md"
    adoption = "not adopted (no file beside the frozen protocol)"
    if adopted.exists():
        same = hashlib.sha256(adopted.read_bytes()).hexdigest() == recorded
        all_signed = all(f["signed"] for f in forms.values())
        if not same:
            integrity.append("an adopted amendment exists that is NOT byte-identical to the approved candidate")
        if not all_signed:
            integrity.append("an adopted amendment exists while approval forms are unsigned")
        adoption = "adopted, consistent" if same and all_signed else "adopted INCONSISTENTLY"
    unresolved = sorted({f"#{d}" for f in forms.values() if not f["signed"] for d in f["decisions"]},
                        key=lambda x: (not x[1:].isdigit(), int(x[1:]) if x[1:].isdigit() else 0, x))
    return {"candidate_sha256": actual, "recorded_sha256": recorded, "integrity_problems": integrity,
            "forms": forms, "adoption": adoption, "unresolved_decisions": unresolved}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol-dir", type=Path, default=REPO / "docs/experimental_mvp/protocol")
    ap.add_argument("--frozen-dir", type=Path, default=REPO / "docs/research/live_stepping")
    a = ap.parse_args()
    r = verify(a.protocol_dir, a.frozen_dir)
    print(json.dumps(r, indent=1))
    return 1 if r["integrity_problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
