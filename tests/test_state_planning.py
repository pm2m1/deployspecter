"""Offline tests of the state-goal planner (step 5B). Synthetic, hand-written contexts and workloads only."""

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
from deployspecter.live import state_planning as sp
from deployspecter.live import workload_identity as wi
from deployspecter.live import workload_spec as ws

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_argument_validation import book, copy, make_contracts, make_registry  # noqa: E402
from test_workload_identity import validated  # noqa: E402

NONE = object()
SCHEMA_DIGEST = "5" * 64


def column(field, pg_type, *, nullable=True, **extra):
    if pg_type in {"text", "varchar"}:
        extra.setdefault("collation", "deterministic")
    return {"field": field, "column": field, "pg_type": pg_type, "nullable": nullable, **extra}


def context(registry=None, mutate=None):
    registry = registry or make_registry()
    config = {
        "context_version": 1, "application": "ticketing", "registry_digest": registry.digest,
        "schema_digest": SCHEMA_DIGEST, "encoding": "UTF8",
        "entities": [
            {"entity": "event", "table": "events", "primary_key": ["id"], "key_assignment": "ascending_int",
             "unique": [{"fields": ["code"], "nulls": "distinct"}],
             "foreign_keys": [{"field": "venue_id", "references_entity": "venue", "references_field": "id"}],
             "columns": [
                 column("id", "integer", nullable=False),
                 column("capacity", "integer", nullable=False, filler={"type": "int", "value": 10}),
                 column("reserved", "integer", nullable=False, has_default=True),
                 column("title", "text"), column("price", "double precision"),
                 column("active", "boolean", nullable=False, filler={"type": "bool", "value": False}),
                 column("big", "bigint"), column("small", "smallint"),
                 column("code", "varchar", max_length=8), column("venue_id", "integer"),
                 column("amount", "numeric"), column("starts", "opaque"),
                 column("slug", "text", generated=True),
                 column("label", "text", filler={"type": "str", "value": "x"}),
             ]},
            {"entity": "venue", "table": "venues", "primary_key": ["id"],
             "columns": [column("id", "integer", nullable=False),
                         column("name", "text", nullable=False, filler={"type": "str", "value": "venue"})]},
            {"entity": "ticket", "table": "tickets", "primary_key": ["id"], "key_assignment": "ascending_int",
             "has_insert_triggers": True,
             "foreign_keys": [{"field": "event_id", "references_entity": "event", "references_field": "id"}],
             "columns": [column("id", "bigint", nullable=False), column("event_id", "integer", nullable=False),
                         column("holder", "varchar", nullable=False, max_length=16)]},
            {"entity": "audit_log", "table": "audit_log", "primary_key": ["id"], "key_assignment": "ascending_int",
             "trigger_written": True,
             "columns": [column("id", "integer", nullable=False), column("note", "text")]},
        ],
    }
    if mutate:
        mutate(config)
    return config


def pin(config):
    try:
        digest = sp.context_digest(sp.SchemaContextConfig.model_validate(config))
    except ValueError:
        digest = "0" * 64
    return sp.TrustedPlanning(reviewer="schema_reviewer", pinned_digest=digest)


def c(field, op, value=NONE):
    return {"field": field, "op": op, **({} if value is NONE else {"value": ws._typed(value)})}


def g(entity, label, *constraints, count=1):
    return {"entity": entity, "label": label, "count": count, "constraints": list(constraints)}


def plan(*goals, config=None, trusted=None, budget=None):
    registry = make_registry()
    config = config if config is not None else context(registry)
    workload = validated(book("a"), book("b"), goals=list(goals))
    return sp.plan_state(workload, registry=registry, contracts=make_contracts(registry), context=config,
                         trusted=trusted if trusted is not None else pin(config), budget=budget)


def values(result, row=0):
    return {v.field: (v.type, v.value, v.origin) for v in result.request.rows[row].values}


def outcome(result):
    return result.outcome, getattr(result, "code", None)


# ------------------------------------------------------------------ the contract boundary


