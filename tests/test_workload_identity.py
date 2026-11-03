"""Offline tests of validated-plan identity and deduplication. Synthetic plans, hand-written arguments only."""

from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from deployspecter.live import argument_validation as av
from deployspecter.live import registry as rg
from deployspecter.live import workload_identity as wi
from deployspecter.live import workload_spec as ws

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_argument_validation import (  # noqa: E402
    REGISTRY,
    book,
    contract_config,
    copy,
    make_contracts,
    make_registry,
    trusted_contracts,
)

GOAL = {"entity": "event", "label": "nearly_full", "count": 1, "constraints": [
    {"field": "capacity", "op": "eq", "value": {"type": "int", "value": 3}},
    {"field": "reserved", "op": "ge", "value": {"type": "int", "value": 2}}]}


def validated(*actors, origin="hand_written", author="fixture_author", rationale="", goals=None, name="fixture",
              registry=None, contracts=None):
    registry = registry or make_registry()
    contracts = contracts or make_contracts(registry)
    spec = ws.parse_workload({
        "schema_version": 2, "name": name, "application": "ticketing", "rationale": rationale,
        "provenance": {"origin": origin, "author": author}, "actors": list(actors) or [book("a"), book("b")],
        "state_goals": goals if goals is not None else [],
    })
    authorized = rg.authorize(spec, registry)
    assert authorized.accepted, authorized.rejections
    result = av.validate_arguments(authorized.workload, contracts)
    assert result.accepted, result.rejections
    return result.workload


def trust(registry=None, contracts=None):
    registry = registry or make_registry()
    return {"registry": registry, "contracts": contracts or make_contracts(registry)}


def identity(workload, contracts=None, registry=None):
    result = wi.validated_plan_identity(workload, **trust(registry, contracts))
    assert result.supported, result.reason
    return result.identity.value


def unsupported(workload, contracts=None, registry=None):
    result = wi.validated_plan_identity(workload, **trust(registry, contracts))
    assert not result.supported and result.identity is None
    return result.reason


def with_rules(registry_mutate=None, rules=None):
    """A registry and contract set changed for one test. ``rules`` replaces book's constraints by argument name."""
    registry_config = copy(REGISTRY)
    if registry_mutate:
        registry_mutate(registry_config)
    registry = make_registry(registry_config)

    def mutate(config):
        for contract in config["contracts"][:2]:
            for rule in contract["arguments"]:
                if rule["name"] in (rules or {}):
                    rule["constraints"] = rules[rule["name"]]

    return registry, make_contracts(registry, mutate)


# ------------------------------------------------------------------ what doesn't change the identity


class TestExcludedMetadata:
    def test_origin_and_author(self):
        values = {identity(validated(origin=o, author=a)) for o, a in (
            ("hand_written", "fixture_author"), ("synthetic", "other"), ("deterministic_baseline", "baseline_b"),
            ("model_proposal", "a_model"))}
        assert len(values) == 1

    def test_rationale_and_specification_name(self):
        assert identity(validated(rationale="", name="one")) == identity(validated(rationale="because", name="two"))

    def test_full_record_digests_differ_while_plan_identities_match(self):
        first, second = validated(author="x"), validated(author="y")
        assert first.authorized.spec_digest != second.authorized.spec_digest
        assert identity(first) == identity(second)

    def test_actor_order_and_argument_order(self):
        forwards = validated(book("a", price=1.5, coupon="ABCD1234"), book("b"))
        a = book("a", price=1.5, coupon="ABCD1234")
        a["arguments"].reverse()
        backwards = validated(book("b"), a)
        assert identity(forwards) == identity(backwards)

    def test_plan_fields_dont_rely_on_upstream_sorting(self):
        """Authorization sorts actors, arguments and goals; the encoding sorts them again itself."""
        other = {"entity": "event", "label": "other_event", "count": 2, "constraints": []}
        workload = validated(book("a", price=1.5), book("b"), goals=[GOAL, other])
        authorized = workload.authorized
        shuffled = authorized.model_copy(update={
            "actors": tuple(actor.model_copy(update={"arguments": tuple(reversed(actor.arguments))})
                            for actor in reversed(authorized.actors)),
            "state_goals": tuple(reversed(authorized.state_goals)),
        })
        assert wi.plan_fields(workload.model_copy(update={"authorized": shuffled})) == wi.plan_fields(workload)

    def test_a_reordered_authorized_workload_is_not_genuine(self):
        """No real stage 2 output is ever out of order, so one that is was built by hand and gets no identity."""
        workload = validated(book("a"), book("b"))
        authorized = workload.authorized.model_copy(update={"actors": tuple(reversed(workload.authorized.actors))})
        revalidated = av.validate_arguments(authorized, make_contracts())
        assert revalidated.accepted
        assert "isn't what the registry authorizes" in unsupported(revalidated.workload)

    def test_state_goal_and_constraint_order(self):
        reordered = copy(GOAL)
        reordered["constraints"].reverse()
        other = {"entity": "event", "label": "other_event", "count": 2, "constraints": []}
        assert identity(validated(goals=[GOAL, other])) == identity(validated(goals=[other, reordered]))


