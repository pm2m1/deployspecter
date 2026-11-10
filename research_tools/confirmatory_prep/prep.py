"""DEVELOPMENT-ONLY preparation path for revision candidate r1 (PROPOSED; not adopted). It implements proposed
decisions #23–#28 so they can be reviewed and tested before any approval. It **never** runs a confirmatory
evaluation:
* ``PrepExecutor`` refuses ``real=True`` outright;
* it accepts only SIMULATION integrations or packages whose cases are all in the development split;
* real confirmatory execution stays behind ``research_tools/confirmatory/run.py``'s unchanged real-mode gates, which
  this module does not touch.

What it implements (each piece is tested in ``tests/experimental/test_confirmatory_prep_offline.py``):
* **#24 allocation:** ``K`` slots × ``S`` schedules per revision for every arm, the same seeds for every arm, slot k
  running ``list[k % len(list)]``, no early stopping. ``admit`` takes proposals in model order up to K and counts
  every invalid one.
* **#25 RA-DET-G:** ``generate_det_g``, an outcome-blind generator over the workload schema only, seeded from the
  case id, returning K **distinct** valid workloads (or reporting the shortfall). It uses the same sampling as
  ds-nbc/2's D2.
* **#23 target clauses:** an oracle exposes ``check_clauses(final_state, raw_results) -> {clause: "HOLDS" |
  "VIOLATED" | "NO_VERDICT"}``, and the frozen oracle spec declares the target clauses.
  - **Detection:** a SOUND, corroborated target-clause violation on the defective revision.
  - **Void:** any clause violated on the corrected revision voids the case for the arm (primary), and is queued for
    triage with the clauses named.
  - **Secondary:** a slot-level reading that ignores slots whose corrected runs violate only non-target clauses.
* **#28 accounting:**
  - INCOMPLETE and REFUSED runs consume their schedules and are nondetections, never retried.
  - Primary time is defective-revision schedules to the first target detection in slot order, censored at K × S.
  - Execution seconds are secondary, and end-to-end seconds (adding the arm's generation time) are tertiary.
* **#27 freeze:** ``freeze_record`` hashes the proposer, its instructions, the canonical-JSON context and schema, and
  the model settings. ``verify_freeze`` refuses any request whose inputs differ.
* **No label leakage:** ``leak_check_inputs`` scans every frozen arm input with ``seal_labels.sensitive_tokens``.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools"))

VERSION = "ds-confirm-prep/1"
SOUND = "SOUND"


class PrepRefused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(msg if msg.startswith("refused") else f"refused: {msg}")


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(obj: Any) -> str:
    return hashlib.sha256(obj if isinstance(obj, bytes) else canonical(obj)).hexdigest()


# ------------------------------------------------------------------------------------------------ #24 allocation

def seed(case: str, k: int, j: int) -> int:
    return int(hashlib.sha256(f"ds-confirm-prep|{case}|{k}|{j}".encode()).hexdigest()[:8], 16)


def slots(workloads: list[Any], k: int) -> list[Any | None]:
    """Slot i runs workloads[i % len]; an empty list gives K empty slots (each run REFUSED, no valid workload)."""
    return [workloads[i % len(workloads)] if workloads else None for i in range(k)]


def admit(proposals: list[Any], validate: Callable[[Any], list[str]], k: int) -> dict[str, Any]:
    """Model order, up to K valid; every proposal keeps its place in the accounting."""
    admitted, invalid, beyond = [], [], 0
    for i, p in enumerate(proposals):
        problems = validate(p)
        if problems:
            invalid.append({"index": i, "problems": problems})
        elif len(admitted) < k:
            admitted.append(p)
        else:
            beyond += 1
    return {"admitted": admitted, "invalid": invalid, "valid_beyond_k": beyond, "proposals": len(proposals)}


# ------------------------------------------------------------------------------------------------ #25 RA-DET-G

def _sample(schema: dict[str, Any], rnd: random.Random) -> Any:
    if "enum" in schema:
        return rnd.choice(schema["enum"])
    t = schema.get("type")
    if t == "object":
        return {k: _sample(v, rnd) for k, v in schema["properties"].items()}
    if t == "array":
        return [_sample(schema["items"], rnd) for _ in range(rnd.randint(schema.get("minItems", 0),
                                                                          schema.get("maxItems", 3)))]
    if t == "boolean":
        return rnd.random() < 0.5
    if t == "integer":
        lo, hi = schema["minimum"], schema["maximum"]
        return rnd.choice([lo, hi]) if rnd.random() < 0.5 else rnd.randint(lo, hi)
    raise PrepRefused(f"RA-DET-G can't sample schema type {t!r}")


def generate_det_g(schema: dict[str, Any], validate: Callable[[Any], list[str]], case: str, k: int,
                   max_draws: int = 100_000) -> dict[str, Any]:
    """K distinct valid workloads from the schema alone. A shortfall is reported, never padded with duplicates."""
    t0 = time.monotonic()
    rnd = random.Random(int(hashlib.sha256(f"ds-confirm-prep|RA-DET-G|{case}".encode()).hexdigest()[:16], 16))
    out, seen, draws, rejected = [], set(), 0, 0
    while len(out) < k and draws < max_draws:
        draws += 1
        w = _sample(schema, rnd)
        if validate(w):
            rejected += 1
            continue
        if (h := sha(w)) not in seen:
            seen.add(h)
            out.append(w)
    return {"workloads": out, "distinct": len(out), "shortfall": k - len(out), "draws": draws,
            "rejected_by_validate": rejected, "generation_seconds": round(time.monotonic() - t0, 4),
            "schema_sha256": sha(schema)}


# ------------------------------------------------------------------------------------------------ #27 freeze

def freeze_record(*, proposer_source: bytes, instructions: str, context: Any, schema: Any, model: str,
                  reasoning_effort: str, store: bool, max_output_tokens: int) -> dict[str, Any]:
    return {"proposer_sha256": sha(proposer_source), "instructions_sha256": sha(instructions.encode()),
            "context_sha256": sha(context), "schema_sha256": sha(schema), "model": model,
            "reasoning_effort": reasoning_effort, "store": store, "max_output_tokens": max_output_tokens}


def verify_freeze(frozen: dict[str, Any], current: dict[str, Any]) -> None:
    diff = sorted(k for k in frozen if frozen[k] != current.get(k)) + sorted(set(current) - set(frozen))
    if diff:
        raise PrepRefused(f"the request's frozen inputs changed ({', '.join(diff)}); no request is sent")


# ------------------------------------------------------------------------------------------------ leakage

def leak_check_inputs(labels: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, str]]:
    """Every frozen arm input (schema, context, workloads, prompts) against the sealed labels' sensitive tokens."""
    import seal_labels
    tokens = seal_labels.sensitive_tokens(labels)
    hits = []
    for name, obj in inputs.items():
        text = (obj if isinstance(obj, str) else canonical(obj).decode()).lower()
        hits += [{"input": name, "label_field": what} for tok, what in tokens.items() if tok in text]
    return hits


