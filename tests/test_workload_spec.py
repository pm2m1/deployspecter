"""Offline tests of the version 2 workload specification. No database, network, model or external application."""

from __future__ import annotations

import ast
import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from deployspecter.live import workload_spec as ws
from deployspecter.live.workload import WorkloadProposal

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests/live_app/workload_spec_fixtures.json").read_text())["fixtures"]
SCHEMA_FILE = ROOT / "docs/research/live_stepping/workload/workload-spec-v2.candidate.schema.json"
HAND = ws.Provenance(origin="hand_written", author="fixture_author")


def by_id(name: str) -> dict:
    return next(f for f in FIXTURES if f["id"] == name)["document"]


def fixtures(expect: str) -> list[dict]:
    return [f for f in FIXTURES if f["expect"] == expect]


# ------------------------------------------------------------------ fixtures


class TestFixtures:
    def test_every_fixture_is_labelled_hand_written(self):
        for fixture in FIXTURES:
            assert fixture["label"] == "hand-written synthetic fixture", fixture["id"]
            provenance = fixture["document"].get("provenance", {}) if isinstance(fixture["document"], dict) else {}
            assert provenance.get("origin") != "model_proposal", fixture["id"]

    def test_no_fixture_mentions_a_model_as_its_source(self):
        text = json.dumps(FIXTURES).lower()
        assert "muse" not in text

    def test_ids_are_unique_and_every_category_is_present(self):
        ids = [f["id"] for f in FIXTURES]
        assert len(set(ids)) == len(ids)
        assert {f["expect"] for f in FIXTURES} == {"valid", "structurally_valid", "invalid", "unsupported_version"}

    @pytest.mark.parametrize("fixture", fixtures("valid") + fixtures("structurally_valid"), ids=lambda f: f["id"])
    def test_accepted(self, fixture):
        spec = ws.parse_workload(fixture["document"])
        assert spec.schema_version == 2

    @pytest.mark.parametrize("fixture", fixtures("invalid"), ids=lambda f: f["id"])
    def test_rejected_with_a_reason(self, fixture):
        with pytest.raises(ws.SpecRejected) as caught:
            ws.parse_workload(fixture["document"])
        assert fixture["reason_contains"].lower() in " ".join(caught.value.reasons).lower(), caught.value.reasons

    @pytest.mark.parametrize("fixture", fixtures("unsupported_version"), ids=lambda f: f["id"])
    def test_unsupported_version(self, fixture):
        with pytest.raises(ws.UnsupportedSchemaVersion):
            ws.parse_workload(fixture["document"])


# ------------------------------------------------------------------ parsing, serialization, size


