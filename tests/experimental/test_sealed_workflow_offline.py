"""EXPERIMENTAL, offline rehearsal of the whole sealed workflow, with synthetic cases and fake labels only.

    curator submission → eligibility → seal → freeze → evaluator package → offline evaluation → blinded results
    → reveal authorization → scoring → integrity verification

Then the attacks and mistakes. The last test documents what the process can't hide: the two revisions' contents.
"""

from __future__ import annotations

import io
import json
import secrets
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research_tools"))
import corpus_intake as ci  # noqa: E402
import sealed_evaluation as se  # noqa: E402

FIXED_CODE = b"def finish(job):\n    update(job, fence=job.claim)  # fenced\n"
BUGGY_CODE = b"def finish(job):\n    update(job)\n"


def git_like_archive(path: Path, commit: str, mtime: int, code: bytes, changelog: bytes) -> None:
    """What ``git archive <commit>`` produces: a pax global header holding the commit id, commit-time mtimes, a root
    owner."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as tf:
        for name, data in (("app/__init__.py", b"VERSION = '1.0'\n"), ("app/queue.py", code),
                           ("CHANGELOG.md", changelog)):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.uname, info.gname = len(data), mtime, "root", "root"
            tf.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())


def curator_case(tmp: Path, case_id: str = "syn-1", app: str = "synthapp", split: str = "confirmatory"):
    """A synthetic case: a buggy parent and its fix, archived as git would, then normalized by the curator."""
    buggy_c, fixed_c = secrets.token_hex(20), secrets.token_hex(20)
    raw = tmp / "curator_raw"
    raw.mkdir(exist_ok=True)
    git_like_archive(raw / f"{case_id}-p.tar", buggy_c, 1_700_000_000, BUGGY_CODE, b"# changes\n")
    git_like_archive(raw / f"{case_id}-f.tar", fixed_c, 1_700_086_400, FIXED_CODE,
                     b"# changes\n- fixed a late completion overwriting a cancel\n")
    norm = []
    for name in (f"{case_id}-p.tar", f"{case_id}-f.tar"):
        dst = raw / f"norm-{name}"
        se.normalize_archive(raw / name, dst)
        norm.append(dst)
    sub = {"case_id": case_id, "application": app, "repository": f"https://example.org/{app}.git", "split": split,
           "revisions": [{"archive": str(norm[0]), "commit": buggy_c}, {"archive": str(norm[1]), "commit": fixed_c}],
           "buggy": 0, "fix_paths": ["app/queue.py"], "defect_summary": "a late completion overwrites a cancel",
           "keywords": ["fence", "overwrite"],
           "contract": {"kind": "single-version", "citation": "docs/upgrade.md#stop-then-migrate"},
           "oracle_spec_sha256": "d" * 64, "exposure": {"curator": ["fix diff", "tests"], "public": []}}
    p = raw / f"{case_id}.submission.json"
    p.write_text(json.dumps(sub))
    return p, sub


def offline_evaluator(package: Path, case: str) -> dict[str, str]:
    """A stand-in for an evaluation run that sees only the package: a deterministic verdict per revision."""
    out = {}
    for slot in ("R1", "R2"):
        with tarfile.open(package / "cases" / case / f"{slot}.tar") as tf:
            code = tf.extractfile("app/queue.py").read()
        out[slot] = "HOLDS" if b"fence=" in code else "VIOLATED"
    return out


@pytest.fixture
def world(tmp_path):
    p, sub = curator_case(tmp_path)
    case = tmp_path / "curator_cases" / "syn-1"
    reveal = tmp_path / "curator_private" / "syn-1.reveal.json"
    ci.submit(p, case, reveal)
    reg = tmp_path / "corpus" / "REGISTRY_confirmatory.jsonl"
    ci.register(case, reg)
    ci.freeze(reg)
    oracle = tmp_path / "oracle_checker.py"
    oracle.write_text("def check(state):\n    return state['cancelled_stays_failed']\n")
    return {"tmp": tmp_path, "sub": sub, "case": case, "reveal": reveal, "reg": reg, "oracle": oracle}


def test_the_whole_workflow_end_to_end(world):
    w = world
    pkg = w["tmp"] / "evaluator_package"
    se.build_package(w["reg"], [w["case"]], pkg, reveal_dirs=[w["reveal"].parent])
    assert sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file()) == [
        "PACKAGE.json", "REGISTRY_confirmatory.jsonl", "cases/syn-1/R1.tar", "cases/syn-1/R2.tar",
        "cases/syn-1/case.json"]
    assert se.scan_package(pkg, reveal_dirs=[w["reveal"].parent]) == []
    # the curator's own scan, with the labels, of what the evaluator will see
    labels = json.loads(w["reveal"].read_text())["labels"]
    assert se.scan_package(pkg, labels=labels) == []
    run_id = ci.run_id(pkg / "cases" / "syn-1", pkg / "REGISTRY_confirmatory.jsonl", protocol_sha256="a" * 64,
                       code_revision="c0ffee", policy={"kind": "alternate"})
    ledger = w["tmp"] / "results.jsonl"
    verdicts = offline_evaluator(pkg, "syn-1")
    se.commit_results(ledger, case_id="syn-1", run_id=run_id, verdicts=verdicts, oracle_sha256=se.sha(w["oracle"]))
    se.authorize_reveal(ledger, authorizer="(owner, synthetic rehearsal)", oracle_sha256=se.sha(w["oracle"]))
    result = se.score(ledger, w["case"], w["reveal"], oracle_file=w["oracle"])
    assert result["runs"] == [{"run_id": run_id, "buggy": "VIOLATED", "fixed": "HOLDS", "detected": True,
                               "corrected_version_violation": False}]
    assert se.verify_all(registry=w["reg"], package=pkg, ledger=ledger, oracle_file=w["oracle"]) == []


# ------------------------------------------------------------------------------------------------ attacks, mistakes

def test_raw_git_archives_carry_the_commit_and_the_order_and_are_refused(tmp_path):
    raw = tmp_path / "x.tar"
    c = secrets.token_hex(20)
    git_like_archive(raw, c, 1_700_000_000, BUGGY_CODE, b"")
    leaks = se.archive_metadata_leaks(raw)
    assert any("pax" in x for x in leaks) and any("mtime" in x for x in leaks)
    with tarfile.open(raw) as tf:
        assert tf.pax_headers["comment"] == c  # this is exactly what git archive leaks
    norm = tmp_path / "n.tar"
    se.normalize_archive(raw, norm)
    assert se.archive_metadata_leaks(norm) == [] and c.encode() not in norm.read_bytes()


def test_a_package_with_an_unnormalized_archive_is_refused(world):
    w = world
    git_like_archive(w["case"] / "R1.tar", secrets.token_hex(20), 1_700_000_000, BUGGY_CODE, b"")
    with pytest.raises(SystemExit):  # the archive no longer matches its registered hash, and carries identity
        se.build_package(w["reg"], [w["case"]], w["tmp"] / "pkg")


def test_modifying_a_registered_case_is_detected(world):
    w = world
    m = json.loads((w["case"] / "case.json").read_text())
    m["contract"]["citation"] = "changed after registration"
    (w["case"] / "case.json").write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    with pytest.raises(SystemExit, match="registered"):
        se.build_package(w["reg"], [w["case"]], w["tmp"] / "pkg")
    with pytest.raises(SystemExit, match="isn't registered"):
        ci.run_id(w["case"], w["reg"], protocol_sha256="a" * 64, code_revision="c", policy={})


def test_adding_or_replacing_a_case_after_the_freeze_is_refused(world):
    w = world
    p2, _ = curator_case(w["tmp"], case_id="syn-2")
    ci.submit(p2, w["tmp"] / "curator_cases" / "syn-2", w["tmp"] / "curator_private" / "syn-2.json")
    with pytest.raises(SystemExit, match="frozen"):
        ci.register(w["tmp"] / "curator_cases" / "syn-2", w["reg"])
    with pytest.raises(SystemExit):
        ci.register(w["case"], w["reg"])  # the same case again


def test_r1_r2_names_carry_no_role(world):
    # the role assignment is random (corpus_intake tests show both orders occur); the package names are only R1/R2
    w = world
    roles = ci.score(w["case"], w["reveal"])["revision_roles"]
    assert set(roles) == {"R1", "R2"} and sorted(roles.values()) == ["buggy", "fixed"]
    names = [p.name for p in w["case"].iterdir()]
    assert not any(x in " ".join(names).lower() for x in ("buggy", "fixed", "parent", "fix"))


def test_a_commit_id_injected_into_evaluator_metadata_is_found(world):
    w = world
    pkg = w["tmp"] / "pkg"
    se.build_package(w["reg"], [w["case"]], pkg)
    m = json.loads((pkg / "cases" / "syn-1" / "case.json").read_text())
    m["exposure_public"] = [f"see commit {w['sub']['revisions'][1]['commit']}"]
    (pkg / "cases" / "syn-1" / "case.json").write_text(json.dumps(m))
    assert any("40-hex" in x for x in se.scan_package(pkg))
    labels = json.loads(w["reveal"].read_text())["labels"]
    assert any("fixed_commit" in x for x in se.scan_package(pkg, labels=labels))


def test_a_development_case_cannot_be_registered_as_confirmatory(tmp_path):
    p, s = curator_case(tmp_path, case_id="dev-1", split="development")
    d = tmp_path / "cases" / "dev-1"
    ci.submit(p, d, tmp_path / "priv" / "dev-1.json")
    ci.register(d, tmp_path / "corpus" / "REGISTRY_development.jsonl")
    s["split"], s["case_id"] = "confirmatory", "conf-1"
    p.write_text(json.dumps(s))
    ci.submit(p, tmp_path / "cases" / "conf-1", tmp_path / "priv" / "conf-1.json")
    with pytest.raises(SystemExit, match="other split"):
        ci.register(tmp_path / "cases" / "conf-1", tmp_path / "corpus" / "REGISTRY_confirmatory.jsonl")


def test_revealing_or_scoring_before_results_and_authorization_is_refused(world):
    w = world
    ledger = w["tmp"] / "results.jsonl"
    with pytest.raises(SystemExit, match="committed"):
        se.authorize_reveal(ledger, authorizer="x", oracle_sha256=se.sha(w["oracle"]))
    se.commit_results(ledger, case_id="syn-1", run_id="r", verdicts={"R1": "HOLDS", "R2": "VIOLATED"},
                      oracle_sha256=se.sha(w["oracle"]))
    with pytest.raises(SystemExit, match="authorization"):
        se.score(ledger, w["case"], w["reveal"], oracle_file=w["oracle"])
    se.authorize_reveal(ledger, authorizer="x", oracle_sha256=se.sha(w["oracle"]))
    with pytest.raises(SystemExit, match="authorized"):  # no results are accepted after the reveal
        se.commit_results(ledger, case_id="syn-1", run_id="late", verdicts={"R1": "HOLDS", "R2": "HOLDS"},
                          oracle_sha256=se.sha(w["oracle"]))


def test_the_evaluator_package_holds_no_sealed_path_and_the_reveal_is_owner_only(world):
    w = world
    pkg = w["tmp"] / "pkg"
    se.build_package(w["reg"], [w["case"]], pkg, reveal_dirs=[w["reveal"].parent])
    assert str(w["reveal"].parent) not in "".join(p.read_text(errors="replace") for p in pkg.rglob("*.json"))
    assert (w["reveal"].stat().st_mode & 0o777) == 0o600
    # limit (documented): the same OS user can still open a 0600 file; separation needs another account or machine


def test_a_prompt_with_solution_metadata_is_caught(world):
    w = world
    labels = json.loads(w["reveal"].read_text())["labels"]
    clean = "You design workloads for a job queue. Task kinds: ok, fail, flaky. Constraints: ..."
    assert se.prompt_leaks(clean, labels) == []
    leaky = clean + f" Hint: look at app/queue.py and commit {labels[0]['fixed_commit'][:10]}; the fence matters."
    assert {"syn-1:fix_path", "syn-1:fixed_commit[:10]", "syn-1:keyword"} <= set(se.prompt_leaks(leaky, labels))


def test_a_corrupted_results_chain_is_detected_and_blocks_scoring(world):
    w = world
    ledger = w["tmp"] / "results.jsonl"
    se.commit_results(ledger, case_id="syn-1", run_id="r", verdicts={"R1": "HOLDS", "R2": "VIOLATED"},
                      oracle_sha256=se.sha(w["oracle"]))
    se.authorize_reveal(ledger, authorizer="x", oracle_sha256=se.sha(w["oracle"]))
    lines = ledger.read_text().splitlines()
    first = json.loads(lines[0])
    first["verdicts"] = {"R1": "VIOLATED", "R2": "HOLDS"}  # flip a result after the fact
    ledger.write_text("\n".join([json.dumps(first, sort_keys=True)] + lines[1:]) + "\n")
    assert se.verify_chain(ledger)
    with pytest.raises(SystemExit, match="broken"):
        se.score(ledger, w["case"], w["reveal"], oracle_file=w["oracle"])


def test_changing_the_oracle_after_results_is_refused(world):
    w = world
    ledger = w["tmp"] / "results.jsonl"
    se.commit_results(ledger, case_id="syn-1", run_id="r", verdicts={"R1": "HOLDS", "R2": "VIOLATED"},
                      oracle_sha256=se.sha(w["oracle"]))
    se.authorize_reveal(ledger, authorizer="x", oracle_sha256=se.sha(w["oracle"]))
    w["oracle"].write_text("def check(state):\n    return True  # loosened after seeing results\n")
    with pytest.raises(SystemExit, match="oracle code changed"):
        se.score(ledger, w["case"], w["reveal"], oracle_file=w["oracle"])
    pkg = w["tmp"] / "pkg"
    se.build_package(w["reg"], [w["case"]], pkg)
    assert any("oracle" in x for x in se.verify_all(registry=w["reg"], package=pkg, ledger=ledger,
                                                    oracle_file=w["oracle"]))


def test_a_result_from_a_different_oracle_cannot_be_authorized(world):
    w = world
    ledger = w["tmp"] / "results.jsonl"
    se.commit_results(ledger, case_id="syn-1", run_id="r", verdicts={"R1": "HOLDS", "R2": "VIOLATED"},
                      oracle_sha256="e" * 64)
    with pytest.raises(SystemExit, match="different oracle"):
        se.authorize_reveal(ledger, authorizer="x", oracle_sha256=se.sha(w["oracle"]))


def test_limit_the_revisions_contents_still_reveal_the_fix(world):
    """Documented limit, not a property: diffing R1 against R2 shows the fix's location, and a changelog entry can name
    the fixed revision. Blindness rests on the process (the runner never diffs; inputs are leak-checked), not on
    the archives being indistinguishable."""
    w = world
    pkg = w["tmp"] / "pkg"
    se.build_package(w["reg"], [w["case"]], pkg)
    contents = {}
    for slot in ("R1", "R2"):
        with tarfile.open(pkg / "cases" / "syn-1" / f"{slot}.tar") as tf:
            contents[slot] = {m.name: tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()}
    differing = sorted(n for n in contents["R1"] if contents["R1"][n] != contents["R2"][n])
    assert differing == ["CHANGELOG.md", "app/queue.py"]  # the fix path and the changelog are visible by diff
