"""EXPERIMENTAL label sealing and leak checks, offline, with synthetic labels only."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("seal_labels", Path(__file__).resolve().parents[2] / "research_tools"
                                              / "seal_labels.py")
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

LABEL = {"case_id": "synthetic-case-1", "repository": "https://example.invalid/repo",
         "buggy_commit": "a" * 40, "fixed_commit": "b" * 40, "fix_paths": ["pkg/worker.py"],
         "defect_summary": "synthetic", "keywords": ["doubleclaim"], "exposure": "none"}


def test_seal_verify_and_tamper(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([LABEL]))
    seal, reveal = tmp_path / "seal.json", tmp_path / "reveal.json"
    public = sl.seal(labels, seal, reveal)
    assert public["labels"] == 1 and "a" * 40 not in seal.read_text()  # the public seal carries no label material
    assert sl.verify(seal, reveal)
    data = json.loads(reveal.read_text())
    data["labels"][0]["fixed_commit"] = "c" * 40
    reveal.write_text(json.dumps(data))
    assert not sl.verify(seal, reveal)


def test_the_reveal_file_is_refused_inside_the_repository(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([LABEL]))
    with pytest.raises(SystemExit, match="outside the repository"):
        sl.seal(labels, tmp_path / "seal.json", sl.REPO / "reveal_should_not_exist.json")
    assert not (sl.REPO / "reveal_should_not_exist.json").exists()


def test_malformed_labels_are_refused(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([{"case_id": "x"}]))
    with pytest.raises(SystemExit, match="refused"):
        sl.seal(labels, tmp_path / "seal.json", tmp_path / "reveal.json")


def test_leak_check_finds_label_material_in_runner_inputs(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([LABEL]))
    sl.seal(labels, tmp_path / "seal.json", tmp_path / "reveal.json")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "clean.json").write_text('{"handlers": ["withdraw"]}')
    assert sl.leak_check(tmp_path / "reveal.json", bundle) == []
    (bundle / "prompt.txt").write_text("see commit bbbbbbbb and pkg/worker.py; a DoubleClaim bug")
    fields = {h["label_field"] for h in sl.leak_check(tmp_path / "reveal.json", bundle)}
    assert {"synthetic-case-1:fixed_commit[:8]", "synthetic-case-1:fix_path", "synthetic-case-1:keyword"} <= fields