# ------------------------------------------------------------------------------------------------ #23 #28 scoring

def classify(run: dict[str, Any], targets: set[str]) -> dict[str, Any]:
    clauses = run.get("clauses") or {}
    violated = {c for c, v in clauses.items() if v == "VIOLATED"} if run["verdict"] == SOUND else set()
    return {"target_violated": bool(violated & targets), "nontarget_violated": bool(violated - targets),
            "violated": sorted(violated)}


def score_cell(runs: list[dict[str, Any]], targets: set[str], k: int, s: int, generation_seconds: float = 0.0,
               buggy: str = "R1") -> dict[str, Any]:
    """One case × arm. ``runs`` hold slot ``k``, schedule ``j``, ``slot`` (R1/R2), ``verdict``, ``clauses`` and
    ``wall_seconds``."""
    if not targets:
        raise PrepRefused("the oracle declares no target clause (#23)")
    rs = sorted(runs, key=lambda r: (r["k"], r["j"]))
    b = [r for r in rs if r["slot"] == buggy]
    f = [r for r in rs if r["slot"] != buggy]
    if len(b) != k * s or len(f) != k * s:
        raise PrepRefused(f"unequal budget: {len(b)} defective and {len(f)} corrected schedules, expected {k * s}")
    first, secs = None, 0.0
    for pos, r in enumerate(b, 1):
        secs += r.get("wall_seconds") or 0.0
        if first is None and classify(r, targets)["target_violated"]:
            first = (pos, round(secs, 3))
    fixed_viol = [dict(k=r["k"], j=r["j"], **classify(r, targets)) for r in f if classify(r, targets)["violated"]]
    void = bool(fixed_viol)
    bad_slots = {v["k"] for v in fixed_viol} | {r["k"] for r in f if r["verdict"] != SOUND}
    first_slot_level = None
    for pos, r in enumerate(b, 1):
        if r["k"] not in bad_slots and classify(r, targets)["target_violated"]:
            first_slot_level = pos
            break
    count = lambda rs2, v: sum(1 for r in rs2 if r["verdict"] == v)  # noqa: E731
    return {
        "primary": {"void": void, "detected": (first is not None) and not void,
                    "schedules_to_detection": first[0] if first and not void else None,
                    "censored_at": k * s, "censored": not (first and not void)},
        "secondary_slot_level": {"detected": first_slot_level is not None, "schedules_to_detection": first_slot_level},
        "execution_seconds_to_detection": first[1] if first else None,
        "end_to_end_seconds_to_detection": round(first[1] + generation_seconds, 3) if first else None,
        "triage_queue": fixed_viol,
        "defective": {v: count(b, v) for v in ("SOUND", "REFUSED", "INCOMPLETE", "ERROR")},
        "corrected": {v: count(f, v) for v in ("SOUND", "REFUSED", "INCOMPLETE", "ERROR")},
        "schedules_used_defective": len(b), "schedules_used_corrected": len(f),
    }


