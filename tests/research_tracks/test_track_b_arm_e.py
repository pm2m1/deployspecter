"""Track B offline tests: arm E's prototype generator, on the hand-written synthetic ticketing registry only."""

from __future__ import annotations

import ast
import collections
import socket
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.live import workload_spec as ws  # noqa: E402
from deployspecter.research.track_b import arm_e_generator as g  # noqa: E402
from test_argument_validation import make_contracts, make_registry  # noqa: E402
from test_state_planning import context, pin  # noqa: E402

GENERATOR = ROOT / "src/deployspecter/research/track_b/arm_e_generator.py"
OWNER_REPO = "synthetic/ticketing"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the generator tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket, "socket", refuse)


@pytest.fixture(scope="module")
def trusted():
    registry = make_registry()
    return registry, make_contracts(registry)


def policy(registry):
    return g.GoalPolicy(application="ticketing", registry_digest=registry.digest, references=(
        g.GoalReference(entrypoint="book", argument="event_id", entity="event", field="id"),))


A_TESTS = (
    g.ATranslation(test_name="test_two_bookings", document={
        "schema_version": 2, "name": "a_two_bookings", "application": "ticketing",
        "provenance": {"origin": "hand_written", "author": "adapter_author"},
        "actors": [{"name": "a", "entrypoint": "book", "application_version": "v1", "role": "request",
                    "arguments": [{"name": "event_id", "value": ws._typed(5)},
                                  {"name": "holder", "value": ws._typed("x")}, {"name": "qty", "value": ws._typed(2)}]},
                   {"name": "b", "entrypoint": "book", "application_version": "v1", "role": "request",
                    "arguments": [{"name": "event_id", "value": ws._typed(5)},
                                  {"name": "holder", "value": ws._typed("y")},
                                  {"name": "qty", "value": ws._typed(3)}]}],
        "state_goals": [{"entity": "event", "label": "fixture_event", "count": 1,
                         "constraints": [{"field": "id", "op": "eq", "value": ws._typed(5)}]}]}),
    g.ATranslation(test_name="test_uses_a_mocked_clock", reason="calls an unregistered helper between operations"),
)


def run(trusted, version="v1", **kw):
    registry, contracts = trusted
    return g.generate(registry, contracts, version, policy(registry), A_TESTS, **kw)


class TestIsolation:
    def test_imports_no_oracle_scenario_or_application_module(self):
        names = set()
        for node in ast.walk(ast.parse(GENERATOR.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
            elif isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
        bad = {n for n in names if n.split(".")[-1] in {"oracle", "scenarios", "explorer", "properties", "meta_client",
                                                         "state_constructor", "seed_feasibility"}
               or "track_a" in n or "shared" in n}
        assert not bad

    def test_generator_inputs_carry_no_requirement_or_oracle_field(self):
        assert set(g.GoalPolicy.model_fields) == {"policy_version", "application", "registry_digest", "references"}
        assert set(g.ATranslation.model_fields) == {"test_name", "document", "reason"}


class TestGeneration:
    def test_counts_by_source_are_exact(self, trusted):
        result = run(trusted)
        assert result.counts() == {"A": {"generated": 1, "valid": 1, "generation_rejected": 0},
                                   "B": {"generated": 22, "valid": 20, "generation_rejected": 2},
                                   "pair": {"generated": 3, "valid": 3, "generation_rejected": 0},
                                   "A_not_expressible": {"blocked": 1}}
        assert result.blocked_a_tests == [("test_uses_a_mocked_clock",
                                           "calls an unregistered helper between operations")]

    def test_rejections_are_kept_with_stage_and_code(self, trusted):
        rejected = run(trusted).rejected
        assert {(u.stage, u.codes) for u in rejected} == {("validate_arguments", ("cross_field_violated",))}
        assert all(u.document["actors"] for u in rejected)

    def test_single_instance_entrypoints_cant_form_b_units(self, trusted):
        rejected = run(trusted, "v2").rejected
        sweep = [u for u in rejected if u.label.startswith("b:sweep")]
        assert sweep and all(u.codes == ("instance_limit_exceeded",) for u in sweep)
        assert not any(u.label.startswith("pair:sweep+sweep") for u in run(trusted, "v2").units)

    def test_generation_is_deterministic(self, trusted):
        first, second = run(trusted), run(trusted)
        assert [u.unit_id for u in first.units] == [u.unit_id for u in second.units]
        assert [u.document for u in first.units] == [u.document for u in second.units]

    def test_goals_come_only_from_the_policy(self, trusted):
        for unit in run(trusted).units:
            if unit.source == "A":
                continue
            for goal in unit.document["state_goals"]:
                assert goal["entity"] == "event" and goal["count"] == 1
                assert [c["field"] for c in goal["constraints"]] == ["id"]
                assert [c["op"] for c in goal["constraints"]] == ["eq"]

    def test_shared_event_ids_give_one_goal(self, trusted):
        canonical = next(u for u in run(trusted).units if u.label == "b:book:canonical")
        assert len(canonical.document["state_goals"]) == 1

    def test_attribution_never_comes_from_provenance(self, trusted):
        units = run(trusted).units
        a_unit = next(u for u in units if u.source == "A")
        assert a_unit.document["provenance"]["origin"] == "hand_written" and a_unit.source == "A"

    def test_the_ceiling_blocks_without_truncating(self, trusted):
        result = run(trusted, max_units=10)
        assert result.ceiling_reached and result.units == [] and result.candidates_counted == 26

    def test_a_policy_for_another_registry_is_refused(self, trusted):
        registry, contracts = trusted
        other = g.GoalPolicy(application="ticketing", registry_digest="0" * 64)
        with pytest.raises(ValueError, match="different registry"):
            g.generate(registry, contracts, "v1", other)


class TestOrderAndAllocation:
    def test_seeds_reorder_the_same_set(self, trusted):
        units = run(trusted).valid
        orders = [[u.unit_id for u in g.seeded_order(units, s, OWNER_REPO)] for s in (1, 2, 3)]
        assert all(sorted(o) == sorted(orders[0]) for o in orders)
        assert len({tuple(o) for o in orders}) == 3
        assert orders[0] == [u.unit_id for u in g.seeded_order(list(reversed(units)), 1, OWNER_REPO)]
        with pytest.raises(ValueError):
            g.seeded_order(units, 4, OWNER_REPO)

    def test_round_robin_is_fair_and_never_moves_unused_budget(self):
        runs, unused = g.round_robin(["x", "y", "z"], {"x": 1, "y": 3, "z": 0}, max_runs=10)
        assert runs == ["x", "y", "y", "y"] and unused == 6
        runs, unused = g.round_robin(["x", "y"], {"x": 5, "y": 5}, max_runs=3)
        assert runs == ["x", "y", "x"] and unused == 0

    def test_r_needs_es_verified_digest(self):
        out = g.r_state_requirements({"u1": "a" * 64, "u2": None})
        assert out == {"u1": ("required_digest", "a" * 64), "u2": ("blocked", "E's state wasn't verified")}


class TestPlanningBudgetUse:
    def test_every_valid_unit_plans_within_tiny_budgets(self, trusted):
        registry, contracts = trusted
        config = context(registry)
        use = g.planning_use(run(trusted).valid, registry=registry, contracts=contracts, context=config,
                             trusted=pin(config))
        assert collections.Counter(u["outcome"] for u in use) == {"candidate_plan": 24}
        assert max(u["expansions"] for u in use) <= 2 and max(u["constraint_checks"] for u in use) <= 10
