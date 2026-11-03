"""Track A offline tests: the synthetic chain up to a prepared, inert Gate 2A scenario. No database is opened."""

from __future__ import annotations

import ast
import hashlib
import socket
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.live.runner import LiveScenario  # noqa: E402
from deployspecter.research.track_a import acceptance as acc  # noqa: E402
from deployspecter.research.track_a import pipeline as p  # noqa: E402
from deployspecter.research.track_a import replay_policy as rp  # noqa: E402
from deployspecter.research.track_a import scenario as sc  # noqa: E402
from live_app import properties as props  # noqa: E402
from live_app import reservations as app  # noqa: E402

TRACK_A = ROOT / "src/deployspecter/research/track_a"
FORBIDDEN = {"deployspecter.oracle", "deployspecter.scenarios", "deployspecter.explorer", "deployspecter.meta_client",
             "deployspecter.state_constructor", "deployspecter.seed_feasibility", "live_app",
             "deployspecter.live.adapters", "deployspecter.research.track_b", "deployspecter.research.shared"}


@pytest.fixture
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("Track A offline code tried to open a connection")
    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(socket, "socket", refuse)


@pytest.fixture
def chain(offline):
    return p.run_offline_chain()


def imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


class TestScenario:
    def test_setup_sql_is_the_existing_reviewed_schema(self):
        assert hashlib.sha256(app.SETUP_SQL.encode()).hexdigest() == sc.SETUP_SQL_SHA256

    def test_variant_switch_isnt_a_registered_argument(self):
        names = {a["name"] for e in sc.REGISTRY_CONFIG["entrypoints"] for a in e["arguments"]}
        assert not names & {k for v in sc.VARIANTS.values() for k in v}

    def test_operations_name_the_existing_application(self):
        ops = {e["operation"] for e in sc.REGISTRY_CONFIG["entrypoints"]}
        assert ops == {"live_app.reservations:reserve_seats", "live_app.reservations:release_seats"}
        assert callable(app.reserve_seats) and callable(app.release_seats)

    def test_no_forbidden_imports(self):
        for path in TRACK_A.glob("*.py"):
            bad = {m for m in imports(path) if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)}
            assert not bad, (path.name, bad)


class TestChain:
    def test_every_stage_gives_the_expected_outcome(self, chain):
        s = chain.stages
        assert s["authorize"]["accepted"] and s["validate_arguments"]["accepted"] and s["plan_identity"]["supported"]
        assert s["plan_state"]["outcome"] == "candidate_plan"
        assert s["literal_vs_planner"] == {"both_accept": True, "same_rows": True}
        assert s["verify_context"]["outcome"] == "MATCH"
        assert s["verify_context"]["scope"].startswith("consistent with the supplied catalog snapshot only")
        assert s["bridge"]["outcome"] == "bridged"

    def test_seed_is_one_typed_event_row(self, chain):
        assert len(chain.seed) == 1 and chain.seed[0].table == "events"
        assert {v.column: v.value for v in chain.seed[0].values} == {"id": 1, "capacity": 3, "reserved": 0}
        assert chain.bridged.execution_authorized is False

    def test_digests_are_stable(self, chain):
        again = p.run_offline_chain()
        assert chain.digests == again.digests
        assert set(chain.digests) >= {"registry", "contracts", "spec", "plan_identity", "snapshot", "context",
                                      "request", "bridge_typed", "legacy_seed"}

    def test_a_changed_snapshot_doesnt_match_the_pinned_context_silently(self, offline):
        snap = sc.catalog_snapshot_fixture()
        snap["attributes"][2]["attnotnull"] = False  # events.reserved nullable in the catalog, not in the context
        with pytest.raises(p.ChainError, match="context_not_matched"):
            p.run_offline_chain(snap)


class TestScenarioBuilding:
    def test_both_variants_share_one_workload(self, chain):
        buggy = p.live_scenario(chain, variant="buggy", setup_sql=app.SETUP_SQL)
        fixed = p.live_scenario(chain, variant="fixed", setup_sql=app.SETUP_SQL)
        assert isinstance(buggy, LiveScenario) and buggy.seed == fixed.seed
        strip = lambda s: [(a.name, a.entrypoint, {k: v for k, v in a.kwargs.items() if k != "lock_row"})  # noqa: E731
                           for a in s.actors]
        assert strip(buggy) == strip(fixed)
        assert [a.kwargs["lock_row"] for a in buggy.actors] == [False, False]
        assert [a.kwargs["lock_row"] for a in fixed.actors] == [True, True]

    def test_unreviewed_setup_sql_is_refused(self, chain):
        with pytest.raises(p.ChainError, match="reviewed"):
            p.live_scenario(chain, variant="buggy", setup_sql=app.SETUP_SQL + "\n-- changed")

    def test_unknown_variant_is_refused(self, chain):
        with pytest.raises(p.ChainError):
            p.live_scenario(chain, variant="other", setup_sql=app.SETUP_SQL)


