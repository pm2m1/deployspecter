"""DEVELOPMENT-ONLY preparation path for revision candidate r2 (PROPOSED; not adopted, not registered). It extends
``prep.py`` (r1, unchanged) with r2's proposed rules so they can be reviewed and tested before any approval. It
**never** runs a confirmatory evaluation: ``R2Executor`` refuses ``real=True`` and non-development cases exactly as
``prep.PrepExecutor`` does, and ``research_tools/confirmatory/run.py``'s real-mode gates are untouched.

What r2 adds (tests: ``tests/experimental/test_confirmatory_prep_r2_offline.py``):
* **#24 fixed values:** K = 6, S = 2; any other value is refused.
* **#24a requests and slots:** ``RequestPlan`` allows exactly R requests per case and arm, all before the first run;
  any further request, or any request once a run has started, is refused (outcome-dependent). ``slot_accounting``
  reports distinct, repeated and unfilled slots and keeps every invalid proposal in the denominator.
* **Admission journal:** ``journal`` hashes each arm's admitted workload list before any run, and the executor refuses
  a workload list that differs from it (no outcome-dependent selection, substitution or reordering).
* **#15 as amended in r2 (time):** no arm is truncated. Each run is held to the executor limit ``L_RUN_SECONDS``
  (a longer run is INCOMPLETE whatever its verdict, for every arm alike). ``SAFETY_CEILING_SECONDS`` per case and
  arm is above the bound L implies; reaching it raises ``InfrastructureFailure``, which excludes the case for every
  arm. ``time_rule_problems`` and ``ceiling_report`` check a proposed rule against instrument bounds.
* **#28 eligibility:** ``EligibilityRule`` objects are hashed into the freeze (``freeze_eligibility``) and applied by
  one ``UniformValidator`` shared by every arm, RA-DET-G's generator included; a rule changed after the freeze is
  refused.
* **#29 exposure:** ``exposure_record`` and ``independence_problems`` check that the Integrator is a different
  person from every other role, and that no fix-blind role read fix material, before the freeze.
* **#27 model identity:** ``r2_generate`` checks the request plan, then calls ``verified_request.verified_generate``,
  which verifies every frozen hash before sending and the provider-reported model (the exact accepted string)
  after.
"""

from __future__ import annotations

import hashlib
import inspect
import time
from collections.abc import Callable
from typing import Any

import prep

VERSION = "ds-confirm-prep-r2/1"
K, S = 6, 2
COORDINATOR_STOP_SECONDS = 45.0   # run_coordinated: stop.after_s, after which an uncovered run is INCOMPLETE
CONTAINER_LIMIT_SECONDS = 240.0   # run_coordinated: the host's per-run container wall-clock limit, then a kill
L_RUN_SECONDS = 300.0             # r2 executor limit per run: a longer run is INCOMPLETE whatever its verdict
GAP_ALLOWANCE_SECONDS = 1.0       # between consecutive runs (ds-nbc/2: at most 0.9 s)
SAFETY_CEILING_SECONDS = 7500.0   # per case and arm; reaching it excludes the case for every arm
FIX_EXPOSURE_KINDS = {"fix", "fix_commit", "fix_tests", "fix_changelog", "labels", "outcomes"}
FIX_BLIND_ROLES = {"integrator", "oracle_author"}
ROLES = {"curator", "integrator", "oracle_author", "oracle_reviewer", "triage_reviewer", "adjudicator", "developer"}


class InfrastructureFailure(prep.PrepRefused):
    """The per case and arm safety ceiling was reached: the case is excluded for every arm (never censoring)."""


def require_r2_allocation(k: int, s: int) -> None:
    if (k, s) != (K, S):
        raise prep.PrepRefused(f"r2 fixes K = {K} and S = {S} (#24); got K = {k}, S = {s}")


# ------------------------------------------------------------------------------------------------ time rule

def runs_per_arm_case(k: int = K, s: int = S) -> int:
    return 2 * k * s


