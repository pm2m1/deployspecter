"""EXPERIMENTAL, offline: the oracle acceptance template has separate, unsigned author and reviewer rows, references
the unchanged candidate hash, and no longer produces the one-row form gap."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "confirmatory"))
sys.path.insert(0, str(ROOT / "research_tools"))
import approvals  # noqa: E402
import verify_amendment as va  # noqa: E402

CANDIDATE = "ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0"


def test_two_labelled_unsigned_rows_and_the_unchanged_hash():
    fs = approvals.forms()
    f = fs["ORACLE_REVIEWER_ACCEPTANCE.md"]
    assert [r["role"] for r in f.signatures] == ["Oracle author", "Oracle reviewer"]
    assert all(not (r["name"] or r["signature"] or r["date"]) for r in f.signatures)
    assert approvals._row_roles(f.name, "Oracle author") == ["oracle_author"]
    assert approvals._row_roles(f.name, "Oracle reviewer") == ["oracle_reviewer"]
    r = va.verify(ROOT / "docs/experimental_mvp/protocol", ROOT / "docs/research/live_stepping")
    assert r["candidate_sha256"] == CANDIDATE and r["integrity_problems"] == [] and \
        r["adoption"].startswith("not adopted")
    assert not any(f["signed"] for f in r["forms"].values())


def test_no_one_row_gap_and_everything_still_proposed():
    fs = approvals.forms()
    for d in approvals.decisions():
        s = approvals.status(d, fs)
        assert s["status"] == "PROPOSED"
        assert not any("one unlabeled signature row" in g for g in s.get("gaps", []))
