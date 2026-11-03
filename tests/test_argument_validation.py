"""Offline tests of operation-specific argument validation. Synthetic operations, hand-written arguments only."""

from __future__ import annotations

import ast
import json
import math
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from deployspecter.live import argument_validation as av
from deployspecter.live import registry as rg
from deployspecter.live import workload_spec as ws

ROOT = Path(__file__).resolve().parents[1]

# Hand-written synthetic registry: operations that don't exist anywhere, so nothing could be imported or run.
REGISTRY = {
    "registry_version": 1, "application": "ticketing",
    "registered_by": {"registrar": "harness_owner", "source": "harness_config", "config_label": "synthetic_v1"},
    "min_actors": 2, "max_actors": 4,
    "entrypoints": [
        {"id": "book", "operation": "synthetic_ticketing.ops:book", "versions": ["v1", "v2"], "roles": ["request"],
         "max_instances": 4, "arguments": [
             {"name": "event_id", "types": ["int"]}, {"name": "holder", "types": ["str"]},
             {"name": "qty", "types": ["int"]}, {"name": "price", "types": ["float"], "required": False},
             {"name": "coupon", "types": ["str", "null"], "required": False},
             {"name": "express", "types": ["bool"], "required": False},
             {"name": "min_qty", "types": ["int"], "required": False},
             {"name": "max_qty", "types": ["int"], "required": False}]},
        {"id": "sweep", "operation": "synthetic_ticketing.jobs:sweep", "versions": ["v2"],
         "roles": ["request", "background_job"], "max_instances": 1, "arguments": [
             {"name": "batch", "types": ["int"]}, {"name": "mode", "types": ["str"]}]},
    ],
}

BOOK_RULES = [
    {"name": "event_id", "constraints": [{"kind": "int", "minimum": 1, "maximum": 1000}]},
    {"name": "holder", "constraints": [{"kind": "str", "min_length": 1, "max_length": 16,
                                        "charset": "lower_identifier"}]},
    {"name": "qty", "constraints": [{"kind": "int", "minimum": 1, "maximum": 10}]},
    {"name": "price", "constraints": [{"kind": "float", "minimum": 0.0, "maximum": 1000000.0}]},
    {"name": "coupon", "constraints": [{"kind": "str", "min_length": 4, "max_length": 12, "charset": "alnum"}]},
    {"name": "min_qty", "constraints": [{"kind": "int", "minimum": 0, "maximum": 10}]},
    {"name": "max_qty", "constraints": [{"kind": "int", "minimum": 0, "maximum": 10}]},
]
CONTRACTS = {
    "contracts_version": 1, "application": "ticketing", "registry_digest": "",
    "contracts": [
        {"entrypoint": "book", "versions": ["v1"], "roles": ["request"],
         "arguments": [*BOOK_RULES, {"name": "express", "constraints": [{"kind": "bool", "allowed": [False]}]}],
         "predicates": [{"op": "less_or_equal", "left": "min_qty", "right": "max_qty"},
                        {"op": "requires", "if_present": "coupon", "then_present": "price"}]},
        {"entrypoint": "book", "versions": ["v2"], "roles": ["request"],
         "arguments": [*BOOK_RULES, {"name": "express", "constraints": [{"kind": "bool"}]}],
         "predicates": [{"op": "less_than", "left": "min_qty", "right": "max_qty"},
                        {"op": "excludes", "left": "express", "right": "coupon"}]},
        {"entrypoint": "sweep", "versions": ["v2"], "roles": ["request"],
         "arguments": [{"name": "batch", "constraints": [{"kind": "int", "minimum": 1, "maximum": 100}]},
                       {"name": "mode", "constraints": [{"kind": "str", "allowed": ["full", "incremental"]}]}]},
    ],
}


def copy(value):
    return json.loads(json.dumps(value))


def make_registry(config=None):
    config = copy(config or REGISTRY)
    digest = rg.registry_digest(rg.RegistryConfig.model_validate(config))
    return rg.build_registry(config, trusted=rg.TrustedRegistration(
        registrar="harness_owner", config_label="synthetic_v1", pinned_digest=digest))


def contract_config(registry, mutate=None):
    config = copy(CONTRACTS)
    config["registry_digest"] = registry.digest
    if mutate:
        mutate(config)
    return config


