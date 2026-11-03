"""Correctness requirements for the reservations application, stated over committed state and returned values.

This module imports nothing from the application or the engine, and reads only plain dictionaries. The requirements
come from what the application promises, not from how either variant is written:

R1  An event's ``reserved`` equals the seats in its reservations.
R2  An event never has more seats in reservations than its capacity.
R3  Each caller told "reserved" has a reservation, and each reservation belongs to a caller told so or to the seed.
R4  The committed state is one some serial order of the same calls could produce (ignoring generated ids).
"""

from __future__ import annotations

from typing import Any

State = dict[str, list[dict[str, Any]]]


def check_invariants(state: State) -> list[str]:
    violations = []
    for event in state["events"]:
        seats = sum(r["qty"] for r in state["reservations"] if r["event_id"] == event["id"])
        if event["reserved"] != seats:
            violations.append(f"R1 event {event['id']}: reserved={event['reserved']} but reservations hold {seats}")
        if seats > event["capacity"]:
            violations.append(f"R2 event {event['id']}: {seats} seats reserved of capacity {event['capacity']}")
    return violations


def check_results(state: State, seeded_holders: set[str], results: dict[str, Any], holders: dict[str, str],
                  ) -> list[str]:
    violations = []
    present = {r["holder"] for r in state["reservations"]}
    told = {holders[name] for name, value in results.items() if (value or {}).get("status") == "reserved"}
    for holder in sorted(told - present):
        violations.append(f"R3 {holder} was told reserved but has no reservation")
    for holder in sorted(present - told - seeded_holders):
        violations.append(f"R3 {holder} has a reservation but was not told reserved")
    return violations


def canonical(state: State) -> dict[str, Any]:
    """Comparable state, without ids that depend on the order sequences were used in."""
    return {
        "events": sorted((e["id"], e["capacity"], e["reserved"]) for e in state["events"]),
        "reservations": sorted((r["event_id"], r["holder"], r["qty"]) for r in state["reservations"]),
        "rejections": sorted((r["event_id"], r["holder"], r["seats_available"]) for r in state["rejections"]),
    }


def check_serializable(state: State, serial_states: list[dict[str, Any]]) -> list[str]:
    if canonical(state) in serial_states:
        return []
    return ["R4 committed state matches no serial order of these calls"]
