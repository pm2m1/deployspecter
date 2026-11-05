"""EXPERIMENTAL, offline: the Amendment 003 integrity verifier, on temporary copies (the real files are untouched)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools"))
import verify_amendment as va  # noqa: E402

PROTOCOL = ROOT / "docs/experimental_mvp/protocol"


@pytest.fixture
def copy(tmp_path):
    d = tmp_path / "protocol"
    (d / "approval").mkdir(parents=True)
    shutil.copyfile(PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE.md", d / "AMENDMENT_003_ADOPTION_CANDIDATE.md")
    for f in (PROTOCOL / "approval").iterdir():
        shutil.copyfile(f, d / "approval" / f.name)
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    return d, frozen


def test_the_real_candidate_is_consistent_unadopted_and_unsigned(copy):
    r = va.verify(*copy)
    assert r["integrity_problems"] == [] and r["candidate_sha256"].startswith("ad5ea331")
    assert r["adoption"].startswith("not adopted") and not any(f["signed"] for f in r["forms"].values())
    assert len(r["unresolved_decisions"]) == 24


def test_a_one_byte_edit_of_the_candidate_is_detected(copy):
    d, frozen = copy
    c = d / "AMENDMENT_003_ADOPTION_CANDIDATE.md"
    c.write_bytes(c.read_bytes().replace(b"Not adopted", b"Not adoptet", 1))
    assert any("isn't the recorded" in x for x in va.verify(d, frozen)["integrity_problems"])


def test_a_form_naming_another_hash_is_detected(copy):
    d, frozen = copy
    f = d / "approval" / "CURATOR_ACCEPTANCE.md"
    f.write_text(f.read_text().replace(f.read_text().split("**sha256:** `")[1][:64], "0" * 64))
    assert any("CURATOR_ACCEPTANCE.md" in x for x in va.verify(d, frozen)["integrity_problems"])


def test_an_adoption_that_differs_from_the_candidate_or_precedes_signatures_is_detected(copy):
    d, frozen = copy
    text = (d / "AMENDMENT_003_ADOPTION_CANDIDATE.md").read_bytes()
    (frozen / "EVALUATION_PROTOCOL_AMENDMENT_003.md").write_bytes(text)  # identical, but nobody has signed
    r = va.verify(d, frozen)
    assert any("unsigned" in x for x in r["integrity_problems"]) and "INCONSISTENTLY" in r["adoption"]
    (frozen / "EVALUATION_PROTOCOL_AMENDMENT_003.md").write_bytes(text + b"\n- an edit made after approval\n")
    r = va.verify(d, frozen)
    assert any("NOT byte-identical" in x for x in r["integrity_problems"])