def trusted_contracts(config):
    try:
        digest = av.contracts_digest(av.ContractSet.model_validate(config))
    except ValueError:
        digest = "0" * 64
    return av.TrustedContracts(reviewer="contract_reviewer", pinned_digest=digest)


def make_contracts(registry=None, mutate=None):
    registry = registry or make_registry()
    config = contract_config(registry, mutate)
    return av.build_contracts(config, registry=registry, trusted=trusted_contracts(config))


def typed(value):
    return ws._typed(value)


def book(name, version="v1", **args):
    values = {"event_id": 5, "holder": name.lower(), "qty": 2}
    values.update(args)
    return {"name": name, "entrypoint": "book", "application_version": version, "role": "request",
            "arguments": [{"name": k, "value": typed(v)} for k, v in values.items() if v is not ...]}


def sweep(name, role="request", **args):
    values = {"batch": 10, "mode": "full"} | args
    return {"name": name, "entrypoint": "sweep", "application_version": "v2", "role": role,
            "arguments": [{"name": k, "value": typed(v)} for k, v in values.items()]}


def authorized(*actors, registry=None):
    spec = ws.parse_workload({"schema_version": 2, "name": "fixture", "application": "ticketing",
                              "provenance": {"origin": "hand_written", "author": "fixture_author"},
                              "actors": list(actors)})
    result = rg.authorize(spec, registry or make_registry())
    assert result.accepted, result.rejections
    return result.workload


def validate(*actors, contracts=None):
    return av.validate_arguments(authorized(*actors), contracts or make_contracts())


def codes(result):
    return [r.code for r in result.rejections]


def only(result):
    assert not result.accepted
    return [(r.actor, r.argument, r.code) for r in result.rejections]


# ------------------------------------------------------------------ accepted workloads


class TestAccepted:
    def test_two_actors_with_independent_arguments(self):
        result = validate(book("a", price=10.5, coupon="ABCD1234"), book("b", "v2", express=True))
        assert result.accepted and result.rejections == ()
        validated = result.workload
        assert validated.argument_values_validated is True
        assert validated.execution_authorized is False and validated.correctness_evaluated is False
        assert validated.contracts_digest == make_contracts().digest
        assert [a.actor for a in validated.authorized.actors] == ["a", "b"]

    def test_boundaries_are_inclusive(self):
        assert validate(book("a", event_id=1, qty=10, price=0.0), book("b", event_id=1000, qty=1,
                                                                       price=1000000.0)).accepted

    def test_optional_arguments_may_be_missing_or_null(self):
        assert validate(book("a"), book("b", coupon=None)).accepted

    def test_comparison_needs_both_values(self):
        assert validate(book("a", min_qty=9), book("b", max_qty=0)).accepted

    def test_smallest_positive_float(self):
        assert validate(book("a", price=5e-324), book("b")).accepted


# ------------------------------------------------------------------ single-value rules


class TestValueRules:
    @pytest.mark.parametrize("args,expected", [
        ({"event_id": 0}, [("a", "event_id", "below_minimum")]),
        ({"event_id": 1001}, [("a", "event_id", "above_maximum")]),
        ({"event_id": -(2**63)}, [("a", "event_id", "below_minimum")]),
        ({"event_id": 2**63 - 1}, [("a", "event_id", "above_maximum")]),
        ({"qty": 11, "event_id": 0}, [("a", "event_id", "below_minimum"), ("a", "qty", "above_maximum")]),
        ({"price": -0.5}, [("a", "price", "below_minimum")]),
        ({"price": math.nextafter(1000000.0, math.inf)}, [("a", "price", "above_maximum")]),
        ({"price": -5e-324}, [("a", "price", "below_minimum")]),
    ])
    def test_numeric_boundaries(self, args, expected):
        assert only(validate(book("a", **args), book("b"))) == expected

    @pytest.mark.parametrize("holder,code", [
        ("", "too_short"), ("x" * 17, "too_long"), ("Alice", "character_not_permitted"),
        ("al ice", "character_not_permitted"), ("9lives", "character_not_permitted"),
        ("zoë", "character_not_permitted"), ("a-b", "character_not_permitted"),
        ("x';drop", "character_not_permitted"),
    ])
    def test_string_rules(self, holder, code):
        assert only(validate(book("a", holder=holder), book("b"))) == [("a", "holder", code)]

    def test_string_limits_exactly(self):
        assert validate(book("a", holder="x"), book("b", holder="x" * 16)).accepted

    @pytest.mark.parametrize("coupon,code", [
        ("abc", "too_short"), ("a" * 13, "too_long"), ("ABCD-123", "character_not_permitted"),
        ("ABCD 123", "character_not_permitted"), ("ABCD;--1", "character_not_permitted"),
    ])
    def test_unsafe_characters_in_a_restricted_argument(self, coupon, code):
        # price is given, so the requires predicate is satisfied and only the coupon rule is under test
        assert only(validate(book("a", coupon=coupon, price=1.0), book("b"))) == [("a", "coupon", code)]

    def test_allowed_values(self):
        assert only(validate(book("a"), sweep("job", mode="everything"))) == [("job", "mode", "not_in_allowed_values")]
        assert validate(book("a"), sweep("job", mode="incremental")).accepted

    def test_boolean_allowed_values_differ_by_version(self):
        assert only(validate(book("a", express=True), book("b"))) == [("a", "express", "not_in_allowed_values")]
        assert validate(book("a", "v2", express=True), book("b")).accepted