class TestBoundary:
    def test_validated_workloads_stay_non_executable(self):
        workload = validated(goals=[g("event", "e", c("capacity", "eq", 3))])
        assert workload.execution_authorized is False and workload.correctness_evaluated is False
        assert set(ws.StateGoal.model_fields) == {"entity", "label", "count", "constraints"}

    def test_forged_workload_is_refused_not_planned(self):
        workload = validated(goals=[g("event", "e", c("capacity", "eq", 3))])
        forged = workload.model_copy(update={"contracts_digest": "0" * 64})
        registry = make_registry()
        config = context(registry)
        with pytest.raises(sp.UntrustedWorkload):
            sp.plan_state(forged, registry=registry, contracts=make_contracts(registry), context=config,
                          trusted=pin(config))
        with pytest.raises(sp.UntrustedWorkload, match="different contract set"):  # check_validated's own reason
            sp.plan_state(forged, registry=registry, contracts=make_contracts(registry), context=config,
                          trusted=pin(config))
        status = av.ValidatedWorkload.model_construct(**{**dict(workload), "execution_authorized": True})
        with pytest.raises(sp.UntrustedWorkload):
            sp.plan_state(status, registry=registry, contracts=make_contracts(registry), context=config,
                          trusted=pin(config))

    def test_a_forged_workload_is_refused_before_anything_else_is_checked(self):
        """Even with an invalid context and a bad budget, a forged workload is refused for its own reason."""
        workload = validated(goals=[g("event", "e", c("capacity", "eq", 3))])
        forged = workload.model_copy(update={"contracts_digest": "0" * 64})
        registry = make_registry()
        with pytest.raises(sp.UntrustedWorkload) as caught:
            sp.plan_state(forged, registry=registry, contracts=make_contracts(registry), context={"bad": 1},
                          trusted=None, budget="not a budget")
        assert str(caught.value) == "validated against a different contract set"

    def test_reordered_goals_in_a_forged_object_are_refused(self):
        """The planner relies on authorized goals being in label order; only forged objects could break that."""
        workload = validated(goals=[g("event", "a", c("capacity", "eq", 1)), g("event", "b", c("capacity", "eq", 2))])
        reordered = workload.model_copy(update={"authorized": workload.authorized.model_copy(
            update={"state_goals": tuple(reversed(workload.authorized.state_goals))})})
        registry = make_registry()
        config = context(registry)
        with pytest.raises(sp.UntrustedWorkload):
            sp.plan_state(reordered, registry=registry, contracts=make_contracts(registry), context=config,
                          trusted=pin(config))

    def test_wrong_types(self):
        registry = make_registry()
        config = context(registry)
        workload = validated()
        with pytest.raises(TypeError):
            sp.plan_state(workload.authorized, registry=registry, contracts=make_contracts(), context=config,
                          trusted=pin(config))
        with pytest.raises(TypeError):
            sp.plan_state(workload, registry=registry.config, contracts=make_contracts(), context=config,
                          trusted=pin(config))
        with pytest.raises(TypeError):
            sp.plan_state(workload, registry=registry, contracts=make_contracts(), context=config,
                          trusted=pin(config), budget={"max_rows": 5})


# ------------------------------------------------------------------ supported mappings


class TestCandidates:
    def test_equality_with_exact_types(self):  # fixture 1
        result = plan(g("event", "e", c("id", "eq", 7), c("capacity", "eq", 3), c("title", "eq", "Gala"),
                        c("price", "eq", 0.1), c("active", "eq", True), c("code", "eq", "A1")))
        assert result.outcome == "candidate_plan"
        got = values(result)
        assert got["id"] == ("int", 7, "goal_constraint") and got["capacity"] == ("int", 3, "goal_constraint")
        assert got["title"] == ("str", "Gala", "goal_constraint") and got["price"] == ("float", 0.1, "goal_constraint")
        assert got["active"] == ("bool", True, "goal_constraint") and type(got["active"][1]) is bool
        assert result.request.feasibility == "candidate_pending_postgresql_validation"
        assert result.request.assumptions == sp.ASSUMPTIONS

    def test_positive_counts_get_deterministic_keys(self):  # fixture 2
        result = plan(g("event", "e", c("capacity", "eq", 3), count=3))
        assert [values(result, i)["id"] for i in range(3)] == [("int", n, "key_assignment") for n in (1, 2, 3)]
        assert [r.row_id for r in result.request.rows] == ["e#0", "e#1", "e#2"]

    def test_keys_avoid_values_other_goals_fixed(self):
        result = plan(g("event", "a", c("capacity", "eq", 1), count=2), g("event", "b", c("id", "eq", 1)))
        ids = sorted(v.value for r in result.request.rows for v in r.values if v.field == "id")
        assert ids == [1, 2, 3]

    def test_missing_null_and_defaults_stay_distinct(self):
        result = plan(g("event", "e", c("title", "is_null")))
        row = result.request.rows[0]
        assert values(result)["title"] == ("null", None, "goal_constraint")
        omitted = {o.field: o.reason for o in row.omitted}
        assert omitted["price"] == "nullable_unset" and omitted["reserved"] == "database_default"
        assert omitted["slug"] == "generated" and "title" not in omitted
        assert {"database_default_not_compared", "generated_not_compared", "nullable_unset_not_compared"} <= set(
            row.obligations)

    def test_guarded_not_null(self):  # fixture 10
        filled = plan(g("event", "e", c("active", "not_null")))
        assert values(filled)["active"] == ("bool", False, "filler")
        assert outcome(plan(g("event", "e", c("title", "not_null")))) == ("unsupported_goal", "no_filler_policy")
        assert values(plan(g("event", "e", c("title", "not_null"), c("title", "eq", "t"))))["title"][1] == "t"

    def test_a_string_filler_must_avoid_excluded_values(self):
        assert values(plan(g("event", "e", c("label", "ne", "y"))))["label"] == ("str", "x", "filler")
        assert outcome(plan(g("event", "e", c("label", "ne", "x")))) == ("unsupported_goal", "string_value_required")

    def test_boolean_inequality_takes_the_other_value(self):
        assert values(plan(g("event", "e", c("active", "ne", False))))["active"] == ("bool", True,
                                                                                    "comparison_choice")

    def test_required_column_without_a_policy(self):
        assert outcome(plan(g("ticket", "t", c("event_id", "eq", 1)), g("event", "e", c("id", "eq", 1)))) == (
            "unsupported_goal", "no_filler_policy")  # ticket.holder has no filler
        assert outcome(plan(g("venue", "v", c("name", "eq", "Hall")))) == ("unsupported_goal", "no_key_policy")

    def test_supported_foreign_key(self):  # fixture 11
        result = plan(g("venue", "v", c("id", "eq", 4)), g("event", "e", c("venue_id", "eq", 4)))
        assert result.outcome == "candidate_plan"
        dependency = result.request.dependencies[0]
        assert (dependency.child_row, dependency.parent_row, dependency.field) == ("e#0", "v#0", "venue_id")
        assert "foreign_key_order" in result.request.obligations

    def test_trigger_destinations_carry_obligations(self):  # fixture 13
        result = plan(g("audit_log", "a", c("note", "eq", "n")),
                      g("ticket", "t", c("event_id", "eq", 2), c("holder", "eq", "h")),
                      g("event", "e", c("id", "eq", 2)))
        by_entity = {r.entity: r for r in result.request.rows}
        assert "trigger_written_entity" in by_entity["audit_log"].obligations
        assert "insert_triggers_unverified" in by_entity["ticket"].obligations
        assert all("postgresql_validation_required" in r.obligations for r in result.request.rows)