class TestParsing:
    def test_round_trip_through_json(self):
        spec = ws.parse_workload(by_id("valid_typed_arguments"))
        again = ws.parse_workload_json(spec.model_dump_json())
        assert again == spec

    def test_typed_values_keep_their_types(self):
        spec = ws.parse_workload(by_id("valid_typed_arguments"))
        values = {a.name: ws.plain_value(a.value) for a in spec.actors[0].arguments}
        assert values == {"event_id": 1, "price": 12.5, "lock_row": True, "holder": "alice", "coupon": None}
        assert type(values["event_id"]) is int and type(values["lock_row"]) is bool

    @pytest.mark.parametrize("text", ['{"schema_version": 2, "x": NaN}', '{"schema_version": 2, "x": Infinity}'])
    def test_non_finite_numbers_are_refused(self, text):
        with pytest.raises(ws.SpecRejected, match="isn't allowed"):
            ws.parse_workload_json(text)

    def test_non_finite_float_value_is_refused_in_python_input(self):
        document = json.loads(json.dumps(by_id("valid_typed_arguments")))
        document["actors"][0]["arguments"][1]["value"]["value"] = float("inf")
        with pytest.raises(ws.SpecRejected):
            ws.parse_workload(document)

    def test_document_size_is_bounded(self):
        document = by_id("valid_simple") | {
            "rationale": "r" * 800,
            "source_refs": [{"path": "a/" + "p" * 190, "start_line": 1, "end_line": 2, "claim": "c" * 400}] * 24,
            "state_goals": [
                {"entity": "event", "label": f"g{i}", "count": 1, "constraints": [
                    {"field": f"field_{j}", "op": "eq", "value": {"type": "str", "value": "v" * 128}}
                    for j in range(16)
                ]}
                for i in range(12)
            ],
        }
        with pytest.raises(ws.SpecRejected, match="bytes"):
            ws.parse_workload(document)

    def test_size_is_measured_the_same_however_the_text_was_written(self):
        document = json.loads(json.dumps(by_id("valid_simple")))
        document["rationale"] = "é" * 700
        escaped, raw = json.dumps(document), json.dumps(document, ensure_ascii=False, indent=4)
        assert len(escaped.encode()) != len(raw.encode())
        assert ws.document_size(json.loads(escaped)) == ws.document_size(json.loads(raw))
        assert ws.document_size(document) == len(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def test_size_counts_utf8_bytes_not_characters(self):
        empty = json.loads(json.dumps(by_id("valid_simple"))) | {"rationale": ""}
        filled = empty | {"rationale": "€" * 400}  # 400 characters, 1,200 bytes
        assert ws.document_size(filled) - ws.document_size(empty) == 1200

    def test_float_precision_canonicalizes_to_the_shortest_round_trip_form(self):
        texts = ['0.1', '0.10000000000000001', '1e16', '1E16', '10000000000000000.0']
        forms = []
        for text in texts:
            document = json.dumps(by_id("argument_float_smallest_subnormal")).replace("5e-324", text)
            forms.append(ws.canonical_json(ws.parse_workload_json(document)).split('"x"')[1])
        assert forms[0] == forms[1] and forms[2] == forms[3] == forms[4]

    def test_not_an_object(self):
        with pytest.raises(ws.SpecRejected, match="object"):
            ws.parse_workload(["schema_version", 2])

    @pytest.mark.parametrize("text", [
        '{"schema_version": 2, "schema_version": 1}',
        '{"schema_version": 2, "name": "a", "provenance": {"origin": "hand_written", "origin": "synthetic"}}',
    ])
    def test_repeated_keys_are_refused(self, text):
        with pytest.raises(ws.SpecRejected, match="repeated key"):
            ws.parse_workload_json(text)

    def test_invalid_json_text(self):
        with pytest.raises(ws.SpecRejected, match="not valid JSON"):
            ws.parse_workload_json("{schema_version: 2")

    def test_every_problem_is_listed(self):
        document = by_id("valid_simple") | {"application": "Bad.App", "name": "Bad Name"}
        with pytest.raises(ws.SpecRejected) as caught:
            ws.parse_workload(document)
        reasons = " ".join(caught.value.reasons)
        assert "application" in reasons and "name" in reasons


# ------------------------------------------------------------------ canonical form


class TestCanonicalForm:
    def test_digest_is_pinned_for_a_fixed_fixture(self):
        """Changing the canonical form changes every digest, so it must be a deliberate, reviewed change."""
        spec = ws.parse_workload(by_id("valid_state_goals_and_sources"))
        assert ws.digest(spec) == PINNED_DIGEST

    def test_order_without_meaning_does_not_change_the_digest(self):
        document = by_id("valid_typed_arguments")
        base = ws.digest(ws.parse_workload(document))
        args = document["actors"][0]["arguments"]
        for order in itertools.islice(itertools.permutations(args), 12):
            changed = json.loads(json.dumps(document))
            changed["actors"][0]["arguments"] = list(order)
            changed["actors"].reverse()
            assert ws.digest(ws.parse_workload(changed)) == base

    def test_a_meaningful_change_changes_the_digest(self):
        document = by_id("valid_old_and_new_versions")
        changed = json.loads(json.dumps(document))
        changed["actors"][0]["application_version"] = "v2"
        assert ws.digest(ws.parse_workload(document)) != ws.digest(ws.parse_workload(changed))

    def test_canonical_json_is_compact_sorted_and_parses_back(self):
        spec = ws.parse_workload(by_id("valid_state_goals_and_sources"))
        text = ws.canonical_json(spec)
        assert ": " not in text and ", " not in text.replace(", then", "")
        assert ws.parse_workload_json(text) == ws.parse_workload(json.loads(text))
        assert ws.canonical_json(ws.parse_workload_json(text)) == text

    def test_unicode_is_kept_as_text(self):
        document = json.loads(json.dumps(by_id("valid_simple")))
        document["actors"][0]["arguments"][0]["value"]["value"] = "zoë"
        assert "zoë" in ws.canonical_json(ws.parse_workload(document))

    @settings(max_examples=60, deadline=None)
    @given(
        names=st.lists(st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True), min_size=2, max_size=4, unique=True),
        values=st.lists(st.one_of(st.integers(-10**6, 10**6), st.booleans(), st.none(),
                                  st.text(alphabet="abcxyz019 _-'", max_size=20),
                                  st.floats(allow_nan=False, allow_infinity=False, width=32)
                                  .filter(lambda f: not (f == 0.0 and str(f).startswith("-")))),
                        min_size=0, max_size=6),
    )
    def test_any_valid_spec_round_trips_with_a_stable_digest(self, names, values):
        actors = [
            {"name": f"A{name}", "entrypoint": name, "application_version": "v1", "role": "request",
             "arguments": [{"name": f"p{i}", "value": ws._typed(v)} for i, v in enumerate(values)]}
            for name in names
        ]
        spec = ws.parse_workload({"schema_version": 2, "name": "prop", "application": "app",
                                  "provenance": HAND.model_dump(), "actors": actors})
        again = ws.parse_workload_json(ws.canonical_json(spec))
        assert ws.digest(again) == ws.digest(spec)


