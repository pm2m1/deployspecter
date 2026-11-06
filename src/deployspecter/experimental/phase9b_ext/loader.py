"""EXPERIMENTAL ds-p9x/1: run the **unmodified** Phase 9B constructor with one declared, versioned substitution.

The pinned source files (``state_construction/phase9b_source``; their sha256 is checked before each file runs) are
executed under a private package of their own, ``deployspecter._phase9b_ext_v1``. That keeps them apart from
``deployspecter._phase9b``, which the reservations path uses with no substitution.

**The one substitution** (``SUBSTITUTIONS``) is ``seed_feasibility._same``, Phase 9B's loose comparison "used only to
attribute rows". It is installed after ``seed_feasibility`` runs and before ``state_constructor`` imports it.

**same_v1.1 (identity-based).** A requested jsonb value is a ``JsonbText`` (a ``str`` subclass carrying its parsed
jsonb/1 value), placed into the seed with pydantic's ``model_construct``, so it isn't coerced back to a plain str. The
constructor passes seed rows through by reference (``_effective``; model instances aren't revalidated).
* When either argument is a ``JsonbText``, the comparison is ``jsonb_v1.equal`` (exact JSON types, no floats), or
  text equality if both are.
* **Every other call is the original ``_same``, with the same arguments.** Values read back through ``to_jsonb`` are
  never ``JsonbText``, so stored-vs-stored comparisons and every non-jsonb comparison are unchanged by
  construction. ``tests/experimental/test_p9x_assurance_same.py`` checks this property-based.
* If the marker were ever lost, a requested jsonb text would fall back to the original comparison. That makes
  ``str(dict) != text``, so attribution fails and the constructor refuses (fail-closed), and every accepted state is
  re-verified on the database regardless.

v1 (before ``ds-p9x/1.1``) keyed jsonb semantics on the requested *text*. That could misclassify a stored text-column
value that happened to equal a registered jsonb text, in the constructor's stored-vs-stored comparisons
(``state_constructor.py`` lines 481 and 489). Found in assurance A1; superseded.

Defense in depth: a construction in which a jsonb text also appears as a non-jsonb value is still refused.

Phase 9B's classification is not trusted on its own. ``ORIGINAL_VALID`` rests on attribution alone, so
``execute`` re-verifies every constructed state on the database (``execute.write_and_verify``).
"""

from __future__ import annotations

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
SUBSTITUTIONS = {"seed_feasibility._same": "deployspecter.experimental.phase9b_ext.loader.same_v1 (same_v1.1)"}
SAME_VERSION = "same_v1.1"
PROVEN = ("ORIGINAL_VALID", "RECONCILED_EQUIVALENT", "CONSTRUCTED_EQUIVALENT")

_original_same: Any = None


class JsonbText(str):
    """A requested jsonb value: its canonical jsonb/1 text, with the parsed value. Only the extension creates these."""

    parsed: Any

    def __new__(cls, text: str) -> JsonbText:
        obj = super().__new__(cls, text)
        obj.parsed = jsonb_v1.parse(text)
        return obj


class ExtensionRefused(RuntimeError):
    """The extension can't load or can't run this construction. Raised, never worked around."""


def same_v1(a: Any, b: Any) -> bool:
    """Phase 9B attribution (same_v1.1). jsonb semantics only for ``JsonbText`` arguments; otherwise the original."""
    if isinstance(b, JsonbText):
        return str(a) == str(b) if isinstance(a, JsonbText) else jsonb_v1.equal(a, b.parsed)
    if isinstance(a, JsonbText):
        return jsonb_v1.equal(b, a.parsed)
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
    return {"extension": "ds-p9x/1.1", "same": SAME_VERSION, "jsonb_contract": jsonb_v1.VERSION, "package": PACKAGE,
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
        cells = [ColumnValue.model_construct(column=c, value=JsonbText(v) if (c in jsonb_cols.get(i, set()) and
                                                                            v is not None) else v)
                 for c, v in vals.items()]
        out.append(SeedRow.model_construct(table=table, values=cells))
    if clash := sorted(set(registered) & others):
        raise ExtensionRefused(f"jsonb text also used as a non-jsonb value: {clash}; attribution would be ambiguous")
    return out, registered


def construct(sandbox: Any, setup_sql: str, seed: list[SeedRow], registered: dict[str, Any], *,
              budget: Any = None) -> dict[str, Any]:
    """The unmodified ``read_context`` and ``construct_state`` under the substitution. ``registered`` (the jsonb texts)
    must be exactly the seed's ``JsonbText`` cells: a mismatch means the marker was lost, and is refused. Returns the
    constructor's own ``ConstructionReport`` as plain data."""
    marked = {str(v.value) for r in seed for v in r.values if isinstance(v.value, JsonbText)}
    if marked != set(registered):
        raise ExtensionRefused("the seed's jsonb markers don't match the registered jsonb values")
    m = load()
    context = m.seed_feasibility.read_context(sandbox, setup_sql)
    report = m.state_constructor.construct_state(sandbox, setup_sql, seed, context, budget=budget)
    return json.loads(report.model_dump_json())
