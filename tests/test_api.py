"""End-to-end through FastAPI against real PostgreSQL. No results are stubbed."""

import dataclasses
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deployspecter import api, database
from deployspecter.api import app
from deployspecter.config import load_settings
from deployspecter.replay import load_artifact, save_artifact


@pytest.fixture(scope="module", autouse=True)
def isolated_environment(tmp_path_factory):
    # A separate lock lets the API open its own sandbox while the session fixture is open.
    patch = pytest.MonkeyPatch()
    patch.setattr(database, "_ADVISORY_LOCK_KEY", database._ADVISORY_LOCK_KEY + 1)
    real = load_settings()
    results = tmp_path_factory.mktemp("results")
    patch.setattr(api, "load_settings", lambda: dataclasses.replace(real, results_dir=results))
    yield results
    patch.undo()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _wait(client, run_id):
    for _ in range(300):
        record = client.get(f"/runs/{run_id}").json()
        if record["status"] in {"finished", "failed"}:
            return record
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def _run(client, scenario="backfill_overwrite", **body):
    response = client.post(f"/scenarios/{scenario}/run", json=body)
    assert response.status_code == 202
    return _wait(client, response.json()["run_id"])


def test_health_reports_database_and_hides_key(client):
    body = client.get("/health").json()
    assert body["database"]["reachable"] is True
    assert body["database"]["server_version"].startswith("16.")
    assert set(body) == {"status", "database", "model", "model_key_configured"}


def test_scenarios_lists_all_fixtures_with_splits(client):
    scenarios = client.get("/scenarios").json()
    assert {s["split"] for s in scenarios} == {"dev", "heldout"}
    assert len(scenarios) == 5


def test_unknown_scenario_and_run_are_404(client):
    assert client.post("/scenarios/nope/run", json={"variant": "buggy"}).status_code == 404
    assert client.get("/runs/not-a-run-id").status_code == 404
    assert client.get("/runs/" + "0" * 32).status_code == 404


def test_invalid_request_is_refused(client):
    body = {"variant": "other"}
    assert client.post("/scenarios/backfill_overwrite/run", json=body).status_code == 422
    body = {"variant": "buggy", "max_schedules": 10**9}
    assert client.post("/scenarios/backfill_overwrite/run", json=body).status_code == 422


def test_path_traversal_in_run_id_is_refused(client):
    assert client.get("/runs/..%2F..%2Fpyproject").status_code == 404


def _front_end_files():
    static = Path(api.__file__).parent / "static"
    return [static / "app.html", *sorted((static / "js").rglob("*.js"))]


def test_workbench_is_served_and_holds_no_measured_values(client):
    assert "DeploySpecter" in client.get("/app").text
    for path in _front_end_files():
        for fake in ("Alicia", "muse-spark", "true_violation", "2/2", "61 of"):
            assert fake not in path.read_text(), (path.name, fake)


def test_every_script_parses(tmp_path):
    """A syntax error once left a whole page blank while every API test passed."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for path in _front_end_files()[1:]:
        copy = tmp_path / f"{path.stem}.mjs"
        copy.write_text(path.read_text())
        result = subprocess.run([node, "--check", str(copy)], capture_output=True, text=True)
        assert result.returncode == 0, (path.name, result.stderr)


def test_buggy_run_returns_real_violation_and_replay(client):
    record = _run(client, variant="buggy", generator="hand")
    assert record["status"] == "finished", record
    view = record["result"]
    assert view["detected"] is True and view["classification"] == "true_violation"
    assert view["violations"][0]["expected"] == "Alicia" and view["violations"][0]["actual"] == "Alice"
    assert view["final_state"]["profiles"][0]["display_name"] == "Alice"
    assert view["minimized_replay"]["reproduced"] is True
    assert view["replay_command"].startswith("uv run deployspecter replay ")
    assert view["serial_order_violation"] is False


def test_corrected_run_reports_no_violation_and_real_state(client):
    view = _run(client, variant="corrected", generator="hand")["result"]
    assert view["detected"] is False and view["schedules_executed"] == 84
    assert "violations" not in view
    assert view["final_state"]["profiles"][0]["display_name"] == "Alicia"


def test_source_shows_both_versions_and_what_differs(client):
    body = client.get("/scenarios/backfill_overwrite/source").json()
    assert body["differing_files"] == ["backfill.py"]
    assert "AND display_name IS NULL" in body["variants"]["corrected"]["backfill.py"]
    assert "AND display_name IS NULL" not in body["variants"]["buggy"]["backfill.py"]
    assert client.get("/scenarios/nope/source").status_code == 404


def test_replay_endpoint_re_executes_the_saved_failure(client):
    record = _run(client, variant="buggy", generator="hand")
    body = client.post(f"/runs/{record['run_id']}/replay").json()
    assert body["reproduced"] is True and body["fingerprints_match"] == 3
    assert body["final_state"]["profiles"][0]["display_name"] == "Alice"
    assert body["violations"][0]["expected"] == "Alicia"


def test_replay_reports_when_a_failure_no_longer_reproduces(client):
    record = _run(client, variant="buggy", generator="hand")
    path = api.PROJECT_ROOT / record["result"]["artifact_path"]
    artifact = load_artifact(path)
    artifact.fingerprint = "0" * 64  # as if the saved behaviour no longer matched what runs now
    save_artifact(artifact, path)
    body = client.post(f"/runs/{record['run_id']}/replay", params={"attempts": 1}).json()
    assert body["reproduced"] is False and body["mismatches"]


def test_replay_refuses_a_run_with_no_failure(client):
    record = _run(client, variant="corrected", generator="hand", max_schedules=3)
    assert client.post(f"/runs/{record['run_id']}/replay").status_code == 409


def test_page_never_treats_an_unexecuted_case_as_clean():
    assert "not executed" in (Path(api.__file__).parent / "static" / "js" / "app" / "comparison.js").read_text()


def test_generated_specs_are_exposed_for_comparison(client):
    view = _run(client, variant="buggy", generator="baseline", max_schedules=10)["result"]
    spec = view["tests"][0]["spec"]
    assert spec["actors"][0]["function"] and spec["actors"][0]["steps"]


def test_run_is_retrievable_from_disk_after_memory_is_cleared(client):
    record = _run(client, variant="corrected", generator="hand", max_schedules=5)
    api._runs.pop(record["run_id"])
    assert client.get(f"/runs/{record['run_id']}").json()["result"]["schedules_executed"] == 5


def test_ai_generator_without_recorded_response_never_calls_the_model(client, monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("the interface must not construct a Meta client")

    monkeypatch.setattr("deployspecter.meta_client.MetaClient.__init__", forbidden)
    monkeypatch.setattr("openai.OpenAI", forbidden)
    view = _run(client, variant="buggy", generator="ai")["result"]  # results dir here is empty
    assert "NoRecordedResponse" in view["error"]
    assert view["detected"] is False and view["schedules_executed"] == 0


def test_comparison_is_empty_without_saved_results(client):
    assert client.get("/comparison").json() == {}