PINNED_DIGEST = "034f084437db6943c4b5a35032272909851a92d28f5526fa32f232f05922e680"


# ------------------------------------------------------------------ provenance


class TestProvenance:
    def test_actor_and_version_identity_survive_canonicalization(self):
        spec = ws.parse_workload(by_id("valid_old_and_new_versions"))
        actors = {a["name"]: (a["entrypoint"], a["application_version"], a["role"])
                  for a in ws.canonical(spec)["actors"]}
        assert actors == {
            "old_app": ("reserve_seats", "v1", "request"), "new_app": ("reserve_seats", "v2", "request"),
            "backfill": ("backfill_reservations", "v2", "background_job"),
        }

    def test_origin_is_recorded_and_grants_nothing(self):
        spec = ws.parse_workload(by_id("valid_simple"))
        assert spec.provenance.origin == "hand_written"
        assert set(ws.Provenance.model_fields) == {"origin", "author", "generator_version", "note"}

    def test_origin_has_a_fixed_set_of_values(self):
        allowed = ws.Provenance.model_json_schema()["properties"]["origin"]["enum"]
        assert allowed == ["hand_written", "synthetic", "deterministic_baseline", "model_proposal"]


# ------------------------------------------------------------------ executable payloads


class TestNoExecutablePayload:
    def test_the_schema_has_no_field_for_code_sql_or_commands(self):
        def properties(node):
            if isinstance(node, dict):
                yield from node.get("properties", {})
                for value in node.values():
                    yield from properties(value)
            elif isinstance(node, list):
                for value in node:
                    yield from properties(value)

        names = set(properties(ws.json_schema()))
        assert names
        forbidden = {"sql", "code", "python", "query", "statement", "script", "command", "shell", "exec", "eval",
                     "function", "callable", "module", "import", "seed_rows", "requirement", "oracle", "schedule"}
        assert not names & forbidden, names & forbidden

    def test_value_types_are_closed(self):
        types = {json.dumps(v) for v in [{"type": t} for t in ("int", "float", "bool", "str", "null")]}
        assert len(types) == 5
        with pytest.raises(ws.SpecRejected):
            ws.parse_workload(by_id("argument_code_type"))

    def test_sql_text_in_an_argument_is_kept_as_inert_data(self):
        spec = ws.parse_workload(by_id("sql_text_as_a_string_argument"))
        value = ws.plain_value(spec.actors[0].arguments[0].value)
        assert value == "x'; DROP TABLE events; --"
        assert json.dumps(ws.canonical(spec)).count("DROP TABLE") == 1, "it appears only as that argument's value"

    def test_module_has_no_code_path_that_could_execute_text(self):
        tree = ast.parse(Path(ws.__file__).read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert not {c.func.id for c in calls} & {"eval", "exec", "compile", "__import__", "open", "setattr"}
        for call in (c for c in calls if c.func.id == "getattr"):  # only fixed attribute names, never input text
            assert isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "psycopg", "builtins"}