# ------------------------------------------------------------------ cross-field predicates


class TestPredicates:
    def test_less_or_equal(self):
        assert validate(book("a", min_qty=3, max_qty=3), book("b")).accepted
        result = validate(book("a", min_qty=4, max_qty=3), book("b"))
        assert [(r.actor, r.predicate, r.code) for r in result.rejections] == [
            ("a", "less_or_equal", "cross_field_violated")]

    def test_less_than_in_the_other_version(self):
        result = validate(book("a", "v2", min_qty=3, max_qty=3), book("b"))
        assert [(r.predicate, r.code) for r in result.rejections] == [("less_than", "cross_field_violated")]

    def test_requires(self):
        result = validate(book("a", coupon="ABCD1234"), book("b"))
        assert [(r.predicate, r.code) for r in result.rejections] == [("requires", "cross_field_violated")]

    def test_null_doesnt_count_as_present(self):
        assert validate(book("a", coupon=None), book("b")).accepted

    def test_excludes(self):
        result = validate(book("a", "v2", express=True, coupon="ABCD1234", price=1.0), book("b"))
        assert [(r.predicate, r.code) for r in result.rejections] == [("excludes", "cross_field_violated")]

    def test_a_value_that_failed_its_own_rule_doesnt_take_part(self):
        result = validate(book("a", min_qty=11, max_qty=3), book("b"))
        assert codes(result) == ["above_maximum"]


# ------------------------------------------------------------------ roles and versions


class TestComparisonTypes:
    def _with_max_qty(self, types, constraint):
        registry_config = copy(REGISTRY)
        registry_config["entrypoints"][0]["arguments"][7]["types"] = types
        registry = make_registry(registry_config)
        config = contract_config(registry)
        for contract in config["contracts"][:2]:
            for rule in contract["arguments"]:
                if rule["name"] == "max_qty":
                    rule["constraints"] = [constraint]
        return registry, config

    def test_mixed_int_and_float_comparison_is_refused(self):
        registry, config = self._with_max_qty(["float"], {"kind": "float", "minimum": 0.0, "maximum": 10.0})
        with pytest.raises(av.ContractError, match="share one numeric type"):
            av.build_contracts(config, registry=registry, trusted=trusted_contracts(config))

    def test_an_argument_that_may_be_int_or_float_cant_be_compared(self):
        registry, config = self._with_max_qty(["int", "float"], {"kind": "int", "minimum": 0, "maximum": 10})
        for contract in config["contracts"][:2]:
            for rule in contract["arguments"]:
                if rule["name"] == "max_qty":
                    rule["constraints"].append({"kind": "float", "minimum": 0.0, "maximum": 10.0})
        with pytest.raises(av.ContractError, match="share one numeric type"):
            av.build_contracts(config, registry=registry, trusted=trusted_contracts(config))

    def test_integer_comparison_is_exact_beyond_2_53(self):
        registry_config = copy(REGISTRY)
        registry = make_registry(registry_config)
        config = contract_config(registry)
        for contract in config["contracts"][:2]:
            for rule in contract["arguments"]:
                if rule["name"] in {"min_qty", "max_qty"}:
                    rule["constraints"] = [{"kind": "int"}]
        contracts = av.build_contracts(config, registry=registry, trusted=trusted_contracts(config))
        big = 2**53
        # As floats these two are equal; as integers they differ by one, and the rule must see the difference.
        assert float(big + 1) == float(big)
        rejected = av.validate_arguments(authorized(book("a", min_qty=big + 1, max_qty=big), book("b")), contracts)
        assert [r.predicate for r in rejected.rejections] == ["less_or_equal"]
        assert av.validate_arguments(authorized(book("a", min_qty=big, max_qty=big + 1), book("b")), contracts).accepted