# ------------------------------------------------------------------ numbers


class TestNumbers:
    def test_integers_near_2_53_stay_exact(self):
        exact = plan(g("event", "e", c("big", "eq", 2**53 + 1)))
        assert values(exact)["big"] == ("int", 9007199254740993, "goal_constraint")
        chosen = plan(g("event", "e", c("big", "gt", 2**53)))
        assert values(chosen)["big"] == ("int", 2**53 + 1, "comparison_choice")
        assert exact.request.request_digest != plan(g("event", "e", c("big", "eq", 2**53))).request.request_digest

    @pytest.mark.parametrize("goal,code", [
        (g("event", "e", c("big", "gt", 2**63 - 1)), "empty_domain"),
        (g("event", "e", c("big", "lt", -(2**63))), "empty_domain"),
        (g("event", "e", c("capacity", "gt", 2**31 - 1)), "empty_domain"),
        (g("event", "e", c("capacity", "eq", 2**31)), "outside_type_range"),
        (g("event", "e", c("small", "eq", -(2**15) - 1)), "outside_type_range"),
        (g("event", "e", c("capacity", "ge", 5), c("capacity", "le", 3)), "empty_domain"),  # fixture 4
        (g("event", "e", c("capacity", "ge", 1), c("capacity", "le", 2), c("capacity", "ne", 1),
           c("capacity", "ne", 2)), "empty_domain"),
        (g("event", "e", c("id", "ge", 1), c("id", "le", 2), count=3), "insufficient_distinct_values"),
    ])
    def test_sound_integer_infeasibility(self, goal, code):  # fixture 5
        assert outcome(plan(goal)) == ("infeasible_under_assumptions", code)

    def test_signed_64_bit_extremes_are_reachable(self):
        assert values(plan(g("event", "e", c("big", "eq", 2**63 - 1))))["big"][1] == 2**63 - 1
        assert values(plan(g("event", "e", c("big", "lt", -(2**63) + 1))))["big"][1] == -(2**63)

    def test_comparisons_choose_the_nearest_value(self):
        assert values(plan(g("event", "e", c("capacity", "gt", 5))))["capacity"][1] == 6
        assert values(plan(g("event", "e", c("capacity", "lt", 5))))["capacity"][1] == 4
        assert values(plan(g("event", "e", c("capacity", "ge", 5), c("capacity", "ne", 5))))["capacity"][1] == 6
        assert values(plan(g("event", "e", c("capacity", "ne", 0))))["capacity"][1] == 1

    def test_binary64_boundaries(self):
        assert values(plan(g("event", "e", c("price", "gt", 0.0))))["price"][1] == 5e-324
        assert values(plan(g("event", "e", c("price", "lt", 1.0))))["price"][1] == math.nextafter(1.0, 0.0)
        assert values(plan(g("event", "e", c("price", "le", -5e-324))))["price"][1] == -5e-324
        assert outcome(plan(g("event", "e", c("price", "ge", 1.0), c("price", "le", 1.0), c("price", "ne", 1.0)))) \
            == ("infeasible_under_assumptions", "empty_domain")
        assert outcome(plan(g("event", "e", c("price", "gt", 1.7976931348623157e308)))) == (
            "unsupported_goal", "non_finite_value_required")
        for goal in (g("event", "e", c("price", "lt", 5e-324)), g("event", "e", c("price", "gt", -5e-324))):
            chosen = values(plan(goal))["price"][1]
            assert chosen == 0.0 and math.copysign(1.0, chosen) > 0, "never negative zero"


# ------------------------------------------------------------------ unsupported and infeasible


