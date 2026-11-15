"""The OSF package's FREEZE.json matches the files it names, and r3 is unchanged (offline; no database)."""

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OSF = REPO / "docs/experimental_mvp/osf"
R3 = "docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md"
R3_SHA256 = "af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c"
sys.path.insert(0, str(REPO / "research_tools" / "solo"))
import build_freeze  # noqa: E402


def _freeze() -> dict:
    return json.loads((OSF / "FREEZE.json").read_text())


def _entries():
    for group, value in _freeze().items():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "sha256" in value[0]:
            for e in value:
                yield group, e


def test_every_file_hash_matches():
    seen = 0
    for group, e in _entries():
        data = (REPO / e["path"]).read_bytes()
        if "section" in e:
            data = build_freeze.section_text(e["section"]).encode()
        assert hashlib.sha256(data).hexdigest() == e["sha256"], (group, e["path"], e.get("section"))
        seen += 1
    assert seen >= 25


def test_freeze_is_exactly_a_rebuild():
    assert (OSF / "FREEZE.json").read_text() == build_freeze.render(build_freeze.build())


def test_required_groups_are_present():
    f = _freeze()
    for group in ("protocol", "adoption", "owner_decisions", "selection_rule", "candidate_list", "selection_code",
                  "mutant_generator", "oracle_code", "analysis_plan", "admission_code"):
        assert f[group], group
    for k in ("selection_seed_sha256", "mutant_seed_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", f["sealed_seeds"][k])


def test_r3_is_the_candidate_and_the_copy_is_identical():
    r3 = (REPO / R3).read_bytes()
    assert hashlib.sha256(r3).hexdigest() == R3_SHA256
    assert (OSF / "AMENDMENT_003_ADOPTION_CANDIDATE_r3.md").read_bytes() == r3
    assert (OSF / "R3_SHA256").read_text().split()[0] == R3_SHA256


def test_accepted_model_and_label():
    f = _freeze()
    assert f["accepted_model"]["string"] == "muse-spark-1.3"
    assert f["label"] == "preregistered, automation-blinded, single-investigator. Not independent"
    assert f["status"].startswith("DRAFT")
