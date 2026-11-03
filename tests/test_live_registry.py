"""Offline tests of the entrypoint registry. Hand-written synthetic registrations only; nothing is imported or run."""

from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from deployspecter.live import registry as rg
from deployspecter.live import workload_spec as ws

ROOT = Path(__file__).resolve().parents[1]
NEVER_IMPORTED = "never_imported_synthetic_module"

# Hand-written synthetic registry configuration. The operation references point at modules that don't exist, which is
# deliberate: if anything tried to import them, the tests would see it.
CONFIG = {
    "registry_version": 1,
    "application": "reservations",
    "registered_by": {"registrar": "harness_owner", "source": "harness_config", "config_label": "synthetic_v1"},
    "min_actors": 2,
    "max_actors": 3,
    "entrypoints": [
        {"id": "reserve_seats", "operation": f"{NEVER_IMPORTED}.reservations:reserve_seats",
         "versions": ["v1", "v2"], "roles": ["request"], "max_instances": 3,
         "arguments": [{"name": "event_id", "types": ["int"]}, {"name": "holder", "types": ["str"]},
                       {"name": "qty", "types": ["int"]}, {"name": "lock_row", "types": ["bool"], "required": False},
                       {"name": "coupon", "types": ["str", "null"], "required": False}]},
        {"id": "release_seats", "operation": f"{NEVER_IMPORTED}.reservations:release_seats",
         "versions": ["v2"], "roles": ["request", "background_job"], "max_instances": 1,
         "arguments": [{"name": "holder", "types": ["str"]}]},
    ],
}


def copy(value):
    return json.loads(json.dumps(value))


def trusted_for(config, registrar="harness_owner", label="synthetic_v1"):
    """What harness code would pin for this configuration. Invalid configurations get a digest they can't match."""
    try:
        digest = rg.registry_digest(rg.RegistryConfig.model_validate(config))
    except ValueError:
        digest = "0" * 64
    return rg.TrustedRegistration(registrar=registrar, config_label=label, pinned_digest=digest)


def build(config):
    return rg.build_registry(config, trusted=trusted_for(config))


def registry(config=None):
    config = copy(config or CONFIG)
    return build(config)


def actor(name, entrypoint="reserve_seats", version="v1", role="request", **args):
    defaults = {"event_id": ("int", 1), "holder": ("str", name), "qty": ("int", 2)} if entrypoint == "reserve_seats" \
        else {"holder": ("str", name)}
    defaults.update(args)
    arguments = [{"name": k, "value": {"type": t} if t == "null" else {"type": t, "value": v}}
                 for k, (t, v) in defaults.items() if t is not None]
    return {"name": name, "entrypoint": entrypoint, "application_version": version, "role": role,
            "arguments": arguments}


def spec(*actors, application="reservations", origin="hand_written", author="fixture_author", **extra):
    return ws.parse_workload({
        "schema_version": 2, "name": "fixture", "application": application,
        "provenance": {"origin": origin, "author": author}, "actors": list(actors) or [actor("a"), actor("b")],
        **extra,
    })


def codes(result):
    return [r.code for r in result.rejections]


# ------------------------------------------------------------------ registration