# ------------------------------------------------------------------------------------------------ executor

class PrepExecutor:
    """Runs arms under #24 for development or SIMULATION cases only."""

    def __init__(self, *, k: int, s: int, integration: Any, oracle_targets: set[str], real: bool = False,
                 split: str = "development", simulation: bool = False) -> None:
        if real:
            raise PrepRefused("real confirmatory execution is not enabled in the preparation path; it requires "
                              "run.py real and its approval, registration and corpus gates")
        if not simulation and split != "development":
            raise PrepRefused("the preparation path runs only development or SIMULATION cases")
        self.k, self.s, self.integ, self.targets = k, s, integration, oracle_targets

    def run_arm(self, case: str, arm: str, workloads: list[Any], handles: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for i, w in enumerate(slots(workloads, self.k)):
            for j in range(1, self.s + 1):
                for slot in ("R1", "R2"):
                    rec = {"case_id": case, "arm": arm, "k": i, "j": j, "slot": slot, "seed": seed(case, i, j),
                           "workload_sha256": sha(w) if w is not None else None}
                    if w is None:
                        rec.update(verdict="REFUSED", reason="no valid workload", clauses={}, wall_seconds=0.0)
                    else:
                        t0 = time.monotonic()
                        r = self.integ.run(handles[slot], w, rec["seed"])  # never retried
                        rec.update(verdict=r["verdict"], clauses=r.get("clauses") or {},
                                   wall_seconds=round(time.monotonic() - t0, 4))
                    out.append(rec)
        return out


# ------------------------------------------------------------------------------------------------ #24 feasibility

def worst_case_seconds(k: int, s: int, max_run_seconds: float) -> float:
    """An arm's worst-case execution time for one case: K × S schedules on each of the two revisions, every run
    stopping at the coordinator's fixed limit (an INCOMPLETE run). Derived from the instrument, not from outcomes."""
    return 2 * k * s * max_run_seconds


def time_cap_problems(k: int, s: int, t_seconds: float, max_run_seconds: float) -> list[str]:
    """A per-arm time cap T must never cut an arm's schedules short, or equal budgets (#24) would break."""
    worst = worst_case_seconds(k, s, max_run_seconds)
    return [] if worst <= t_seconds else [f"T = {t_seconds:g} s can censor an arm: worst case {worst:g} s "
                                          f"({2 * k * s} runs at {max_run_seconds:g} s)"]
