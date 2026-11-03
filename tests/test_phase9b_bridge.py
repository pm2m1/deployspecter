"""Offline tests of the typed Phase 9B bridge (step 5C.3). Synthetic contexts and snapshots only; nothing runs."""

from __future__ import annotations

import ast
import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from deployspecter.live import literal_translation as lt
from deployspecter.live import phase9b_bridge as pb
from deployspecter.live import state_planning as sp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_catalog_verification as tcv  # noqa: E402
from test_argument_validation import book, make_contracts, make_registry  # noqa: E402
from test_state_planning import c, g  # noqa: E402
from test_workload_identity import validated  # noqa: E402

EVENTS, VENUES, INT8, TEXT, BOOL = tcv.EVENTS, tcv.VENUES, tcv.INT8, tcv.TEXT, 16


def entity(config, name):
    return next(e for e in config["entities"] if e["entity"] == name)


def col(config, ent, field):
    return next(x for x in entity(config, ent)["columns"] if x["field"] == field)


def bigint_capacity():
    return (lambda s: tcv.attr(s, EVENTS, "capacity").update(atttypid=INT8),
            lambda c_: col(c_, "event", "capacity").update(pg_type="bigint"))


def boolean_flag():
    return (lambda s: s["attributes"].append(tcv.attribute(EVENTS, 9, "flag", BOOL)),
            lambda c_: entity(c_, "event")["columns"].append(tcv.column("flag", "boolean")))


def combine(*pairs):
    snaps = [p[0] for p in pairs]
    ctxs = [p[1] for p in pairs]
    return (lambda s: [f(s) for f in snaps], lambda c_: [f(c_) for f in ctxs])


def setup(*goals, change=None, method="planner", budget=None):
    snap_change, ctx_change = change or (None, None)
    s = tcv.snapshot()
    if snap_change:
        snap_change(s)
    config = tcv.context(tcv.digest_of(s))
    if ctx_change:
        ctx_change(config)
    registry = make_registry()
    contracts = make_contracts(registry)
    workload = validated(book("a"), book("b"), goals=list(goals))
    kw = {"registry": registry, "contracts": contracts, "context": config, "trusted": tcv.pin_context(config),
          "budget": budget}
    produced = sp.plan_state(workload, **kw) if method == "planner" else lt.translate_literally(workload, **kw)
    args = {"validated": workload, "registry": registry, "contracts": contracts, "context": config,
            "context_trust": tcv.pin_context(config), "snapshot": s, "snapshot_trust": tcv.pin_snapshot(s),
            "planning_budget": budget}
    return produced, args


def bridge(*goals, **kw):
    produced, args = setup(*goals, **kw)
    assert produced.outcome in {"candidate_plan", "literal_candidate"}, produced
    return pb.bridge_to_phase9b(produced.request, **args)


def typed(result, row=0):
    return {v.field: (v.type, v.value, v.legacy) for v in result.representation.rows[row].values}


EVENT = [c("id", "eq", 7), c("capacity", "eq", 3)]


# ------------------------------------------------------------------ exact preservation