class TestPresenceIsNotTruthiness:
    def test_false_is_present_for_excludes(self):
        result = validate(book("a", "v2", express=False, coupon="ABCD1234", price=1.0), book("b"))
        assert [(r.predicate, r.code) for r in result.rejections] == [("excludes", "cross_field_violated")]

    def test_zero_is_present_for_comparisons(self):
        result = validate(book("a", min_qty=0, max_qty=0), book("b"))
        assert result.accepted, "0 <= 0 holds; a falsy value is still a value"
        result = validate(book("a", "v2", min_qty=0, max_qty=0), book("b"))
        assert [r.predicate for r in result.rejections] == ["less_than"]

    def test_zero_satisfies_requires(self):
        assert validate(book("a", coupon="ABCD1234", price=0.0), book("b")).accepted

    def test_null_is_absent_and_missing_is_absent(self):
        assert validate(book("a", coupon=None), book("b")).accepted  # requires: coupon null, price missing
        assert validate(book("a", "v2", express=True, coupon=None), book("b")).accepted  # excludes: coupon null


class TestDigestsSurvive:
    def test_registry_and_contract_digests_are_carried_unchanged(self):
        registry, contracts = make_registry(), make_contracts()
        validated = av.validate_arguments(authorized(book("a"), book("b"), registry=registry), contracts).workload
        assert validated.authorized.registry_digest == registry.digest == contracts.registry_digest
        assert validated.contracts_digest == contracts.digest


class TestRolesAndVersions:
    def test_role_the_contract_doesnt_cover(self):
        result = validate(book("a"), sweep("job", role="background_job"))
        assert only(result) == [("job", None, "role_not_covered")]

    def test_version_not_registered_never_reaches_validation(self):
        spec = ws.parse_workload({"schema_version": 2, "name": "x", "application": "ticketing",
                                  "provenance": {"origin": "hand_written", "author": "a"},
                                  "actors": [book("a", "v9"), book("b")]})
        assert not rg.authorize(spec, make_registry()).accepted

    def test_contract_set_for_another_registry(self):
        other = copy(REGISTRY)
        other["entrypoints"][1]["max_instances"] = 2
        other_registry = make_registry(other)
        contracts = make_contracts(other_registry)
        result = av.validate_arguments(authorized(book("a"), book("b")), contracts)
        assert codes(result) == ["registry_mismatch"]


# ------------------------------------------------------------------ the contract set itself