# ------------------------------------------------------------------ what does change it


class TestIncludedFields:
    def base(self):
        return identity(validated(book("a"), book("b")))

    @pytest.mark.parametrize("actors", [
        [book("a", qty=3), book("b")],
        [book("a", price=1.5), book("b")],
        [book("a", coupon=None), book("b")],
        [book("a", "v2"), book("b")],
        [book("c"), book("b")],
        [book("a"), book("b"), book("c")],
    ], ids=["argument", "extra_argument", "null_vs_missing", "version", "actor_name", "extra_actor"])
    def test_execution_relevant_changes(self, actors):
        assert identity(validated(*actors)) != self.base()

    def test_role(self):
        from test_argument_validation import sweep

        registry_config = copy(REGISTRY)
        registry = make_registry(registry_config)
        config = contract_config(registry, lambda c: c["contracts"][2].update(roles=["request", "background_job"]))
        contracts = av.build_contracts(config, registry=registry, trusted=trusted_contracts(config))
        request = validated(book("a"), sweep("job"), registry=registry, contracts=contracts)
        job = validated(book("a"), sweep("job", role="background_job"), registry=registry, contracts=contracts)
        assert identity(request, contracts, registry) != identity(job, contracts, registry)

    def test_registry_digest(self):
        changed = copy(REGISTRY)
        changed["entrypoints"][1]["max_instances"] = 2  # a different registry, same operations for book
        registry = make_registry(changed)
        contracts = make_contracts(registry)
        other = validated(registry=registry, contracts=contracts)
        assert identity(other, contracts, registry) != self.base()

    def test_contract_digest(self):
        registry = make_registry()
        contracts = make_contracts(registry, lambda c: c["contracts"][2]["arguments"][0]["constraints"][0].update(
            maximum=99))
        assert identity(validated(registry=registry, contracts=contracts), contracts, registry) != self.base()

    def test_state_goals(self):
        changed = copy(GOAL)
        changed["count"] = 2
        relabelled = copy(GOAL) | {"label": "almost_full"}
        assert len({identity(validated(goals=g)) for g in ([], [GOAL], [changed], [relabelled])}) == 4


