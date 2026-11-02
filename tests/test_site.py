"""Public site and guided walkthrough. Real PostgreSQL where execution is involved, and no model requests."""

import dataclasses
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deployspecter import api, database, guided
from deployspecter.api import app
from deployspecter.config import load_settings
from deployspecter.scheduler import ScheduleRunner
from deployspecter.site import EXPORT_FILES, STATIC, build_site_data, export_static_site

REAL = load_settings()
WALKTHROUGH = json.loads((STATIC / "data" / "walkthrough.json").read_text())
SITE = json.loads((STATIC / "data" / "site.json").read_text())


@pytest.fixture(scope="module", autouse=True)
def isolated(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(database, "_ADVISORY_LOCK_KEY", database._ADVISORY_LOCK_KEY + 6)
    results = tmp_path_factory.mktemp("results")
    patch.setattr(api, "load_settings", lambda: dataclasses.replace(REAL, results_dir=results))
    yield results
    patch.undo()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


class TestPages:
    @pytest.mark.parametrize(("path", "title"), [
        ("/", "find data loss"), ("/app", "Workbench"), ("/demo", "Recorded walkthrough"),
        ("/demo.html", "Recorded walkthrough"),
    ])
    def test_page_has_title_metadata_and_favicon(self, client, path, title):
        page = client.get(path)
        assert page.status_code == 200 and title in page.text
        assert 'name="description"' in page.text and "favicon.svg" in page.text and 'lang="en"' in page.text

    def test_favicon_and_assets_are_served(self, client):
        assert client.get("/favicon.ico").headers["content-type"].startswith("image/svg")
        assert client.get("/static/js/lib/timeline.js").status_code == 200

    def test_static_mount_cannot_escape_its_directory(self, client):
        assert client.get("/static/../api.py").status_code == 404
        assert client.get("/static/%2e%2e/config.py").status_code == 404

    def test_old_root_fragments_still_reach_the_workbench(self):
        script = (STATIC / "js" / "landing.js").read_text()
        for old in ("gitlab", "scenarios", "pipeline", "comparison"):
            assert f'"{old}"' in script
        ids = re.findall(r'id="([\w-]+)"', (STATIC / "landing.html").read_text())
        assert not {"gitlab", "scenarios", "pipeline", "comparison"} & set(ids)

    def test_every_page_loads_only_local_assets(self):
        for name in ("landing.html", "demo.html", "app.html"):
            text = (STATIC / name).read_text()
            for match in re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="([^"]+)"', text):
                assert not match.startswith(("http", "//", "/")), match

    def test_external_links_are_only_the_project_and_published_sources(self):
        text = (STATIC / "landing.html").read_text()
        assert set(re.findall(r'href="(https?://[^"]+)"', text)) == {
            "https://github.com/pm2m1/deployspecter/tree/hackathon-release",
            "https://github.com/pm2m1/deployspecter/blob/hackathon-release/README.md#setup",
        }


