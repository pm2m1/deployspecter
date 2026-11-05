"""EXPERIMENTAL, offline: the simultaneous-pilot scenario catalogue is internally consistent."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research_tools" / "isolated"))
import run_coordinated as rc  # noqa: E402


@pytest.mark.parametrize("name", rc.SCENARIOS)
def test_every_gate_names_real_actors_and_markers(name):
    spec = rc.scenario(name)
    actors = {a["actor"]: p for p, acts in spec["processes"].items() for a in acts}
    assert set(spec["processes"]) <= {"A", "B"}
    for g in spec["gates"]:
        assert g["actor"] in actors
        if g.get("match"):
            re.compile(g["match"])
        for c in g.get("from", []) + g["until"]:
            assert c["actor"] in actors and c["marker"] in rc.MARKERS


@pytest.mark.parametrize("name", rc.SCENARIOS)
def test_every_producer_waits_for_every_worker_to_be_ready(name):
    spec = rc.scenario(name)
    actors = [a["actor"] for acts in spec["processes"].values() for a in acts]
    workers = {a for a in actors if a.endswith("_worker")}
    for p in (a for a in actors if a.endswith("_push")):
        window = [g for g in spec["gates"] if g["actor"] == p and g.get("match") is None]
        assert window and {c["actor"] for c in window[0]["until"]} == workers
        assert all(c["marker"] == "fetch" for c in window[0]["until"])


def test_the_two_releases_always_run_in_their_own_containers():
    for name in rc.SCENARIOS:
        for p, acts in rc.scenario(name)["processes"].items():
            for a in acts:
                assert a["actor"].startswith("old_" if p == "A" else "new_")


def test_the_mixed_cancel_cells_put_canceller_and_worker_in_different_containers():
    for name, canceller, worker in [("cancel_new_old", "B", "A"), ("cancel_old_new", "A", "B")]:
        procs = rc.scenario(name)["processes"]
        assert any(a["actor"].endswith("_push") for a in procs[canceller])
        assert any(a["actor"].endswith("_worker") for a in procs[worker])