class TestContracts:
    @pytest.mark.parametrize("mutate,reason", [
        (lambda c: c["contracts"][0]["predicates"].append({"op": "greater_than", "left": "min_qty",
                                                           "right": "max_qty"}), "predicates"),
        (lambda c: c["contracts"][0]["predicates"].append({"op": "expression", "code": "min_qty < max_qty"}),
         "predicates"),
        (lambda c: c["contracts"][0]["predicates"].append({"op": "less_than", "left": "min_qty", "right": "nope"}),
         "unknown argument"),
        (lambda c: c["contracts"][0]["predicates"].append({"op": "excludes", "left": "qty", "right": "qty"}),
         "itself"),
        (lambda c: c["contracts"][0]["predicates"].append({"op": "less_than", "left": "holder", "right": "coupon"}),
         "numeric"),
        (lambda c: c["contracts"][0]["arguments"][0]["constraints"][0].update(pattern="^[0-9]+$"), "Extra inputs"),
        (lambda c: c["contracts"][0]["arguments"][1]["constraints"][0].update(charset="anything"), "charset"),
        (lambda c: c["contracts"][0]["arguments"][0]["constraints"][0].update(minimum=10, maximum=1), "greater"),
        (lambda c: c["contracts"][0]["arguments"][0]["constraints"][0].update(minimum=True), "minimum"),
        (lambda c: c["contracts"][0]["arguments"][3]["constraints"][0].update(maximum=float("inf")), "maximum"),
        (lambda c: c["contracts"][2]["arguments"][1]["constraints"][0].update(allowed=["full", "full"]), "repeated"),
        (lambda c: c["contracts"][2]["arguments"][1]["constraints"][0].update(allowed=["x" * 200]), "length"),
        (lambda c: c["contracts"][0]["arguments"][1]["constraints"][0].update(allowed=["Bad Name"]), "charset"),
        (lambda c: c["contracts"].pop(1), "no contract for 'book' version 'v2'"),
        (lambda c: c["contracts"][0]["arguments"].pop(0), "no rule for argument 'event_id'"),
        (lambda c: c["contracts"][0]["arguments"].append({"name": "extra", "constraints": []}),
         "unregistered argument"),
        (lambda c: c["contracts"][0]["arguments"][0].update(constraints=[]), "no int constraint"),
        (lambda c: c["contracts"][0]["arguments"][0]["constraints"].append({"kind": "str"}), "unregistered type"),
        (lambda c: c["contracts"][1].update(versions=["v1", "v2"]), "ambiguous"),
        (lambda c: c["contracts"][2].update(roles=["admin"]), "roles"),
        (lambda c: c.update(contracts_version=True), "contracts_version"),
        (lambda c: c.update(contracts_version=2), "contracts_version"),
        (lambda c: c.update(application="other_app"), "different application"),
        (lambda c: c.update(registry_digest="f" * 64), "different registry"),
        (lambda c: c["contracts"][0].update(run="import os"), "Extra inputs"),
    ])
    def test_refused(self, mutate, reason):
        with pytest.raises(av.ContractError) as caught:
            make_contracts(mutate=mutate)
        assert reason in " ".join(caught.value.reasons)

    def test_pinned_digest_must_match_and_come_from_outside(self):
        registry = make_registry()
        config = contract_config(registry)
        changed = contract_config(registry, lambda c: c["contracts"][2]["arguments"][0]["constraints"][0].update(
            maximum=99))
        with pytest.raises(av.ContractError, match="pinned"):
            av.build_contracts(changed, registry=registry, trusted=trusted_contracts(config))
        for fake in (None, {"reviewer": "contract_reviewer", "pinned_digest": "0" * 64}):
            with pytest.raises(av.ContractError, match="TrustedContracts"):
                av.build_contracts(config, registry=registry, trusted=fake)

    def test_digest_is_order_insensitive(self):
        registry = make_registry()
        reordered = contract_config(registry)
        reordered["contracts"].reverse()
        reordered["contracts"][0]["arguments"].reverse()
        assert av.contracts_digest(av.ContractSet.model_validate(reordered)) == make_contracts(registry).digest

    def test_vocabulary_is_closed(self):
        assert av.PREDICATE_OPS == ("less_than", "less_or_equal", "requires", "excludes")
        assert set(av.CHARSETS) == {"digits", "alnum", "alnum_underscore_dash", "lower_identifier",
                                    "printable_ascii"}


# ------------------------------------------------------------------ no coercion, and state separation


def forged(workload, actor_index, argument, kind, value):
    """An authorized workload with one value swapped, as a buggy or hostile earlier stage might produce."""
    actors = list(workload.actors)
    actor = actors[actor_index]
    arguments = [rg.AuthorizedArgument(name=a.name, type=kind if a.name == argument else a.type,
                                       value=value if a.name == argument else a.value) for a in actor.arguments]
    actors[actor_index] = actor.model_copy(update={"arguments": tuple(arguments)})
    return workload.model_copy(update={"actors": tuple(actors)})


class TestNoCoercion:
    @pytest.mark.parametrize("argument,kind,value,code", [
        ("qty", "int", True, "wrong_python_type"),
        ("qty", "int", 2.0, "wrong_python_type"),
        ("qty", "int", "2", "wrong_python_type"),
        ("holder", "str", 5, "wrong_python_type"),
        ("event_id", "int", None, "wrong_python_type"),
    ])
    def test_values_are_never_converted(self, argument, kind, value, code):
        result = av.validate_arguments(forged(authorized(book("a"), book("b")), 0, argument, kind, value),
                                       make_contracts())
        assert only(result) == [("a", argument, code)]

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_non_finite_floats(self, value):
        workload = authorized(book("a", price=1.0), book("b"))
        result = av.validate_arguments(forged(workload, 0, "price", "float", value), make_contracts())
        assert only(result) == [("a", "price", "non_finite")]

    def test_null_where_the_registry_doesnt_allow_it(self):
        result = av.validate_arguments(forged(authorized(book("a"), book("b")), 0, "qty", "null", None),
                                       make_contracts())
        assert only(result) == [("a", "qty", "null_not_allowed")]

    def test_the_authorized_workload_is_left_unchanged(self):
        workload = authorized(book("a", qty=99), book("b"))
        before = workload.model_dump_json()
        av.validate_arguments(workload, make_contracts())
        assert workload.model_dump_json() == before


