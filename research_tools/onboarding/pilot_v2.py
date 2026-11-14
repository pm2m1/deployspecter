"""EXPERIMENTAL onboarding harness for the three-application study (onb-study/2, DRAFT; NOT RUN).

It is ``pilot.py`` (onb-pilot/1, unchanged and still pinned by onb-study/1's manifest) with one difference: the
``muse`` command logs each Muse consultation's duration, so execution-only time can be computed. Every other command
is ``pilot.py``'s, unchanged.

    pilot_v2.py muse --session S --manifest M --sdists DIR --env-file F --label L
    pilot_v2.py <any other pilot.py command>

**What the timed ``muse`` records**, on the same ``model_call`` event that ``pilot.py`` writes (the event kinds and the
hash chain are unchanged):
* ``call_started_t``: the recorder's clock (UTC seconds) just before the request;
* ``call_seconds``: the request's duration on a monotonic clock, from just before the request to the reply (or the
  error);
* ``timing``: ``"onb-study/2"``.

The event's own ``t`` is its end. A failed call is timed too. The condition and budget checks are ``pilot.py``'s: Muse
is refused outside condition M and over the frozen call and dollar budget.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pilot  # noqa: E402

from deployspecter.experimental.onboarding_pilot import session, spec  # noqa: E402

TIMING = "onb-study/2"


def _real_ask(a: Any, b: dict[str, Any], spent: float, info: str, log: Path) -> dict[str, Any]:
    from deployspecter.experimental import muse
    from deployspecter.experimental.onboarding_pilot import muse_assist
    client = muse.client_from_env_file(a.env_file, max_requests=1,
                                       max_estimated_usd=round(b["max_estimated_usd"] - spent, 6))
    return muse_assist.ask(client, pkg_info=info, contract=spec.CONTRACT, log=log, label=a.label)


def cmd_muse_timed(a: Any, *, ask: Callable[..., dict[str, Any]] = _real_ask,
                   monotonic: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                   pkginfo: Callable[[Path], str] | None = None) -> int:
    st = session.events(a.session)[0]
    m, pins = pilot.manifest_entry(a.manifest, st["app"])
    if st["condition"] != "M":
        print("refused: Muse is condition M only")
        return 2
    calls = [e for e in session.events(a.session) if e["kind"] == "model_call"]
    spent = sum(c.get("est_usd") or 0 for c in calls)
    b = m["budgets"]["muse"]
    if len(calls) >= b["max_calls"] or spent >= b["max_estimated_usd"]:
        session.append(a.session, "budget_stop", detail=f"Muse budget: {len(calls)} calls, ${spent:.4f}")
        print("refused: Muse budget spent")
        return 2
    info = (pkginfo or pilot._pkginfo)(a.sdists / pins["sdist"])
    log = a.session / "muse_calls.jsonl"
    started_t, t0 = wall(), monotonic()
    try:
        entry = ask(a, b, spent, info, log)
    except Exception as exc:  # noqa: BLE001 - timed and recorded, never retried
        session.append(a.session, "model_call", label=a.label, error=type(exc).__name__, tokens_in=0, tokens_out=0,
                       est_usd=0, call_started_t=started_t, call_seconds=round(monotonic() - t0, 3), timing=TIMING)
        print("Muse call failed:", type(exc).__name__)
        return 1
    secs = round(monotonic() - t0, 3)
    session.append(a.session, "model_call", label=a.label, tokens_in=entry.get("tokens_in"),
                   tokens_out=entry.get("tokens_out"), est_usd=entry.get("est_usd"),
                   reply_sha256=entry.get("reply_sha256"), call_started_t=started_t, call_seconds=secs,
                   timing=TIMING)
    print(json.dumps(entry.get("proposal"), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    pilot.cmd_muse = cmd_muse_timed  # the only command that differs; pilot.py's file is not changed
    return pilot.main(argv)


if __name__ == "__main__":
    sys.exit(main())