class TestRegistration:
    def test_valid_configuration_builds(self):
        built = registry()
        assert built.application == "reservations" and built.ids() == ["release_seats", "reserve_seats"]
        assert built.registered_by.registrar == "harness_owner" and built.registered_by.source == "harness_config"

    def test_lookup_is_exact(self):
        built = registry()
        assert built.lookup("reserve_seats").operation.endswith(":reserve_seats")
        for near in ("Reserve_Seats", "reserve_seat", "reserve_seats ", "reserve", ""):
            assert built.lookup(near) is None

    def test_digest_is_deterministic_and_order_insensitive(self):
        reordered = copy(CONFIG)
        reordered["entrypoints"].reverse()
        reordered["entrypoints"][1]["versions"].reverse()
        assert registry().digest == registry(reordered).digest == registry().digest

    def test_pinned_digest_is_required_and_must_match(self):
        trusted = trusted_for(CONFIG)
        assert rg.build_registry(copy(CONFIG), trusted=trusted).digest == trusted.pinned_digest
        changed = copy(CONFIG)
        changed["entrypoints"][0]["max_instances"] = 2
        with pytest.raises(rg.RegistryError, match="pinned"):
            rg.build_registry(changed, trusted=trusted)

    def test_a_trusted_registration_object_is_required(self):
        with pytest.raises(TypeError):
            rg.build_registry(copy(CONFIG))  # no keyword at all
        for fake in (None, {"registrar": "harness_owner", "config_label": "synthetic_v1",
                            "pinned_digest": trusted_for(CONFIG).pinned_digest}):
            with pytest.raises(rg.RegistryError, match="TrustedRegistration"):
                rg.build_registry(copy(CONFIG), trusted=fake)

    def test_registered_by_must_agree_with_the_trusted_registration(self):
        trusted = trusted_for(CONFIG, registrar="real_owner")
        with pytest.raises(rg.RegistryError, match="registered_by"):
            rg.build_registry(copy(CONFIG), trusted=trusted)

    def test_provenance_comes_from_the_trusted_object(self):
        built = registry()
        assert built.registered_by == rg.RegisteredBy(registrar="harness_owner", source="harness_config",
                                                      config_label="synthetic_v1")
        assert built.trusted.pinned_digest == built.digest

    def test_pinned_digest_must_look_like_a_digest(self):
        with pytest.raises(ValueError):
            rg.TrustedRegistration(registrar="harness_owner", config_label="synthetic_v1", pinned_digest="abc")

    @pytest.mark.parametrize("mutate,reason", [
        (lambda c: c["entrypoints"].append(copy(c["entrypoints"][0])), "repeated entrypoint id"),
        (lambda c: c["entrypoints"].append(dict(copy(c["entrypoints"][0]), id="reserve_again")), "ambiguous"),
        (lambda c: c["entrypoints"][0]["versions"].append("v1"), "repeated version"),
        (lambda c: c["entrypoints"][0]["roles"].append("request"), "repeated role"),
        (lambda c: c["entrypoints"][0]["arguments"].append({"name": "qty", "types": ["int"]}), "repeated argument"),
        (lambda c: c["entrypoints"][0]["arguments"][0].update(types=["int", "int"]), "repeated type"),
        (lambda c: c["entrypoints"][0]["arguments"][0].update(types=["null"]), "may only be null"),
        (lambda c: c["entrypoints"][0]["arguments"][0].update(types=["object"]), "types"),
        (lambda c: c["entrypoints"][0].update(max_instances=0), "max_instances"),
        (lambda c: c["entrypoints"][0].update(max_instances=5), "max_instances"),
        (lambda c: c.update(min_actors=3, max_actors=2), "greater than"),
        (lambda c: c.update(max_actors=5), "max_actors"),
        (lambda c: c.update(min_actors=1), "min_actors"),
        (lambda c: c.update(entrypoints=[]), "entrypoints"),
        (lambda c: c["entrypoints"][0].update(versions=[]), "versions"),
        (lambda c: c["entrypoints"][0].update(roles=["admin"]), "roles"),
        (lambda c: c["entrypoints"][0].update(id="Reserve.Seats"), "pattern"),
    ])
    def test_invalid_registrations(self, mutate, reason):
        config = copy(CONFIG)
        mutate(config)
        with pytest.raises(rg.RegistryError) as caught:
            build(config)
        assert reason in " ".join(caught.value.reasons)

    @pytest.mark.parametrize("operation", [
        "os:system", "os.system", "__import__('os')", "mod:fn; rm -rf /", "mod:fn()", "Mod:Fn", "../mod:fn",
        "mod:", ":fn", "mod.fn",
    ])
    def test_operation_references_are_plain_module_and_function_names(self, operation):
        config = copy(CONFIG)
        config["entrypoints"][0]["operation"] = operation
        if operation == "os:system":
            assert registry(config).lookup("reserve_seats").operation == "os:system", "valid text, never imported"
            return
        with pytest.raises(rg.RegistryError):
            build(config)


class TestForgedMetadata:
    @pytest.mark.parametrize("mutate,reason", [
        (lambda c: c["registered_by"].update(source="model_proposal"), "source"),
        (lambda c: c["registered_by"].update(source="workload_spec"), "source"),
        (lambda c: c.pop("registered_by"), "registered_by"),
        (lambda c: c["registered_by"].update(trusted=True), "extra"),
        (lambda c: c.update(config_digest="0" * 64), "extra"),
        (lambda c: c.update(registry_version=2), "registry_version"),
        (lambda c: c.update(registry_version=True), "registry_version"),
        (lambda c: c["entrypoints"][0].update(execute="DROP TABLE events"), "extra"),
        (lambda c: c["entrypoints"][0].update(max_instances=True), "max_instances"),
        (lambda c: c["entrypoints"][0].update(max_instances="2"), "max_instances"),
    ])
    def test_refused(self, mutate, reason):
        config = copy(CONFIG)
        mutate(config)
        with pytest.raises(rg.RegistryError) as caught:
            build(config)
        assert reason in " ".join(caught.value.reasons).lower()

    def test_repeated_json_keys(self):
        text = json.dumps(CONFIG).replace('"min_actors": 2', '"min_actors": 2, "min_actors": 3')
        with pytest.raises(rg.RegistryError, match="repeated key"):
            rg.load_registry_json(text, trusted=trusted_for(CONFIG))

    def test_json_round_trip_keeps_the_digest(self):
        assert rg.load_registry_json(json.dumps(CONFIG), trusted=trusted_for(CONFIG)).digest == registry().digest

    def test_a_specification_cannot_register_anything(self):
        """The only way in is registry configuration. A spec naming a new entrypoint is simply unknown."""
        result = rg.authorize(spec(actor("a", entrypoint="new_operation"), actor("b")), registry())
        assert not result.accepted and codes(result) == ["unknown_entrypoint"]
        assert "new_operation" not in registry().ids()