class TestTypedPreservation:
    def test_exact_values_and_projection(self):
        result = bridge(g("event", "e", *EVENT, c("title", "eq", "Gala"), c("price", "eq", 5e-324)))
        assert result.outcome == "bridged"
        assert typed(result) == {"id": ("int", 7, "equivalent"), "capacity": ("int", 3, "equivalent"),
                                 "title": ("str", "Gala", "equivalent"), "price": ("float", 5e-324, "equivalent")}
        values = result.projection.rows[0]["values"]
        assert [(v["column"], v["value"]) for v in values] == [("capacity", 3), ("id", 7), ("price", 5e-324),
                                                               ("title", "Gala")]
        assert type(values[2]["value"]) is float and type(values[1]["value"]) is int
        assert result.projection.status == "equivalent_for_listed_columns_only"

    @pytest.mark.parametrize("value", [2**53 - 1, -(2**53 - 1), 0, 1])
    def test_integers_strictly_inside_2_53_are_equivalent(self, value):
        result = bridge(g("event", "e", c("id", "eq", 7), c("capacity", "eq", value)), change=bigint_capacity())
        assert typed(result)["capacity"] == ("int", value, "equivalent")

    @pytest.mark.parametrize("value", [2**53, -(2**53), 2**53 + 1, 2**63 - 1, -(2**63)])
    def test_integers_at_or_beyond_2_53_are_refused_but_kept_exactly(self, value):
        result = bridge(g("event", "e", c("id", "eq", 7), c("capacity", "eq", value)), change=bigint_capacity())
        assert (result.outcome, result.code, result.column) == ("refused", "matcher_ambiguous_integer", "capacity")
        assert typed(result)["capacity"] == ("int", value, "refused"), "the typed encoding is preserved exactly"

    @pytest.mark.parametrize("value", [1.7976931348623157e308, -1.7976931348623157e308, 5e-324, -2.5, 0.1])
    def test_finite_binary64_values_are_equivalent(self, value):
        result = bridge(g("event", "e", *EVENT, c("price", "eq", value)))
        assert typed(result)["price"] == ("float", value, "equivalent")
        assert result.projection.rows[0]["values"][2]["value"].hex() == value.hex()

    def test_booleans_stay_booleans(self):
        result = bridge(g("event", "e", *EVENT, c("flag", "eq", True)), change=boolean_flag())
        assert typed(result)["flag"] == ("bool", True, "equivalent")
        flag = next(v for v in result.projection.rows[0]["values"] if v["column"] == "flag")
        assert flag["value"] is True and json.dumps(flag["value"]) == "true"

    def test_explicit_null_is_listed_and_missing_is_not(self):
        result = bridge(g("event", "e", *EVENT, c("title", "is_null")))
        values = {v["column"]: v["value"] for v in result.projection.rows[0]["values"]}
        assert "title" in values and values["title"] is None
        assert "price" not in values and "e#0.price" in result.projection.not_compared
        assert typed(result)["title"] == ("null", None, "equivalent")

    def test_unicode_is_kept_without_normalization(self):
        a = bridge(g("event", "e", *EVENT, c("title", "eq", "é")))
        b = bridge(g("event", "e", *EVENT, c("title", "eq", "é")))
        assert typed(a)["title"][1] == "é" and typed(b)["title"][1] == "é"
        assert a.representation.typed_digest != b.representation.typed_digest
        assert a.projection.legacy_seed_sha256 != b.projection.legacy_seed_sha256


# ------------------------------------------------------------------ the legacy matcher


