"""Offline tests of literal translation and its comparison with the planner (step 5C.2). Synthetic inputs only."""

from __future__ import annotations

import ast
import json
import math
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from deployspecter.live import literal_translation as lt
from deployspecter.live import state_planning as sp
from deployspecter.live import workload_spec as ws

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_argument_validation import book, copy, make_contracts, make_registry  # noqa: E402
from test_state_planning import c, column, context, g, pin  # noqa: E402
from test_workload_identity import validated  # noqa: E402

FULL = [c("id", "eq", 7), c("capacity", "eq", 3), c("active", "eq", True)]  # event's required columns


def full(*more):
    return [*FULL, *more]


def inputs(*goals, config=None, trusted=None, budget=None):
    registry = make_registry()
    config = config if config is not None else context(registry)
    workload = validated(book("a"), book("b"), goals=list(goals))
    return workload, {"registry": registry, "contracts": make_contracts(registry), "context": config,
                      "trusted": trusted if trusted is not None else pin(config), "budget": budget}


def literal(*goals, **kw):
    workload, args = inputs(*goals, **kw)
    return lt.translate_literally(workload, **args)


def both(*goals, **kw):
    workload, args = inputs(*goals, **kw)
    return lt.compare_with_planner(workload, **args)


def values(result, row=0):
    return {v.field: (v.type, v.value, v.origin) for v in result.request.rows[row].values}


def outcome(result):
    return result.outcome, getattr(result, "code", None)


def with_keyless(config):
    config["entities"].append({"entity": "note_entry", "table": "note_entries",
                               "columns": [column("body", "text", nullable=False)]})


# ------------------------------------------------------------------ the literal rules


