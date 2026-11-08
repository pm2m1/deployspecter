"""ds-nbc/1 oracle for case chancy-2895c4a (NONBLIND development; developer-encoded, maintainer-sourced).

Source of the property: the historical case's frozen property H0-H2, ``docs/experimental_mvp/historical/
chancy_2895c4a/CASE.md`` (frozen at ``cf4b303``), as implemented by the frozen checker
``src/deployspecter/experimental/historical_oracles.py::chancy_2895c4a`` (sha256 ``db9bc5fe…``, pinned below and
checked on every call; this module never edits it). That property was encoded from:
* the 0.26.0 ``Chancy.cancel_job`` docstring ("Marks the job failed and invalidates its execution claim, preventing a
  late completion from overwriting the cancellation"), and
* the maintainers' regression test ``tests/plugins/test_recovery.py::test_cancellation_fences_execution_completion``
  (added in ``2895c4a``): a cancelled job ends ``failed``; on a schema with ``claim_id`` its claim is NULL and
  ``completed_at`` is set.

H1 a job whose ``cancel_job`` updated 1 row ends ``failed``; H2 (schemas with ``claim_id`` only) its claim is NULL
and ``completed_at`` set; H0 (sanity, no verdict otherwise) its handler ran exactly once.

``check(final_state, raw_results)``: ``final_state`` is the host's independent read after a SOUND run
(``jobs_detail`` rows; ``jobs`` rows carry ``has_claim_id`` only when the column exists).
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[6]
CHECKER = REPO / "src" / "deployspecter" / "experimental" / "historical_oracles.py"
CHECKER_SHA256 = "db9bc5fee1790e7d53895261e34b16f51c789c07dcaaae4f27bf357dbfe33f72"


def _checker() -> Any:
    if hashlib.sha256(CHECKER.read_bytes()).hexdigest() != CHECKER_SHA256:
        raise RuntimeError("historical_oracles.py isn't the frozen checker")
    s = importlib.util.spec_from_file_location("ds_nbc_frozen_historical_oracles", CHECKER)
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(m)
    return m


def evaluate(final_state: dict[str, Any], raw_results: dict[str, Any]) -> dict[str, Any]:
    jobs = final_state.get("jobs_detail")
    if not isinstance(jobs, list) or not final_state.get("jobs"):
        return {"verdict": "NO_VERDICT", "why": "no job rows in the final state"}
    has_claim_column = all("has_claim_id" in j for j in final_state["jobs"])
    return _checker().chancy_2895c4a(jobs, raw_results, has_claim_column=has_claim_column)


def check(final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
    return evaluate(final_state, raw_results)["verdict"]