class TestTypedValues:
    def test_int_float_bool_never_collide(self):
        registry_config = copy(REGISTRY)
        registry_config["entrypoints"][0]["arguments"][2]["types"] = ["int", "float", "bool"]
        registry = make_registry(registry_config)

        def mutate(config):
            for contract in config["contracts"][:2]:
                for rule in contract["arguments"]:
                    if rule["name"] == "qty":
                        rule["constraints"] = [{"kind": "int"}, {"kind": "float"}, {"kind": "bool"}]

        contracts = make_contracts(registry, mutate)
        values = [1, 1.0, True, 0, 0.0, False]
        ids = {identity(validated(book("a", qty=v), book("b"), registry=registry, contracts=contracts), contracts,
                        registry) for v in values}
        assert len(ids) == len(values)

    def test_float_encoding_is_exact(self):
        assert wi._encode("float", 0.1) == ["float", (0.1).hex()]
        assert wi._encode("float", 0.1) != wi._encode("float", 0.30000000000000004 - 0.2)
        assert wi._encode("float", 5e-324) == ["float", "0x0.0000000000001p-1022"]

    def test_integers_beyond_2_53_stay_distinct(self):
        assert wi._encode("int", 2**53) != wi._encode("int", 2**53 + 1)
        assert float(2**53) == float(2**53 + 1)

    def test_same_double_from_different_text_is_the_same_value(self):
        assert wi._encode("float", 0.1) == wi._encode("float", float("0.10000000000000001"))

    def test_signed_64_bit_extremes_through_the_pipeline(self):
        registry, contracts = with_rules(rules={"event_id": [{"kind": "int"}]})
        values = [2**63 - 1, 2**63 - 2, -(2**63), -(2**63) + 1, 0]
        ids = [identity(validated(book("a", event_id=v), book("b"), registry=registry, contracts=contracts),
                        contracts, registry) for v in values]
        assert len(set(ids)) == len(values)
        with pytest.raises(ws.SpecRejected):
            validated(book("a", event_id=2**63), book("b"), registry=registry, contracts=contracts)

    def test_subnormal_floats_through_the_pipeline(self):
        registry, contracts = with_rules()
        values = [0.0, 5e-324, 1e-323, 2.2250738585072014e-308, 2.225073858507201e-308]
        ids = [identity(validated(book("a", price=v), book("b"), registry=registry, contracts=contracts),
                        contracts, registry) for v in values]
        assert len(set(ids)) == len(values)

    def test_unicode_forms_through_the_pipeline(self):
        registry, contracts = with_rules(rules={"holder": [{"kind": "str", "max_length": 16}]})
        forms = ["\u00e9", "e\u0301", "\uff41", "a"]  # precomposed, decomposed, full-width a, plain a
        ids = [identity(validated(book("a", holder=v), book("b"), registry=registry, contracts=contracts),
                        contracts, registry) for v in forms]
        assert len(set(ids)) == len(forms)

    def test_missing_and_null_through_the_pipeline(self):
        assert identity(validated(book("a"), book("b"))) != identity(validated(book("a", coupon=None), book("b")))

    def test_strings_are_compared_without_normalization(self):
        assert wi._encode("str", "é") != wi._encode("str", "é")


# ------------------------------------------------------------------ inputs and failure


def forged(workload, **update):
    return workload.model_copy(update=update)


