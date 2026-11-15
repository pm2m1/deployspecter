"""Solo r3 design: the frozen, seeded case selection, on synthetic git mirrors (offline; throwaway seeds)."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "solo"))
import select_cases as sc  # noqa: E402

RULES = {"window_start": "2020-01-01T00:00:00Z", "window_end": "2030-01-01T00:00:00Z", "per_app_screen_cap": 12,
         "per_app_admit_cap": 3, "target_cases": 4, "min_apps": 2, "screen_budget": 100}


def _git(repo, *a, date="2024-01-01T00:00:00Z"):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(repo)}
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env=env).stdout


def _commit(repo, files, msg, i):
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, date=f"2024-01-{i + 2:02d}T00:00:00Z")


def _app(root, name, driver_line, n_fixes, with_tests=True):
    work = root / f"work_{name}"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _commit(work, {"pyproject.toml": f'[project]\ndependencies = ["{driver_line}"]\n', "app/core.py": "X = 0\n",
                   "tests/test_core.py": "def test_zero():\n    assert True\n"}, "initial", 0)
    for i in range(n_fixes):
        files = {"app/core.py": f"X = {i + 1}\n"}
        if with_tests:
            files[f"tests/test_fix{i}.py"] = f"def test_fix_{i}():\n    x = {i}\n    assert x == {i}\n"
        _commit(work, files, f"fix: avoid a race when jobs are cancelled ({i})", i + 1)
        _commit(work, {"README.md": f"docs {i}\n"}, f"docs: words {i}", i + 1)
    mirror = root / "mirrors" / f"org__{name}.git"
    mirror.parent.mkdir(exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--mirror", str(work), str(mirror)], check=True)
    return {"repo": f"org/{name}", "url": str(work)}


@pytest.fixture()
def world(tmp_path):
    apps = [_app(tmp_path, "alpha", "psycopg[binary]>=3.1", 5),
            _app(tmp_path, "beta", "psycopg>=3.2", 4),
            _app(tmp_path, "gamma", "psycopg2-binary", 3),
            _app(tmp_path, "delta", "psycopg[binary]>=3.1", 3, with_tests=False),
            _app(tmp_path, "eps", "psycopg2>=2.9\", \"Django>=4.2", 2)]
    return {"rules": {**RULES, "applications": apps}, "mirrors": tmp_path / "mirrors", "tmp": tmp_path}


def test_selection_is_deterministic_given_the_seed(world):
    a = sc.select(world["mirrors"], "seed-1", world["rules"], {"org/eps": "pass"}, set())
    b = sc.select(world["mirrors"], "seed-1", world["rules"], {"org/eps": "pass"}, set())
    assert a == b
    admitted, skips, counts = a
    assert counts["admitted"] >= world["rules"]["target_cases"] and counts["apps"] >= world["rules"]["min_apps"]
    assert all(sum(1 for c in admitted if c["repo"] == r) <= 3 for r in {c["repo"] for c in admitted})


def test_the_seed_changes_the_order(world):
    orders = {tuple(c["fix_commit"] for c in sc.select(world["mirrors"], s, world["rules"], {"org/eps": "pass"},
                                                       set())[0]) for s in ("seed-1", "seed-2", "seed-3", "seed-4")}
    assert len(orders) > 1


def test_skip_codes(world):
    rules = {**world["rules"], "target_cases": 99, "min_apps": 99}
    admitted, skips, counts = sc.select(world["mirrors"], "seed-1", rules, {"org/eps": "fail"}, set())
    codes = {(s["repo"], s["code"]) for s in skips}
    assert ("org/gamma", "driver") in codes
    assert ("org/delta", "no_regression_test") in codes
    assert ("org/eps", "s4_failed") in codes
    assert ("org/alpha", "app_cap") in codes
    assert {c["repo"] for c in admitted} == {"org/alpha", "org/beta"}
    assert all(c["tests"] and c["tests"][0]["test_functions"] for c in admitted)


def test_s4_application_without_a_verdict_stops(world):
    rules = {**world["rules"], "target_cases": 99, "min_apps": 99}
    with pytest.raises(sc.Stop, match="org/eps"):
        sc.select(world["mirrors"], "seed-1", rules, {}, set())


def test_adapter_refusals_are_skipped_and_selection_continues(world):
    admitted, _, _ = sc.select(world["mirrors"], "seed-1", world["rules"], {"org/eps": "pass"}, set())
    first = admitted[0]
    again, skips, _ = sc.select(world["mirrors"], "seed-1", world["rules"], {"org/eps": "pass"},
                                {(first["repo"], first["fix_commit"])})
    assert {"repo": first["repo"], "commit": first["fix_commit"], "code": "adapter_refused"} in skips
    assert first["fix_commit"] not in {c["fix_commit"] for c in again}


def test_select_refuses_before_registration_and_with_the_wrong_seed(world, tmp_path):
    cands = tmp_path / "c.json"
    cands.write_text(json.dumps(world["rules"]))
    freeze = tmp_path / "FREEZE.json"
    seed = "ef" * 32
    seed_sha = hashlib.sha256(seed.encode()).hexdigest()
    freeze.write_text(json.dumps({"sealed_seeds": {"selection_seed_sha256": seed_sha}}))
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"selection_seed": seed}))
    reg = tmp_path / "REGISTRATION.json"
    base = ["select", "--mirrors", str(world["mirrors"]), "--candidates", str(cands), "--freeze", str(freeze),
            "--seeds", str(seeds), "--registration", str(reg), "--s4-verdicts", str(tmp_path / "s4.json")]
    (tmp_path / "s4.json").write_text(json.dumps({"org/eps": "pass"}))
    with pytest.raises(SystemExit, match="no registration"):
        sc.main([*base, "--out", str(tmp_path / "o1")])
    reg.write_text(json.dumps({"osf_url": "https://osf.io/xxxxx", "freeze_sha256": "0" * 64}))
    with pytest.raises(SystemExit, match="doesn't name"):
        sc.main([*base, "--out", str(tmp_path / "o2")])
    reg.write_text(json.dumps({"osf_url": "https://osf.io/xxxxx",
                               "freeze_sha256": hashlib.sha256(freeze.read_bytes()).hexdigest()}))
    assert sc.main([*base, "--out", str(tmp_path / "o3")]) == 0
    sel = json.loads((tmp_path / "o3" / "SELECTION.json").read_text())
    assert sel["counts"]["admitted"] >= 4
    skips = (tmp_path / "o3" / "SKIPS.jsonl").read_text()
    assert "race" not in skips and "fix:" not in skips, "no commit message is stored"
    seeds.write_text(json.dumps({"selection_seed": "00" * 32}))
    with pytest.raises(Exception, match="committed seed"):
        sc.main([*base, "--out", str(tmp_path / "o4")])


def test_frozen_candidate_list():
    doc = json.loads((REPO / "docs/experimental_mvp/solo/CANDIDATES_FROZEN.json").read_text())
    assert len(doc["applications"]) == 20 and doc["target_cases"] == 10 and doc["min_apps"] == 5
    assert doc["per_app_admit_cap"] == 3 and doc["screen_budget"] == 100