class TestLiteralRules:
    def test_equality_literals_are_translated_exactly(self):
        result = literal(g("event", "e", *full(c("title", "eq", "Gala"), c("price", "eq", 0.1))))
        assert result.outcome == "literal_candidate"
        request = result.request
        assert request.method == "literal_translation" and request.feasibility == sp.FEASIBILITY
        assert values(result) == {"id": ("int", 7, "goal_constraint"), "capacity": ("int", 3, "goal_constraint"),
                                  "active": ("bool", True, "goal_constraint"),
                                  "title": ("str", "Gala", "goal_constraint"),
                                  "price": ("float", 0.1, "goal_constraint")}
        assert result.counters.expansions == 0, "no search, ever"

    def test_exact_types_at_the_boundaries(self):
        result = literal(g("event", "e", *full(c("big", "eq", 2**63 - 1), c("small", "eq", -(2**15)),
                                               c("price", "eq", 5e-324))))
        got = values(result)
        assert got["big"] == ("int", 9223372036854775807, "goal_constraint") and type(got["big"][1]) is int
        assert got["small"][1] == -32768 and got["price"][1] == 5e-324 and type(got["price"][1]) is float
        assert type(got["active"][1]) is bool

    def test_explicit_null_and_missing_stay_distinct(self):
        result = literal(g("event", "e", *full(c("title", "is_null"))))
        row = result.request.rows[0]
        assert values(result)["title"] == ("null", None, "goal_constraint")
        omitted = {o.field: o.reason for o in row.omitted}
        assert "title" not in omitted and omitted["price"] == "nullable_unset"

    def test_is_null_on_a_not_null_column_is_a_contradiction(self):
        result = literal(g("event", "e", c("id", "eq", 7), c("capacity", "is_null"), c("active", "eq", True)))
        assert outcome(result) == ("infeasible_under_assumptions", "not_null_violated")

    def test_is_null_needs_every_other_required_value(self):
        assert outcome(literal(g("event", "e", c("title", "is_null")))) == ("unsupported_goal", "key_not_literal")

    @pytest.mark.parametrize("goal,code", [
        (g("event", "e", c("capacity", "eq", 3), c("active", "eq", True)), "key_not_literal"),
        (g("event", "e", c("id", "eq", 7), c("active", "eq", True)), "required_column_unresolved"),
        (g("event", "e", c("id", "eq", 7), c("capacity", "eq", 3)), "required_column_unresolved"),
    ], ids=["no_key_generation", "no_filler_for_capacity", "no_filler_for_active"])
    def test_no_keys_or_fillers_are_generated(self, goal, code):
        assert outcome(literal(goal)) == ("unsupported_goal", code)

    @pytest.mark.parametrize("extra", [c("big", "gt", 5), c("big", "not_null"), c("big", "ne", 4),
                                       c("price", "lt", 1.0)])
    def test_no_value_is_chosen_from_a_comparison(self, extra):
        assert outcome(literal(g("event", "e", *full(extra)))) == ("unsupported_goal", "value_synthesis_required")

    def test_comparisons_only_check_a_stated_literal(self):
        assert literal(g("event", "e", *full(c("big", "eq", 5), c("big", "ne", 4), c("big", "gt", 1)))).outcome \
            == "literal_candidate"
        assert outcome(literal(g("event", "e", *full(c("big", "eq", 5), c("big", "gt", 5))))) == (
            "infeasible_under_assumptions", "bounds_exclude_value")

    @pytest.mark.parametrize("extra,code", [
        (c("capacity", "eq", "3"), "cast_dependent"), (c("price", "eq", 2), "cast_dependent"),
        (c("big", "eq", 1.0), "cast_dependent"), (c("amount", "eq", 1.5), "numeric_coercion"),
        (c("title", "lt", "m"), "collation_dependent_order"), (c("active", "gt", False), "boolean_order"),
        (c("title", "eq", None), "null_comparison"), (c("starts", "eq", "x"), "opaque_type"),
        (c("slug", "eq", "s"), "generated_column"), (c("colour", "eq", 1), "unknown_field"),
    ])
    def test_no_casts_and_the_planners_type_rules(self, extra, code):
        assert outcome(literal(g("event", "e", *full(extra)))) == ("unsupported_goal", code)

    def test_defaults_and_trigger_effects_are_never_inferred(self):
        result = literal(g("event", "e", *full()), g("ticket", "t", c("id", "eq", 1), c("event_id", "eq", 7),
                                                    c("holder", "eq", "h")))
        rows = {r.entity: r for r in result.request.rows}
        assert {o.field: o.reason for o in rows["event"].omitted}["reserved"] == "database_default"
        assert "insert_triggers_unverified" in rows["ticket"].obligations
        assert [(d.child_row, d.parent_row) for d in result.request.dependencies] == [("t#0", "e#0")]

    def test_foreign_keys_need_a_literal_parent(self):
        assert outcome(literal(g("event", "e", *full(c("venue_id", "eq", 9))))) == ("unsupported_goal",
                                                                                   "foreign_key_unresolved")
        ok = literal(g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall")),
                     g("event", "e", *full(c("venue_id", "eq", 4))))
        assert ok.outcome == "literal_candidate" and ok.request.dependencies[0].parent_row == "v#0"

    def test_a_foreign_key_set_by_a_database_default_is_refused(self):
        config = context(mutate=lambda x: x["entities"][0]["columns"][9].update(has_default=True))
        result = literal(g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall")), g("event", "e", *full()),
                         config=config, trusted=pin(config))
        assert outcome(result) == ("unsupported_goal", "foreign_key_unresolved")

    def test_rows_carry_no_method_so_the_request_must(self):
        """Rows equal the planner's; method provenance lives on the request and has to travel with the rows."""
        result = literal(g("event", "e", *full()))
        assert "method" not in sp.CandidateRow.model_fields and result.request.method == "literal_translation"

    def test_a_foreign_key_matches_only_the_exact_literal(self):
        result = literal(g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall")),
                         g("event", "e", *full(c("venue_id", "eq", 5))))
        assert outcome(result) == ("unsupported_goal", "foreign_key_unresolved")

    def test_reasons_dont_depend_on_constraint_order(self):
        forwards = literal(g("event", "e", c("colour", "eq", 1), c("weight", "eq", 2)))
        backwards = literal(g("event", "e", c("weight", "eq", 2), c("colour", "eq", 1)))
        assert forwards.reason == backwards.reason and "colour" in forwards.reason

    def test_cardinality_never_invents_identifiers(self):
        assert outcome(literal(g("event", "e", *full(), count=2))) == ("unsupported_goal", "literal_cardinality")
        keyless = context(mutate=with_keyless)
        result = literal(g("note_entry", "n", c("body", "eq", "same"), count=3), config=keyless,
                         trusted=pin(keyless))
        assert result.outcome == "literal_candidate" and len(result.request.rows) == 3
        assert len({json.dumps(values(result, i)) for i in range(3)}) == 1, "identical literal rows, no keys added"

    def test_overlapping_goals_are_never_merged(self):
        same_key = literal(g("event", "a", *full()), g("event", "b", *full(c("title", "eq", "t"))))
        assert outcome(same_key) == ("unsupported_goal", "overlapping_goal_keys")
        same_code = literal(g("event", "a", *full(c("code", "eq", "A"))),
                            g("event", "b", c("id", "eq", 8), c("capacity", "eq", 3), c("active", "eq", True),
                              c("code", "eq", "A")))
        assert outcome(same_code) == ("unsupported_goal", "overlapping_goal_keys")

    def test_nulls_not_distinct_is_respected(self):
        config = context(mutate=lambda x: x["entities"][0]["unique"][0].update(nulls="not_distinct"))
        second = [c("id", "eq", 8), c("capacity", "eq", 3), c("active", "eq", True)]
        explicit = literal(g("event", "a", *full(c("code", "is_null"))), g("event", "b", *second, c("code", "is_null")),
                           config=config, trusted=pin(config))
        assert outcome(explicit) == ("unsupported_goal", "overlapping_goal_keys")
        unset = literal(g("event", "a", *full()), g("event", "b", *second), config=config, trusted=pin(config))
        assert outcome(unset) == ("unsupported_goal", "unset_nulls_collide")

    @pytest.mark.parametrize("extra,code", [
        (c("capacity", "eq", 4), "contradictory_values"), (c("title", "is_null"), None),
        (c("code", "eq", "toolong99"), "too_long_for_column"),
    ])
    def test_contradictions_carry_evidence(self, extra, code):
        result = literal(g("event", "e", *full(extra)))
        if code is None:
            assert result.outcome == "literal_candidate"
            return
        assert outcome(result) == ("infeasible_under_assumptions", code)
        assert result.evidence.goals == ("e",) and result.evidence.constraints and result.evidence.context_facts

    def test_sound_infeasibility_independent_of_other_columns(self):
        assert outcome(literal(g("event", "e", *full(c("title", "is_null"), c("title", "ne", "x"))))) == (
            "infeasible_under_assumptions", "null_with_comparison")
        assert outcome(literal(g("event", "e", c("id", "eq", 2**31), c("capacity", "eq", 3),
                                 c("active", "eq", True)))) == ("infeasible_under_assumptions", "outside_type_range")

    def test_explicit_unsupported_outcomes_are_all_reported(self):
        result = literal(g("concert", "a"), g("event", "b", count=0), g("event", "c", c("capacity", "eq", 1)))
        assert [(s.label, s.status, s.code) for s in result.goals] == [
            ("a", "unsupported", "unknown_entity"), ("b", "unsupported", "absence_claim"),
            ("c", "unsupported", "key_not_literal")]

    def test_infeasibility_outranks_unsupported(self):
        result = literal(g("concert", "a"), g("event", "b", *full(c("capacity", "eq", 4))))
        assert outcome(result) == ("infeasible_under_assumptions", "contradictory_values")