class TestLegacyMatcher:
    @pytest.mark.parametrize("text", ["01", "1", "1.0", "1e3", " 1", "1_000", "inf", "Infinity", "nan", "-0",
                                      "١٢", "１"])
    def test_numeric_looking_strings_are_refused(self, text):
        result = bridge(g("event", "e", *EVENT, c("title", "eq", text)))
        assert (result.outcome, result.code, result.column) == ("refused", "matcher_ambiguous_text", "title")

    @pytest.mark.parametrize("text", ["abc", "1a", "Zoë", "", "é", "0x1F", "1,000"])
    def test_other_strings_are_equivalent(self, text):
        assert typed(bridge(g("event", "e", *EVENT, c("title", "eq", text))))["title"][2] == "equivalent"

    @pytest.mark.parametrize("kind,value,pg_type,expected", [
        ("bool", True, "boolean", "equivalent"), ("bool", True, "integer", "refused"),
        ("int", 1, "boolean", "refused"), ("int", 1, "double precision", "refused"),
        ("float", 1.0, "integer", "refused"), ("float", -0.0, "double precision", "refused"),
        ("float", float("nan"), "double precision", "refused"), ("float", float("inf"), "double precision", "refused"),
        ("str", "a", "integer", "refused"), ("null", None, "integer", "equivalent"),
        ("int", True, "integer", "refused"), ("str", b"abc", "text", "refused"),
    ])
    def test_value_classification(self, kind, value, pg_type, expected):
        extra = {"max_length": 8} if pg_type == "varchar" else {}
        if pg_type in {"text", "varchar"}:
            extra["collation"] = "deterministic"
        column = sp.TrustedColumn(field="f", column="f", pg_type=pg_type, nullable=True, **extra)
        candidate = sp.CandidateValue.model_construct(field="f", column="f", type=kind, value=value,
                                                      origin="goal_constraint")
        assert pb._legacy_status(candidate, column)[0] == expected

    def test_nondeterministic_collation_is_refused(self):
        column = sp.TrustedColumn(field="f", column="f", pg_type="text", nullable=True, collation="nondeterministic")
        value = sp.CandidateValue(field="f", column="f", type="str", value="a", origin="goal_constraint")
        assert pb._legacy_status(value, column)[0] == "refused"

    def test_assumptions_and_unverifiable_behaviour_are_stated(self):
        result = bridge(g("event", "e", *EVENT))
        assert result.projection.assumptions == pb.EQUIVALENCE_ASSUMPTIONS
        assert result.projection.unverifiable_legacy_behaviour == pb.LEGACY_UNVERIFIABLE

    def test_a_bridge_is_never_execution_authorization(self):
        result = bridge(g("event", "e", *EVENT))
        assert result.execution_authorized is False
        assert result.feasibility == "candidate_pending_postgresql_validation"
        with pytest.raises(ValueError):
            result.model_validate({**result.model_dump(), "execution_authorized": True})

    def test_a_refused_value_can_never_appear_in_a_projection(self):
        refused = bridge(g("event", "e", *EVENT, c("title", "eq", "01")))
        good = bridge(g("event", "e", *EVENT, c("title", "eq", "ab")))
        with pytest.raises(ValueError, match="refused value"):
            pb.Bridged(representation=refused.representation, projection=good.projection)
        assert "projection" not in pb.BridgeRefusal.model_fields

    def test_a_projection_must_be_its_representations_own(self):
        a = bridge(g("event", "e", *EVENT, c("title", "eq", "ab")))
        b = bridge(g("event", "e", *EVENT, c("title", "eq", "cd")))
        with pytest.raises(ValueError, match="representation's own"):
            pb.Bridged(representation=a.representation, projection=b.projection)
        tampered = a.representation.model_copy(update={"application": "elsewhere"})
        with pytest.raises(ValueError, match="typed digest"):
            pb.Bridged(representation=tampered, projection=a.projection)
        too_many = bridge(g("event", "e", c("capacity", "eq", 3), count=11)).representation
        with pytest.raises(ValueError, match="row limit"):
            pb.Bridged(representation=too_many, projection=a.projection)

    def test_a_matching_legacy_digest_doesnt_establish_equivalence(self):
        planned = bridge(g("event", "e", *EVENT))
        literal = bridge(g("event", "e", *EVENT), method="literal")
        assert planned.projection.legacy_seed_sha256 == literal.projection.legacy_seed_sha256
        assert planned.representation.typed_digest != literal.representation.typed_digest
        assert (planned.representation.method, literal.representation.method) == ("deterministic_planner",
                                                                                   "literal_translation")


# ------------------------------------------------------------------ ordering and limits


def fk_cycle():
    def snap(s):
        s["attributes"].append(tcv.attribute(VENUES, 3, "event_ref", tcv.INT4))
        s["constraints"].append(tcv.constraint(30020, "venues_event_fk", "f", VENUES, [3], confrelid=EVENTS,
                                               confkey=[1]))

    def ctx(config):
        entity(config, "venue")["columns"].append(tcv.column("event_ref", "integer"))
        entity(config, "venue")["foreign_keys"] = [{"field": "event_ref", "references_entity": "event",
                                                    "references_field": "id"}]
    return snap, ctx


def self_reference():
    def snap(s):
        s["attributes"].append(tcv.attribute(EVENTS, 9, "parent_id", tcv.INT4))
        s["constraints"].append(tcv.constraint(30021, "events_parent_fk", "f", EVENTS, [9], confrelid=EVENTS,
                                               confkey=[1]))

    def ctx(config):
        entity(config, "event")["columns"].append(tcv.column("parent_id", "integer"))
        entity(config, "event")["foreign_keys"].append({"field": "parent_id", "references_entity": "event",
                                                        "references_field": "id"})
    return snap, ctx