# ------------------------------------------------------------------ authorization


class TestAuthorization:
    def test_accepted_workload_carries_trusted_references(self):
        result = rg.authorize(spec(actor("b"), actor("a", version="v2", lock_row=("bool", True))), registry())
        assert result.accepted and result.rejections == ()
        workload = result.workload
        assert [a.actor for a in workload.actors] == ["a", "b"]
        first = workload.actors[0]
        assert first.operation == f"{NEVER_IMPORTED}.reservations:reserve_seats"
        assert (first.entrypoint, first.application_version, first.role) == ("reserve_seats", "v2", "request")
        assert [(x.name, x.type, x.value) for x in first.arguments] == [
            ("event_id", "int", 1), ("holder", "str", "a"), ("lock_row", "bool", True), ("qty", "int", 2)]
        assert first.argument_values_validated is False, "values are a later step's job"
        assert workload.registry_digest == registry().digest

    def test_background_job_role(self):
        result = rg.authorize(spec(actor("a"), actor("job", "release_seats", "v2", "background_job")), registry())
        assert result.accepted and result.workload.actors[1].role == "background_job"

    def test_optional_null_argument(self):
        result = rg.authorize(spec(actor("a", coupon=("null", None)), actor("b")), registry())
        assert result.accepted

    @pytest.mark.parametrize("actors,expected", [
        ([actor("a", entrypoint="delete_everything"), actor("b")], ["unknown_entrypoint"]),
        ([actor("a", version="v9"), actor("b")], ["version_not_registered"]),
        ([actor("a", "release_seats", "v1"), actor("b")], ["version_not_registered"]),
        ([actor("a", role="background_job"), actor("b")], ["role_not_registered"]),
        ([actor("a", extra=("int", 1)), actor("b")], ["unknown_argument"]),
        ([actor("a", qty=(None, None)), actor("b")], ["missing_argument"]),
        ([actor("a", qty=("str", "2")), actor("b")], ["argument_type_not_registered"]),
        ([actor("a", holder=("null", None)), actor("b")], ["argument_type_not_registered"]),
        ([actor("a", "release_seats", "v2"), actor("b", "release_seats", "v2")], ["instance_limit_exceeded"]),
        ([actor(n) for n in "abcd"], ["too_many_actors", "instance_limit_exceeded"]),
    ])
    def test_rejections(self, actors, expected):
        result = rg.authorize(spec(*actors), registry())
        assert not result.accepted and result.workload is None
        assert codes(result) == expected

    def test_application_mismatch(self):
        result = rg.authorize(spec(application="inventory"), registry())
        assert codes(result) == ["application_mismatch"]

    def test_too_few_actors_for_the_registry(self):
        config = copy(CONFIG) | {"min_actors": 3}
        assert codes(rg.authorize(spec(), registry(config))) == ["too_few_actors"]

    def test_every_reason_is_listed_in_a_fixed_order(self):
        bad = [actor("z", version="v9", role="background_job", extra=("int", 1)),
               actor("a", entrypoint="unknown_thing")]
        first = rg.authorize(spec(*bad, application="inventory"), registry())
        second = rg.authorize(spec(*reversed(bad), application="inventory"), registry())
        assert first == second
        assert codes(first) == ["application_mismatch", "unknown_entrypoint", "version_not_registered",
                                "role_not_registered", "unknown_argument"]
        assert [r.actor for r in first.rejections] == [None, "a", "z", "z", "z"]

    def test_spec_limits_are_not_weakened(self):
        """A registry may be stricter than the specification format, never looser."""
        assert registry(copy(CONFIG) | {"max_actors": ws.MAX_ACTORS}).config.max_actors == ws.MAX_ACTORS
        with pytest.raises(rg.RegistryError, match="max_actors"):
            registry(copy(CONFIG) | {"max_actors": ws.MAX_ACTORS + 1})
        looser = copy(CONFIG)
        looser["entrypoints"][0]["max_instances"] = ws.MAX_ACTORS + 1
        with pytest.raises(rg.RegistryError, match="max_instances"):
            registry(looser)
        with pytest.raises(ws.SpecRejected):
            spec(*[actor(n) for n in "abcde"])

    def test_takes_only_parsed_specifications_and_built_registries(self):
        with pytest.raises(TypeError, match="parsed WorkloadSpec"):
            rg.authorize({"schema_version": 2}, registry())
        with pytest.raises(TypeError, match="Registry"):
            rg.authorize(spec(), CONFIG)