class TestStateSeparation:
    def test_takes_only_an_authorized_workload(self):
        spec = ws.parse_workload({"schema_version": 2, "name": "x", "application": "ticketing",
                                  "provenance": {"origin": "hand_written", "author": "a"},
                                  "actors": [book("a"), book("b")]})
        for wrong in (spec, spec.model_dump(), rg.authorize(spec, make_registry())):
            with pytest.raises(TypeError, match="registry-authorized"):
                av.validate_arguments(wrong, make_contracts())
        with pytest.raises(TypeError, match="contracts"):
            av.validate_arguments(authorized(book("a"), book("b")), CONTRACTS)

    def test_stages_are_marked_and_cannot_be_claimed(self):
        validated = validate(book("a"), book("b")).workload
        assert validated.authorized.execution_ready is False
        assert all(a.argument_values_validated is False for a in validated.authorized.actors)
        with pytest.raises(ValueError):
            av.ValidatedWorkload(authorized=validated.authorized, contracts_digest="x", execution_authorized=True)
        with pytest.raises(ValueError):
            av.ValidatedWorkload(authorized=validated.authorized, contracts_digest="x", correctness_evaluated=True)

    def test_validated_workload_is_immutable(self):
        validated = validate(book("a"), book("b")).workload
        with pytest.raises(ValueError):
            validated.argument_values_validated = False


# ------------------------------------------------------------------ determinism and isolation


class TestDeterminism:
    def test_rejections_are_ordered_and_structured_the_same_way_every_time(self):
        bad = [book("z", qty=0, holder="BAD"), book("a", event_id=0, min_qty=5, max_qty=1)]
        first = av.validate_arguments(authorized(*bad), make_contracts())
        second = av.validate_arguments(authorized(*reversed(bad)), make_contracts())
        assert first == second
        assert [(r.actor, r.argument, r.predicate, r.code) for r in first.rejections] == [
            ("a", "event_id", None, "below_minimum"),
            ("a", None, "less_or_equal", "cross_field_violated"),
            ("z", "holder", None, "character_not_permitted"),
            ("z", "qty", None, "below_minimum"),
        ]
        assert len({av.validate_arguments(authorized(*bad), make_contracts()).model_dump_json()
                    for _ in range(10)}) == 1


class TestIsolation:
    def test_contract_path_imports_no_engine_oracle_or_application(self):
        code = ("import sys, deployspecter.live.argument_validation, deployspecter.live.registry,"
                " deployspecter.live.workload_spec;"
                "print([m for m in sys.modules if m.startswith(('psycopg', 'deployspecter.live.runner',"
                " 'deployspecter.live.driver', 'deployspecter.database', 'deployspecter.oracle', 'live_app',"
                " 'langgraph', 'synthetic_ticketing'))])")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "[]"

    def test_version_1_upgrade_still_works(self):
        v1 = {"schema_version": 1, "name": "x", "application": "ticketing", "actors": [
            {"name": "a", "entrypoint": "book", "application_version": "v1",
             "arguments": [{"name": "qty", "value": 1}]},
            {"name": "b", "entrypoint": "book", "application_version": "v1", "arguments": []}]}
        spec = ws.parse_workload(v1, v1_provenance=ws.Provenance(origin="hand_written", author="a"))
        assert spec.actors[0].arguments[0].value.type == "int"

    def test_module_has_no_dynamic_import_or_execution(self):
        tree = ast.parse(Path(av.__file__).read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert not {c.func.id for c in calls} & {"eval", "exec", "compile", "__import__", "open", "setattr"}
        for call in (c for c in calls if c.func.id == "getattr"):
            assert isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "socket", "psycopg", "re", "urllib"}

    def test_no_network_or_database(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no network or database access is allowed here")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        assert validate(book("a"), book("b")).accepted