class TestInputs:
    def test_only_validated_workloads(self):
        workload = validated()
        for wrong in (workload.authorized, workload.model_dump(), "vplan1:" + "0" * 64):
            with pytest.raises(TypeError, match="ValidatedWorkload"):
                wi.validated_plan_identity(wrong, **trust())
        with pytest.raises(TypeError, match="Contracts"):
            wi.validated_plan_identity(workload, registry=make_registry(), contracts=None)
        with pytest.raises(TypeError, match="Registry"):
            wi.validated_plan_identity(workload, registry=make_registry().config, contracts=make_contracts())
        with pytest.raises(TypeError):
            wi.validated_plan_identity(workload, make_contracts())  # both must be named

    def test_other_contract_set(self):
        registry = make_registry()
        other = make_contracts(registry, lambda c: c["contracts"][2]["arguments"][0]["constraints"][0].update(
            maximum=99))
        assert "different contract set" in unsupported(validated(), other, registry)

    def test_stale_registry_pin(self):
        """A workload authorized by one registry, presented with another registry and its own contract set."""
        changed = copy(REGISTRY)
        changed["entrypoints"][1]["max_instances"] = 2
        newer = make_registry(changed)
        assert "different" in unsupported(validated(), make_contracts(newer), newer)

    def test_contracts_bound_to_another_registry(self):
        changed = copy(REGISTRY)
        changed["entrypoints"][1]["max_instances"] = 2
        assert "bound to a different registry" in unsupported(validated(), make_contracts(), make_registry(changed))

    def test_forged_contract_digest(self):
        assert unsupported(forged(validated(), contracts_digest="0" * 64))

    def test_forged_registry_digest(self):
        workload = validated()
        stale = forged(workload, authorized=workload.authorized.model_copy(update={"registry_digest": "0" * 64}))
        assert "different registry" in unsupported(stale)

    @pytest.mark.parametrize("change", [
        {"operation": "somewhere_else.module:other_function"},
        {"entrypoint": "sweep"},
        {"application_version": "v9"},
        {"role": "background_job"},
        {"actor": "renamed!"},
    ], ids=["operation", "entrypoint", "unregistered_version", "unregistered_role", "invalid_name"])
    def test_forged_actor_fields_are_reauthorized(self, change):
        workload = validated()
        actors = list(workload.authorized.actors)
        actors[0] = actors[0].model_copy(update=change)
        tampered = forged(workload, authorized=workload.authorized.model_copy(update={"actors": tuple(actors)}))
        assert unsupported(tampered)

    def test_dropped_required_argument(self):
        workload = validated()
        actors = list(workload.authorized.actors)
        actors[0] = actors[0].model_copy(update={
            "arguments": tuple(a for a in actors[0].arguments if a.name != "qty")})
        tampered = forged(workload, authorized=workload.authorized.model_copy(update={"actors": tuple(actors)}))
        assert av.validate_arguments(tampered.authorized, make_contracts()).accepted, "stage 3 alone misses this"
        assert "missing_argument" in unsupported(tampered)

    def test_unregistered_argument(self):
        workload = validated()
        actors = list(workload.authorized.actors)
        extra = rg.AuthorizedArgument(name="seat", type="int", value=1)
        actors[0] = actors[0].model_copy(update={"arguments": (*actors[0].arguments, extra)})
        tampered = forged(workload, authorized=workload.authorized.model_copy(update={"actors": tuple(actors)}))
        assert unsupported(tampered)

    @pytest.mark.parametrize("change", [
        {"application": "elsewhere"},
        {"registered_by": rg.RegisteredBy(registrar="someone_else", source="harness_config",
                                          config_label="synthetic_v1")},
    ], ids=["application", "registered_by"])
    def test_forged_workload_fields_are_reauthorized(self, change):
        workload = validated()
        assert unsupported(forged(workload, authorized=workload.authorized.model_copy(update=change)))

    def test_too_many_actors_after_forgery(self):
        workload = validated(book("a"), book("b"), book("c"), book("d"))
        actors = workload.authorized.actors
        fifth = actors[0].model_copy(update={"actor": "e"})
        tampered = forged(workload, authorized=workload.authorized.model_copy(update={"actors": (*actors, fifth)}))
        assert unsupported(tampered)

    def test_wrongly_typed_value_is_refused_not_coerced(self):
        workload = validated()
        actors = list(workload.authorized.actors)
        actors[0] = actors[0].model_copy(update={"arguments": tuple(
            rg.AuthorizedArgument(name=a.name, type=a.type, value=True if a.name == "qty" else a.value)
            for a in actors[0].arguments)})
        tampered = forged(workload, authorized=workload.authorized.model_copy(update={"actors": tuple(actors)}))
        assert unsupported(tampered)

    def test_forged_value_that_wouldnt_validate(self):
        workload = validated()
        actors = list(workload.authorized.actors)
        arguments = tuple(rg.AuthorizedArgument(name=a.name, type=a.type, value=999 if a.name == "qty" else a.value)
                          for a in actors[0].arguments)
        actors[0] = actors[0].model_copy(update={"arguments": arguments})
        tampered = forged(workload, authorized=workload.authorized.model_copy(update={"actors": tuple(actors)}))
        assert unsupported(tampered)

    def test_model_provided_digest_is_ignored(self):
        workload = validated()
        claimed = forged(workload, authorized=workload.authorized.model_copy(update={"spec_digest": "f" * 64}))
        assert identity(claimed) == identity(workload)

    def test_unencodable_value_is_unsupported_not_guessed(self):
        with pytest.raises(wi._Unsupported):
            wi._encode("float", float("nan"))
        with pytest.raises(wi._Unsupported):
            wi._encode("int", True)
        with pytest.raises(wi._Unsupported):
            wi._encode("float", -0.0)