class TestTrustAndBudget:
    def test_forged_workloads_are_refused_before_anything_else(self):
        workload, args = inputs(g("event", "e", *full()))
        forged = workload.model_copy(update={"contracts_digest": "0" * 64})
        with pytest.raises(sp.UntrustedWorkload) as caught:
            lt.translate_literally(forged, **{**args, "context": {"bad": 1}, "trusted": None, "budget": "x"})
        assert str(caught.value) == "validated against a different contract set"

    def test_untrusted_context(self):
        config = context()
        result = literal(g("event", "e", *full()), config=config,
                         trusted=sp.TrustedPlanning(reviewer="r", pinned_digest="0" * 64))
        assert result.outcome == "invalid_trusted_context"

    def test_wrong_types(self):
        workload, args = inputs(g("event", "e", *full()))
        with pytest.raises(TypeError):
            lt.translate_literally(workload.authorized, **args)
        with pytest.raises(TypeError):
            lt.translate_literally(workload, **{**args, "budget": {"max_rows": 1}})

    def test_budgets_are_indeterminate_never_infeasible(self):
        assert outcome(literal(g("event", "e", *full()), budget=sp.PlanningBudget(max_rows=1))) == (
            "literal_candidate", None)
        keyless = context(mutate=with_keyless)
        assert outcome(literal(g("note_entry", "n", c("body", "eq", "b"), count=3), config=keyless,
                               trusted=pin(keyless), budget=sp.PlanningBudget(max_rows=2))) == ("indeterminate",
                                                                                               "row_budget")
        assert outcome(literal(g("event", "e", *full()), budget=sp.PlanningBudget(max_constraint_checks=1))) == (
            "indeterminate", "constraint_checks_budget")


