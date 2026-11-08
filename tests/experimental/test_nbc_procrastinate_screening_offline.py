"""ds-nbc/1, procrastinate (NONBLIND development): the screening record follows the frozen rule's §4 form, and its
tree pins reproduce when the public mirror is present. Offline: no database, no container, no third-party code."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NBC = REPO / "docs" / "experimental_mvp" / "nonblind_corpus"
SCREENING = NBC / "procrastinate" / "SCREENING.json"
CRITERIA = ["E1", "E2", "E3", "E4", "E5", "E6"]
HEX40, HEX64 = re.compile(r"[0-9a-f]{40}"), re.compile(r"[0-9a-f]{64}")
MIRROR = Path("/private/tmp/claude-501/-Users-kiyo-Desktop-deployspecter/b1c95b43-b64d-4432-a288-8cf30f7b145b/"
              "scratchpad/nbc_repos/procrastinate.git")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def rec() -> dict:
    return json.loads(SCREENING.read_text())


def test_bound_to_the_frozen_rule_and_scan(rec: dict) -> None:
    assert rec["rule_sha256"] == sha(NBC / "SELECTION_RULE.md")
    assert rec["candidates_json_sha256"] == sha(NBC / "CANDIDATES.json")
    assert rec["label"] == "NONBLIND development"


def test_every_candidate_screened_most_recent_first(rec: dict) -> None:
    scan = json.loads((NBC / "CANDIDATES.json").read_text())["apps"]["procrastinate"]["candidates"]
    want = [c["commit"] for c in sorted(scan, key=lambda c: c["date"], reverse=True)]
    got = [c["commit"] for c in rec["candidates"]]
    assert got == want[:rec["cap"]]
    assert [c["rank"] for c in rec["candidates"]] == list(range(1, len(got) + 1))
    assert rec["screened"] == len(got) and rec["candidates_total"] == len(scan)
    assert rec["not_screened_capacity"] == want[rec["cap"]:]


def test_first_failing_criterion_is_the_first_fail(rec: dict) -> None:
    for c in rec["candidates"]:
        assert list(c["criteria"]) == CRITERIA
        first = next((k for k in CRITERIA if c["criteria"][k]["verdict"] == "FAIL"), None)
        assert c["first_failing"] == first, c["commit"]
        # nothing before the first failure is undecided
        for k in CRITERIA[:CRITERIA.index(first)] if first else CRITERIA:
            assert c["criteria"][k]["verdict"] == "PASS", (c["commit"], k)
        assert c["outcome"] == (f"INELIGIBLE ({first})" if first else "ELIGIBLE")
        assert c["read"], "the exposure (what was read) is recorded"
    assert rec["eligible"] == [c["commit"] for c in rec["candidates"] if c["first_failing"] is None]


def test_pins_are_the_fix_and_its_first_parent(rec: dict) -> None:
    for c in rec["candidates"]:
        assert c["pins"]["R2"]["commit"] == c["commit"]
        assert c["pins"]["R1"]["commit"] == c["parent"] != c["commit"]
        for slot in ("R1", "R2"):
            p = c["pins"][slot]
            assert HEX40.fullmatch(p["commit"]) and HEX40.fullmatch(p["tree"]) and HEX64.fullmatch(p["sha256"])
            assert p["archive"].endswith(f"/procrastinate-{c['commit'][:7]}/{slot}.tar")


def test_no_forbidden_labels() -> None:
    for f in (SCREENING, SCREENING.with_suffix(".md")):
        text = f.read_text()
        assert not re.search(r"\bBLIND\b|confirmatory|independent", text, re.IGNORECASE), f.name


@pytest.mark.skipif(not MIRROR.exists(), reason="the public mirror isn't present on this machine")
def test_pins_reproduce_from_the_mirror(rec: dict, tmp_path: Path) -> None:
    s = importlib.util.spec_from_file_location("pin_procrastinate", REPO / "research_tools/nbc/pin_procrastinate.py")
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(m)
    c = rec["candidates"][0]
    got = m.pin_pair(MIRROR, c["commit"], tmp_path)
    for slot in ("R1", "R2"):
        assert {k: got[slot][k] for k in ("commit", "tree", "sha256")} == \
            {k: c["pins"][slot][k] for k in ("commit", "tree", "sha256")}