class TestUnsupported:
    @pytest.mark.parametrize("goal,code", [
        (g("concert", "e"), "unknown_entity"),  # fixture 6
        (g("event", "e", c("colour", "eq", "red")), "unknown_field"),
        (g("event", "e", count=0), "absence_claim"),  # fixture 9
        (g("event", "e", c("title", "lt", "m")), "collation_dependent_order"),  # fixture 7
        (g("event", "e", c("active", "gt", False)), "boolean_order"),
        (g("event", "e", c("capacity", "eq", "3")), "cast_dependent"),  # fixture 8
        (g("event", "e", c("price", "eq", 2)), "cast_dependent"),
        (g("event", "e", c("capacity", "eq", 3.0)), "cast_dependent"),
        (g("event", "e", c("amount", "eq", 1.5)), "numeric_coercion"),
        (g("event", "e", c("amount", "eq", 1)), "cast_dependent"),
        (g("event", "e", c("starts", "eq", "2026-01-01")), "opaque_type"),
        (g("event", "e", c("title", "eq", None)), "null_comparison"),
        (g("event", "e", c("slug", "eq", "s")), "generated_column"),
        (g("event", "e", c("title", "ne", "x")), "string_value_required"),
        (g("event", "e", c("venue_id", "eq", 9)), "foreign_key_unresolved"),  # fixture 11
    ])
    def test_explicit_unsupported_outcomes(self, goal, code):
        result = plan(goal)
        assert outcome(result) == ("unsupported_goal", code)
        assert result.goals[0].status == "unsupported"

    def test_reference_to_a_planner_assigned_key_is_never_inferred(self):
        result = plan(g("event", "e", c("capacity", "eq", 1)), g("ticket", "t", c("event_id", "eq", 1),
                                                                 c("holder", "eq", "h")))
        assert outcome(result) == ("unsupported_goal", "foreign_key_unresolved")

    def test_a_filler_never_supplies_a_reference(self):
        config = context(mutate=lambda x: x["entities"][0]["columns"][9].update(
            nullable=False, filler={"type": "int", "value": 4}))
        result = plan(g("venue", "v", c("id", "eq", 4)), g("event", "e", c("capacity", "eq", 1)), config=config)
        assert outcome(result) == ("unsupported_goal", "foreign_key_value_not_from_goal")


class TestConflictsAndOverlap:
    @pytest.mark.parametrize("goal,code", [
        (g("event", "e", c("capacity", "is_null")), "not_null_violated"),  # fixture 3
        (g("event", "e", c("title", "is_null"), c("title", "ne", "x")), "null_with_comparison"),
        (g("event", "e", c("title", "is_null"), c("title", "not_null")), "null_with_comparison"),
        (g("event", "e", c("capacity", "eq", 3), c("capacity", "eq", 4)), "contradictory_values"),
        (g("event", "e", c("capacity", "eq", 3), c("capacity", "gt", 3)), "bounds_exclude_value"),
        (g("event", "e", c("code", "eq", "toolong99")), "too_long_for_column"),
        (g("event", "e", c("id", "eq", 1), count=2), "duplicate_key_within_goal"),
        (g("event", "e", c("code", "eq", "A"), count=2), "duplicate_key_within_goal"),
        (g("event", "e", c("active", "ne", False), c("active", "ne", True)), "empty_domain"),
    ])
    def test_sound_contradictions(self, goal, code):
        assert outcome(plan(goal)) == ("infeasible_under_assumptions", code)

    def test_a_nullable_column_can_be_null(self):
        assert plan(g("event", "e", c("title", "is_null"))).outcome == "candidate_plan"  # fixture 3

    def test_a_duplicate_within_a_goal_is_found_behind_another_goals_key(self):
        result = plan(g("event", "a", c("id", "eq", 1)), g("event", "b", c("id", "eq", 1), count=2))
        assert outcome(result) == ("infeasible_under_assumptions", "duplicate_key_within_goal")
        assert result.goal_label == "b"

    def test_goals_sharing_a_key_are_unsupported_not_infeasible(self):
        result = plan(g("event", "a", c("id", "eq", 1), c("capacity", "eq", 3)),
                      g("event", "b", c("id", "eq", 1), c("capacity", "ge", 2)))
        assert outcome(result) == ("unsupported_goal", "overlapping_goal_keys")

    def test_a_filler_on_a_unique_column_collides(self):
        config = context(mutate=lambda x: x["entities"][0]["columns"][8].update(
            nullable=False, filler={"type": "str", "value": "C"}))
        assert outcome(plan(g("event", "e", count=2), config=config)) == ("unsupported_goal",
                                                                          "filler_collides_on_unique")

    def test_overlapping_cardinality_is_reported_not_proven(self):
        result = plan(g("event", "a", c("capacity", "eq", 3)), g("event", "b", c("capacity", "ge", 2)),
                      g("event", "c", c("reserved", "eq", 0)))
        goals = {s.label: s for s in result.goals}
        assert goals["b"].also_matching_rows == ("a#0", "c#0"), "c#0 gets the trusted filler capacity 10"
        assert goals["c"].undetermined_rows == ("a#0", "b#0"), "reserved has a database default"
        assert all("exact_count_unverified" in r.obligations for r in result.request.rows)

    def test_infeasibility_outranks_an_unsupported_goal(self):
        result = plan(g("concert", "a"), g("event", "b", c("capacity", "is_null")))
        assert outcome(result) == ("infeasible_under_assumptions", "not_null_violated")
        assert [s.status for s in result.goals] == ["unsupported", "infeasible"]

    def test_every_goal_is_examined_and_reported(self):
        result = plan(g("concert", "a"), g("event", "b", c("title", "lt", "m")), g("event", "c"))
        assert [(s.label, s.status, s.code) for s in result.goals] == [
            ("a", "unsupported", "unknown_entity"), ("b", "unsupported", "collation_dependent_order"),
            ("c", "planned", None)]