class TestOrdering:
    def test_parents_come_first_and_identities_are_kept(self):
        result = bridge(g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall")),
                        g("event", "e", *EVENT, c("venue_id", "eq", 4)))
        rep = result.representation
        assert rep.candidate_order == ("e#0", "v#0") and rep.seed_order == ("v#0", "e#0")
        rows = {r.row_id: r for r in rep.rows}
        assert (rows["e#0"].candidate_index, rows["e#0"].seed_position) == (0, 1)
        assert (rows["v#0"].candidate_index, rows["v#0"].seed_position) == (1, 0)
        assert rows["e#0"].depends_on == ("v#0",)
        assert [r["table"] for r in result.projection.rows] == ["venues", "events"]

    def test_independent_rows_keep_their_candidate_order(self):
        result = bridge(g("event", "a", c("id", "eq", 1), c("capacity", "eq", 3)),
                        g("venue", "b", c("id", "eq", 4), c("name", "eq", "Hall")),
                        g("event", "c", c("id", "eq", 2), c("capacity", "eq", 3)))
        rep = result.representation
        assert rep.candidate_order == rep.seed_order == ("a#0", "b#0", "c#0")

    def test_cycles_are_refused_not_broken(self):
        result = bridge(g("event", "e", *EVENT, c("venue_id", "eq", 4)),
                        g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall"), c("event_ref", "eq", 7)),
                        change=fk_cycle())
        assert (result.outcome, result.code, result.representation) == ("refused", "dependency_cycle", None)

    def test_a_self_reference_needs_no_ordering_edge(self):
        result = bridge(g("event", "e", *EVENT, c("parent_id", "eq", 7)), change=self_reference())
        assert result.outcome == "bridged" and result.representation.rows[0].depends_on == ()

    def test_ten_rows_bridge_and_eleven_are_refused_whole(self):
        assert bridge(g("event", "e", c("capacity", "eq", 3), count=10)).outcome == "bridged"
        refused = bridge(g("event", "e", c("capacity", "eq", 3), count=11))
        assert (refused.outcome, refused.code) == ("refused", "too_many_rows_for_phase9b")
        assert len(refused.representation.rows) == 11, "never truncated or split"

    def test_explicit_values_into_defaulted_columns_are_flagged(self):
        change = (lambda s: tcv.attr(s, VENUES, "id").update(atthasdef=True),
                  lambda c_: col(c_, "venue", "id").update(has_default=True))
        result = bridge(g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall")), change=change)
        assert "explicit_value_with_database_default" in result.representation.rows[0].obligations

    def test_trigger_uncertainty_is_carried(self):
        change = (lambda s: s["triggers"].append(tcv.trigger(50009, EVENTS, "stamp")),
                  lambda c_: entity(c_, "event").update(has_insert_triggers=True))
        result = bridge(g("event", "e", *EVENT), change=change)
        assert {"insert_triggers_unverified", "trigger_rows_may_satisfy_or_change_requested_rows"} <= set(
            result.representation.rows[0].obligations)


# ------------------------------------------------------------------ trust


class TestTrust:
    def base(self):
        return setup(g("event", "e", *EVENT, c("title", "eq", "Gala")))

    def test_a_genuine_request_bridges(self):
        produced, args = self.base()
        assert pb.bridge_to_phase9b(produced.request, **args).outcome == "bridged"

    def test_a_forged_request_is_refused(self):
        produced, args = self.base()
        rows = list(produced.request.rows)
        rows[0] = rows[0].model_copy(update={"values": tuple(
            v.model_copy(update={"value": 4}) if v.field == "capacity" else v for v in rows[0].values)})
        altered = produced.request.model_copy(update={"rows": tuple(rows)})
        with pytest.raises(pb.UntrustedBridgeInput, match="digest"):
            pb.bridge_to_phase9b(altered, **args)
        redigested = altered.model_copy(update={"request_digest": sp.request_digest(altered)})
        with pytest.raises(pb.UntrustedBridgeInput, match="isn't what its method produces"):
            pb.bridge_to_phase9b(redigested, **args)

    def test_a_forged_literal_request_is_refused_by_its_digest(self):
        produced, args = setup(g("event", "e", *EVENT), method="literal")
        rows = list(produced.request.rows)
        rows[0] = rows[0].model_copy(update={"values": tuple(
            v.model_copy(update={"value": 4}) if v.field == "capacity" else v for v in rows[0].values)})
        with pytest.raises(pb.UntrustedBridgeInput, match="literal request's digest"):
            pb.bridge_to_phase9b(produced.request.model_copy(update={"rows": tuple(rows)}), **args)

    def test_inputs_that_no_longer_produce_a_candidate(self):
        produced, args = self.base()
        with pytest.raises(pb.UntrustedBridgeInput, match="don't produce a candidate"):
            pb.bridge_to_phase9b(produced.request, **{**args, "planning_budget": sp.PlanningBudget(
                max_constraint_checks=1)})

    def test_negative_zero_or_a_changed_digest_cant_be_smuggled_in(self):
        produced, args = setup(g("event", "e", *EVENT, c("price", "eq", 0.5)))
        rows = list(produced.request.rows)
        rows[0] = rows[0].model_copy(update={"values": tuple(
            v.model_copy(update={"value": -0.0}) if v.field == "price" else v for v in rows[0].values)})
        forged = produced.request.model_copy(update={"rows": tuple(rows)})
        forged = forged.model_copy(update={"request_digest": sp.request_digest(forged)})
        with pytest.raises(pb.UntrustedBridgeInput):
            pb.bridge_to_phase9b(forged, **args)
        with pytest.raises(pb.UntrustedBridgeInput, match="digest"):
            pb.bridge_to_phase9b(produced.request.model_copy(update={"request_digest": "0" * 64}), **args)

    def test_a_request_from_another_workload_is_stale(self):
        produced, _ = setup(g("event", "e", *EVENT, c("title", "eq", "Old")))
        _, args = self.base()
        with pytest.raises(pb.UntrustedBridgeInput):
            pb.bridge_to_phase9b(produced.request, **args)

    def test_mismatched_registry_or_contracts(self):
        produced, args = self.base()
        other = make_contracts(args["registry"], lambda x: x["contracts"][2]["arguments"][0]["constraints"][0]
                               .update(maximum=99))
        with pytest.raises(pb.UntrustedBridgeInput, match="validated workload"):
            pb.bridge_to_phase9b(produced.request, **{**args, "contracts": other})
        with pytest.raises(TypeError):
            pb.bridge_to_phase9b(produced.request, **{**args, "registry": args["registry"].config})

    def test_a_stale_planning_context(self):
        produced, args = self.base()
        changed = json.loads(json.dumps(args["context"]))
        col(changed, "event", "title").update(nullable=True)
        entity(changed, "venue")["trigger_written"] = True
        with pytest.raises(pb.UntrustedBridgeInput):
            pb.bridge_to_phase9b(produced.request, **{**args, "context": changed,
                                                      "context_trust": tcv.pin_context(changed)})

    def test_a_mismatched_catalog_snapshot(self):
        produced, args = self.base()
        other = tcv.snapshot()
        other["database"]["datname"] = "elsewhere"
        with pytest.raises(pb.UntrustedBridgeInput):
            pb.bridge_to_phase9b(produced.request, **{**args, "snapshot": other,
                                                      "snapshot_trust": tcv.pin_snapshot(other)})
        with pytest.raises(pb.UntrustedBridgeInput, match="catalog verification"):
            pb.bridge_to_phase9b(produced.request, **{**args, "snapshot_trust": tcv.cv.TrustedSnapshot(
                exporter="x", source_label="y", exported_on="2026-11-04", pinned_digest="0" * 64)})

    def test_match_is_recomputed_never_accepted(self):
        """There's no parameter for a verification result. A snapshot that doesn't MATCH is refused."""
        import inspect

        assert "verification" not in inspect.signature(pb.bridge_to_phase9b).parameters
        change = (lambda s: s["constraints"].append(tcv.constraint(30030, "events_check", "c", EVENTS, [2])), None)
        produced, args = setup(g("event", "e", *EVENT), change=change)
        result = pb.bridge_to_phase9b(produced.request, **args)
        assert (result.outcome, result.code) == ("refused", "context_not_matched")

    def test_wrong_request_types(self):
        produced, args = self.base()
        for wrong in (produced, produced.request.model_dump(), "request"):
            with pytest.raises(TypeError):
                pb.bridge_to_phase9b(wrong, **args)


# ------------------------------------------------------------------ digests, determinism, isolation


GOALS = [g("venue", "v", c("id", "eq", 4), c("name", "eq", "Hall")),
         g("event", "e", *EVENT, c("venue_id", "eq", 4), c("price", "eq", 2.5), c("title", "is_null"))]


class TestDigestsAndDeterminism:
    def test_byte_identical_output(self):
        assert len({bridge(*GOALS).model_dump_json() for _ in range(4)}) == 1

    def test_typed_digest_is_pinned_and_domain_separated(self):
        rep = bridge(*GOALS).representation
        assert rep.typed_digest == PINNED_TYPED == pb.typed_digest(rep)
        bare = json.dumps(pb._canonical(rep.model_dump(mode="python", exclude={"typed_digest"})), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
        assert rep.typed_digest != hashlib.sha256(bare.encode()).hexdigest()

    def test_legacy_digest_golden(self):
        """Phase 9B's formula, recomputed through this branch's byte-identical release SeedRow model."""
        from deployspecter.models import SeedRow

        projection = bridge(*GOALS).projection
        rows = [SeedRow.model_validate(r).model_dump() for r in projection.rows]
        independent = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        assert projection.legacy_seed_sha256 == independent == PINNED_LEGACY

    def test_nothing_is_modified(self):
        produced, args = setup(*GOALS)
        before = (produced.request.model_dump_json(), args["validated"].model_dump_json(),
                  json.dumps(args["context"], sort_keys=True), json.dumps(args["snapshot"], sort_keys=True))
        pb.bridge_to_phase9b(produced.request, **args)
        after = (produced.request.model_dump_json(), args["validated"].model_dump_json(),
                 json.dumps(args["context"], sort_keys=True), json.dumps(args["snapshot"], sort_keys=True))
        assert before == after


class TestIsolation:
    def test_imports_no_database_engine_oracle_or_phase_9b(self):
        code = ("import sys, deployspecter.live.phase9b_bridge;"
                "print(sorted(m for m in sys.modules if m.startswith(('psycopg', 'langgraph', 'live_app',"
                " 'deployspecter.live.runner', 'deployspecter.live.driver', 'deployspecter.live.supervisor',"
                " 'deployspecter.database', 'deployspecter.oracle', 'deployspecter.scenarios',"
                " 'deployspecter.state_constructor', 'deployspecter.seed_feasibility', 'deployspecter.triggers',"
                " 'deployspecter.models'))))")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        assert done.stdout.strip() == "[]"

    def test_no_dynamic_code_files_or_sql(self):
        tree = ast.parse(Path(pb.__file__).read_text())
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "input"}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"importlib", "subprocess", "os", "socket", "psycopg", "pathlib"}
        strings = " ".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        assert "INSERT INTO" not in strings and "SELECT " not in strings.upper().replace("SELECT TO_JSONB", "")

    def test_no_network_or_database(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no network or database access is allowed here")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        assert bridge(*GOALS).outcome == "bridged"


PINNED_TYPED = "dc3c84b4d5d5630deb0b281821714534aa7c83ec2db730f91d327a19c9edf4b3"
PINNED_LEGACY = "ad85e488616f1f52325277c5794883598331a298613f9be858993a0063a2bb7b"