class TestGuidedExecution:
    def test_recorded_runs_match_a_fresh_postgresql_execution(self):
        with database.Sandbox(REAL) as sandbox, ScheduleRunner(sandbox) as runner:
            for key, recorded in WALKTHROUGH["runs"].items():
                source, variant, preset = key.split("/")
                fresh = guided.execute(sandbox, runner, REAL, source, variant, preset)
                assert fresh["fingerprint"] == recorded["fingerprint"], key
                assert fresh["final_value"] == recorded["final_value"], key

    def test_the_stale_schedule_is_the_failing_one(self):
        failing = {k for k, r in WALKTHROUGH["runs"].items() if r["violations"]}
        assert failing == {"reference/buggy/stale_read", "muse/buggy/stale_read"}
        run = WALKTHROUGH["runs"]["reference/buggy/stale_read"]
        assert (run["expected_value"], run["final_value"]) == ("Alicia", "Alice")
        assert run["final_state"]["profiles"][0]["display_name"] == "Alice"
        assert run["replay"]["reproduced"] is True

    def test_timeline_marks_point_at_the_overwrite(self):
        run = WALKTHROUGH["runs"]["reference/buggy/stale_read"]
        events, marks = run["events"], run["marks"]
        assert events[marks["user_commit_seq"]]["committed_value"] == "Alicia"
        assert events[marks["overwrite_seq"]]["params"]["display_name"] == "Alice"
        assert events[marks["overwrite_commit_seq"]]["committed_value"] == "Alice"

    def test_corrected_guard_is_visible_in_the_recording(self):
        run = WALKTHROUGH["runs"]["reference/corrected/stale_read"]
        write = next(e for e in run["events"] if e["role"] == "job" and e["rows"] is None and e["kind"] == "sql")
        assert write["rowcount"] == 0 and run["final_value"] == "Alicia"

    def test_muse_section_is_the_saved_response(self):
        path = REAL.results_dir / "ai_responses" / "v2" / "backfill_overwrite__buggy__r0.json"
        record = json.loads(path.read_text())
        muse = WALKTHROUGH["muse"]
        assert muse["proposal"] == record["output"]["tests"][0]
        assert muse["output_tokens"] == record["attempts"][0]["reply"]["output_tokens"]
        assert WALKTHROUGH["model_requests"] == 0

    def test_live_execute_runs_and_replays(self, client):
        view = client.post("/guided/stale-backfill/execute", json={}).json()
        assert view["final_value"] == "Alice" and view["violations"]
        replayed = client.post(f"/runs/{view['run_id']}/replay").json()
        assert replayed["reproduced"] is True and replayed["fingerprints_match"] == 3

    def test_live_corrected_has_nothing_to_replay(self, client):
        view = client.post("/guided/stale-backfill/execute", json={"variant": "corrected"}).json()
        assert view["final_value"] == "Alicia" and not view["violations"]
        assert client.post(f"/runs/{view['run_id']}/replay").status_code == 409

    def test_live_explore_reports_real_counts(self, client):
        result = client.post("/guided/stale-backfill/explore", json={"source": "muse"}).json()
        assert result == {**WALKTHROUGH["exploration"]["muse/buggy"]}

    @pytest.mark.parametrize("body", [
        {"sql": "DROP TABLE profiles"}, {"preset": "anything"}, {"source": "other"}, {"variant": "other"},
        {"schedule": ["user"]},
    ])
    def test_requests_cannot_supply_sql_or_arbitrary_schedules(self, client, body):
        assert client.post("/guided/stale-backfill/execute", json=body).status_code == 422

    def test_database_outage_is_a_clear_503_and_recorded_pages_still_work(self, client, monkeypatch):
        down = dataclasses.replace(
            REAL, admin_dsn="postgresql://x:y@127.0.0.1:9/none", test_dsn="postgresql://x:y@127.0.0.1:9/none_test"
        )
        monkeypatch.setattr(api, "load_settings", lambda: down)
        response = client.post("/guided/stale-backfill/execute", json={})
        assert response.status_code == 503 and "PostgreSQL is not available" in response.json()["detail"]
        pipeline = client.get("/scenarios/backfill_overwrite/ai-pipeline")
        assert pipeline.status_code == 503
        for path in ("/", "/app", "/demo", "/static/data/walkthrough.json"):
            assert client.get(path).status_code == 200


class TestSiteData:
    def test_numbers_match_the_saved_reports(self):
        rebuilt = build_site_data(REAL)
        assert rebuilt == json.loads(json.dumps(SITE, default=str))
        held = SITE["benchmark"]["splits"]["heldout"]["systems"]
        saved = json.loads((REAL.results_dir / "benchmark" / "heldout_2026-11-01T105013Z.json").read_text())["summary"]
        assert held["ai"]["defects_found"] == saved["ai"]["detected"]
        assert held["ai"]["requests"] == saved["ai"]["requests"]
        assert held["ai"]["estimated_usd"] == round(saved["ai"]["approx_cost_usd"], 2)
        assert held["baseline_b"]["median_schedules_to_failure"] == saved["baseline_b"]["median_schedules_to_failure"]

    def test_an_unexecuted_case_is_not_counted_clean(self):
        ai = SITE["benchmark"]["splits"]["heldout"]["systems"]["ai"]
        assert (ai["corrected_runs_executed"], ai["corrected_runs_not_executed"]) == (2, 1)

    def test_baseline_c_comes_from_its_newest_report(self):
        for split in SITE["benchmark"]["splits"].values():
            assert split["systems"]["baseline_c"]["false_flags"] == 0

    def test_landing_page_states_no_measured_number_itself(self):
        text = re.sub(r"<(style|script)[^>]*>.*?</\\1>", "", (STATIC / "landing.html").read_text(), flags=re.S)
        for hard_coded in ("61 of", "of 84", "20 of 20", "$0.39", "18 of 22", "27 of 32", "Alicia"):
            assert hard_coded not in text

    def test_no_claim_of_ai_superiority_or_production_safety(self):
        text = " ".join(
            (STATIC / name).read_text() for name in ("landing.html", "demo.html", "js/landing.js", "js/lib/guided.js")
        ).lower()
        for claim in ("outperform", "superior", "production-ready", "guarantees", "trusted by", "customers", "10x"):
            assert claim not in text