# ------------------------------------------------------------------ budgets and Phase 9B


class TestBudgets:
    def test_more_than_ten_rows_is_a_candidate_marked_for_phase_9b(self):  # fixture 12
        result = plan(g("event", "e", count=11))
        assert result.outcome == "candidate_plan" and len(result.request.rows) == 11
        assert result.request.phase9b_constructor_compatible is False and result.request.phase9b_notes
        assert plan(g("event", "e", count=10)).request.phase9b_constructor_compatible is True

    def test_sixty_four_rows_are_allowed(self):
        goals = [g("event", f"g{i}", count=16) for i in range(4)]
        assert len(plan(*goals).request.rows) == 64

    def test_row_budget_is_indeterminate(self):
        result = plan(g("event", "e", count=5), budget=sp.PlanningBudget(max_rows=4))
        assert outcome(result) == ("indeterminate", "row_budget")

    @pytest.mark.parametrize("budget,code", [
        (sp.PlanningBudget(max_expansions=3), "expansions_budget"),
        (sp.PlanningBudget(max_constraint_checks=2), "constraint_checks_budget"),
    ])
    def test_exhaustion_is_never_infeasibility(self, budget, code):
        goal = g("event", "e", *[c("capacity", "ne", n) for n in range(1, 9)], c("capacity", "gt", 0))
        result = plan(goal, budget=budget)
        assert outcome(result) == ("indeterminate", code)
        assert plan(goal).outcome == "candidate_plan"

    @pytest.mark.parametrize("goal", [
        g("event", "e", c("capacity", "ge", 1), c("capacity", "le", 2), c("capacity", "ne", 1),
          c("capacity", "ne", 2)),
        g("event", "e", c("capacity", "ge", 5), c("capacity", "le", 3)),
        g("event", "e", c("price", "ge", 1.0), c("price", "le", 1.0), c("price", "ne", 1.0)),
    ])
    def test_static_infeasibility_needs_no_search_budget(self, goal):
        result = plan(goal, budget=sp.PlanningBudget(max_expansions=1))
        assert outcome(result) == ("infeasible_under_assumptions", "empty_domain")
        assert result.counters.expansions == 0

    def test_counters_are_recorded_and_bounded(self):
        result = plan(g("event", "e", c("capacity", "gt", 0), count=2))
        counters = result.counters
        assert (counters.rows, counters.max_rows) == (2, 64)
        assert 0 < counters.expansions <= counters.max_expansions
        assert 0 < counters.constraint_checks <= counters.max_constraint_checks

    def test_different_sufficient_budgets_give_the_same_request(self):
        goal = g("event", "e", c("capacity", "gt", 0), count=3)
        small = plan(goal, budget=sp.PlanningBudget(max_expansions=50, max_constraint_checks=200))
        large = plan(goal, budget=sp.PlanningBudget(max_expansions=100_000, max_constraint_checks=1_000_000))
        assert small.request == large.request and small.counters.expansions == large.counters.expansions

    def test_budget_limits(self):
        with pytest.raises(ValueError):
            sp.PlanningBudget(max_rows=65)
        assert "seconds" not in json.dumps(sp.PlanningBudget.model_json_schema())


# ------------------------------------------------------------------ the trusted context