# ------------------------------------------------------------------ versions and compatibility


V1 = {
    "schema_version": 1, "name": "two_buyers", "application": "reservations", "rationale": "two buyers",
    "actors": [
        {"name": "a", "entrypoint": "reserve", "application_version": "v1",
         "arguments": [{"name": "event_id", "value": 1}, {"name": "holder", "value": "alice"},
                       {"name": "lock_row", "value": False}, {"name": "note", "value": None}]},
        {"name": "b", "entrypoint": "release", "application_version": "v2", "role": "background_job",
         "arguments": [{"name": "holder", "value": "bob"}]},
    ],
    "seed_goals": [{"entity": "event", "label": "full", "fields": [
        {"field": "capacity", "op": "eq", "value": 3}, {"field": "note", "op": "is_null"}]}],
    "constraints": [{"path": "app/reservations.py", "start_line": 2, "end_line": 9, "claim": "reads then writes"}],
}


class TestVersionOneCompatibility:
    def test_upgrade_keeps_meaning_and_infers_types_from_json(self):
        spec = ws.parse_workload(V1, v1_provenance=HAND)
        a, b = spec.actors
        assert [(x.name, ws.plain_value(x.value), x.value.type) for x in a.arguments] == [
            ("event_id", 1, "int"), ("holder", "alice", "str"), ("lock_row", False, "bool"), ("note", None, "null"),
        ]
        assert (b.role, b.application_version, a.role) == ("background_job", "v2", "request")
        assert spec.state_goals[0].constraints[1].value is None
        assert spec.source_refs[0].claim == "reads then writes"
        assert spec.provenance == HAND

    def test_upgrade_needs_stated_provenance(self):
        with pytest.raises(ws.SpecRejected, match="provenance"):
            ws.parse_workload(V1)

    def test_identifier_version_2_cannot_express_is_refused_not_renamed(self):
        document = json.loads(json.dumps(V1))
        document["actors"][0]["entrypoint"] = "reserve.seats"
        assert WorkloadProposal.model_validate(document)  # valid in version 1
        with pytest.raises(ws.SpecRejected, match="without changing its meaning"):
            ws.parse_workload(document, v1_provenance=HAND)

    def test_version_1_without_an_explicit_version_is_refused(self):
        document = {k: v for k, v in V1.items() if k != "schema_version"}
        with pytest.raises(ws.UnsupportedSchemaVersion, match="explicitly"):
            ws.parse_workload(document, v1_provenance=HAND)

    def test_version_1_rules_still_apply_before_upgrade(self):
        document = json.loads(json.dumps(V1)) | {"sql": "DROP TABLE events"}
        with pytest.raises(ws.SpecRejected):
            ws.parse_workload(document, v1_provenance=HAND)

    def test_version_1_module_is_unchanged(self):
        done = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "src/deployspecter/live/workload.py"], cwd=ROOT)
        assert done.returncode == 0


# ------------------------------------------------------------------ independence from oracles, and the schema file


class TestIndependenceAndSchema:
    def test_validation_imports_no_oracle_requirement_or_engine_scoring(self):
        code = ("import sys, deployspecter.live.workload_spec;"
                "bad = [n for n in sys.modules if n.endswith(('oracle', 'properties', 'supervisor', 'serverlog'))"
                " or n.startswith(('live_app', 'langgraph'))];"
                "print(bad)")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "[]"

    def test_parse_takes_only_the_document(self):
        import inspect

        params = list(inspect.signature(ws.parse_workload).parameters)
        assert params == ["document", "v1_provenance"]

    def test_state_goals_are_not_seed_rows_or_requirements(self):
        assert set(ws.StateGoal.model_fields) == {"entity", "label", "count", "constraints"}

    def test_candidate_schema_file_matches_the_model(self):
        assert json.loads(SCHEMA_FILE.read_text()) == json.loads(json.dumps(ws.json_schema()))

    def test_schema_states_its_version_and_status(self):
        schema = ws.json_schema()
        assert schema["$id"] == "urn:deployspecter:workload-spec:2" and "candidate" in schema["x-status"]
        assert schema["properties"]["schema_version"] == {"const": 2, "title": "Schema Version", "type": "integer"}


