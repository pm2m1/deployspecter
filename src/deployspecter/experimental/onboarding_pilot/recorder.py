"""EXPERIMENTAL onboarding pilot: the harness-owned handler recorder. It runs inside the container.

An adapter's trusted task function calls ``handled(key)`` once per execution. The in-container script reports
``HANDLED`` after the work phase. The independent check (``correctness``) compares it with the enqueued keys; it
never takes the adapter's own account of what ran.
"""

from __future__ import annotations

HANDLED: list[str] = []


def handled(key: str) -> str:
    if not isinstance(key, str):
        raise TypeError("a job key is a str")
    HANDLED.append(key)
    return key


def reset() -> None:
    HANDLED.clear()