class TestInitialStateCheck:
    def test_exact_state_passes(self, chain):
        expected = p.expected_initial_state(chain)
        observed = {"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [], "rejections": []}
        assert p.check_initial_state(expected, observed) == []

    @pytest.mark.parametrize("observed, fragment", [
        ({"events": [], "reservations": [], "rejections": []}, "no row matching"),
        ({"events": [{"id": 1, "capacity": 3, "reserved": 0}] * 2, "reservations": [], "rejections": []},
         "unrequested"),
        ({"events": [{"id": 1, "capacity": 3, "reserved": False}], "reservations": [], "rejections": []},
         "no row matching"),
        ({"events": [{"id": 1, "capacity": 3.0, "reserved": 0}], "reservations": [], "rejections": []},
         "no row matching"),
        ({"events": [{"id": 1, "capacity": 3, "reserved": 0}], "reservations": [{"id": 9}], "rejections": []},
         "unrequested"),
        ({"events": [{"id": 1, "capacity": 3, "reserved": 0}], "rejections": []}, "not read"),
    ])
    def test_any_difference_fails(self, chain, observed, fragment):
        problems = p.check_initial_state(p.expected_initial_state(chain), observed)
        assert problems and any(fragment in x for x in problems)


class TestOracleIsIndependent:
    def test_the_oracle_imports_nothing_from_the_tool_or_application(self):
        mods = imports(ROOT / "tests/live_app/properties.py")
        assert not {m for m in mods if m.startswith("deployspecter") or m.startswith("live_app")}

    def test_the_expected_buggy_end_state_violates_r1_and_r2(self):
        lost = {"events": [{"id": 1, "capacity": 3, "reserved": 2}],
                "reservations": [{"event_id": 1, "holder": "alice", "qty": 2}, {"event_id": 1, "holder": "bob",
                                                                                "qty": 2}], "rejections": []}
        found = {v.split()[0] for v in props.check_invariants(lost)}
        assert found == {"R1", "R2"}


def attempt(sig=(), fp="f", outcome="completed", reqs=("R1",), state="s", inconclusive=False, followed=True):
    return rp.Attempt(sig, fp, outcome, frozenset(reqs), state, inconclusive, followed)


class TestReplayPolicy:
    def test_three_matching_attempts_reproduce(self):
        c = attempt()
        out = rp.confirm(c, lambda i: attempt(fp="other" if i == 1 else "f"))
        assert out.result == "reproduced" and out.attempts == 3 and out.classes == [
            "exact", "conditional_semantic", "exact"]

    def test_one_failed_comparable_attempt_fails(self):
        out = rp.confirm(attempt(), lambda i: attempt(state="x") if i == 2 else attempt())
        assert out.result == "failed_reproduction" and out.attempts == 3

    def test_not_comparable_attempts_dont_count_and_the_limit_is_125(self):
        out = rp.confirm(attempt(sig=("victim", "a")), lambda i: attempt(sig=("victim", "b")))
        assert out.result == "rare_outcome_not_encountered" and out.attempts == rp.ATTEMPT_LIMIT == 125
        assert out.comparable_seen == 0 and out.unconditional_semantic == 125

    def test_an_inconclusive_attempt_makes_a_shortfall_inconclusive(self):
        out = rp.confirm(attempt(sig=(1,)), lambda i: attempt(inconclusive=i == 0, sig=(2,)))
        assert out.result == "inconclusive_reproduction"

    def test_unfollowable_decisions_fail(self):
        assert rp.classify(attempt(), attempt(followed=False)) == "failed"


class TestAcceptance:
    def test_nothing_executed_is_not_run_not_pass(self, chain):
        bundle = {"chain": {"stages": chain.stages, "rechecked": True}, "digests": chain.digests}
        verdict, judgements = acc.judge(bundle)
        status = {j.condition: j.status for j in judgements}
        assert verdict == "FAIL"  # condition 8: source and setup hashes not yet recorded
        assert status["1_validated"] == "pass"
        assert all(status[k] == "not_run" for k in ("2_schema_matched", "3_state_checked", "4_supervised",
                                                    "5_buggy_violates", "6_fixed_passes", "7_replay",
                                                    "9_fail_closed"))

    def test_a_dry_run_snapshot_never_passes_condition_2(self):
        bundle = {"catalog_export": {"source_label": "hand_written_track_a_fixture", "verify_context": "MATCH"}}
        assert dict((j.condition, j.status) for j in acc.judge(bundle)[1])["2_schema_matched"] == "fail"

    def test_missing_fail_closed_evidence_fails(self):
        bundle = {"traces": [{"supervised": True, "scoreable": True, "run_id": "r"}]}
        assert dict((j.condition, j.status) for j in acc.judge(bundle)[1])["9_fail_closed"] == "fail"
