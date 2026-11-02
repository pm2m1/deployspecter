"""Validator and client plumbing. A stub stands in for the network only; the live
call is the `live` test. Nothing here asserts that a bug detector found anything."""

import json
import types

import pytest

from deployspecter.analyzer import (
    AnalysisOutput,
    PromptLeak,
    analyze,
    assert_no_leak,
    build_prompt,
    parse_output,
)
from deployspecter.analyzer_support import read_catalog, read_schema
from deployspecter.config import load_settings, redact
from deployspecter.meta_client import MetaClient, MissingApiKey, ModelBudgetExceeded, strict_json_schema
from deployspecter.models import TestProposal
from deployspecter.pipeline import compile_all, run_ai
from deployspecter.scenarios import load_all_cases, load_case
from deployspecter.validation import SpecRejected, actor_ids, compile_proposal


class StubResponses:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            output_text=self.replies.pop(0), status="completed",
            usage=types.SimpleNamespace(
                input_tokens=1000, output_tokens=2000,
                output_tokens_details=types.SimpleNamespace(reasoning_tokens=1500),
            ),
        )


def stub_client(settings, replies):
    responses = StubResponses(replies)
    client = MetaClient(
        settings, client=types.SimpleNamespace(responses=responses), max_requests=10, max_estimated_usd=10
    )
    return client, responses


def _hand(load):
    return load("backfill_overwrite", "buggy")


def _proposal(case, **changes):
    data = case.hand_spec.model_dump()
    data.update(changes)
    return TestProposal.model_validate(data)


def test_missing_key_is_a_clear_error(settings):
    import dataclasses

    with pytest.raises(MissingApiKey, match="MODEL_API_KEY"):
        MetaClient(dataclasses.replace(settings, model_api_key=None))


def test_strict_schema_has_no_refs_and_requires_every_property():
    schema = strict_json_schema(AnalysisOutput)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text

    def walk(node):
        if isinstance(node, dict):
            if "properties" in node:
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)


def test_strict_schema_accepts_everything_the_parser_requires():
    """Regression: a field named `description` was once stripped as if it were a keyword."""
    invariant = strict_json_schema(AnalysisOutput)["properties"]["tests"]["items"]["properties"]["invariants"]["items"]
    assert "description" in invariant["properties"] and "description" in invariant["required"]

    def walk(node, model_fields_seen):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties":
                    model_fields_seen.update(value)
                walk(value, model_fields_seen)
        elif isinstance(node, list):
            for value in node:
                walk(value, model_fields_seen)

    seen = set()
    walk(strict_json_schema(AnalysisOutput), seen)
    from deployspecter import analyzer, models

    for cls in (analyzer.AnalysisOutput, analyzer.ConflictNote, analyzer.OperationNote, models.TestProposal,
                models.ActorProposal, models.StatementCall, models.ParamBinding, models.SeedRow,
                models.ColumnValue, models.InvariantProposal):
        assert set(cls.model_fields) <= seen, cls.__name__


def test_usage_and_cost_are_captured(settings):
    client, responses = stub_client(settings, ["{}"])
    reply = client.generate("i", "p", "n", {"type": "object"})
    assert (reply.input_tokens, reply.output_tokens, reply.reasoning_tokens) == (1000, 2000, 1500)
    assert reply.approx_cost_usd == pytest.approx(0.00975)
    assert responses.calls[0]["text"]["format"]["strict"] is True
    assert responses.calls[0]["reasoning"] == {"effort": "medium"}
    assert responses.calls[0]["store"] is False


def test_legacy_request_configuration_omits_new_fields(settings):
    responses = StubResponses(["{}"])
    client = MetaClient(
        settings, client=types.SimpleNamespace(responses=responses),
        max_requests=1, max_estimated_usd=1,
        reasoning_effort=None, store=None,
    )
    client.generate("i", "p", "n", {"type": "object"})
    assert "reasoning" not in responses.calls[0]
    assert "store" not in responses.calls[0]


