"""EXPERIMENTAL: what a sealed evaluator can and cannot reach (research_tools/evaluator_sandbox.py). Synthetic cases and
fake labels only. Host-mode tests are offline. The container tests run only when Docker and the runner's pinned
Python base image are already present locally (never pulled), and they use throwaway ``ds-mvp-evalbox-*``
containers with no network."""

from __future__ import annotations

import io
import json
import secrets
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools"))
import corpus_intake as ci  # noqa: E402
import evaluator_sandbox as es  # noqa: E402
import sealed_evaluation as se  # noqa: E402

PROBE = r"""
import json, os, socket, sys
out = {"cwd": os.getcwd(), "env": sorted(os.environ), "files": sorted(
    os.path.relpath(os.path.join(d, f), ".") for d, _, fs in os.walk(".") for f in fs), "reach": {}}
for p in sys.argv[1:]:
    try:
        open(p, "rb").read(1); out["reach"][p] = True
    except OSError:
        out["reach"][p] = False
try:
    open("probe_write", "w").write("x"); out["can_write_package"] = True
except OSError:
    out["can_write_package"] = False
try:
    socket.create_connection(("1.1.1.1", 53), timeout=2); out["network"] = True
except OSError:
    out["network"] = False
print(json.dumps(out))
"""


def git_like_archive(path: Path, commit: str, mtime: int, code: bytes) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as tf:
        for name, data in (("app/__init__.py", b"VERSION = '1.0'\n"), ("app/queue.py", code)):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.uname = len(data), mtime, "root"
            tf.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())


@pytest.fixture
def world(tmp_path):
    curator = tmp_path / "curator_workspace"  # the curator's raw material: never given to the evaluator
    curator.mkdir()
    commits = [secrets.token_hex(20), secrets.token_hex(20)]
    norm = []
    for i, (c, code) in enumerate(zip(commits, (b"update(job)\n", b"update(job, fence=claim)\n"), strict=True)):
        raw = curator / f"raw{i}.tar"
        git_like_archive(raw, c, 1_700_000_000 + i * 86_400, code)
        assert es.main(["normalize", str(raw), str(curator / f"norm{i}.tar")]) == 0
        norm.append(curator / f"norm{i}.tar")
    sub = {"case_id": "syn-acc", "application": "synthapp", "repository": "https://example.org/synthapp.git",
           "split": "confirmatory", "revisions": [{"archive": str(norm[0]), "commit": commits[0]},
                                                  {"archive": str(norm[1]), "commit": commits[1]}],
           "buggy": 0, "fix_paths": ["app/queue.py"], "defect_summary": "a late completion overwrites a cancel",
           "keywords": ["fence", "overwrite"], "contract": {"kind": "single-version", "citation": "docs/x.md"},
           "oracle_spec_sha256": "d" * 64, "exposure": {"curator": ["fix diff"], "public": []}}
    (curator / "submission.json").write_text(json.dumps(sub))
    private = tmp_path / "curator_private"
    reveal = private / "syn-acc.reveal.json"
    case = tmp_path / "cases" / "syn-acc"
    ci.submit(curator / "submission.json", case, reveal)
    reg = tmp_path / "corpus" / "REGISTRY_confirmatory.jsonl"
    ci.register(case, reg)
    ci.freeze(reg)
    oracles = tmp_path / "oracles"
    oracles.mkdir()
    (oracles / "checker.py").write_text("def check(s):\n    return s['ok']\n")
    allow = tmp_path / "approved_oracles.json"
    allow.write_text(json.dumps({"checker.py": se.sha(oracles / "checker.py")}))
    ledger = tmp_path / "results.jsonl"
    se.commit_results(ledger, case_id="syn-acc", run_id="r0", verdicts={"R1": "HOLDS", "R2": "VIOLATED"},
                      oracle_sha256=se.sha(oracles / "checker.py"))
    se.authorize_reveal(ledger, authorizer="synthetic rehearsal", oracle_sha256=se.sha(oracles / "checker.py"))
    return {"tmp": tmp_path, "curator": curator, "private": private, "reveal": reveal, "case": case, "reg": reg,
            "oracle": oracles / "checker.py", "allow": allow, "ledger": ledger, "commits": commits}