# ------------------------------------------------------------------ JSON Schema and Python parser agree


def _schema_valid(instance, node, root):
    """A small validator for the JSON Schema keywords pydantic emits here. Unknown keywords fail loudly."""
    if "$ref" in node:
        return _schema_valid(instance, root["$defs"][node["$ref"].rsplit("/", 1)[1]], root)
    known = {"$ref", "type", "const", "enum", "pattern", "minLength", "maxLength", "minimum", "maximum",
             "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "items", "properties", "required",
             "additionalProperties", "anyOf", "oneOf", "discriminator", "default", "title", "description",
             "$schema", "$id", "$defs", "x-status", "x-not-enforced-by-schema"}  # the last five only annotate
    assert set(node) <= known, set(node) - known
    if "anyOf" in node and not any(_schema_valid(instance, n, root) for n in node["anyOf"]):
        return False
    if "oneOf" in node and sum(_schema_valid(instance, n, root) for n in node["oneOf"]) != 1:
        return False
    kind = node.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str), "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float), "boolean": lambda v: type(v) is bool,
        "null": lambda v: v is None,
    }
    if kind is not None and not checks[kind](instance):
        return False
    if "const" in node and not (type(instance) is type(node["const"]) and instance == node["const"]):
        return False
    if "enum" in node and instance not in node["enum"]:
        return False
    if isinstance(instance, str):
        import re

        if "pattern" in node and not re.search(node["pattern"], instance):
            return False
        if len(instance) < node.get("minLength", 0) or len(instance) > node.get("maxLength", 10**9):
            return False
    if type(instance) in (int, float):
        if "minimum" in node and instance < node["minimum"]:
            return False
        if "maximum" in node and instance > node["maximum"]:
            return False
        if "exclusiveMaximum" in node and instance >= node["exclusiveMaximum"]:
            return False
        if "exclusiveMinimum" in node and instance <= node["exclusiveMinimum"]:
            return False
    if isinstance(instance, list):
        if len(instance) < node.get("minItems", 0) or len(instance) > node.get("maxItems", 10**9):
            return False
        if "items" in node and not all(_schema_valid(v, node["items"], root) for v in instance):
            return False
    if isinstance(instance, dict) and kind == "object":
        properties = node.get("properties", {})
        if not set(node.get("required", [])) <= set(instance):
            return False
        if node.get("additionalProperties") is False and not set(instance) <= set(properties):
            return False
        if not all(_schema_valid(v, properties[k], root) for k, v in instance.items() if k in properties):
            return False
    return True


class TestSchemaParserAgreement:
    SCHEMA = ws.json_schema()

    @pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
    def test_every_fixture(self, fixture):
        schema_ok = _schema_valid(fixture["document"], self.SCHEMA, self.SCHEMA)
        try:
            ws.parse_workload(fixture["document"])
            python_ok = True
        except (ws.SpecRejected, ws.UnsupportedSchemaVersion):
            python_ok = False
        if python_ok:
            assert schema_ok, "the schema refuses a document the parser accepts"
        elif fixture["schema_enforces"]:
            assert not schema_ok, "the schema accepts a document the parser refuses, by a rule it should express"
        else:
            assert schema_ok, "a Python-only rule; the schema says so in x-not-enforced-by-schema"

    def test_python_only_rules_are_declared_in_the_schema(self):
        declared = " ".join(self.SCHEMA["x-not-enforced-by-schema"])
        for phrase in ("unique actor names", "is_null", "end_line", "64 rows", "fraction or exponent",
                       "negative zero", "repeated keys", "bytes"):
            assert phrase in declared, phrase
