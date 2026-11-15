"""Solo r3 design: the owner's admission decision 3, a missing request excludes the case for every arm.

``research_tools/confirmatory_prep/prep_r3.py`` is unchanged: it counts a planned request index with no record as a
failed request ("missing") and lets the arm proceed. Under the owner's decision of 2026-11-16
(``protocol/OWNER_DECISIONS_r3_admission.md`` item 3) a missing record is an infrastructure failure instead:

* ``admit_arm`` refuses (``CaseExcluded``) before any admission when an arm's replies lack a planned index;
* ``admit_case`` applies that to every model arm of a case at once, so one arm's missing request excludes the case
  for **every** arm, deterministic ones included, and returns the exclusion record;
* otherwise it is exactly ``prep_r3.admit_replies``: decisions 1 (an unparseable reply is a failed request) and 2
  (canonical JSON as ``prep.canonical``) are what ``prep_r3`` already implements.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "confirmatory_prep"))
import prep_r3  # noqa: E402

VERSION = "ds-solo-admission/1"
DECISION = "missing request -> case excluded for every arm (OWNER_DECISIONS_r3_admission.md item 3, 2026-11-16)"


class CaseExcluded(Exception):
    def __init__(self, record: dict[str, Any]):
        super().__init__(record["reason"])
        self.record = record


def missing_indexes(replies: list[dict[str, Any]], r: int) -> list[int]:
    have = {rep.get("index") for rep in replies}
    return [i for i in range(r) if i not in have]


def admit_arm(replies: list[dict[str, Any]], r: int, validate: Callable[[Any], list[str]],
              k: int = prep_r3.K) -> dict[str, Any]:
    missing = missing_indexes(replies, r)
    if missing:
        raise CaseExcluded({"version": VERSION, "excluded_for_every_arm": True, "reason": "missing_request",
                            "missing_indexes": missing, "decision": DECISION})
    return {**prep_r3.admit_replies(replies, r, validate, k), "admission_path": VERSION}


def admit_case(arms: dict[str, list[dict[str, Any]]], r: int, validate: Callable[[Any], list[str]],
               k: int = prep_r3.K) -> dict[str, Any]:
    """Admission for every model arm of one case, or the case's exclusion for every arm."""
    missing = {arm: missing_indexes(replies, r) for arm, replies in sorted(arms.items())}
    missing = {arm: m for arm, m in missing.items() if m}
    if missing:
        return {"version": VERSION, "excluded_for_every_arm": True, "reason": "missing_request",
                "missing_indexes": missing, "decision": DECISION}
    return {"version": VERSION, "excluded_for_every_arm": False,
            "arms": {arm: admit_arm(replies, r, validate, k) for arm, replies in sorted(arms.items())}}
