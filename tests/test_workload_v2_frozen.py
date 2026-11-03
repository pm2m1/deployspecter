"""Integrity of the frozen WorkloadSpec v2 contract. Offline: reads files and the local git history only.

This file isn't itself frozen, so it can guard the frozen files without being one of them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "docs/research/live_stepping/workload/V2_FROZEN_MANIFEST.json"
MANIFEST = json.loads((ROOT / MANIFEST_PATH).read_text())
EXPECTED = {
    "src/deployspecter/live/workload_spec.py", "src/deployspecter/live/registry.py",
    "src/deployspecter/live/argument_validation.py", "src/deployspecter/live/workload_identity.py",
    "docs/research/live_stepping/workload/workload-spec-v2.schema.json",
    "tests/test_workload_spec.py", "tests/test_live_registry.py", "tests/test_argument_validation.py",
    "tests/test_workload_identity.py", "tests/live_app/workload_spec_fixtures.json",
    "docs/research/live_stepping/workload/WORKLOAD_CONTRACT.md", "docs/research/live_stepping/workload/REGISTRY.md",
    "docs/research/live_stepping/workload/ARGUMENT_VALIDATION.md",
    "docs/research/live_stepping/workload/WORKLOAD_IDENTITY.md",
}


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)


class TestFrozenContract:
    def test_inventory_is_exactly_the_frozen_contract(self):
        paths = [f["path"] for f in MANIFEST["frozen_files"]]
        assert set(paths) == EXPECTED and len(paths) == len(EXPECTED)
        assert paths == sorted(paths), "the manifest is ordered, so it's deterministic"
        assert all(f["component"] and f["frozen_version"] for f in MANIFEST["frozen_files"])

    def test_the_manifest_isnt_one_of_its_own_inputs(self):
        listed = {f["path"] for f in MANIFEST["frozen_files"]} | {e["path"] for e in MANIFEST["evidence_not_frozen"]}
        assert MANIFEST_PATH not in listed

    def test_every_frozen_file_is_unchanged_in_the_working_tree(self):
        changed = [f["path"] for f in MANIFEST["frozen_files"]
                   if hashlib.sha256((ROOT / f["path"]).read_bytes()).hexdigest() != f["sha256"]]
        assert changed == [], f"frozen files edited: {changed}"

    def test_hashes_are_those_of_the_freeze_commit(self):
        commit = MANIFEST["freeze_commit"]
        assert len(commit) == 40
        assert git("merge-base", "--is-ancestor", commit, "HEAD").returncode == 0, "freeze commit isn't in history"
        for entry in MANIFEST["frozen_files"] + MANIFEST["evidence_not_frozen"]:
            shown = git("show", f"{commit}:{entry['path']}")
            assert shown.returncode == 0, entry["path"]
            assert hashlib.sha256(shown.stdout).hexdigest() == entry["sha256"], entry["path"]

    def test_the_schema_is_frozen_and_the_candidate_is_gone(self):
        schema = json.loads((ROOT / "docs/research/live_stepping/workload/workload-spec-v2.schema.json").read_text())
        assert schema["x-status"] == "frozen"
        assert schema["$id"] == "urn:deployspecter:workload-spec:2"
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["properties"]["schema_version"]["const"] == 2
        assert not (ROOT / "docs/research/live_stepping/workload/workload-spec-v2.candidate.schema.json").exists()

    def test_unfrozen_components_are_listed_separately(self):
        frozen = {f["path"] for f in MANIFEST["frozen_files"]}
        unfrozen = {e.get("path") for e in MANIFEST["not_frozen"]} - {None}
        elsewhere = {e["path"] for e in MANIFEST["frozen_elsewhere_not_part_of_this_contract"]}
        assert not frozen & (unfrozen | elsewhere)
        assert "src/deployspecter/live/workload.py" in elsewhere
