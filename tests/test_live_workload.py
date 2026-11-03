"""The declarative workload format a future Muse Spark driver would produce. Everything here is offline."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from deployspecter.live.runner import ExplicitSchedule, LiveRunner, LiveScenario
from deployspecter.live.workload import (
    AppRegistry,
    ArgumentSpec,
    EntrypointSpec,
    WorkloadProposal,
    WorkloadRejected,
    compile_workload,
    source_entry,
    validate_workload,
)
from deployspecter.models import ColumnValue, SeedRow
from live_app import reservations as app

REGISTRY = AppRegistry(
    application="reservations",
    entrypoints={
        "reserve": EntrypointSpec(app.reserve_seats, {
            "event_id": ArgumentSpec("int", minimum=1, maximum=1000),
            "holder": ArgumentSpec("str", pattern=r"[a-z]{1,16}"),
            "qty": ArgumentSpec("int", minimum=1, maximum=100),
            "lock_row": ArgumentSpec("bool", required=False),
        }, frozenset({"v1", "v2"})),
        "sweep": EntrypointSpec(app.release_seats, {
            "holder": ArgumentSpec("str", choices=("alice", "bob")),
        }, frozenset({"v2"}), role="background_job"),
    },
    entities={"event": frozenset({"capacity", "reserved"})},
    sources={"app/reservations.py": source_entry("line\n" * 50)},
)


def proposal(**changes) -> dict:
    data = {
        "name": "two_buyers",
        "application": "reservations",
        "rationale": "Two buyers compete for the last seats.",
        "actors": [
            {"name": "a", "entrypoint": "reserve", "application_version": "v1", "arguments": [
                {"name": "event_id", "value": 1}, {"name": "holder", "value": "alice"}, {"name": "qty", "value": 2},
            ]},
            {"name": "b", "entrypoint": "sweep", "application_version": "v2", "role": "background_job", "arguments": [
                {"name": "holder", "value": "bob"},
            ]},
        ],
        "seed_goals": [{"entity": "event", "label": "almost_full", "fields": [
            {"field": "capacity", "op": "eq", "value": 3}, {"field": "reserved", "op": "ge", "value": 0},
        ]}],
        "constraints": [{"path": "app/reservations.py", "start_line": 2, "end_line": 9, "claim": "reads then writes"}],
    }
    data.update(changes)
    return data


def reasons(**changes) -> str:
    with pytest.raises(WorkloadRejected) as exc:
        validate_workload(WorkloadProposal.model_validate(proposal(**changes)), REGISTRY)
    return " | ".join(exc.value.reasons)


def with_actor(index: int, **changes) -> dict:
    data = proposal()
    data["actors"][index] = {**data["actors"][index], **changes}
    return data


class TestShape:
    def test_valid_proposal_is_accepted(self):
        validate_workload(WorkloadProposal.model_validate(proposal()), REGISTRY)

    @pytest.mark.parametrize("field", ["sql", "code", "python", "query", "setup_sql", "schedule"])
    def test_no_extra_field_can_carry_code(self, field):
        with pytest.raises(ValidationError):
            WorkloadProposal.model_validate({**proposal(), field: "DROP TABLE events"})

    def test_actor_has_no_field_for_code(self):
        with pytest.raises(ValidationError):
            WorkloadProposal.model_validate(with_actor(0, sql="SELECT 1"))

    def test_schema_has_no_sql_or_code_property(self):
        def properties(node):
            if isinstance(node, dict):
                yield from node.get("properties", {})
                for value in node.values():
                    yield from properties(value)
            elif isinstance(node, list):
                for value in node:
                    yield from properties(value)

        names = set(properties(WorkloadProposal.model_json_schema()))
        assert names and not names & {"sql", "code", "python", "query", "statement", "script", "expression"}

    @pytest.mark.parametrize("bad", ["reserve(); drop", "a b", "'; DROP TABLE x; --", ""])
    def test_entrypoint_must_be_an_identifier(self, bad):
        with pytest.raises(ValidationError):
            WorkloadProposal.model_validate(with_actor(0, entrypoint=bad))

    def test_argument_values_must_be_scalars(self):
        data = with_actor(0, arguments=[{"name": "qty", "value": {"$eval": "x"}}])
        with pytest.raises(ValidationError):
            WorkloadProposal.model_validate(data)

    def test_needs_at_least_two_actors(self):
        with pytest.raises(ValidationError):
            WorkloadProposal.model_validate({**proposal(), "actors": proposal()["actors"][:1]})

    def test_round_trips_through_json(self):
        parsed = WorkloadProposal.model_validate(proposal())
        assert WorkloadProposal.model_validate_json(json.dumps(proposal())) == parsed


class TestAgainstRegistration:
    def _rejected(self, data) -> str:
        with pytest.raises(WorkloadRejected) as exc:
            validate_workload(WorkloadProposal.model_validate(data), REGISTRY)
        return " | ".join(exc.value.reasons)

    def test_unregistered_application(self):
        assert "not 'reservations'" in reasons(application="other")

    @pytest.mark.parametrize("name", ["delete_everything", "os.system"])
    def test_unregistered_entrypoint(self, name):
        assert "not registered" in self._rejected(with_actor(0, entrypoint=name))

    def test_unregistered_version(self):
        assert "version 'v9'" in self._rejected(with_actor(0, application_version="v9"))

    def test_job_selection_must_match_the_registration(self):
        assert "registered as request" in self._rejected(with_actor(0, role="background_job"))

    @pytest.mark.parametrize("arguments,expected", [
        ([{"name": "event_id", "value": 1}, {"name": "holder", "value": "alice"}], "missing argument 'qty'"),
        ([{"name": "event_id", "value": "1"}, {"name": "holder", "value": "a"}, {"name": "qty", "value": 1}],
         "expected int"),
        ([{"name": "event_id", "value": True}, {"name": "holder", "value": "a"}, {"name": "qty", "value": 1}],
         "expected int"),
        ([{"name": "event_id", "value": 0}, {"name": "holder", "value": "a"}, {"name": "qty", "value": 1}],
         "below minimum"),
        ([{"name": "event_id", "value": 1}, {"name": "holder", "value": "a"}, {"name": "qty", "value": 101}],
         "above maximum"),
        ([{"name": "event_id", "value": 1}, {"name": "holder", "value": "x'; DROP TABLE events; --"},
          {"name": "qty", "value": 1}], "registered pattern"),
        ([{"name": "event_id", "value": 1}, {"name": "holder", "value": "a"}, {"name": "qty", "value": 1},
          {"name": "extra", "value": 1}], "unknown argument"),
        ([{"name": "event_id", "value": 1}, {"name": "event_id", "value": 2}, {"name": "holder", "value": "a"},
          {"name": "qty", "value": 1}], "repeated argument"),
    ])
    def test_argument_checks(self, arguments, expected):
        assert expected in self._rejected(with_actor(0, arguments=arguments))

    def test_string_with_no_declared_pattern_is_refused(self):
        registry = AppRegistry("reservations", {"reserve": EntrypointSpec(app.reserve_seats, {
            "holder": ArgumentSpec("str"),
        }, frozenset({"v1"}))}, {})
        data = proposal(seed_goals=[], constraints=[])
        data["actors"] = [
            {"name": n, "entrypoint": "reserve", "application_version": "v1",
             "arguments": [{"name": "holder", "value": "anything"}]}
            for n in "ab"
        ]
        with pytest.raises(WorkloadRejected, match="registered pattern"):
            validate_workload(WorkloadProposal.model_validate(data), registry)

    def test_choice_argument(self):
        data = with_actor(1, arguments=[{"name": "holder", "value": "mallory"}])
        assert "registered choices" in self._rejected(data)

    def test_seed_goals_only_use_registered_entities_and_fields(self):
        assert "unknown entity" in reasons(seed_goals=[{"entity": "users", "label": "x"}])
        assert "unknown field" in reasons(seed_goals=[
            {"entity": "event", "label": "x", "fields": [{"field": "password", "op": "eq", "value": 1}]},
        ])

    def test_seed_goals_cannot_carry_sql(self):
        with pytest.raises(ValidationError):
            WorkloadProposal.model_validate(proposal(seed_goals=[{"entity": "event", "label": "x", "sql": "INSERT"}]))
        with pytest.raises(ValidationError):
            WorkloadProposal.model_validate(proposal(seed_goals=[{"entity": "event", "label": "x", "fields": [
                {"field": "capacity", "op": "; DROP", "value": 1},
            ]}]))

    @pytest.mark.parametrize("constraint,expected", [
        ({"path": "other.py", "start_line": 1, "end_line": 2, "claim": "x"}, "not a registered source"),
        ({"path": "app/reservations.py", "start_line": 40, "end_line": 51, "claim": "x"}, "lines 40-51"),
        ({"path": "app/reservations.py", "start_line": 9, "end_line": 2, "claim": "x"}, "lines 9-2"),
    ])
    def test_constraints_must_point_at_real_lines(self, constraint, expected):
        assert expected in reasons(constraints=[constraint])

    def test_every_reason_is_reported(self):
        data = with_actor(0, entrypoint="nope", application_version="v1")
        data["application"] = "other"
        data["seed_goals"] = [{"entity": "users", "label": "x"}]
        message = self._rejected(data)
        assert all(part in message for part in ("not 'reservations'", "not registered", "unknown entity"))


class TestExecution:
    def test_compiled_workload_runs_the_registered_function(self, sandbox):
        registry = AppRegistry("reservations", {"reserve": REGISTRY.entrypoints["reserve"]}, REGISTRY.entities)
        data = proposal(constraints=[])
        data["actors"] = [
            {"name": n, "entrypoint": "reserve", "application_version": "v1", "arguments": [
                {"name": "event_id", "value": 1}, {"name": "holder", "value": h}, {"name": "qty", "value": 2},
            ]}
            for n, h in (("a", "alice"), ("b", "bob"))
        ]
        actors, goals = compile_workload(WorkloadProposal.model_validate(data), registry)
        assert goals[0].label == "almost_full", "seed goals stay unresolved for a constructor"
        assert all(a.entrypoint is app.reserve_seats for a in actors)
        seed = [SeedRow(table="events", values=[
            ColumnValue(column="id", value=1), ColumnValue(column="capacity", value=3),
            ColumnValue(column="reserved", value=0),
        ])]
        scen = LiveScenario("from_workload", app.SETUP_SQL, seed, actors)
        trace = LiveRunner(sandbox).run(scen, ExplicitSchedule(["a"] * 5 + ["b"] * 4))
        assert trace.outcome == "completed"
        assert trace.results()["a"]["status"] == "reserved" and trace.results()["b"]["status"] == "rejected"

    def test_module_cannot_reach_a_model(self):
        from deployspecter.live import workload

        text = open(workload.__file__).read()
        assert "MetaClient" not in text and "OpenAI" not in text and "eval(" not in text and "exec(" not in text