def bound_seconds(per_run_seconds: float, k: int = K, s: int = S, gap: float = GAP_ALLOWANCE_SECONDS) -> float:
    """Wall-clock for one case and arm if every run took ``per_run_seconds``, with ``gap`` between runs."""
    n = runs_per_arm_case(k, s)
    return round(n * per_run_seconds + (n - 1) * gap, 3)


def time_rule_problems(ceiling: float, l_run: float = L_RUN_SECONDS, k: int = K, s: int = S,
                       gap: float = GAP_ALLOWANCE_SECONDS) -> list[str]:
    """A per case and arm limit is safe only if it can't be reached while every run respects the per-run limit."""
    need = bound_seconds(l_run, k, s, gap)
    return [] if ceiling > need else [f"a {ceiling:g} s limit can be reached by {runs_per_arm_case(k, s)} runs "
                                      f"within the {l_run:g} s per-run limit ({need:g} s): it would truncate arms"]


def ceiling_report(candidates: list[float], observed_incomplete_max: float, observed_gap_max: float) -> list[dict]:
    """For each candidate ceiling: the margin over the observed worst case (every run stopping at the slowest
    observed INCOMPLETE time) and over the per-run-limit bound. Instrument-derived; no outcome is used."""
    observed = bound_seconds(observed_incomplete_max, gap=observed_gap_max)
    hard = bound_seconds(L_RUN_SECONDS)
    return [{"ceiling_s": c, "observed_worst_case_s": observed, "margin_over_observed_s": round(c - observed, 3),
             "per_run_limit_bound_s": hard, "margin_over_bound_s": round(c - hard, 3),
             "can_truncate_an_arm": c <= hard} for c in candidates]


# ------------------------------------------------------------------------------------------------ #24a requests

class RequestPlan:
    """Exactly ``r`` requests for one case and arm, all made before the case's first run."""

    def __init__(self, case: str, arm: str, r: int) -> None:
        self.case, self.arm, self.r = case, arm, r
        self.requests: list[dict[str, Any]] = []
        self.runs_started = False

    def begin(self) -> int:
        if self.runs_started:
            raise prep.PrepRefused("a request after a run has started would be outcome-dependent (#24a)")
        if len(self.requests) >= self.r:
            raise prep.PrepRefused(f"the plan allows exactly R = {self.r} requests; no additional, replacement or "
                                   "retry request is made (#24a)")
        self.requests.append({"index": len(self.requests), "status": "sent"})
        return len(self.requests) - 1

    def record(self, index: int, result: dict[str, Any]) -> None:
        self.requests[index].update(status=result.get("status"), usable=bool(result.get("usable")))

    def start_runs(self) -> None:
        self.runs_started = True

    def summary(self) -> dict[str, Any]:
        return {"case": self.case, "arm": self.arm, "R": self.r, "made": len(self.requests),
                "unused_allowance": self.r - len(self.requests),
                "by_status": {s: sum(1 for q in self.requests if q["status"] == s)
                              for s in sorted({q["status"] for q in self.requests})}}


def r2_generate(client: Any, frozen: dict[str, Any], plan: RequestPlan, **request: Any) -> dict[str, Any]:
    """One planned request through #27's verified client. A refused freeze check still uses up the plan's slot,
    because nothing outcome-dependent may replace it."""
    import verified_request
    i = plan.begin()
    try:
        out = verified_request.verified_generate(client, frozen, **request)
    except prep.PrepRefused:
        plan.record(i, {"status": "refused_before_sending", "usable": False})
        raise
    plan.record(i, out)
    return out


# ------------------------------------------------------------------------------------------------ #24a slots