class TestDeterminismAndIsolation:
    GOALS = [g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall")),
             g("event", "e", *full(c("venue_id", "eq", 4), c("price", "eq", 2.5)))]

    def test_byte_identical_and_order_independent(self):
        outputs = {literal(*self.GOALS).model_dump_json() for _ in range(5)}
        assert len(outputs) == 1
        reordered = [copy(goal) for goal in reversed(self.GOALS)]
        for goal in reordered:
            goal["constraints"].reverse()
        assert literal(*reordered).model_dump_json() == outputs.pop()

    def test_request_digest_is_pinned_and_distinct_from_the_planners(self):
        result = literal(*self.GOALS)
        assert result.request.request_digest == PINNED_LITERAL == lt.request_digest(result.request)
        workload, args = inputs(*self.GOALS)
        planned = sp.plan_state(workload, **args)
        assert planned.request.rows == result.request.rows, "the same rows here"
        assert planned.request.request_digest != result.request.request_digest, "different domain and method"

    def test_inputs_are_not_modified(self):
        workload, args = inputs(*self.GOALS)
        before = (workload.model_dump_json(), json.dumps(args["context"], sort_keys=True))
        lt.translate_literally(workload, **args)
        lt.compare_with_planner(workload, **args)
        assert (workload.model_dump_json(), json.dumps(args["context"], sort_keys=True)) == before

    def test_imports_no_engine_database_oracle_or_phase_9b(self):
        code = ("import sys, deployspecter.live.literal_translation;"
                "print(sorted(m for m in sys.modules if m.startswith(('psycopg', 'langgraph', 'live_app',"
                " 'deployspecter.live.runner', 'deployspecter.live.driver', 'deployspecter.live.supervisor',"
                " 'deployspecter.database', 'deployspecter.oracle', 'deployspecter.scenarios',"
                " 'deployspecter.state_constructor', 'deployspecter.seed_feasibility', 'deployspecter.triggers'))))")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "[]"

    def test_no_dynamic_code_files_or_sql(self):
        tree = ast.parse(Path(lt.__file__).read_text())
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "input"}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "socket", "psycopg", "pathlib", "random"}

    def test_no_network_or_database(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no network or database access is allowed here")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        assert literal(*self.GOALS).outcome == "literal_candidate"


# ------------------------------------------------------------------ comparison with the planner (part D)


class TestComparison:
    def test_both_give_the_same_rows(self):
        result = both(g("event", "e", *full(c("title", "eq", "Gala"))))
        assert result.both_accept and result.same_rows and result.row_differences == ()
        assert result.obligations_only_planner == result.obligations_only_literal == ()
        assert not result.assumptions_differ
        assert result.literal_counters.expansions == 0 < result.planner_counters.expansions

    @pytest.mark.parametrize("goals,literal_code", [
        ([g("event", "e", c("capacity", "eq", 3))], "key_not_literal"),
        ([g("event", "e", *full(c("big", "gt", 5)))], "value_synthesis_required"),
        ([g("event", "e", c("capacity", "eq", 3), count=3)], "literal_cardinality"),
        ([g("venue", "v", c("id", "eq", 4)), g("event", "e", *full(c("venue_id", "eq", 4)))],
         "required_column_unresolved"),
    ], ids=["key_generation", "comparison_choice", "invented_identifiers", "filler"])
    def test_the_planner_succeeds_where_literal_translation_cannot(self, goals, literal_code):
        result = both(*goals)
        assert result.planner_only and (result.literal_outcome, result.literal_code) == ("unsupported_goal",
                                                                                         literal_code)

    @pytest.mark.parametrize("goal,code", [
        (g("event", "e", *full(c("title", "lt", "m"))), "collation_dependent_order"),
        (g("concert", "e"), "unknown_entity"), (g("event", "e", *full(c("amount", "eq", 1.5))), "numeric_coercion"),
        (g("event", "e", *full(), count=0), "absence_claim"),
    ])
    def test_both_reject_unsupported_behaviour(self, goal, code):
        result = both(goal)
        assert (result.planner_outcome, result.planner_code) == (result.literal_outcome, result.literal_code) == (
            "unsupported_goal", code)

    def test_a_contradiction_both_identify(self):
        result = both(g("event", "e", *full(c("capacity", "eq", 4))))
        assert result.planner_code == result.literal_code == "contradictory_values"
        assert result.planner_outcome == result.literal_outcome == "infeasible_under_assumptions"

    def test_the_comparator_reports_row_differences(self, monkeypatch):
        """Unreachable through the public API today (literal acceptance implies the planner's rows are identical),
        so the comparator's own difference logic is exercised against an altered planner result."""
        real = sp.plan_state

        def altered(*args, **kwargs):
            result = real(*args, **kwargs)
            row = result.request.rows[0]
            values = tuple(v.model_copy(update={"value": 99}) if v.field == "capacity" else v for v in row.values)
            rows = (row.model_copy(update={"values": values}),)
            return result.model_copy(update={"request": result.request.model_copy(update={"rows": rows})})

        monkeypatch.setattr(lt.sp, "plan_state", altered)
        result = both(g("event", "e", *full()))
        assert result.both_accept and result.same_rows is False
        assert result.row_differences == ("e#0: differs in capacity",)

    def test_typed_boundaries_are_preserved_by_both(self):
        result = both(g("event", "e", *full(c("big", "eq", -(2**63)), c("price", "eq", 1.7976931348623157e308))))
        assert result.both_accept and result.same_rows

    def test_missing_trusted_information_stops_both(self):
        config = context()
        result = both(g("event", "e", *full()), config=config,
                      trusted=sp.TrustedPlanning(reviewer="r", pinned_digest="0" * 64))
        assert result.planner_outcome == result.literal_outcome == "invalid_trusted_context"
        assert not result.planner_only and not result.literal_only


# ------------------------------------------------------------------ properties


SAFE = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=40).filter(
    lambda s: not any(ws.forbidden_code_point(ch) for ch in s))
