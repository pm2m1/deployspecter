"""EXPERIMENTAL corpus selection tool, offline: synthetic archives, no network, no candidate code run."""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("corpus_select", Path(__file__).resolve().parents[2]
                                              / "research_tools" / "corpus_select.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


def archive(tmp_path: Path, root: str, files: dict[str, str]) -> Path:
    path = tmp_path / f"{root}.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def layout(tmp_path, root, files, package):
    exposure: list[str] = []
    found = cs.discover(archive(tmp_path, root, files), exposure)
    return found, cs.apply_frozen_rule(found, package), exposure


def test_a_core_numbered_migrations_directory_selects_its_highest_file(tmp_path):
    files = {f"lib/migrations/v{i}.py": "" for i in range(1, 10)} | {"lib/plugins/x/migrations/v1.py": "",
                                                                     "lib/migrate.py": ""}
    _found, verdict, _ = layout(tmp_path, "lib-1.0", files, "lib")
    assert verdict == {"verdict": "SELECTED", "area": "lib/migrations/v9.py", "rule": verdict["rule"]}


def test_several_django_app_migration_dirs_need_a_ruling_and_installed_apps_is_read_literally(tmp_path):
    files = {"proj/trans/migrations/0001_initial.py": "", "proj/trans/migrations/0002_x.py": "",
             "proj/accounts/migrations/0001_initial.py": "",
             "proj/settings.py": "INSTALLED_APPS = ['proj.trans', 'proj.accounts', 'django.contrib.auth']\n"}
    found, verdict, exposure = layout(tmp_path, "proj-1.0", files, "proj")
    django = found["layouts"][0]
    assert django["app_migration_dirs"] == {"proj/accounts": 1, "proj/trans": 2}
    assert django["installed_apps"] == ["django.contrib.auth", "proj.accounts", "proj.trans"]
    assert verdict["verdict"] == "NEEDS_PROTOCOL_RULING"
    assert any("INSTALLED_APPS" in e for e in exposure)


def test_a_computed_installed_apps_is_reported_unresolved_not_guessed(tmp_path):
    files = {"proj/app/migrations/0001_initial.py": "", "proj/settings.py": "INSTALLED_APPS = base() + ['x']\n"}
    found, _verdict, _ = layout(tmp_path, "proj-1.0", files, "proj")
    assert found["layouts"][0]["installed_apps"] is None and found["layouts"][0]["installed_apps_unresolved"]


def test_alembic_revisions_give_a_graph_and_need_a_ruling(tmp_path):
    rev = 'revision = "{r}"\ndown_revision = {d}\n\ndef upgrade():\n    secret_body_never_kept()\n'
    files = {"app/db/env.py": "", "app/db/alembic.ini": "[alembic]\nscript_location = app/db\n",
             "app/db/versions/a1.py": rev.format(r="a1", d="None"),
             "app/db/versions/b2.py": rev.format(r="b2", d='"a1"'),
             "app/db/versions/c3.py": rev.format(r="c3", d='"a1"')}
    found, verdict, exposure = layout(tmp_path, "app-1.0", files, "app")
    alembic = found["layouts"][0]
    assert (alembic["revisions"], alembic["bases"], alembic["heads"]) == (3, ["a1"], ["b2", "c3"])
    assert verdict["verdict"] == "NEEDS_PROTOCOL_RULING"
    assert "secret_body_never_kept" not in str(found) and any("headers only" in e for e in exposure)


def test_no_migrations_directory_selects_nothing(tmp_path):
    _found, verdict, _ = layout(tmp_path, "q-1.0", {"q/queue/postgres_migrations.py": ""}, "q")
    assert verdict["verdict"] == "NONE_BY_RULE"


def test_a_held_out_application_is_refused_before_any_network_access(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "fetch", lambda *_a, **_k: pytest.fail("fetched a held-out application"))
    row = cs.select({"pypi": "saleor", "version": "1.0"}, tmp_path)
    assert row["role"] == "EXCLUDED_HELD_OUT" and row["exposure"] == ["name only"]


@pytest.mark.parametrize("requires, psycopg3", [
    (["psycopg[binary]>=3.1"], "required"), (["psycopg[binary]==3.3.8; extra == \"postgres\""], "extra_only:postgres"),
    (["psycopg2-binary>=2.9"], "absent"), (["asyncpg>=0.30"], "absent")])
def test_driver_declarations_are_classified(requires, psycopg3):
    assert cs.classify_dependencies(requires)["psycopg3"] == psycopg3


def test_licences_are_checked_against_osi(tmp_path):
    assert cs.classify_licence({"license_expression": "MIT"})["osi_approved"] is True
    assert cs.classify_licence({"license_expression": "GPL-3.0-or-later"})["osi_approved"] is True
    assert cs.classify_licence({"classifiers": ["License :: Other/Proprietary License"]})["osi_approved"] is False