def slot_accounting(admission: dict[str, Any], k: int = K) -> dict[str, Any]:
    n = len(admission["admitted"])
    distinct = len({prep.sha(w) for w in admission["admitted"]})
    return {"proposals": admission["proposals"], "invalid": len(admission["invalid"]),
            "valid_beyond_k": admission["valid_beyond_k"], "admitted": n, "distinct_admitted": distinct,
            "slots": k, "slots_filled_first_time": min(n, k) if n else 0,
            "slots_filled_by_repetition": (k - n) if 0 < n < k else 0,
            "slots_unfilled_refused": k if n == 0 else 0}


def journal(admissions: dict[str, list[Any]]) -> dict[str, str]:
    """The admitted workload list of every arm, hashed before any run."""
    return {arm: prep.sha(wl) for arm, wl in sorted(admissions.items())}


# ------------------------------------------------------------------------------------------------ #28 eligibility

class EligibilityRule:
    def __init__(self, rule_id: str, description: str, rejects: Callable[[Any], bool]) -> None:
        self.rule_id, self.description, self.rejects = rule_id, description, rejects

    def digest(self) -> str:
        try:
            src = inspect.getsource(self.rejects)
        except (OSError, TypeError):
            src = repr(self.rejects)
        return hashlib.sha256(f"{self.rule_id}\0{self.description}\0{src}".encode()).hexdigest()


def freeze_eligibility(rules: list[EligibilityRule]) -> str:
    return prep.sha(sorted((r.rule_id, r.digest()) for r in rules))


class UniformValidator:
    """The one validator every arm uses: the integration's ``validate`` plus the frozen eligibility rules."""

    def __init__(self, validate: Callable[[Any], list[str]], rules: list[EligibilityRule], frozen_sha: str) -> None:
        if freeze_eligibility(rules) != frozen_sha:
            raise prep.PrepRefused("the eligibility rules differ from the freeze; none may be added, removed or "
                                   "changed after it (#28)")
        self.validate, self.rules, self.frozen_sha = validate, rules, frozen_sha
        self.rejections: dict[str, int] = {r.rule_id: 0 for r in rules}

    def __call__(self, w: Any) -> list[str]:
        problems = list(self.validate(w))
        if problems:
            return problems
        for r in self.rules:
            if r.rejects(w):
                self.rejections[r.rule_id] += 1
                problems.append(f"ineligible:{r.rule_id}")
        return problems


# ------------------------------------------------------------------------------------------------ #29 exposure

def exposure_record(person: str, role: str, case: str, reads: list[dict[str, str]], at: float) -> dict[str, Any]:
    if role not in ROLES:
        raise prep.PrepRefused(f"unknown role {role!r}")
    rec = {"person": person, "role": role, "case": case, "reads": reads, "at": at}
    return {**rec, "sha256": prep.sha(rec)}


def independence_problems(records: list[dict[str, Any]], freeze_at: float) -> list[str]:
    p: list[str] = []
    for r in records:
        body = {k: v for k, v in r.items() if k != "sha256"}
        if prep.sha(body) != r.get("sha256"):
            p.append(f"{r['person']}: exposure record altered")
        if r["at"] >= freeze_at:
            p.append(f"{r['person']} ({r['role']}, {r['case']}): exposure recorded after the freeze")
        if r["role"] in FIX_BLIND_ROLES:
            bad = sorted({x["kind"] for x in r["reads"]} & FIX_EXPOSURE_KINDS)
            if bad:
                p.append(f"{r['person']} ({r['role']}, {r['case']}): read {', '.join(bad)}")
    for case in sorted({r["case"] for r in records}):
        roles: dict[str, set[str]] = {}
        for r in records:
            if r["case"] == case:
                roles.setdefault(r["person"], set()).add(r["role"])
        if not any("integrator" in rs for rs in roles.values()):
            p.append(f"{case}: no Integrator recorded")
        for person, rs in sorted(roles.items()):
            if len(rs) > 1:
                p.append(f"{case}: {person} holds {', '.join(sorted(rs))}; the Integrator and every role are "
                         "separate people (#29)")
    return p


# ------------------------------------------------------------------------------------------------ executor