class TestProvenance:
    def test_self_reported_provenance_decides_nothing(self):
        built = registry()
        results = [rg.authorize(spec(origin=o, author=a), built)
                   for o, a in (("hand_written", "fixture_author"), ("synthetic", "someone_else"),
                                ("deterministic_baseline", "baseline_b"), ("model_proposal", "any_model"))]
        assert all(r.accepted for r in results)
        stripped = [r.workload.model_dump(exclude={"spec_digest", "self_reported_provenance"}) for r in results]
        assert all(s == stripped[0] for s in stripped)

    def test_registration_provenance_comes_from_the_registry(self):
        workload = rg.authorize(spec(origin="model_proposal", author="claims_to_be_harness"), registry()).workload
        assert workload.registered_by == registry().registered_by
        assert workload.self_reported_provenance.author == "claims_to_be_harness"

    def test_actor_identity_is_preserved(self):
        workload = rg.authorize(spec(actor("Old_App", version="v1"), actor("new_app", version="v2")),
                                registry()).workload
        assert {(a.actor, a.application_version) for a in workload.actors} == {("Old_App", "v1"), ("new_app", "v2")}

    def test_state_goals_pass_through_unresolved(self):
        goal = {"entity": "event", "label": "full", "count": 1,
                "constraints": [{"field": "capacity", "op": "eq", "value": {"type": "int", "value": 3}}]}
        workload = rg.authorize(spec(state_goals=[goal]), registry()).workload
        assert workload.state_goals[0].label == "full" and not hasattr(workload, "seed_rows")


# ------------------------------------------------------------------ isolation


class TestIsolation:
    def test_nothing_is_imported_from_operation_references(self):
        code = (
            "import json, sys\n"
            "from deployspecter.live import registry as rg, workload_spec as ws\n"
            f"config = json.loads({json.dumps(json.dumps(CONFIG))})\n"
            "digest = rg.registry_digest(rg.RegistryConfig.model_validate(config))\n"
            "r = rg.build_registry(config, trusted=rg.TrustedRegistration(registrar='harness_owner',"
            " config_label='synthetic_v1', pinned_digest=digest))\n"
            "s = ws.parse_workload({'schema_version': 2, 'name': 'x', 'application': 'reservations',"
            " 'provenance': {'origin': 'hand_written', 'author': 'a'},"
            " 'actors': [{'name': n, 'entrypoint': 'release_seats', 'application_version': 'v2',"
            " 'role': 'request', 'arguments': [{'name': 'holder', 'value': {'type': 'str', 'value': n}}]}"
            " for n in ('a', 'b')]})\n"
            "rg.authorize(s, r)\n"
            f"print(any(m.startswith('{NEVER_IMPORTED}') for m in sys.modules),"
            " [m for m in sys.modules if m.endswith(('oracle', 'properties', 'supervisor', 'serverlog'))"
            " or m.startswith(('live_app', 'langgraph'))])\n"
        )
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "False []"

    def test_module_has_no_dynamic_import_or_execution(self):
        tree = ast.parse(Path(rg.__file__).read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert not {c.func.id for c in calls} & {"eval", "exec", "compile", "__import__", "open", "setattr"}
        for call in (c for c in calls if c.func.id == "getattr"):
            assert isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "socket", "psycopg", "builtins", "urllib"}

    def test_no_network_or_database_during_authorization(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no network or database access is allowed here")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        built = rg.load_registry_json(json.dumps(CONFIG), trusted=trusted_for(CONFIG))
        assert rg.authorize(spec(), built).accepted

    def test_rejections_carry_structured_fields(self):
        result = rg.authorize(spec(actor("a", qty=("str", "2"), extra=("int", 1)), actor("b", entrypoint="nope")),
                              registry())
        rows = [r.model_dump(exclude={"detail"}) for r in result.rejections]
        assert rows == [
            {"code": "unknown_argument", "actor": "a", "entrypoint": "reserve_seats", "argument": "extra"},
            {"code": "argument_type_not_registered", "actor": "a", "entrypoint": "reserve_seats", "argument": "qty"},
            {"code": "unknown_entrypoint", "actor": "b", "entrypoint": "nope", "argument": None},
        ]

    def test_authorized_workload_is_not_runnable(self):
        workload = rg.authorize(spec(), registry()).workload
        assert workload.execution_ready is False
        assert all(a.argument_values_validated is False for a in workload.actors)
        assert not any(callable(getattr(workload, name)) for name in type(workload).model_fields)

    def test_authorization_is_deterministic(self):
        built = registry()
        results = {rg.authorize(spec(actor("b"), actor("a")), built).model_dump_json() for _ in range(20)}
        assert len(results) == 1
