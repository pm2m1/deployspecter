"""EXPERIMENTAL, offline: the sealed corpus intake (research_tools/corpus_intake.py), on synthetic cases only."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research_tools"))
import corpus_intake as ci  # noqa: E402


def submission(tmp: Path, *, case_id="case-a1", app="exampleapp", split="confirmatory"):
    revs = []
    for i in range(2):
        f = tmp / f"{case_id}-rev{i}.tar"
        f.write_bytes(secrets.token_bytes(256))
        revs.append({"archive": str(f), "commit": secrets.token_hex(20)})
    s = {"case_id": case_id, "application": app, "repository": f"https://example.org/{app}.git", "split": split,
         "revisions": revs, "buggy": 0, "fix_paths": ["exampleapp/queue/claim.py"],
         "defect_summary": "a late completion overwrites a cancellation", "keywords": ["overwrite", "cancellation"],
         "contract": {"kind": "blue-green", "citation": "docs/upgrading.md#zero-downtime"},
         "oracle_spec_sha256": "a" * 64, "exposure": {"curator": ["issue tracker", "fix diff"], "public": []}}
    p = tmp / f"{case_id}.submission.json"
    p.write_text(json.dumps(s))
    return p, s


def test_the_public_manifest_has_no_label_material_and_the_reveal_recovers_the_roles(tmp_path):
    p, s = submission(tmp_path)
    m = ci.submit(p, tmp_path / "cases" / "case-a1", tmp_path / "private" / "a1.reveal.json")
    text = json.dumps(m)
    assert s["revisions"][0]["commit"] not in text and s["revisions"][1]["commit"] not in text
    assert "claim.py" not in text and "overwrite" not in text and "buggy" not in text
    assert ci.verify_case(tmp_path / "cases" / "case-a1") == []
    roles = ci.score(tmp_path / "cases" / "case-a1", tmp_path / "private" / "a1.reveal.json")["revision_roles"]
    assert sorted(roles.values()) == ["buggy", "fixed"]


def test_r1_and_r2_are_assigned_at_random(tmp_path):
    seen = set()
    for i in range(24):
        p, _ = submission(tmp_path, case_id=f"c{i}")
        ci.submit(p, tmp_path / "cases" / f"c{i}", tmp_path / "private" / f"c{i}.json")
        seen.add(ci.score(tmp_path / "cases" / f"c{i}", tmp_path / "private" / f"c{i}.json")["revision_roles"]["R1"])
    assert seen == {"buggy", "fixed"}


def test_the_reveal_is_refused_inside_the_repository(tmp_path):
    p, _ = submission(tmp_path)
    with pytest.raises(SystemExit, match="outside the repository"):
        ci.submit(p, tmp_path / "cases" / "x", ci.REPO / "reveal.json")


def test_label_material_in_a_public_field_is_refused(tmp_path):
    p, s = submission(tmp_path)
    s["contract"]["citation"] = "see the overwrite fix"
    p.write_text(json.dumps(s))
    with pytest.raises(SystemExit, match="label material"):
        ci.submit(p, tmp_path / "cases" / "y", tmp_path / "private" / "y.json")
    assert not (tmp_path / "cases" / "y").exists()


@pytest.mark.parametrize("app", ["procrastinate", "chancy", "saleor", "pgqueuer"])
def test_development_and_held_out_applications_are_never_confirmatory(tmp_path, app):
    p, _ = submission(tmp_path, app=app)
    with pytest.raises(SystemExit, match="never confirmatory"):
        ci.submit(p, tmp_path / "cases" / "z", tmp_path / "private" / "z.json")


def test_a_tampered_archive_or_manifest_is_detected(tmp_path):
    p, _ = submission(tmp_path)
    d = tmp_path / "cases" / "case-a1"
    ci.submit(p, d, tmp_path / "private" / "a1.json")
    (d / "R2.tar").write_bytes(b"changed")
    assert any("R2.tar" in x for x in ci.verify_case(d))


def test_the_registry_is_append_only_frozen_and_disjoint(tmp_path):
    reg = tmp_path / "corpus" / "REGISTRY_confirmatory.jsonl"
    dev = tmp_path / "corpus" / "REGISTRY_development.jsonl"
    p, _ = submission(tmp_path)
    d = tmp_path / "cases" / "case-a1"
    ci.submit(p, d, tmp_path / "private" / "a1.json")
    ci.register(d, reg)
    with pytest.raises(SystemExit, match="already registered"):
        ci.register(d, reg)
    with pytest.raises(SystemExit, match="REGISTRY_confirmatory"):
        ci.register(d, dev)  # a confirmatory case can't be filed as development
    p2, _ = submission(tmp_path, case_id="case-b2")
    d2 = tmp_path / "cases" / "case-b2"
    ci.submit(p2, d2, tmp_path / "private" / "b2.json")
    ci.freeze(reg)
    with pytest.raises(SystemExit, match="frozen"):
        ci.register(d2, reg)
    assert ci.verify_registry(reg) == []
    lines = reg.read_text().splitlines()
    first = json.loads(lines[0])
    first["manifest_sha256"] = "0" * 64
    reg.write_text("\n".join([json.dumps(first, sort_keys=True)] + lines[1:]) + "\n")
    assert ci.verify_registry(reg)  # an edited entry breaks the chain


def test_the_same_case_cannot_enter_both_splits(tmp_path):
    p, s = submission(tmp_path, split="development")
    d = tmp_path / "cases" / "dev1"
    ci.submit(p, d, tmp_path / "private" / "dev1.json")
    ci.register(d, tmp_path / "corpus" / "REGISTRY_development.jsonl")
    s["split"], s["case_id"] = "confirmatory", "conf1"
    p.write_text(json.dumps(s))  # the same archives, resubmitted for the confirmatory set
    d2 = tmp_path / "cases" / "conf1"
    ci.submit(p, d2, tmp_path / "private" / "conf1.json")
    with pytest.raises(SystemExit, match="other split"):
        ci.register(d2, tmp_path / "corpus" / "REGISTRY_confirmatory.jsonl")


def test_run_ids_need_a_frozen_registry_and_are_deterministic(tmp_path):
    reg = tmp_path / "corpus" / "REGISTRY_confirmatory.jsonl"
    p, _ = submission(tmp_path)
    d = tmp_path / "cases" / "case-a1"
    ci.submit(p, d, tmp_path / "private" / "a1.json")
    ci.register(d, reg)
    kw = {"protocol_sha256": "b" * 64, "code_revision": "c0ffee", "policy": {"kind": "alternate"}}
    with pytest.raises(SystemExit, match="frozen"):
        ci.run_id(d, reg, **kw)
    ci.freeze(reg)
    a, b = ci.run_id(d, reg, **kw), ci.run_id(d, reg, **kw)
    assert a == b and a != ci.run_id(d, reg, **{**kw, "policy": {"kind": "seeded", "seed": 1}})


def test_a_reveal_that_doesnt_match_the_seal_is_refused(tmp_path):
    p, _ = submission(tmp_path)
    d = tmp_path / "cases" / "case-a1"
    rv = tmp_path / "private" / "a1.json"
    ci.submit(p, d, rv)
    r = json.loads(rv.read_text())
    r["labels"][0]["revision_roles"] = {k: ("fixed" if v == "buggy" else "buggy")
                                        for k, v in r["labels"][0]["revision_roles"].items()}
    rv.write_text(json.dumps(r))
    with pytest.raises(SystemExit, match="doesn't match"):
        ci.score(d, rv)
