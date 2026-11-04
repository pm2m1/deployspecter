"""Arm E coverage and fairness audit: pure measurements over the prototype generator's output. Research-only.

It measures, and never changes, what the generator produces. The protocol's E definition is "union of A and B, plus
pairs of registered entrypoints". The audit reports what of A, B and the pairs survives into valid units, and why
the rest doesn't.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from deployspecter.live import argument_validation as av
from deployspecter.live import registry as rg

from . import arm_e_generator as g


def _values_in(units: list[g.Unit], entrypoint: str) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    for unit in units:
        for actor in unit.document["actors"]:
            if actor["entrypoint"] != entrypoint:
                continue
            for argument in actor["arguments"]:
                seen.setdefault(argument["name"], set()).add(json.dumps(argument["value"], sort_keys=True))
    return seen


def _typed(kind: str, value: Any) -> str:
    return json.dumps({"type": "null"} if kind == "null" else {"type": kind, "value": value}, sort_keys=True)


def audit(registry: rg.Registry, contracts: av.Contracts, version: str, policy: g.GoalPolicy,
          a_translations: tuple[g.ATranslation, ...] = (), *, options: g.GeneratorOptions | None = None,
          planning: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    result = g.generate(registry, contracts, version, policy, a_translations, options=options)
    seconds = time.perf_counter() - started
    entries = [registry.lookup(i) for i in registry.ids() if version in registry.lookup(i).versions]
    valid = result.valid

    a_units = [u for u in result.units if u.source == "A"]
    a_cov = {"tests": len(a_translations), "expressible": len(a_units),
             "valid": sum(u.status == "valid" for u in a_units),
             "rejected": sum(u.status != "valid" for u in a_units),
             "not_expressible": [name for name, _ in result.blocked_a_tests]}

    boundary: dict[str, Any] = {}
    for entry in entries:
        sig = g._signature(entry, contracts.lookup(entry.id, version))
        seen = _values_in(valid, entry.id)
        per_arg = {}
        for name, _required, values in sig:
            wanted = {_typed(k, v) for k, v in values}
            got = wanted & seen.get(name, set())
            per_arg[name] = {"values": len(wanted), "covered": len(got),
                             "uncovered": sorted(wanted - got)}
        total = sum(a["values"] for a in per_arg.values())
        covered = sum(a["covered"] for a in per_arg.values())
        boundary[entry.id] = {"values": total, "covered": covered, "arguments": per_arg}

    possible = [(a.id, b.id) for i, a in enumerate(entries) for b in entries[i:]]
    excluded_self = [(a.id, a.id) for a in entries if a.max_instances < 2]
    pair_units = [u for u in result.units if u.source == "pair"]
    pair_names = {tuple(u.label.split(":")[1].split("+")) for u in pair_units}
    pair_valid = {tuple(u.label.split(":")[1].split("+")) for u in pair_units if u.status == "valid"}
    pairs = {"possible": len(possible), "excluded_by_max_instances": excluded_self,
             "generated": len(pair_names), "with_a_valid_unit": len(pair_valid),
             "missing": sorted(set(possible) - pair_valid)}

    rejected = Counter((u.source, u.stage, u.codes) for u in result.rejected)
    identities = Counter(u.plan_identity for u in valid)
    out = {
        "version": version, "options": (options or g.GeneratorOptions()).label(),
        "candidates": result.candidates_counted, "ceiling": g.MAX_UNITS, "ceiling_reached": result.ceiling_reached,
        "valid": len(valid), "rejected": len(result.rejected),
        "rejections": [{"source": s, "stage": st, "codes": list(c), "count": n} for (s, st, c), n in
                       sorted(rejected.items())],
        "max_instances_rejections": sum(n for (s, st, c), n in rejected.items() if "instance_limit_exceeded" in c),
        "a": a_cov, "boundary": boundary, "pairs": pairs,
        "distinct_plan_identities": len(identities),
        "duplicate_units": sum(n - 1 for n in identities.values() if n > 1),
        "generation_seconds": round(seconds, 4),  # descriptive only
    }
    if out["options"] is not None:
        # Nothing is silently replaced: list what the default policy would have generated and rejected, and the
        # option-generated unit, if any, that stands in its place.
        default = g.generate(registry, contracts, version, policy, a_translations)
        labels = {u.label for u in result.units}
        out["default_policy_rejections"] = [
            {"label": u.label, "stage": u.stage, "codes": list(u.codes),
             "option_unit": next((x for x in sorted(labels) if x.startswith(u.label + "+")), None)}
            for u in default.rejected]
        out["option_generated_units"] = sorted(x for x in labels if "+companion:" in x or "+partner:" in x)
    if planning is not None:
        use = g.planning_use(valid, **planning)
        out["planning"] = {"outcomes": dict(Counter(f"{u['outcome']}:{u['code']}" for u in use)),
                           "max_expansions": max((u["expansions"] for u in use), default=0),
                           "max_constraint_checks": max((u["constraint_checks"] for u in use), default=0),
                           "unsupported_goal_units": sum(u["outcome"] != "candidate_plan" for u in use)}
    return out