class TestStaticExport:
    def test_export_holds_only_the_listed_files(self, tmp_path):
        out = tmp_path / "site"
        written = export_static_site(out)
        on_disk = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
        assert on_disk == written
        assert set(written) == {*EXPORT_FILES.values(), "static/js/config.js"}
        assert "staticSite: true" in (out / "static" / "js" / "config.js").read_text()

    def test_export_has_no_workbench_or_python(self, tmp_path):
        out = tmp_path / "site"
        export_static_site(out)
        assert not (out / "app.html").exists() and not (out / "static" / "js" / "app").exists()
        assert not list(out.rglob("*.py")) and not list(out.rglob("*.sqlite*"))

    def test_export_has_no_workbench_link_even_without_javascript(self, tmp_path):
        out = tmp_path / "site"
        export_static_site(out)
        for page in ("index.html", "demo.html"):
            text = (out / page).read_text()
            assert 'href="app"' not in text and "live-only" not in text
            assert '<html lang="en" data-site="static">' in text
        assert "static-only" in (out / "index.html").read_text()
        assert 'own files. <span class="static-only">' in (out / "demo.html").read_text()

    def test_export_links_to_the_public_repository_and_not_to_local_endpoints(self, tmp_path):
        out = tmp_path / "site"
        export_static_site(out)
        allowed = {
            "https://github.com/pm2m1/deployspecter/tree/hackathon-release",
            "https://github.com/pm2m1/deployspecter/blob/hackathon-release/README.md#setup",
        }
        index = (out / "index.html").read_text()
        assert set(re.findall(r'href="(https?://[^"]+)"', index)) == allowed
        for page in ("index.html", "demo.html"):
            text = (out / page).read_text()
            assert set(re.findall(r'href="(https?://[^"]+)"', text)) <= allowed
            assert "Run it locally" not in text and "127.0.0.1" not in text and "CHANGE_ME" not in text
            assert "local-only" not in text and 'href="app"' not in text
        assert 'href="demo.html"' in index

    def test_export_says_the_walkthrough_is_recorded(self, tmp_path):
        out = tmp_path / "site"
        export_static_site(out)
        assert "replays saved runs and doesn't touch a database" in (out / "index.html").read_text()
        assert "This page needs no database" in (out / "demo.html").read_text()

    def test_export_keeps_the_published_sources(self, tmp_path):
        out = tmp_path / "site"
        export_static_site(out)
        data = (out / "static" / "data" / "site.json").read_text()
        assert "merge_requests/246505" in data and "merge_requests/246509" in data

    def test_local_page_keeps_its_links_and_setup(self):
        text = (STATIC / "landing.html").read_text()
        assert "github.com/pm2m1/deployspecter/tree/hackathon-release" in text and "Run it locally" in text

    def test_old_fragments_do_not_redirect_to_a_missing_workbench_in_the_export(self):
        assert "!window.DEPLOYSPECTER?.staticSite" in (STATIC / "js" / "landing.js").read_text()

    def test_links_in_export_resolve(self, tmp_path):
        out = tmp_path / "site"
        export_static_site(out)
        for page in ("index.html", "demo.html"):
            text = (out / page).read_text()
            for target in re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="(static/[^"]+)"', text):
                assert (out / target).exists(), target
        assert (Path(out) / "demo.html").exists()
