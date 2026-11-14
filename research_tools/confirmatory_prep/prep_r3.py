"""DEVELOPMENT-ONLY proposal admission for revision candidate r3's #24a (PROPOSED; not adopted, not registered).
``prep.py`` and ``prep_r2.py`` are unchanged; this module adds only the admission rule and its accounting. It never
runs a confirmatory evaluation, and ``research_tools/confirmatory/run.py``'s real-mode gates are untouched.

``admit_replies`` takes the case's R reply records, in any order, and applies r3 §4's rule:
1. exactly R requests, indexed 0 … R − 1; a missing index is a failed request ("missing"), never replaced;
2. replies in request-index order, proposals in reply order;
3. every generated proposal in the denominator, including a failed request's (never valid: "provider failure");
4. valid = the shared validator passes (schema, integration validator, frozen eligibility rules);
5. valid workloads deduplicated by the sha256 of their canonical JSON, across requests;
6. the first K distinct valid workloads admitted; later distinct valid ones are overflow, never executed;
7. slot accounting: filled first time, by repetition (1 to K − 1 admitted) or unfilled (none admitted), with a
   per-slot plan naming the workload and, for a repetition, the slot it repeats;
8. request accounting: planned (R), attempted (every index recorded), transmitted (sent to the provider: not
   missing and not refused before sending) and failed, with each status counted.

A reply record is ``{"index": i, "status": "ok" | <failure status>, "proposals": [...] | None}``. ``reply_record``
builds one from ``prep_r2.r2_generate``'s output. Admission sees only the replies and the validator, never labels,
oracles or outcomes, so it can't depend on them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import prep
import prep_r2

VERSION = "ds-confirm-prep-r3/1"
K = prep_r2.K
USABLE = "ok"
NOT_SENT = {"missing", "refused_before_sending"}  # planned, but nothing was transmitted to the provider


def reply_record(index: int, generated: dict[str, Any] | None, *, key: str = "workloads") -> dict[str, Any]:
    """A reply record from ``r2_generate``'s output (``None`` for a request refused before sending). A reply that
    arrived but isn't usable (for example a provider-model mismatch) keeps its proposals, which count as generated
    and never as valid."""
    if generated is None:
        return {"index": index, "status": "refused_before_sending", "proposals": None}
    status = generated.get("status") or "unknown"
    if status == USABLE and not generated.get("usable"):
        status = "unusable"
    proposals = None
    if generated.get("reply_text") is not None:
        try:
            body = json.loads(generated["reply_text"])
        except ValueError:
            body = None
        proposals = body.get(key) if isinstance(body, dict) else None
        if not isinstance(proposals, list):
            proposals = None
            status = "unparseable_reply" if status == USABLE else status
    return {"index": index, "status": status, "proposals": proposals}


def admit_replies(replies: list[dict[str, Any]], r: int, validate: Callable[[Any], list[str]],
                  k: int = K) -> dict[str, Any]:
    prep_r2.require_r2_allocation(k, prep_r2.S)
    by_index: dict[int, dict[str, Any]] = {}
    for rep in replies:
        i = rep["index"]
        if not isinstance(i, int) or not 0 <= i < r:
            raise prep.PrepRefused(f"reply index {i!r} is outside the plan's 0 … {r - 1}")
        if i in by_index:
            raise prep.PrepRefused(f"two replies for request {i}: a request is never repeated or replaced")
        by_index[i] = rep
    requests = [by_index.get(i, {"index": i, "status": "missing", "proposals": None}) for i in range(r)]
    seen: dict[str, dict[str, int]] = {}
    admitted: list[Any] = []
    log: list[dict[str, Any]] = []
    n = {"generated": 0, "valid": 0, "invalid": 0, "distinct_valid": 0, "duplicate": 0, "admitted": 0,
         "overflow": 0}
    invalid_reasons: dict[str, int] = {}
    for req in requests:
        for j, p in enumerate(req["proposals"] or []):
            n["generated"] += 1
            entry = {"request": req["index"], "position": j}
            if req["status"] != USABLE:
                problems = ["provider failure"]
            else:
                problems = list(validate(p))
            if problems:
                n["invalid"] += 1
                for reason in problems:
                    invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
                log.append({**entry, "outcome": "invalid", "problems": problems})
                continue
            n["valid"] += 1
            h = prep.sha(p)
            if h in seen:
                n["duplicate"] += 1
                log.append({**entry, "outcome": "duplicate", "workload_sha256": h, "duplicate_of": seen[h]})
                continue
            seen[h] = entry
            n["distinct_valid"] += 1
            if len(admitted) < k:
                admitted.append(p)
                n["admitted"] += 1
                log.append({**entry, "outcome": "admitted", "slot": len(admitted) - 1, "workload_sha256": h})
            else:
                n["overflow"] += 1
                log.append({**entry, "outcome": "overflow", "workload_sha256": h})
    a = len(admitted)
    status_counts: dict[str, int] = {}
    for req in requests:
        status_counts[req["status"]] = status_counts.get(req["status"], 0) + 1
    return {
        "version": VERSION, "admitted": admitted, "journal_sha256": prep.sha(admitted), "log": log,
        "requests": {"planned": r, "attempted": sum(1 for q in requests if q["status"] != "missing"),
                     "transmitted": sum(1 for q in requests if q["status"] not in NOT_SENT),
                     "failed": sum(1 for q in requests if q["status"] != USABLE), "by_status": status_counts},
        "proposals": {**n, "invalid_by_reason": invalid_reasons,
                      "validity_rate": round(n["valid"] / n["generated"], 4) if n["generated"] else None},
        "slots": {"slots": k, "filled_first_time": a, "filled_by_repetition": (k - a) if 0 < a < k else 0,
                  "unfilled_refused": k if a == 0 else 0},
        "slot_plan": [{"slot": i, "workload_sha256": prep.sha(admitted[i % a]) if a else None,
                       "fill": "first" if i < a else "repetition" if a else "unfilled_refused",
                       "repeats_slot": i % a if a and i >= a else None} for i in range(k)],
    }


def executed_counts(admission: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    """After the arm ran: the distinct workloads executed must be exactly the admitted ones."""
    ran = {r["workload_sha256"] for r in runs if r.get("workload_sha256")}
    want = {prep.sha(w) for w in admission["admitted"]}
    if ran != want:
        raise prep.PrepRefused("the executed workloads differ from the admitted ones")
    return {**admission["proposals"], "executed": len(ran)}
