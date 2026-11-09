"""Regression: the sprint Meta ledger can't be silently written to an obsolete or relocated path (ds-nbc/1 DEVIATION_1:
after the 2026-11-08 folder move the old home-directory default recreated a stray, empty ledger)."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "research_tools" / "nbc" / "budget.py"
sys.path.insert(0, str(ROOT / "research_tools" / "nbc"))


def copy_into(root: Path) -> Path:
    dest = root / "research_tools" / "nbc" / "budget.py"
    dest.parent.mkdir(parents=True)
    shutil.copyfile(SRC, dest)
    return dest


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"budget_copy_{abs(hash(path))}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_default_comes_from_this_worktree_not_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("DS_NBC_META_LEDGER", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    import budget
    importlib.reload(budget)
    p = budget.ledger_path()
    assert p == ROOT / "docs/experimental_mvp/nonblind_corpus/META_LEDGER.jsonl"
    assert str(tmp_path) not in str(p)


def test_a_relocated_copy_outside_any_git_worktree_is_refused(monkeypatch, tmp_path):
    monkeypatch.delenv("DS_NBC_META_LEDGER", raising=False)
    m = load(copy_into(tmp_path / "moved repo"))
    with pytest.raises(m.LedgerPathError, match="not a git worktree"):
        m.ledger_path()
    with pytest.raises(m.LedgerPathError):
        m.reserve("x", 0.01)
    assert not (tmp_path / "moved repo" / "docs").exists()  # nothing created


def test_a_relocated_worktree_without_the_ledger_directory_is_refused_not_created(monkeypatch, tmp_path):
    monkeypatch.delenv("DS_NBC_META_LEDGER", raising=False)
    repo = tmp_path / "deploy spector" / "relocated"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    m = load(copy_into(repo))
    with pytest.raises(m.LedgerPathError, match="refusing to create"):
        m.reserve("x", 0.01)
    assert not (repo / "docs").exists()
    (repo / "docs/experimental_mvp/nonblind_corpus").mkdir(parents=True)
    rid = m.reserve("x", 0.01)  # the worktree's own, existing ledger directory is accepted
    assert rid and (repo / "docs/experimental_mvp/nonblind_corpus/META_LEDGER.jsonl").exists()


def test_an_explicit_obsolete_path_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("DS_NBC_META_LEDGER", str(tmp_path / "gone" / "META_LEDGER.jsonl"))
    import budget
    importlib.reload(budget)
    with pytest.raises(budget.LedgerPathError, match="refusing to create"):
        budget.reserve("x", 0.01)
    assert not (tmp_path / "gone").exists()


def test_a_relative_explicit_path_is_refused(monkeypatch):
    monkeypatch.setenv("DS_NBC_META_LEDGER", "docs/META_LEDGER.jsonl")
    import budget
    importlib.reload(budget)
    with pytest.raises(budget.LedgerPathError, match="absolute"):
        budget.ledger_path()


def test_the_cap_is_recorded_with_every_reservation(monkeypatch, tmp_path):
    monkeypatch.setenv("DS_NBC_META_LEDGER", str(tmp_path / "l.jsonl"))
    monkeypatch.setenv("DS_NBC_META_CAP_USD", "1.00")
    import budget
    importlib.reload(budget)
    budget.reserve("a", 0.6)
    with pytest.raises(budget.BudgetRefused):
        budget.reserve("b", 0.5)
    import json
    es = [json.loads(x) for x in (tmp_path / "l.jsonl").read_text().splitlines()]
    assert es[0]["cap_usd"] == 1.0 and es[1]["kind"] == "refused" and es[1]["cap_usd"] == 1.0