# ------------------------------------------------------------------ deduplication


class TestDeduplicate:
    def test_groups_in_order_of_first_appearance(self):
        a1 = validated(author="first")
        b = validated(book("a", qty=3), book("b"))
        a2 = validated(author="second", rationale="different words")
        a3 = validated(book("b"), book("a"))
        result = wi.deduplicate([b, a1, a2, a3], **trust())
        assert [g.members for g in result.groups] == [(0,), (1, 2, 3)]
        assert result.unsupported == ()

    def test_group_order_follows_input_order_not_identity_values(self):
        x, y = validated(book("a", qty=3), book("b")), validated(book("a", qty=4), book("b"))
        forwards = wi.deduplicate([x, y], **trust())
        backwards = wi.deduplicate([y, x], **trust())
        assert [g.members for g in forwards.groups] == [(0,), (1,)] == [g.members for g in backwards.groups]
        assert [g.identity for g in forwards.groups] == list(reversed([g.identity for g in backwards.groups]))

    def test_unsupported_members_are_never_merged(self):
        good = validated()
        bad = forged(good, contracts_digest="0" * 64)
        result = wi.deduplicate([good, bad, good], **trust())
        assert [g.members for g in result.groups] == [(0, 2)]
        assert [(u.index, "different contract set" in u.reason) for u in result.unsupported] == [(1, True)]

    def test_inputs_are_not_changed(self):
        items = [validated(author="x"), validated(author="y")]
        before = [w.model_dump_json() for w in items]
        wi.deduplicate(items, **trust())
        assert [w.model_dump_json() for w in items] == before

    def test_empty(self):
        assert wi.deduplicate([], **trust()) == wi.DedupResult(groups=(), unsupported=())


class TestDeduplicationIsOnlyAView:
    """Deduplication must never change what an evaluation counts. See WORKLOAD_IDENTITY.md."""

    def items(self):
        good = validated(author="first")
        return [good, validated(author="second"), forged(good, contracts_digest="0" * 64),
                validated(book("a", qty=3), book("b")), good]

    def test_every_input_appears_exactly_once(self):
        items = self.items()
        result = wi.deduplicate(items, **trust())
        positions = [i for g in result.groups for i in g.members] + [u.index for u in result.unsupported]
        assert sorted(positions) == list(range(len(items)))

    def test_it_returns_positions_never_workloads(self):
        """So it can't substitute one proposal for another, or drop one from the caller's records."""
        result = wi.deduplicate(self.items(), **trust())
        for model in (result, *result.groups, *result.unsupported):
            for value in model.model_dump().values():
                assert not isinstance(value, dict) or "authorized" not in value

    def test_it_knows_nothing_of_arms_attempts_or_budgets(self):
        names = set(wi.DedupResult.model_fields) | set(wi.PlanGroup.model_fields) | set(
            wi.UnsupportedMember.model_fields)
        assert names == {"groups", "unsupported", "identity", "members", "index", "reason"}

    def test_self_reported_origin_never_decides_grouping(self):
        a = validated(origin="model_proposal", author="arm_one")
        b = validated(origin="deterministic_baseline", author="arm_two")
        assert [g.members for g in wi.deduplicate([a, b], **trust()).groups] == [(0, 1)]


