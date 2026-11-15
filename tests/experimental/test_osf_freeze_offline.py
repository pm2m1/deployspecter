"""The OSF package's FREEZE.json matches the files it names, and r3 is unchanged (offline; no database)."""

import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OSF = REPO / "docs/experimental_mvp/osf"
R3 = "docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md"
R3_SHA256 = "af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c"


def _freeze() -> dict:
    return json.loads((OSF / "FREEZE.json").read_text())


def _section(text: str, title: str) -> str:
    n = title.split(".", 1)[0]
    m = re.search(rf"^## {n}\. .*?(?=^## \d+\. )", text, re.S | re.M)
    assert m and m.group(0).startswith(f"## {title}")
    return m.group(0).rstrip("\n")


def test_every_file_hash_matches():
    for group in ("protocol", "selection_rule", "mutant_rules", "analysis_plan", "admission_code"):
        for e in _freeze()[group]:
            data = (REPO / e["path"]).read_bytes()
            if "section" in e:
                data = _section(data.decode(), e["section"]).encode()
            assert hashlib.sha256(data).hexdigest() == e["sha256"], (group, e["path"], e.get("section"))


def test_r3_is_the_candidate_and_the_copy_is_identical():
    r3 = (REPO / R3).read_bytes()
    assert hashlib.sha256(r3).hexdigest() == R3_SHA256
    assert (OSF / "AMENDMENT_003_ADOPTION_CANDIDATE_r3.md").read_bytes() == r3
    assert (OSF / "R3_SHA256").read_text().split()[0] == R3_SHA256


def test_accepted_model_and_label():
    f = _freeze()
    assert f["accepted_model"]["string"] == "muse-spark-1.3"
    assert "partially blind" in f["label"] and "NOT" not in f["label"]
    assert f["status"].startswith("DRAFT")
