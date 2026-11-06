"""EXPERIMENTAL onboarding pilot, HARNESS CALIBRATION ONLY (not a pilot app, not an outcome): the already integrated
Chancy 0.26.0 through the onb-pilot/1 adapter contract, built on the existing ``chancy_adapter``. It shows that the
harness builds, runs, corroborates and scores before any pilot session.

Unlike a pilot adapter, it credits the recorder after the work step, from ``chancy_adapter.HANDLED`` (that
adapter's trusted handler), rather than from inside the task function. That is acceptable for calibrating the
harness, and is not the contract a pilot adapter must meet."""

from __future__ import annotations

from typing import Any

from deployspecter.experimental.onboarding_pilot import recorder
from deployspecter.experimental.thirdparty import chancy_adapter as ca

verify_pinned = ca.verify_pinned
_KEYS: dict[int, str] = {}


async def install(conn: Any) -> dict[str, Any]:
    return await ca.phase_setup(conn, ns=[])


async def enqueue(conn: Any, *, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    _KEYS.update({j["args"]["n"]: j["key"] for j in jobs})
    return await ca.phase_push(conn, ns=[j["args"]["n"] for j in jobs])


async def work(conn: Any, *, expect: int, timeout_s: float) -> dict[str, Any]:
    r = await ca.phase_work(conn, handle_at_least=expect, timeout_s=timeout_s)
    for n in ca.HANDLED:
        recorder.handled(_KEYS.get(n, f"unknown_{n}"))
    return r