def test_invalid_reasoning_effort_fails_without_request(settings):
    responses = StubResponses(["{}"])
    with pytest.raises(ValueError, match="reasoning_effort"):
        MetaClient(settings, client=types.SimpleNamespace(responses=responses), reasoning_effort="maximum")
    assert not responses.calls


@pytest.mark.parametrize("case", load_all_cases(load_settings().scenarios_dir), ids=lambda c: c.case_id)
def test_prompt_never_contains_withheld_information(case, sandbox):
    prompt = build_prompt(case.files, case.source_model(), read_schema(sandbox, case.setup_sql))
    assert_no_leak(prompt)
    for req in case.requirements:
        assert req.id not in prompt and req.text not in prompt
    assert case.scenario_id not in prompt and case.title not in prompt


def test_leak_guard_rejects_labels():
    with pytest.raises(PromptLeak):
        assert_no_leak("see buggy/backfill.py")


def test_unparseable_response_is_recorded_not_raised(settings):
    client, _ = stub_client(settings, ["not json"])
    attempt = analyze(client, "p")
    assert parse_output(attempt) is None
    assert attempt.parse_error


class TestValidatorRejects:
    """Each case is a way a generated spec could otherwise fabricate a defect."""

    @pytest.fixture
    def ctx(self, load, sandbox):
        case = load("backfill_overwrite", "corrected")
        return case, case.source_model(), read_catalog(sandbox, case.setup_sql)

    def _rejected(self, ctx, mutate):
        case, source, catalog = ctx
        data = case.hand_spec.model_dump()
        mutate(data)
        with pytest.raises(SpecRejected) as exc:
            compile_proposal(TestProposal.model_validate(data), source, catalog, "t")
        return " ".join(exc.value.reasons)

    def test_statement_from_another_function(self, ctx):
        def m(d):
            d["actors"][1]["calls"][0]["statement_id"] = "backfill.py:backfill_display_names#1"
        assert "is not in" in self._rejected(ctx, m)

    def test_statement_not_in_source(self, ctx):
        def m(d):
            d["actors"][0]["calls"][1]["statement_id"] = "backfill.py:backfill_display_names#9"
        assert "is not in" in self._rejected(ctx, m)

    def test_unknown_function(self, ctx):
        def m(d):
            d["actors"][0]["function"] = "evil.py:drop_everything"
        assert "unknown function" in self._rejected(ctx, m)

    def test_extra_sql_field_cannot_be_smuggled(self, load):
        case = load("backfill_overwrite", "corrected")
        data = case.hand_spec.model_dump()
        data["actors"][0]["calls"][0]["sql"] = "DROP TABLE profiles"
        with pytest.raises(ValueError):
            TestProposal.model_validate(data)

    def test_binding_that_contradicts_source_data_flow(self, ctx):
        def m(d):  # source passes row["legacy_name"]; a literal would invent a value
            p = d["actors"][0]["calls"][1]["params"][0]
            p.update(source="literal", value="made up", bind=None, column=None)
        assert "binding disagrees" in self._rejected(ctx, m)

    def test_row_ref_to_unbound_name(self, ctx):
        def m(d):
            d["actors"][0]["calls"][1]["params"][1]["bind"] = "nope"
        assert "not produced" in self._rejected(ctx, m)

    def test_missing_param(self, ctx):
        def m(d):
            d["actors"][1]["calls"][0]["params"].pop()
        assert "needs params" in self._rejected(ctx, m)

    def test_omitting_required_statement(self, ctx):
        def m(d):
            d["actors"][1]["calls"] = []
        assert "omits unconditional statement" in self._rejected(ctx, m)

    def test_seed_on_unknown_table(self, ctx):
        def m(d):
            d["seed"][0]["table"] = "pg_authid"
        assert "unknown table" in self._rejected(ctx, m)

    def test_seed_on_unknown_column(self, ctx):
        def m(d):
            d["seed"][0]["values"][0]["column"] = "id; DROP TABLE profiles"
        assert "unknown column" in self._rejected(ctx, m)

    def test_too_many_actors(self, ctx):
        def m(d):
            d["actors"] = [dict(d["actors"][1], name=f"u{i}") for i in range(4)]
        assert "actors" in self._rejected(ctx, m)


