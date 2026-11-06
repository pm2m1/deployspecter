"""EXPERIMENTAL ds-p9x/1 execution tracer: which constructor functions actually ran, by module name.

``sys.setprofile`` records every Python call whose module (``f_globals['__name__']``) is in the extension's private
constructor package or in ``phase9b_ext``. Module names, not file paths, tell the extension's instance apart from the
plain ``deployspecter._phase9b`` instance: both execute the same pinned files. ``require`` turns a missing call into a
refusal, so a wrapper that skips the constructor can't pass.
"""

from __future__ import annotations

import collections
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

PREFIXES = ("deployspecter._phase9b_ext_v1", "deployspecter.experimental.phase9b_ext", "deployspecter._phase9b.")


class TraceRefused(RuntimeError):
    """A required function didn't run."""


@contextmanager
def traced() -> Iterator[collections.Counter]:
    calls: collections.Counter = collections.Counter()
    previous = sys.getprofile()

    def prof(frame: Any, event: str, arg: Any) -> None:
        if event == "call":
            mod = frame.f_globals.get("__name__", "")
            if mod.startswith(PREFIXES):
                calls[f"{mod}.{frame.f_code.co_name}"] += 1

    sys.setprofile(prof)
    try:
        yield calls
    finally:
        sys.setprofile(previous)


REQUIRED = ("deployspecter._phase9b_ext_v1.state_constructor.construct_state",  # every outcome reaches these
            "deployspecter._phase9b_ext_v1.seed_feasibility.read_context",
            "deployspecter._phase9b_ext_v1.seed_feasibility.check_seed")
REQUIRED_PROVEN = ("deployspecter._phase9b_ext_v1.state_constructor.assign_rows",)  # a PROVEN outcome attributes rows


def require(calls: collections.Counter, *, jsonb: bool, proven: bool) -> dict[str, Any]:
    """The proof record. ``proven``: the constructor reported a PROVEN state, so row attribution must have run;
    ``jsonb`` and ``proven``: the jsonb-aware substitution must have run too."""
    need = list(REQUIRED) + (list(REQUIRED_PROVEN) if proven else []) + \
        (["deployspecter.experimental.phase9b_ext.loader.same_v1"] if jsonb and proven else [])
    missing = [n for n in need if not calls.get(n)]
    plain = [k for k in calls if k.startswith("deployspecter._phase9b.")]
    if missing:
        raise TraceRefused(f"required constructor functions did not run: {missing}")
    if plain:
        raise TraceRefused(f"the unsubstituted constructor instance ran instead: {plain[:3]}")
    return {"required": {n: calls[n] for n in need}, "functions": len(calls), "calls": sum(calls.values()),
            "all": dict(sorted(calls.items()))}