class TestTrustedContext:
    def invalid(self, config=None, trusted=None):
        result = plan(g("event", "e"), config=config if config is not None else context(), trusted=trusted)
        assert result.outcome == "invalid_trusted_context"
        return " ".join(result.reasons)

    def test_stale_or_forged_pin(self):  # fixture 14
        config = context()
        assert "pinned" in self.invalid(config, sp.TrustedPlanning(reviewer="r", pinned_digest="0" * 64))
        changed = context(mutate=lambda x: x["entities"][1]["columns"][1].update(filler=None))
        assert "pinned" in self.invalid(changed, pin(config))

    @pytest.mark.parametrize("trusted", [None, {"reviewer": "r", "pinned_digest": "0" * 64}, "pin",
                                         sp.TrustedPlanning.model_construct(reviewer="Bad Name!",
                                                                            pinned_digest="0" * 64)])
    def test_trust_comes_only_from_a_harness_object(self, trusted):
        config = context()
        result = sp.plan_state(validated(), registry=make_registry(), contracts=make_contracts(), context=config,
                               trusted=trusted)
        assert result.outcome == "invalid_trusted_context" and "TrustedPlanning" in result.reasons[0]

    def test_bound_to_registry_and_application(self):
        other = context(mutate=lambda x: x.update(registry_digest="f" * 64))
        assert "different registry" in self.invalid(other, pin(other))
        app = context(mutate=lambda x: x.update(application="other_app"))
        assert "different application" in self.invalid(app, pin(app))

    def test_build_context_checks_the_application_itself_and_reports_every_reason(self):
        app = context(mutate=lambda x: x.update(application="other_app"))
        with pytest.raises(sp.ContextError, match="different application"):
            sp.build_context(app, registry=make_registry(), trusted=pin(app))
        both = context(mutate=lambda x: x.update(application="other_app", registry_digest="f" * 64))
        result = plan(g("event", "e"), config=both, trusted=pin(both))
        assert result.reasons == ("the schema context is bound to a different registry",
                                  "the schema context is for a different application")

    @pytest.mark.parametrize("mutate,phrase", [
        (lambda x: x["entities"][0]["columns"][1].update(pg_type="money"), "pg_type"),
        (lambda x: x["entities"][0]["columns"].append(column("title", "text")), "repeated"),
        (lambda x: x["entities"][0]["foreign_keys"][0].update(references_entity="nowhere"), "unknown"),
        (lambda x: x["entities"][0]["columns"][1].update(filler={"type": "str", "value": "ten"}), "filler"),
        (lambda x: x["entities"][1].update(key_assignment="ascending_int", primary_key=["name"]), "key assignment"),
        (lambda x: x.update(trusted=True), "Extra inputs"),
        (lambda x: x.update(digest="0" * 64), "Extra inputs"),
        (lambda x: x["entities"][0].update(sql="DROP TABLE events"), "Extra inputs"),
        (lambda x: x["entities"][0]["columns"][3].update(column="title; DROP"), "pattern"),
        (lambda x: x["entities"][0]["columns"][3].update(filler={"type": "str", "value": "x‮"}), "refused"),
        (lambda x: x.update(context_version=2), "context_version"),
        (lambda x: x.pop("encoding"), "encoding"),
        (lambda x: x.update(encoding="SQL_ASCII"), "encoding"),
        (lambda x: x["entities"][0]["columns"][3].pop("collation"), "collation"),
        (lambda x: x["entities"][0]["columns"][1].update(collation="deterministic"), "collation"),
        (lambda x: x["entities"][0]["unique"][0].pop("nulls"), "nulls"),
        (lambda x: x["entities"][0]["unique"][0].update(nulls="maybe"), "nulls"),
        (lambda x: x["entities"][0].update(unique=[["code"]]), "unique"),
    ])
    def test_inconsistent_or_untrusted_content(self, mutate, phrase):
        config = context(mutate=mutate)
        assert phrase in self.invalid(config, pin(config))

    def test_digest_is_order_insensitive_and_excludes_nothing(self):
        config = context()
        reordered = copy(config)
        reordered["entities"].reverse()
        reordered["entities"][0]["columns"].reverse()
        digest = sp.context_digest(sp.SchemaContextConfig.model_validate(config))
        assert sp.context_digest(sp.SchemaContextConfig.model_validate(reordered)) == digest
        changed = context(mutate=lambda x: x.update(schema_digest="6" * 64))
        assert sp.context_digest(sp.SchemaContextConfig.model_validate(changed)) != digest


# ------------------------------------------------------------------ determinism, purity and isolation