@pytest.mark.parametrize("statement", ["RESET ROLE", "SELECT 1; DROP TABLE profiles", "DROP TABLE profiles"])
def test_source_statement_outside_the_allowlist_is_rejected(statement, sandbox, load):
    case = load("backfill_overwrite", "corrected")
    case.files["app_v2.py"] = f"""
def update_display_name(conn, user_id, display_name):
    conn.execute("{statement}")
"""
    data = case.hand_spec.model_dump()
    data["actors"][1]["calls"] = [{"statement_id": "app_v2.py:update_display_name#0", "params": [], "bind_as": None}]
    catalog = read_catalog(sandbox, case.setup_sql)
    with pytest.raises(SpecRejected, match="not a single"):
        compile_proposal(TestProposal.model_validate(data), case.source_model(), catalog, "t")


class TestActorNames:
    @pytest.mark.parametrize(("names", "expected"), [
        (["backfill", "user_1"], ["backfill", "user_1"]),
        (["credit-500", "resync same row"], ["credit_500", "resync_same_row"]),
        (["a-b", "a_b"], ["a_b_2", "a_b"]),
        (["a-b", "a.b", "a b"], ["a_b", "a_b_2", "a_b_3"]),
        (["1st", "---"], ["a_1st", "actor_2"]),
        (["cr\u00e9dit", "\u4f7f\u7528\u8005"], ["cr_dit", "actor_2"]),
    ])
    def test_ids_are_deterministic_and_unique(self, names, expected):
        assert actor_ids(names) == expected
        assert actor_ids(names) == expected

    def test_truncation_keeps_ids_unique_and_short(self):
        ids = actor_ids(["x" * 40 + "-a", "x" * 40 + "-b"])
        assert len(set(ids)) == 2 and all(len(i) <= 31 for i in ids)

    def test_exact_duplicates_stay_rejected_as_ambiguous(self, load, sandbox):
        case = load("backfill_overwrite", "corrected")
        data = case.hand_spec.model_dump()
        data["actors"][1]["name"] = data["actors"][0]["name"]
        catalog = read_catalog(sandbox, case.setup_sql)
        with pytest.raises(SpecRejected, match="duplicate actor names"):
            compile_proposal(TestProposal.model_validate(data), case.source_model(), catalog, "t")

    def test_hyphenated_names_execute_and_are_reported(self, load, sandbox, runner):
        case = load("backfill_overwrite", "buggy")
        data = case.hand_spec.model_dump()
        data["actors"][0]["name"], data["actors"][1]["name"] = "back-fill", "user-1"
        catalog = read_catalog(sandbox, case.setup_sql)
        report = compile_proposal(TestProposal.model_validate(data), case.source_model(), catalog, "t")
        assert report.renamed_actors == {"back-fill": "back_fill", "user-1": "user_1"}
        schedule = ["back_fill"] * 3 + ["user_1"] * 3 + ["back_fill"] * 3
        trace = runner.run(report.spec, case.setup_sql, schedule)
        assert trace.final_state["profiles"][0]["display_name"] == "Alice"

    def test_valid_names_keep_their_spec_identity(self, load, sandbox):
        case = load("backfill_overwrite", "buggy")
        catalog = read_catalog(sandbox, case.setup_sql)
        report = compile_proposal(case.hand_spec, case.source_model(), catalog, "t")
        assert report.renamed_actors == {}
        assert [a.name for a in report.spec.actors] == ["backfill", "user"]


def test_seed_violating_constraints_is_rejected_by_postgres(load, sandbox):
    case = load("mixed_version_overwrite", "buggy")
    data = case.hand_spec.model_dump()
    data["seed"][0]["values"][2]["value"] = "not-a-plan"
    reports, rejections = compile_all([TestProposal.model_validate(data)], case, sandbox, "t")
    assert not reports
    assert "seed rejected by PostgreSQL" in rejections[0].reasons[0]