# ------------------------------------------------------------------ stability and isolation


class TestStability:
    def test_repeated_runs(self):
        workload = validated(book("a", price=2.5), book("b"), goals=[GOAL])
        assert len({identity(workload) for _ in range(20)}) == 1

    def test_identity_is_pinned_for_a_fixed_plan(self):
        """Changing the plan encoding changes every identity, so it must be a deliberate, versioned change."""
        assert identity(validated(book("a", price=2.5), book("b"), goals=[GOAL])) == PINNED

    def test_format_is_versioned_and_domain_separated(self):
        workload = validated()
        value = identity(workload)
        assert value.startswith("vplan1:") and len(value) == 7 + 64
        import hashlib

        bare = hashlib.sha256(wi.canonical_plan_json(workload).encode()).hexdigest()
        assert value != "vplan1:" + bare, "the domain tag is part of the hash"

    def test_canonical_json_holds_only_strings(self):
        def leaves(node):
            if isinstance(node, dict):
                for value in node.values():
                    yield from leaves(value)
            elif isinstance(node, list):
                for value in node:
                    yield from leaves(value)
            else:
                yield node

        fields = wi.plan_fields(validated(book("a", price=2.5, qty=3), book("b"), goals=[GOAL]))
        assert all(type(leaf) is str for leaf in leaves(fields))
        assert set(fields) == {"identity_version", "registry_digest", "contracts_digest", "application", "actors",
                               "state_goals"}


PINNED = "vplan1:b3d9e455885ca25da58a972cd98cb7e65ec702377964f6ffc1c9ebd0a78304ae"


class TestExcludedMetadataIsInert:
    """Excluded fields can't steer dispatch, validation, state construction or scheduling under the current contract."""

    DOWNSTREAM = ("registry.py", "argument_validation.py", "workload_identity.py")

    def test_downstream_stages_never_read_explanatory_fields(self):
        live = Path(wi.__file__).parent
        for name in self.DOWNSTREAM:
            tree = ast.parse((live / name).read_text())
            read = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            assert not read & {"rationale", "source_refs", "claim", "generator_version", "note"}, name
            # The specification's own name is never read: actor.name and argument names are other fields.
            assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "name"
                        and isinstance(n.value, ast.Name) and n.value.id == "spec"], name

    def test_provenance_is_only_carried_never_consulted(self):
        tree = ast.parse((Path(wi.__file__).parent / "registry.py").read_text())
        uses = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "provenance"]
        assert len(uses) == 1, "registry copies provenance into the authorized workload and reads it nowhere else"

    def test_same_plan_same_downstream_results(self):
        first = validated(origin="model_proposal", author="a_model", rationale="one reason", name="first")
        second = validated(origin="synthetic", author="other", rationale="", name="second")
        for field in ("actors", "state_goals", "application", "registry_digest", "registered_by"):
            assert getattr(first.authorized, field) == getattr(second.authorized, field), field
        assert first.contracts_digest == second.contracts_digest


class TestIsolation:
    def test_imports_no_engine_oracle_or_application(self):
        code = ("import sys, deployspecter.live.workload_identity;"
                "print([m for m in sys.modules if m.startswith(('psycopg', 'deployspecter.live.runner',"
                " 'deployspecter.database', 'deployspecter.oracle', 'live_app', 'langgraph', 'synthetic_'))])")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "[]"

    def test_module_has_no_dynamic_import_or_execution(self):
        tree = ast.parse(Path(wi.__file__).read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert not {c.func.id for c in calls} & {"eval", "exec", "compile", "__import__", "open", "setattr", "getattr"}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "socket", "psycopg", "urllib"}

    def test_no_network_or_database(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no network or database access is allowed here")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        assert wi.validated_plan_identity(validated(), **trust()).supported
        json.dumps(wi.plan_fields(validated()))