class TestDeterminism:
    GOALS = [g("venue", "v", c("id", "eq", 4)), g("event", "e", c("venue_id", "eq", 4), c("capacity", "gt", 2),
                                                  count=2), g("audit_log", "a")]

    def test_identical_inputs_give_byte_identical_output(self):  # fixture 16
        outputs = {plan(*self.GOALS).model_dump_json() for _ in range(5)}
        assert len(outputs) == 1

    def test_goal_and_constraint_order_are_irrelevant(self):
        reordered = [copy(goal) for goal in reversed(self.GOALS)]
        for goal in reordered:
            goal["constraints"].reverse()
        assert plan(*reordered).model_dump_json() == plan(*self.GOALS).model_dump_json()

    def test_request_is_bound_to_the_plan_and_context(self):
        result = plan(*self.GOALS)
        registry = make_registry()
        workload = validated(book("a"), book("b"), goals=self.GOALS)
        identity = wi.validated_plan_identity(workload, registry=registry, contracts=make_contracts(registry))
        request = result.request
        assert request.plan_identity == identity.identity.value
        assert request.context_digest == pin(context()).pinned_digest and request.schema_digest == SCHEMA_DIGEST
        assert all(r.plan_identity == request.plan_identity and r.context_digest == request.context_digest
                   for r in request.rows)
        assert request.request_digest == sp.request_digest(request)

    def test_request_digest_is_pinned_and_domain_separated(self):
        """Changing the request encoding changes every digest, so it must be a deliberate, versioned change."""
        request = plan(*self.GOALS).request
        assert request.request_digest == PINNED_REQUEST
        import hashlib

        bare = json.dumps(sp._typed_tree(request.model_dump(mode="python", exclude={"request_digest"})),
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert request.request_digest != hashlib.sha256(bare.encode()).hexdigest()

    def test_reasons_dont_depend_on_constraint_order(self):
        forwards = plan(g("event", "e", c("colour", "eq", 1), c("weight", "eq", 2)))
        backwards = plan(g("event", "e", c("weight", "eq", 2), c("colour", "eq", 1)))
        assert forwards.reason == backwards.reason and "colour" in forwards.reason

    def test_typed_values_are_distinct_in_the_digest(self):
        a = plan(g("event", "e", c("active", "eq", True), c("capacity", "eq", 1)))
        b = plan(g("event", "e", c("active", "eq", False), c("capacity", "eq", 1)))
        assert a.request.request_digest != b.request.request_digest

    def test_nothing_is_modified(self):
        registry = make_registry()
        workload = validated(book("a"), book("b"), goals=self.GOALS)
        before = workload.model_dump_json()
        identity = wi.validated_plan_identity(workload, registry=registry, contracts=make_contracts(registry))
        config = context(registry)
        snapshot = json.dumps(config, sort_keys=True)
        sp.plan_state(workload, registry=registry, contracts=make_contracts(registry), context=config,
                      trusted=pin(config))
        assert workload.model_dump_json() == before and json.dumps(config, sort_keys=True) == snapshot
        again = wi.validated_plan_identity(workload, registry=registry, contracts=make_contracts(registry))
        assert again == identity


class TestIsolation:
    def test_imports_no_engine_database_oracle_or_phase_9b(self):
        code = ("import sys, deployspecter.live.state_planning;"
                "print(sorted(m for m in sys.modules if m.startswith(('psycopg', 'langgraph', 'live_app',"
                " 'deployspecter.live.runner', 'deployspecter.live.driver', 'deployspecter.live.supervisor',"
                " 'deployspecter.live.worker', 'deployspecter.live.workload', 'deployspecter.database',"
                " 'deployspecter.oracle', 'deployspecter.scenarios', 'deployspecter.state_constructor',"
                " 'deployspecter.seed_feasibility', 'deployspecter.triggers', 'deployspecter.meta_client'))"
                " and m not in ('deployspecter.live.workload_spec', 'deployspecter.live.workload_identity')))")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "[]"

    def test_no_dynamic_code_files_or_sql(self):
        source = Path(sp.__file__).read_text()
        tree = ast.parse(source)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "input"}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "socket", "psycopg", "pathlib", "urllib", "sqlite3"}
        strings = " ".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        for keyword in ("INSERT INTO", "SELECT ", "UPDATE ", "DELETE FROM", "CREATE ", "DROP "):
            assert keyword not in strings.upper()

    def test_no_network_or_database(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no network or database access is allowed here")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        assert plan(g("event", "e", c("capacity", "eq", 1))).outcome == "candidate_plan"

    def test_requests_hold_identifiers_and_typed_values_only(self):
        result = plan(g("event", "e", c("title", "eq", "'; DROP TABLE events; --")))
        row = result.request.rows[0]
        assert set(sp.CandidateRow.model_fields) == {"row_id", "goal_label", "ordinal", "entity", "table", "values",
                                                     "omitted", "obligations", "context_digest", "plan_identity"}
        assert values(result)["title"] == ("str", "'; DROP TABLE events; --", "goal_constraint"), "inert data"
        assert row.table == "events"

    def test_candidate_values_refuse_coercion(self):
        with pytest.raises(ValueError):
            sp.CandidateValue(field="f", column="f", type="int", value=True, origin="goal_constraint")
        with pytest.raises(ValueError):
            sp.CandidateValue(field="f", column="f", type="float", value=1, origin="goal_constraint")


PINNED_REQUEST = "17459d16cd405b0e077ae6eea40fdc88dc9945fa9d000c40300bf82455620d94"


# ------------------------------------------------------------------ uniqueness, NULLs and soundness of infeasibility


def with_code_nulls(nulls):
    return context(mutate=lambda x: x["entities"][0]["unique"][0].update(nulls=nulls))


class TestUniquenessAndNulls:
    def test_nulls_distinct_lets_rows_share_a_null(self):
        config = with_code_nulls("distinct")
        assert plan(g("event", "e", c("code", "is_null"), count=2), config=config).outcome == "candidate_plan"
        assert plan(g("event", "e", count=2), config=config).outcome == "candidate_plan"

    def test_nulls_not_distinct_makes_explicit_nulls_collide(self):
        config = with_code_nulls("not_distinct")
        result = plan(g("event", "e", c("code", "is_null"), count=2), config=config)
        assert outcome(result) == ("infeasible_under_assumptions", "duplicate_key_within_goal")
        assert "unique NULLS NOT DISTINCT" in result.evidence.context_facts[0]
        assert sp.SQL_NULL in result.evidence.assumptions and sp.COUNT_DISTINCT in result.evidence.assumptions

    def test_nulls_not_distinct_with_unset_columns_is_unsupported_not_infeasible(self):
        result = plan(g("event", "e", count=2), config=with_code_nulls("not_distinct"))
        assert outcome(result) == ("unsupported_goal", "unset_nulls_collide")

    def test_nulls_not_distinct_across_goals_is_overlap(self):
        result = plan(g("event", "a", c("code", "is_null")), g("event", "b", c("code", "is_null")),
                      config=with_code_nulls("not_distinct"))
        assert outcome(result) == ("unsupported_goal", "overlapping_goal_keys")

    def test_a_database_default_in_a_unique_constraint_is_never_taken_as_null(self):
        config = context(mutate=lambda x: x["entities"][0]["unique"].append(
            {"fields": ["reserved"], "nulls": "not_distinct"}))
        result = plan(g("event", "e", count=2), config=config)
        assert result.outcome == "candidate_plan"
        assert all("unique_with_database_values" in r.obligations for r in result.request.rows)

    def test_the_nulls_policy_is_part_of_the_pinned_context(self):
        distinct, not_distinct = with_code_nulls("distinct"), with_code_nulls("not_distinct")
        assert pin(distinct).pinned_digest != pin(not_distinct).pinned_digest


class TestInfeasibilityIsSound:
    def test_a_repeated_is_null_is_not_a_contradiction(self):
        assert plan(g("event", "e", c("title", "is_null"), c("title", "is_null"))).outcome == "candidate_plan"

    def test_string_equality_under_a_nondeterministic_collation_is_unsupported(self):
        config = context(mutate=lambda x: x["entities"][0]["columns"][3].update(collation="nondeterministic"))
        for goal in (g("event", "e", c("title", "eq", "a"), c("title", "eq", "A")),
                     g("event", "e", c("title", "ne", "a"))):
            assert outcome(plan(goal, config=config)) == ("unsupported_goal", "collation_dependent_equality")

    def test_an_unsupported_feature_blocks_infeasibility_for_its_goal(self):
        result = plan(g("event", "e", c("capacity", "eq", 3), c("capacity", "eq", 4), c("colour", "eq", 1)))
        assert outcome(result) == ("unsupported_goal", "unknown_field")

    INFEASIBLE = [
        (g("event", "e", c("capacity", "is_null")), "not_null_violated", "NOT NULL"),
        (g("event", "e", c("title", "is_null"), c("title", "ne", "x")), "null_with_comparison", "nullable"),
        (g("event", "e", c("capacity", "eq", 3), c("capacity", "eq", 4)), "contradictory_values", "integer"),
        (g("event", "e", c("capacity", "eq", 3), c("capacity", "gt", 3)), "bounds_exclude_value", "integer"),
        (g("event", "e", c("capacity", "eq", 2**31)), "outside_type_range", "2147483647"),
        (g("event", "e", c("code", "eq", "toolong99")), "too_long_for_column", "UTF8"),
        (g("event", "e", c("capacity", "ge", 5), c("capacity", "le", 3)), "empty_domain", "integer holds"),
        (g("event", "e", c("price", "ge", 1.0), c("price", "le", 1.0), c("price", "ne", 1.0)), "empty_domain",
         "binary64"),
        (g("event", "e", c("active", "ne", False), c("active", "ne", True)), "empty_domain", "boolean"),
        (g("event", "e", c("id", "ge", 1), c("id", "le", 2), count=3), "insufficient_distinct_values", "unique"),
        (g("event", "e", c("id", "eq", 1), count=2), "duplicate_key_within_goal", "primary key"),
    ]

    @pytest.mark.parametrize("goal,code,fact", INFEASIBLE, ids=[f"{i}-{x[1]}" for i, x in enumerate(INFEASIBLE)])
    def test_every_infeasibility_shows_its_contradiction(self, goal, code, fact):
        result = plan(goal)
        assert outcome(result) == ("infeasible_under_assumptions", code)
        evidence = result.evidence
        assert evidence.goals == ("e",) and evidence.entity == "event" and evidence.fields
        assert evidence.constraints, "the constraints that contradict are named"
        assert any(fact in f for f in evidence.context_facts), evidence.context_facts
        assert sp.CONTEXT_EXACT in evidence.assumptions and result.assumptions == evidence.assumptions
        assert result.goals[0].evidence == evidence

    def test_evidence_lists_only_the_assumptions_it_uses(self):
        assert plan(g("event", "e", c("capacity", "is_null"))).evidence.assumptions == (sp.CONTEXT_EXACT,)
        assert plan(g("event", "e", c("title", "is_null"), c("title", "ne", "x"))).evidence.assumptions == (
            sp.SQL_NULL, sp.CONTEXT_EXACT)
        assert plan(g("event", "e", c("id", "eq", 1), count=2)).evidence.assumptions == (
            sp.COUNT_DISTINCT, sp.CONTEXT_EXACT)

    def test_infeasibility_needs_evidence(self):
        with pytest.raises(TypeError):
            sp._infeasible("empty_domain", "no evidence")