def _output_for(case, tests=None):
    return json.dumps({"operations": [], "conflicts": [], "tests": tests if tests is not None else [
        case.hand_spec.model_dump()]})


def test_invalid_spec_gets_one_measured_repair_then_runs(settings, load, sandbox, runner, tmp_path):
    case = load("backfill_overwrite", "corrected")
    bad = case.hand_spec.model_dump()
    bad["actors"][0]["function"] = "nope.py:nope"
    client, responses = stub_client(settings, [_output_for(case, [bad]), _output_for(case)])
    run = run_ai(
        sandbox, runner, case, 100, 30, tmp_path, client, tmp_path / "c.json", reuse=False, allow_repair=True
    )
    assert [a.kind for a in run.analysis.attempts] == ["initial", "repair"]
    assert "unknown function" in responses.calls[1]["input"]
    assert run.tests_valid == 1 and run.schedules_executed == 84
    assert run.detected is False  # corrected code: a valid test must not produce a finding


def test_repair_is_bounded_and_failure_is_reported(settings, load, sandbox, runner, tmp_path):
    case = load("backfill_overwrite", "corrected")
    client, responses = stub_client(settings, ["garbage", "more garbage"])
    run = run_ai(
        sandbox, runner, case, 100, 30, tmp_path, client, tmp_path / "c.json", reuse=False, allow_repair=True
    )
    assert len(responses.calls) == 2
    assert run.tests_valid == 0 and run.schedules_executed == 0 and not run.detected


def test_model_invariant_cannot_create_a_verified_bug(settings, load, sandbox, runner, tmp_path):
    """A wrong hypothesis on correct code must not become a finding."""
    case = load("backfill_overwrite", "corrected")
    data = case.hand_spec.model_dump()
    data["invariants"] = [{
        "id": "wrong", "description": "name stays Alice", "kind": "column_equals", "table": "profiles",
        "key_column": "id", "key_value": 1, "column": "display_name", "expected": ["Alice"],
        "other_column": None,
    }]
    client, _ = stub_client(settings, [_output_for(case, [data])])
    run = run_ai(sandbox, runner, case, 100, 30, tmp_path, client, tmp_path / "c.json", reuse=False)
    assert run.detected is False
    report = run.runs[0].exploration.invariants[0]
    assert report.screened_out and report.failed_without_oracle_violation > 0
    assert report.failed_matching_oracle_violation == 0


def test_response_cache_avoids_a_second_call(settings, load, sandbox, runner, tmp_path):
    case = load_case(settings.scenarios_dir, "backfill_overwrite", "buggy")
    client, responses = stub_client(settings, [_output_for(case)])
    cache = tmp_path / "c.json"
    first = run_ai(sandbox, runner, case, 100, 30, tmp_path, client, cache, reuse=True)
    second = run_ai(sandbox, runner, case, 100, 30, tmp_path, client, cache, reuse=True)
    assert len(responses.calls) == 1
    assert first.detected and second.detected  # real execution both times, on real PostgreSQL