class R2Executor(prep.PrepExecutor):
    """r2's allocation and time rule, for development or SIMULATION cases only."""

    def __init__(self, *, integration: Any, oracle_targets: set[str], journal_sha: dict[str, str],
                 clock: Callable[[], float] = time.monotonic, k: int = K, s: int = S, **kw: Any) -> None:
        require_r2_allocation(k, s)
        super().__init__(k=k, s=s, integration=integration, oracle_targets=oracle_targets, **kw)
        self.journal_sha, self.clock = journal_sha, clock

    def run_arm(self, case: str, arm: str, workloads: list[Any], handles: dict[str, Any],
                plan: RequestPlan | None = None) -> list[dict[str, Any]]:
        if self.journal_sha.get(arm) != prep.sha(workloads):
            raise prep.PrepRefused(f"arm {arm}'s workloads differ from the admission journal; nothing is "
                                   "substituted or reordered after the journal")
        if plan is not None:
            plan.start_runs()
        out, started = [], self.clock()
        for i, w in enumerate(prep.slots(workloads, self.k)):
            for j in range(1, self.s + 1):
                for slot in ("R1", "R2"):
                    rec = {"case_id": case, "arm": arm, "k": i, "j": j, "slot": slot, "seed": prep.seed(case, i, j),
                           "workload_sha256": prep.sha(w) if w is not None else None}
                    if w is None:
                        rec.update(verdict="REFUSED", reason="no valid workload", clauses={}, wall_seconds=0.0)
                    else:
                        t0 = self.clock()
                        r = self.integ.run(handles[slot], w, rec["seed"])  # never retried
                        secs = round(self.clock() - t0, 4)
                        rec.update(verdict=r["verdict"], clauses=r.get("clauses") or {}, wall_seconds=secs)
                        if secs > L_RUN_SECONDS:
                            rec.update(verdict="INCOMPLETE", clauses={}, reason=f"executor limit {L_RUN_SECONDS:g} s",
                                       verdict_before_limit=r["verdict"])
                    out.append(rec)
                    if self.clock() - started >= SAFETY_CEILING_SECONDS:
                        raise InfrastructureFailure(
                            f"{case}/{arm}: safety ceiling {SAFETY_CEILING_SECONDS:g} s reached after {len(out)} "
                            "runs; the case is excluded for every arm (infrastructure failure, never censoring)")
        return out


def score_case(cells: dict[str, list[dict[str, Any]]], targets: set[str], generation_seconds: dict[str, float]
               | None = None, k: int = K, s: int = S) -> dict[str, Any]:
    """Every arm of one case, with equal schedule accounting checked across arms and per-arm time reported."""
    require_r2_allocation(k, s)
    gen = generation_seconds or {}
    out: dict[str, Any] = {"arms": {}, "schedules_per_revision": k * s}
    for arm, runs in sorted(cells.items()):
        cell = prep.score_cell(runs, targets, k, s, generation_seconds=gen.get(arm, 0.0))
        cell["time"] = {"execution_seconds_total": round(sum(r.get("wall_seconds") or 0 for r in runs), 3),
                        "runs_over_executor_limit": sum(1 for r in runs if "verdict_before_limit" in r),
                        "generation_seconds": gen.get(arm, 0.0)}
        cell["distinct_workloads_executed"] = len({r["workload_sha256"] for r in runs if r["workload_sha256"]})
        out["arms"][arm] = cell
    used = {(c["schedules_used_defective"], c["schedules_used_corrected"]) for c in out["arms"].values()}
    if len(used) != 1:
        raise prep.PrepRefused(f"arms used unequal schedules: {sorted(used)}")
    return out


def excluded_case(case: str, reason: str, arms: list[str]) -> dict[str, Any]:
    """An infrastructure failure excludes the case for every arm; it is reported, never rerun."""
    return {"case": case, "excluded_for_every_arm": sorted(arms), "reason": reason, "rerun": False}
