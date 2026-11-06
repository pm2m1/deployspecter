"""EXPERIMENTAL ds-p9x/1: run the **unmodified** Phase 9B constructor with one declared, versioned substitution.

The pinned source files (``state_construction/phase9b_source``; their sha256 is checked before each file runs) are
executed under a private package of their own, ``deployspecter._phase9b_ext_v1``. That keeps them apart from
``deployspecter._phase9b``, which the reservations path uses with no substitution.

**The one substitution** (``SUBSTITUTIONS``) is ``seed_feasibility._same``, Phase 9B's loose comparison "used only to
attribute rows". It is installed after ``seed_feasibility`` runs and before ``state_constructor`` imports it.
* For a value registered as jsonb in this construction (by canonical text), it compares the stored ``to_jsonb``
  value with the parsed requested value under ``jsonb_v1.equal``: exact JSON types, no floats.
* Every other comparison goes to the original ``_same``, unchanged.
* Registration is unambiguous: a construction in which a jsonb text also appears as a non-jsonb value is refused
  before anything runs.

Phase 9B's classification is not trusted on its own. ``ORIGINAL_VALID`` rests on attribution alone, so
``execute`` re-verifies every constructed state on the database (``execute.write_and_verify``).
"""

from __future__ import annotations

import contextvars
import hashlib
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

from deployspecter.models import ColumnValue, SeedRow

from . import jsonb_v1

SOURCE_DIR = Path(__file__).resolve().parents[1] / "state_construction" / "phase9b_source"
PINNED = json.loads((SOURCE_DIR / "SOURCE.json").read_text())["sha256"]
PACKAGE = "deployspecter._phase9b_ext_v1"
ALIASES = ("database", "config", "models", "analyzer_support", "source_model", "scenarios", "validation")
SUBSTITUTIONS = {"seed_feasibility._same": "deployspecter.experimental.phase9b_ext.loader.same_v1"}
PROVEN = ("ORIGINAL_VALID", "RECONCILED_EQUIVALENT", "CONSTRUCTED_EQUIVALENT")

_JSONB: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("ds_p9x_jsonb", default=None)
_original_same: Any = None


class ExtensionRefused(RuntimeError):
    """The extension can't load or can't run this construction. Raised, never worked around."""


def same_v1(a: Any, b: Any) -> bool:
    """Phase 9B attribution, jsonb-aware. ``a`` and ``b``: a stored (``to_jsonb``) value and a requested value, in
    either order."""
    registered = _JSONB.get() or {}
    for stored, requested in ((a, b), (b, a)):
        if isinstance(requested, str) and requested in registered:
            if isinstance(stored, str) and stored in registered:  # two requested jsonb values (dependency edges)
                return stored == requested
            return jsonb_v1.equal(stored, registered[requested])
    return _original_same(a, b)


def load() -> types.SimpleNamespace:
    """The three constructor modules, sha256-checked, with the substitution installed."""
    global _original_same
    pkg = sys.modules.get(PACKAGE)
    if pkg is not None and hasattr(pkg, "_loaded"):
        return pkg._loaded  # type: ignore[attr-defined]
    pkg = types.ModuleType(PACKAGE)
    pkg.__path__ = [str(SOURCE_DIR)]  # type: ignore[attr-defined]
    pkg.__package__ = PACKAGE
    sys.modules[PACKAGE] = pkg
    for name in ALIASES:
        sys.modules[f"{PACKAGE}.{name}"] = importlib.import_module(f"deployspecter.{name}")
    loaded = {}
    for name in ("triggers", "seed_feasibility", "state_constructor"):
        path = SOURCE_DIR / f"{name}.py"
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != PINNED[f"{name}.py"]:
            for k in [k for k in sys.modules if k == PACKAGE or k.startswith(PACKAGE + ".")]:
                del sys.modules[k]
            raise ExtensionRefused(f"{name}.py isn't the pinned Phase 9B source (sha256 {got[:12]})")
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        if name == "seed_feasibility":
            _original_same = mod._same
            mod._same = same_v1  # the declared substitution, before state_constructor imports it
        loaded[name] = mod
    if loaded["state_constructor"]._same is not same_v1:
        raise ExtensionRefused("the substitution did not reach state_constructor")
    pkg._loaded = types.SimpleNamespace(**loaded)  # type: ignore[attr-defined]
    return pkg._loaded  # type: ignore[attr-defined]


def identity() -> dict[str, Any]:
    """What ran: pinned source hashes, this loader's and the contract module's own hashes, the substitution."""
    here = Path(__file__).resolve()
    return {"extension": "ds-p9x/1", "jsonb_contract": jsonb_v1.VERSION, "package": PACKAGE,
            "phase9b_sources_sha256": dict(PINNED), "substitutions": dict(SUBSTITUTIONS),
            "loader_sha256": hashlib.sha256(here.read_bytes()).hexdigest(),
            "jsonb_v1_sha256": hashlib.sha256((here.parent / "jsonb_v1.py").read_bytes()).hexdigest()}


def seed_rows(rows: list[tuple[str, dict[str, Any]]], jsonb_cols: dict[int, set[str]]) -> tuple[list[SeedRow],
                                                                                                    dict[str, Any]]:
    """Phase 9B ``SeedRow``s from plain values. A jsonb cell is given as canonical jsonb/1 text and registered.
    Refused if a registered jsonb text also appears as any non-jsonb value (attribution would be ambiguous)."""
    registered: dict[str, Any] = {}
    others: set[str] = set()
    out = []
    for i, (table, vals) in enumerate(rows):
        for c, v in vals.items():
            if c in jsonb_cols.get(i, set()):
                if v is None:
                    continue  # SQL NULL in a jsonb column: not a jsonb text
                if jsonb_v1.canonical(jsonb_v1.parse(v)) != v:
                    raise ExtensionRefused(f"row {i} {table}.{c}: jsonb text isn't canonical")
                registered[v] = jsonb_v1.parse(v)
            elif isinstance(v, str):
                others.add(v)
        out.append(SeedRow(table=table, values=[ColumnValue(column=c, value=v) for c, v in vals.items()]))
    if clash := sorted(set(registered) & others):
        raise ExtensionRefused(f"jsonb text also used as a non-jsonb value: {clash}; attribution would be ambiguous")
    return out, registered


def construct(sandbox: Any, setup_sql: str, seed: list[SeedRow], registered: dict[str, Any], *,
              budget: Any = None) -> dict[str, Any]:
    """The unmodified ``read_context`` and ``construct_state`` under the substitution, for this construction's jsonb
    values only. Returns the constructor's own ``ConstructionReport`` as plain data."""
    m = load()
    token = _JSONB.set(dict(registered))
    try:
        context = m.seed_feasibility.read_context(sandbox, setup_sql)
        report = m.state_constructor.construct_state(sandbox, setup_sql, seed, context, budget=budget)
    finally:
        _JSONB.reset(token)
    return json.loads(report.model_dump_json())