class TestSpendingControls:
    def test_default_client_refuses_every_request(self, settings):
        responses = StubResponses(["{}"])
        client = MetaClient(settings, client=types.SimpleNamespace(responses=responses))
        with pytest.raises(ModelBudgetExceeded, match="request limit"):
            client.generate("i", "p", "n", {"type": "object"})
        assert responses.calls == []

    def test_request_limit_is_a_hard_stop(self, settings):
        responses = StubResponses(["{}", "{}", "{}"])
        client = MetaClient(
            settings, client=types.SimpleNamespace(responses=responses), max_requests=2, max_estimated_usd=10
        )
        client.generate("i", "p", "n", {"type": "object"})
        client.generate("i", "p", "n", {"type": "object"})
        with pytest.raises(ModelBudgetExceeded):
            client.generate("i", "p", "n", {"type": "object"})
        assert len(responses.calls) == 2

    def test_spend_limit_refuses_a_request_that_could_overrun(self, settings):
        responses = StubResponses(["{}"])
        client = MetaClient(
            settings, client=types.SimpleNamespace(responses=responses), max_requests=5, max_estimated_usd=0.05
        )
        with pytest.raises(ModelBudgetExceeded, match="estimated limit"):
            client.generate("i", "p", "n", {"type": "object"})  # 24k output tokens could cost ~$0.10
        assert responses.calls == []

    def test_failed_request_still_counts(self, settings):
        class Failing:
            def create(self, **_):
                raise RuntimeError("network")

        client = MetaClient(
            settings, client=types.SimpleNamespace(responses=Failing()), max_requests=1, max_estimated_usd=1
        )
        with pytest.raises(RuntimeError):
            client.generate("i", "p", "n", {"type": "object"})
        with pytest.raises(ModelBudgetExceeded):
            client.generate("i", "p", "n", {"type": "object"})

    def test_sdk_retries_are_disabled(self, settings):
        import dataclasses

        client = MetaClient(dataclasses.replace(settings, model_api_key="not-a-real-key"))
        assert client._client.max_retries == 0

    def test_no_repair_unless_requested(self, settings, load, sandbox, runner, tmp_path):
        case = load("backfill_overwrite", "corrected")
        client, responses = stub_client(settings, ["garbage", "garbage"])
        run = run_ai(sandbox, runner, case, 100, 30, tmp_path, client, tmp_path / "c.json", reuse=False)
        assert len(responses.calls) == 1 and run.tests_valid == 0

    def test_cache_miss_without_client_is_an_error_not_a_call(self, load, sandbox, runner, tmp_path):
        case = load("backfill_overwrite", "corrected")
        run = run_ai(sandbox, runner, case, 100, 30, tmp_path, None, tmp_path / "absent.json")
        assert "NoRecordedResponse" in run.error and run.schedules_executed == 0

    def test_redact_removes_secrets(self, monkeypatch):
        monkeypatch.setenv("MODEL_API_KEY", "secret-key-value")
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:db-password@h:1/d")
        assert redact("bad secret-key-value and db-password") == "bad <redacted> and <redacted>"


def test_recorded_prompts_match_current_code_and_withhold_labels(settings, sandbox):
    """Saved responses answer exactly the prompt the current code builds for that case."""
    from pathlib import Path

    from deployspecter.analyzer import AnalysisRecord, prompt_identity

    paths = sorted(Path(settings.results_dir, "ai_responses", "v2").glob("*.json"))
    if not paths:
        pytest.skip("no recorded responses")
    for path in paths:
        scenario, variant, _ = path.stem.split("__")
        case = load_case(settings.scenarios_dir, scenario, variant)
        prompt = build_prompt(case.files, case.source_model(), read_schema(sandbox, case.setup_sql))
        record = AnalysisRecord.model_validate_json(path.read_text())
        assert record.prompt_sha256 == prompt_identity(prompt), path.name


def test_response_is_saved_even_if_validation_crashes(settings, load, sandbox, tmp_path, monkeypatch):
    import deployspecter.pipeline as pipeline

    case = load("backfill_overwrite", "corrected")
    client, _ = stub_client(settings, [_output_for(case)])
    monkeypatch.setattr(pipeline, "compile_all", lambda *_: (_ for _ in ()).throw(RuntimeError("crash")))
    cache = tmp_path / "c.json"
    with pytest.raises(RuntimeError):
        pipeline.obtain_analysis(client, case, sandbox, cache, reuse=False)
    assert json.loads(cache.read_text())["attempts"][0]["reply"]["input_tokens"] == 1000


@pytest.mark.live
def test_live_model_returns_a_valid_structured_analysis(settings, sandbox):
    case = load_case(settings.scenarios_dir, "backfill_overwrite", "corrected")
    prompt = build_prompt(case.files, case.source_model(), read_schema(sandbox, case.setup_sql))
    attempt = analyze(MetaClient(settings, max_requests=1, max_estimated_usd=0.25), prompt)
    assert attempt.reply.input_tokens and attempt.reply.output_tokens
    assert parse_output(attempt) is not None, attempt.parse_error