def sensitive(w):
    """What must never reach the evaluator: the reveal (commits, roles, fix paths, keywords), the authorized results
    ledger, and the raw, unnormalized archives. The normalized archives *are* the permitted R1/R2."""
    return [w["reveal"], w["ledger"], w["curator"] / "raw0.tar", w["curator"] / "raw1.tar"]


def host_launch(w, out, *probe_targets, **kw):
    return es.launch(mode="simulated", registry=w["reg"], cases=[w["case"]], out=out,
                     cmd=[sys.executable, "-I", "-c", PROBE, *map(str, probe_targets)], oracles=[w["oracle"]],
                     allowlist=w["allow"], sensitive=sensitive(w), reveal_dirs=[w["private"]], **kw)


def test_host_mode_gives_only_permitted_inputs_and_a_reduced_environment(world, monkeypatch):
    w = world
    monkeypatch.setenv("MODEL_API_KEY", "sk-synthetic-must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setenv("DS_THIRDPARTY_SDIST_DIR", "/nowhere")
    out = w["tmp"] / "evaluator" / "pkg"
    out.parent.mkdir()
    r = host_launch(w, out, w["reveal"])
    assert r["exit"] == 0 and r["label"].startswith("SIMULATED")
    probe = json.loads(r["stdout_tail"].strip().splitlines()[-1])
    assert Path(probe["cwd"]).resolve() == out.resolve()
    assert set(probe["env"]) <= {"PATH", "LANG", "HOME", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
    assert "MODEL_API_KEY" not in probe["env"] and "DATABASE_URL" not in probe["env"]
    assert sorted(f for f in probe["files"] if f != "probe_write") == [
        "PACKAGE.json", "REGISTRY_confirmatory.jsonl", "cases/syn-acc/R1.tar", "cases/syn-acc/R2.tar",
        "cases/syn-acc/case.json", "oracles/checker.py"]
    labels = json.loads(w["reveal"].read_text())["labels"]
    assert se.scan_package(out, labels=labels) == []                     # no label material in the inputs
    blob = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
    assert not any(c.encode() in blob for c in w["commits"])             # no fix or buggy commit, even in tars
    assert b"authoriz" not in blob and str(w["private"]).encode() not in blob
    # documented limit: in host mode the same OS user CAN open the 0600 reveal if it knows the path
    assert probe["reach"][str(w["reveal"])] is True


def test_confirmatory_is_refused_while_the_amendment_is_unadopted(world):
    w = world
    with pytest.raises(SystemExit, match="not authorized"):
        es.launch(mode="confirmatory", registry=w["reg"], cases=[w["case"]], out=w["tmp"] / "pkg",
                  cmd=["true"], registration=w["tmp"] / "registration.json")
    assert not (w["tmp"] / "pkg").exists()
    why = es.confirmatory_allowed(ROOT / "docs/experimental_mvp/protocol", ROOT / "docs/research/live_stepping", None)
    assert any("not adopted" in x for x in why) and any("registration" in x for x in why)


def test_an_adoption_file_without_signatures_still_refuses_confirmatory(world, tmp_path):
    proto = tmp_path / "protocol"
    shutil.copytree(ROOT / "docs/experimental_mvp/protocol/approval", proto / "approval")
    shutil.copyfile(ROOT / "docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md",
                    proto / "AMENDMENT_003_ADOPTION_CANDIDATE.md")
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    shutil.copyfile(proto / "AMENDMENT_003_ADOPTION_CANDIDATE.md", frozen / "EVALUATION_PROTOCOL_AMENDMENT_003.md")
    reg = tmp_path / "registration.json"
    reg.write_text("{}")
    assert any("INCONSISTENTLY" in x or "unsigned" in x for x in es.confirmatory_allowed(proto, frozen, reg))


def test_unapproved_oracle_revisions_are_refused(world):
    w = world
    with pytest.raises(SystemExit, match="allowlist"):
        es.launch(mode="simulated", registry=w["reg"], cases=[w["case"]], out=w["tmp"] / "p1", cmd=["true"],
                  oracles=[w["oracle"]])
    w["oracle"].write_text("def check(s):\n    return True  # an unapproved revision\n")
    with pytest.raises(SystemExit, match="not an approved oracle revision"):
        es.launch(mode="simulated", registry=w["reg"], cases=[w["case"]], out=w["tmp"] / "p2", cmd=["true"],
                  oracles=[w["oracle"]], allowlist=w["allow"])
    assert not (w["tmp"] / "p2").exists()


def test_an_unfrozen_or_hidden_registry_is_never_packaged(world):
    w = world
    open_reg = w["tmp"] / "curator_open" / "REGISTRY_confirmatory.jsonl"
    ci.register(w["case"], open_reg)  # registered but not frozen: the curator's working state
    with pytest.raises(SystemExit, match="frozen"):
        es.launch(mode="simulated", registry=open_reg, cases=[w["case"]], out=w["tmp"] / "p3", cmd=["true"])


def test_sensitive_material_is_caught_by_bytes_contents_and_values(world, tmp_path):
    w = world
    pkg = tmp_path / "pkg_check"
    pkg.mkdir()
    shutil.copyfile(w["ledger"], pkg / "copied_ledger.jsonl")
    assert any("byte-identical" in x for x in es.sensitive_problems(pkg, sensitive(w)))
    (pkg / "copied_ledger.jsonl").unlink()
    (pkg / "notes.txt").write_text("x" + w["reveal"].read_text() + "y")
    assert any("contains the contents" in x for x in es.sensitive_problems(pkg, sensitive(w)))
    (pkg / "notes.txt").write_text(f"see {w['commits'][1]}")
    assert any("contains a value" in x for x in es.sensitive_problems(pkg, sensitive(w)))
    (pkg / "notes.txt").write_text("nothing sensitive at all")
    assert es.sensitive_problems(pkg, sensitive(w)) == []


def test_normalize_cli_removes_identity_metadata(tmp_path):
    raw = tmp_path / "raw.tar"
    c = secrets.token_hex(20)
    git_like_archive(raw, c, 1_700_000_000, b"x\n")
    assert se.archive_metadata_leaks(raw)
    assert es.main(["normalize", str(raw), str(tmp_path / "n.tar")]) == 0
    assert se.archive_metadata_leaks(tmp_path / "n.tar") == [] and c.encode() not in (tmp_path / "n.tar").read_bytes()
    assert es.main(["launch", "--mode", "nonsense", "--registry", "r", "--cases", "c", "--out", "o", "--", "true"]) == 2


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    p = subprocess.run(["docker", "image", "inspect", es.BASE_IMAGE], capture_output=True, text=True)
    return p.returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="Docker or the pinned base image isn't available locally")
def test_container_mode_reaches_only_the_read_only_package_without_network(world):
    w = world
    out = w["tmp"] / "evaluator_c" / "pkg"
    out.parent.mkdir()
    r = es.launch(mode="simulated", registry=w["reg"], cases=[w["case"]], out=out,
                  cmd=["python", "-I", "-c", PROBE, str(w["reveal"]), str(w["ledger"]),
                       str(w["curator"] / "submission.json"), "/etc/hostname"],
                  oracles=[w["oracle"]], allowlist=w["allow"], sensitive=sensitive(w), reveal_dirs=[w["private"]],
                  container=True, timeout=120)
    assert r["exit"] == 0, r["stderr_tail"]
    assert r["container_name"].startswith("ds-mvp-evalbox-") and r["container_left_behind"] is False
    probe = json.loads(r["stdout_tail"].strip().splitlines()[-1])
    assert probe["cwd"] == "/pkg" and probe["can_write_package"] is False and probe["network"] is False
    assert probe["reach"][str(w["reveal"])] is False                       # not mounted: unreachable
    assert probe["reach"][str(w["ledger"])] is False
    assert probe["reach"][str(w["curator"] / "submission.json")] is False
    assert "MODEL_API_KEY" not in probe["env"] and "DATABASE_URL" not in probe["env"]
    assert sorted(probe["files"]) == ["PACKAGE.json", "REGISTRY_confirmatory.jsonl", "cases/syn-acc/R1.tar",
                                      "cases/syn-acc/R2.tar", "cases/syn-acc/case.json", "oracles/checker.py"]
    ps = subprocess.run(["docker", "ps", "-a", "-q", "--filter", "name=ds-mvp-evalbox-"], capture_output=True,
                        text=True).stdout.strip()
    assert ps == ""
