"""The public reproduction of the GitLab-fixture evidence, on real PostgreSQL. It can send no model request."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from deployspecter import pipeline
from deployspecter import public_reproduction as pr
from deployspecter.config import PROJECT_ROOT

ROOT_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
# Only generic checks belong here. A list of private names would itself publish them.
LOCAL_DETAILS = re.compile(r"/Users/|/home/|corrected_r\d")


@pytest.fixture(scope="module")
def report(settings, sandbox, runner, tmp_path_factory):
    return pr.run_reproduction(settings, sandbox, runner, tmp_path_factory.mktemp("repro"))


class TestReproduction:
    def test_every_check_passes(self, report):
        assert report["checks_failed"] == 0, report["failures"]
        assert report["checks_run"] >= 50

    def test_it_confirms_the_recorded_result(self, report):
        assert report["buggy_fixture_detections"] == "0 of 3"

    def test_it_makes_no_model_requests(self, report):
        assert report["model_requests"] == 0

    def test_validation_and_execution_were_compared(self, report):
        names = {row["check"] for row in report["checks"]}
        for trial in pr.TRIALS:
            for suffix in ("prompt identity", "rejection reasons", "per-test execution", "oracle flag"):
                assert f"{trial}: {suffix}" in names

    def test_documented_defect_is_checked_independently(self, report):
        checks = {row["check"]: row for row in report["checks"]}
        assert checks["reference: failure class"]["actual"] == "serial_migration_defect"
        assert checks["reference: minimized to one actor"]["passed"]
        assert checks["plain SQL: every serial order loses the row on the buggy fixture"]["passed"]

    def test_baselines_cover_the_buggy_fixture_only(self, report):
        checks = {row["check"]: row for row in report["checks"]}
        assert checks["baseline_b: oracle_flagged"]["actual"] is False
        assert checks["baseline_c: schedules_to_first_failure"]["actual"] == 56
        assert not [r for r in report["checks"] if "baseline" in r["check"] and "corrected" in r["check"]]

    def test_it_states_what_it_cannot_reproduce(self, report):
        text = " ".join(report["not_reproducible_from_public_files"])
        assert "three corrected-fixture trials" in text and "production systems" in text
        assert "corrected-fixture trials" in pr.format_report(report)

    def test_report_holds_no_local_details(self, report):
        assert not LOCAL_DETAILS.search(json.dumps(report, default=str))


class TestNoModelClient:
    def test_module_cannot_build_a_client(self):
        text = Path(pr.__file__).read_text()
        assert "MetaClient" not in text and "OpenAI" not in text

    def test_pipeline_receives_no_client(self, settings, sandbox, runner, tmp_path, monkeypatch):
        clients = []
        real = pipeline.run_ai

        def watched(sandbox_, runner_, case, schedules, seconds, artifacts, client, *args, **kwargs):
            clients.append(client)
            return real(sandbox_, runner_, case, schedules, seconds, artifacts, client, *args, **kwargs)

        monkeypatch.setattr(pr, "run_ai", watched)
        pr.check_buggy_trial(settings, sandbox, runner, "r2", pr.package_dir(settings), tmp_path, pr.Checks())
        assert clients == [None]


class TestDetectsTampering:
    def _copy(self, settings, tmp_path):
        target = tmp_path / "pkg"
        shutil.copytree(pr.package_dir(settings), target)
        return target

    def test_published_package_verifies(self, settings):
        assert pr.verify_manifest(pr.package_dir(settings)) == 15

    def test_changed_response_fails_the_manifest(self, settings, tmp_path):
        target = self._copy(settings, tmp_path)
        path = target / "original/responses/buggy_r1/analysis.json"
        path.write_text(path.read_text().replace('"status": "completed"', '"status": "edited"'))
        with pytest.raises(pr.EvidenceMismatch, match="buggy_r1/analysis.json"):
            pr.verify_manifest(target)

    def test_a_different_saved_result_fails_a_check(self, settings, sandbox, runner, tmp_path):
        target = self._copy(settings, tmp_path)
        path = target / "original/responses/buggy_r2/system_run.json"
        saved = json.loads(path.read_text())
        saved["tests_valid"] = 99
        path.write_text(json.dumps(saved))
        checks = pr.Checks()
        pr.check_buggy_trial(settings, sandbox, runner, "r2", target, tmp_path / "a", checks)
        assert [c["check"] for c in checks.failed] == ["r2: tests valid"]

    def test_a_claimed_detection_would_be_caught(self, settings, sandbox, runner, tmp_path):
        target = self._copy(settings, tmp_path)
        path = target / "original/responses/buggy_r3/system_run.json"
        saved = json.loads(path.read_text())
        saved["detected"] = True
        path.write_text(json.dumps(saved))
        checks = pr.Checks()
        pr.check_buggy_trial(settings, sandbox, runner, "r3", target, tmp_path / "b", checks)
        assert [c["check"] for c in checks.failed] == ["r3: oracle flag"]


class TestPublicPackage:
    def test_originals_are_byte_identical_to_the_manifest(self, settings):
        package = pr.package_dir(settings)
        manifest = (package / "MANIFEST.sha256").read_text().splitlines()
        rows = [line.split("  ", 2) for line in manifest]
        identical = [r for r in rows if r[1] == "byte-identical"]
        assert len(identical) == 8
        for digest, _, name in identical:
            assert hashlib.sha256((package / name).read_bytes()).hexdigest() == digest

    def test_only_buggy_fixture_originals_are_published(self, settings):
        package = pr.package_dir(settings)
        names = [str(p.relative_to(package)) for p in (package / "original").rglob("*") if p.is_file()]
        assert names and not [n for n in names if "corrected" in n or "baseline_c" in n]
        ledger = (package / "derived" / "requests_buggy_only.jsonl").read_text().splitlines()
        assert {json.loads(line)["trial"] for line in ledger} == {"buggy/r1", "buggy/r2", "buggy/r3"}

    def test_no_local_details_in_any_public_file(self, settings):
        for path in pr.package_dir(settings).rglob("*"):
            if path.is_file():
                assert not LOCAL_DETAILS.search(path.read_text()), path

    def test_omissions_stay_until_publication_is_complete(self, settings):
        text = (pr.package_dir(settings) / "OMISSIONS.md").read_text()
        for phrase in ("can't be fully reproduced", "Withheld", "reproduce-public-evidence", "isn't a safety result"):
            assert phrase in text

    def test_placing_the_package_does_not_change_site_data(self, settings):
        from deployspecter.site import SITE_DATA, build_site_data

        built = json.loads(json.dumps(build_site_data(settings), default=str))
        assert built == json.loads(SITE_DATA.read_text())
        assert not list(pr.package_dir(settings).glob("report.json"))


class TestWorkflow:
    def test_workflow_is_offline_and_holds_no_production_credentials(self):
        text = ROOT_WORKFLOW.read_text()
        assert 'pytest -m "not live"' in text and "ruff check ." in text
        assert "postgres:16" in text and 'python-version: "3.12"' in text
        assert "secrets." not in text and 'MODEL_API_KEY: ""' in text
        assert "reproduce-public-evidence" in text and "--max-requests" not in text

    def test_workflow_has_read_only_permissions_and_pinned_actions(self):
        text = ROOT_WORKFLOW.read_text()
        assert "contents: read" in text and "persist-credentials: false" in text
        for use in re.findall(r"uses:\s*(\S+)", text):
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", use), use

    def test_default_options_exclude_live_tests(self):
        assert "-m 'not live'" in (PROJECT_ROOT / "pyproject.toml").read_text()