FINITE = st.floats(allow_nan=False, allow_infinity=False).filter(lambda x: not (x == 0.0 and math.copysign(1, x) < 0))


class TestProperties:
    @settings(max_examples=40, deadline=None)
    @given(big=st.integers(min_value=-(2**63), max_value=2**63 - 1), price=FINITE, title=SAFE)
    def test_literals_come_out_exactly_as_they_went_in(self, big, price, title):
        result = literal(g("event", "e", *full(c("big", "eq", big), c("price", "eq", price),
                                               c("title", "eq", title))))
        got = values(result)
        assert got["big"] == ("int", big, "goal_constraint") and type(got["big"][1]) is int
        assert got["price"][0] == "float" and got["price"][1].hex() == price.hex()
        assert got["title"] == ("str", title, "goal_constraint")

    @settings(max_examples=40, deadline=None)
    @given(fields=st.sets(st.sampled_from(["title", "price", "big", "small", "code"])), seed=st.integers(0, 50))
    def test_nothing_is_invented_and_literal_rows_agree_with_the_planner(self, fields, seed):
        literal_values = {"title": f"t{seed}", "price": float(seed) + 0.5, "big": seed, "small": seed,
                          "code": f"C{seed}"}
        extra = [c(f, "eq", literal_values[f]) for f in sorted(fields)]
        result = both(g("event", "e", *full(*extra)))
        assert result.both_accept and result.same_rows
        translated = literal(g("event", "e", *full(*extra)))
        stated = {"id", "capacity", "active", *fields}
        assert {v.field for v in translated.request.rows[0].values} == stated
        assert all(v.origin == "goal_constraint" for v in translated.request.rows[0].values)


PINNED_LITERAL = "ca0657e6c0c9b75cd3d5bda63431152988b8688adad689fa8bcf557d8ce48097"
