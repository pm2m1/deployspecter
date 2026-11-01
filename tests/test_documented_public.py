"""The public GitLab-derived flow, the Muse Spark pipeline view and result separation. Real PostgreSQL, no model."""

import dataclasses
import json
import time

import pytest
from fastapi.testclient import TestClient

from deployspecter import api, database
from deployspecter.api import app
from deployspecter.config import load_settings
from deployspecter.documented import EXTERNAL_CASES, SCENARIO, documented_source, write_documented_report
from deployspecter.scenarios import list_scenarios

REAL = load_settings()


@pytest.fixture(scope="module", autouse=True)
def isolated(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(database, "_ADVISORY_LOCK_KEY", database._ADVISORY_LOCK_KEY + 5)
    results = tmp_path_factory.mktemp("results")
    patch.setattr(api, "load_settings", lambda: dataclasses.replace(REAL, results_dir=results))
    yield results
    patch.undo()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def wait(client, run_id):
    for _ in range(300):
        record = client.get(f"/runs/{run_id}").json()
        if record["status"] in {"finished", "failed"}:
            return record
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def documented(client, variant):
    response = client.post(f"/external-cases/{SCENARIO}/documented-defect/run", json={"variant": variant})
    assert response.status_code == 202
    record = wait(client, response.json()["run_id"])
    assert record["status"] == "finished", record
    return record


class TestDocumentedDefect:
    def test_buggy_trigger_loses_the_destination_row_serially(self, client):
        view = documented(client, "buggy")["result"]
        facts = view["documented"]
        row = {"user_id": 1, "project_id": 1}
        assert facts["destination_before"] == [{**row, "access_level": 30}]
        assert facts["destination_after"] == []
        assert facts["destination_row_disappeared"] == [{**row, "access_level": 30}]
        assert facts["source_rows_remaining"] == [{**row, "access_level": 20}]
        assert facts["failure_class"] == "serial_migration_defect" and facts["fails_in_serial_order"]
        assert facts["actors_in_minimal_failure"] == 1 and facts["transactions_in_minimal_failure"] == 1
        assert facts["violating_schedules"] == facts["schedules"] == 20
        assert view["violations"][0]["requirement_id"] == "EXT-pair-retained"
        assert view["minimized_replay"]["reproduced"] is True

    def test_corrected_logic_keeps_the_lowest_level_in_this_test(self, client):
        facts = documented(client, "corrected")["result"]["documented"]
        assert facts["destination_after"] == [{"user_id": 1, "project_id": 1, "access_level": 20}]
        assert facts["destination_row_disappeared"] == []
        assert facts["failure_class"] == "none_in_this_test" and facts["violating_schedules"] == 0

    def test_replay_button_re_executes_the_saved_failure(self, client):
        record = documented(client, "buggy")
        body = client.post(f"/runs/{record['run_id']}/replay").json()
        assert body["reproduced"] is True and body["fingerprints_match"] == 3
        assert body["final_state"]["project_grants_compact"] == []

    def test_corrected_has_nothing_to_replay(self, client):
        record = documented(client, "corrected")
        assert client.post(f"/runs/{record['run_id']}/replay").status_code == 409

    def test_only_two_variants_can_be_requested(self, client):
        url = f"/external-cases/{SCENARIO}/documented-defect/run"
        assert client.post(url, json={"variant": "other"}).status_code == 422

    def test_unknown_case_is_404(self, client):
        assert client.get("/external-cases/nope/source").status_code == 404
        assert client.post("/external-cases/nope/documented-defect/run", json={"variant": "buggy"}).status_code == 404

    def test_links_point_at_the_public_merge_requests(self, client):
        [case] = client.get("/external-cases").json()
        assert [link["url"].rsplit("/", 1)[-1] for link in case["links"]] == ["246505", "246509"]

    def test_external_case_stays_out_of_the_benchmark(self, client):
        ids = {s["id"] for s in client.get("/scenarios").json()}
        assert SCENARIO not in ids and len(ids) == 5
        assert SCENARIO not in list_scenarios(REAL.scenarios_dir)
        assert client.post(f"/scenarios/{SCENARIO}/run", json={"variant": "buggy"}).status_code == 404


class TestPublicFlowScope:
    def test_source_covers_only_the_two_published_versions(self, client):
        source = client.get(f"/external-cases/{SCENARIO}/source").json()
        assert set(source["variants"]) == {"buggy", "corrected"}
        assert source["differing_files"] == ["migration.sql"]
        assert set(documented_source(REAL, SCENARIO)["variants"]) == {"buggy", "corrected"}
        assert set(EXTERNAL_CASES) == {SCENARIO}

    def test_runs_judge_only_the_published_requirement(self, client):
        view = documented(client, "buggy")["result"]
        assert len(view["requirements"]) == 1
        assert {v["requirement_id"] for v in view["violations"]} == {"EXT-pair-retained"}
        assert [t["name"] for t in view["tests"]] == ["revoke one of two access levels"]

    def test_saved_report_and_artifacts_hold_only_the_published_requirement(self, tmp_path):
        settings = dataclasses.replace(REAL, results_dir=tmp_path)
        report, _ = write_documented_report(settings, out_dir=tmp_path / "out")
        assert report["model_requests"] == 0
        assert report["variants"]["buggy"]["replay_reproduced"] is True
        for path in (tmp_path / "out").rglob("*__min.json"):
            assert [r["id"] for r in json.loads(path.read_text())["requirements"]] == ["EXT-pair-retained"]


class TestMuseSparkPipelineView:
    @pytest.fixture(autouse=True)
    def recorded(self, monkeypatch):
        # Reads the real recorded responses and saved reports; nothing here can reach the model.
        monkeypatch.setattr(api, "load_settings", lambda: REAL)

    def test_shows_every_stage_from_recorded_data(self, client):
        d = client.get("/scenarios/backfill_overwrite/ai-pipeline", params={"variant": "buggy"}).json()
        assert "backfill.py" in d["files"]
        assert any(c["statement_id"] == "backfill.py:backfill_display_names#1" for c in d["catalog"])
        assert d["attempts"][0]["output_tokens"] and d["conflicts"]
        assert d["tests"] and all(t["accepted"] for t in d["tests"])
        assert d["tests"][0]["proposal"]["actors"][0]["calls"][0]["statement_id"]
        assert d["as_scored_in_saved_benchmark"]["tests_proposed"] == len(d["tests"])

    def test_a_held_out_failure_stays_visible_in_saved_scores(self, client):
        d = client.get("/scenarios/wallet_credit_resync/ai-pipeline", params={"variant": "corrected"}).json()
        scored = d["as_scored_in_saved_benchmark"]
        assert scored["tests_valid"] == 0 and scored["rejections"]
        assert all(t["accepted"] for t in d["tests"])  # today's validator normalizes the names

    def test_missing_recording_is_a_clear_404(self, client):
        response = client.get(
            "/scenarios/wallet_credit_resync/ai-pipeline", params={"variant": "corrected", "repeat": 2}
        )
        assert response.status_code == 404 and "no recorded model response" in response.json()["detail"]

    def test_only_existing_recordings_are_offered(self, client):
        assert client.get("/scenarios/wallet_credit_resync/recordings").json() == {"buggy": [0], "corrected": [0]}
        assert client.get("/scenarios/backfill_overwrite/recordings").json()["buggy"] == [0, 1, 2]
        assert client.get("/scenarios/nope/recordings").status_code == 404

    def test_comparison_uses_baseline_c_newest_report_and_keeps_published_files(self, client):
        data = client.get("/comparison").json()
        rows = [s for s in data["baseline_c"]["scores"] if s["system"] == "baseline_c"]
        assert sum(s["flagged"] for s in rows if not s["has_bug"]) == 0
        assert data["heldout"]["generated_at"] == "2026-11-01T10:50:13+00:00"

    def test_usage_is_read_from_recordings(self, client):
        usage = client.get("/usage").json()
        assert usage["versions"]["v2"]["requests"] == 18 and usage["versions"]["v1"]["requests"] == 8

    def test_every_recorded_mode_can_be_requested_without_a_client(self, client, monkeypatch):
        def forbidden(*_, **__):
            raise AssertionError("the interface must not construct a Meta client")

        monkeypatch.setattr("deployspecter.meta_client.MetaClient.__init__", forbidden)
        monkeypatch.setattr("openai.OpenAI", forbidden)
        body = {"variant": "buggy", "generator": "ai", "max_schedules": 10, "repeat": 1}
        record = wait(client, client.post("/scenarios/backfill_overwrite/run", json=body).json()["run_id"])
        assert record["status"] == "finished" and record["result"]["analysis"]["attempts"]
        monkeypatch.undo()
