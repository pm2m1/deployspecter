"""Track B milestone 2, offline: arm E coverage audit and default-off generator policy options."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.research.track_b import arm_e_audit as au  # noqa: E402
from deployspecter.research.track_b import arm_e_generator as g  # noqa: E402
from test_argument_validation import make_contracts, make_registry  # noqa: E402
from test_state_planning import context, pin  # noqa: E402

REPAIR = g.GeneratorOptions(repair_requires=True)
PARTNER = g.GeneratorOptions(single_instance_b="pair_with_partner")
BOTH = g.GeneratorOptions(repair_requires=True, single_instance_b="pair_with_partner")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket, "socket", refuse)


@pytest.fixture(scope="module")
def setup():
    registry = make_registry()
    contracts = make_contracts(registry)
    policy = g.GoalPolicy(application="ticketing", registry_digest=registry.digest, references=(
        g.GoalReference(entrypoint="book", argument="event_id", entity="event", field="id"),))
    config = context(registry)
    planning = {"registry": registry, "contracts": contracts, "context": config, "trusted": pin(config)}
    return registry, contracts, policy, planning


def run(setup, version, options=None):
    registry, contracts, policy, planning = setup
    return au.audit(registry, contracts, version, policy, options=options, planning=planning)


def boundary(report):
    return {k: (v["covered"], v["values"]) for k, v in report["boundary"].items()}


class TestDefaults:
    def test_v1_coverage_is_measured_exactly(self, setup):
        r = run(setup, "v1")
        assert (r["candidates"], r["valid"], r["rejected"]) == (25, 23, 2)
        assert boundary(r) == {"book": (22, 24)}
        assert r["boundary"]["book"]["arguments"]["coupon"]["uncovered"] == [
            '{"type": "str", "value": "aaaa"}', '{"type": "str", "value": "aaaaaaaaaaaa"}']
        assert r["pairs"]["missing"] == [] and r["duplicate_units"] == 2
        assert r["planning"]["unsupported_goal_units"] == 0

    def test_v2_single_instance_exclusions_are_measured(self, setup):
        r = run(setup, "v2")
        assert r["max_instances_rejections"] == 5 and boundary(r) == {"book": (25, 25), "sweep": (4, 6)}
        assert r["pairs"]["excluded_by_max_instances"] == [("sweep", "sweep")]
        assert r["pairs"]["missing"] == [("sweep", "sweep")]


class TestOptions:
    def test_defaults_reproduce_the_prototype_exactly(self, setup):
        registry, contracts, policy, _ = setup
        plain = [u.unit_id for u in g.generate(registry, contracts, "v2", policy).units]
        explicit = [u.unit_id for u in
                    g.generate(registry, contracts, "v2", policy, options=g.GeneratorOptions()).units]
        assert plain == explicit

    def test_requires_repair_closes_the_v1_gap(self, setup):
        r = run(setup, "v1", REPAIR)
        assert (r["valid"], r["rejected"], boundary(r)) == (25, 0, {"book": (24, 24)})

    def test_partner_pairing_closes_the_single_instance_gap(self, setup):
        r = run(setup, "v2", PARTNER)
        assert (r["valid"], r["rejected"], r["max_instances_rejections"]) == (34, 0, 0)
        assert boundary(r)["sweep"] == (6, 6)

    def test_options_change_unit_ids_so_they_never_mix(self, setup):
        registry, contracts, policy, _ = setup
        plain = {u.unit_id for u in g.generate(registry, contracts, "v1", policy).units}
        repaired = {u.unit_id for u in g.generate(registry, contracts, "v1", policy, options=BOTH).units}
        assert not plain & repaired


class TestOptionDisclosure:
    def test_option_generated_units_are_labelled_never_silent(self, setup):
        registry, contracts, policy, _ = setup
        repaired = g.generate(registry, contracts, "v1", policy, options=REPAIR).units
        assert sorted(u.label for u in repaired if "+companion:" in u.label) == [
            'b:book:coupon=str:"aaaa"+companion:price', 'b:book:coupon=str:"aaaaaaaaaaaa"+companion:price']
        partnered = g.generate(registry, contracts, "v2", policy, options=PARTNER).units
        assert sum("+partner:book" in u.label for u in partnered) == 5
        plain = g.generate(registry, contracts, "v1", policy).units
        assert not any("+companion" in u.label or "+partner" in u.label for u in plain)

    def test_partner_pairing_respects_max_instances(self, setup):
        registry, contracts, policy, _ = setup
        for unit in g.generate(registry, contracts, "v2", policy, options=PARTNER).valid:
            counts = {}
            for actor in unit.document["actors"]:
                counts[actor["entrypoint"]] = counts.get(actor["entrypoint"], 0) + 1
            assert all(n <= registry.lookup(e).max_instances for e, n in counts.items())


class TestStrongPolicy:
    def test_strong_policy_discloses_every_default_rejection_and_its_stand_in(self, setup):
        for version, rejected in (("v1", 2), ("v2", 5)):
            r = run(setup, version, BOTH)
            assert len(r["default_policy_rejections"]) == rejected
            assert all(x["option_unit"] for x in r["default_policy_rejections"])
            assert r["rejected"] == 0 and len(r["option_generated_units"]) == rejected
        assert run(setup, "v1").get("default_policy_rejections") is None
