"""EXPERIMENTAL three-application onboarding study (``onb-study/2``, DRAFT for revision candidate r2's #30 option A):
allocation, timing, analysis and sample-size planning. **NOT RUN.** No participant has been recruited or simulated;
the tests use synthetic sessions only. ``participant_study.py`` (onb-study/1, 2 applications) is unchanged.

**Design** (``onboarding/participant_study/PROTOCOL_r2_3APP.md``). Three applications X, Y, Z and two conditions
(D, M). Each participant onboards two *different* applications, one per condition, in two periods.
* ``min18``: six sequences (3 application pairs × 2 condition orders), randomized blocks of 6, at least 3
  participants per sequence (18). Applications and conditions are balanced and conditions are balanced over periods;
  application order within a pair is fixed.
* ``pref24``: those six plus their six mirrors (application order reversed), randomized blocks of 12, at least 2 per
  sequence (24). Application × period is balanced too.

**Timing.** ``timing`` reads a session log written by ``pilot_v2.py``:
* end-to-end seconds to S3 (primary), from the recorder's clock;
* execution-only seconds to S3 (secondary): end-to-end minus the logged duration of every Muse call made before
  S3. A call without a logged duration makes execution-only time unavailable for that session (never imputed).

**Analysis** (``analyze``), fixed before any data:
* each session's time is divided by its application's median time over all sessions (censored values at the
  budget), because a participant's two sessions are on different applications;
* the paired difference is M − D, standardized; an exact two-sided sign test on the pairs whose direction is
  determined. A pair with one censored session is determined only when the censored session's lower bound is at
  least the other's value; both censored, or undetermined, are counted and excluded;
* sessions that don't match the participant's allocated sequence (application, condition, period) are protocol
  deviations: counted and excluded;
* below the design's minimum, or with any sequence under its per-sequence minimum, the verdict is INSUFFICIENT
  SAMPLE;
* RA-Q4's claim rule is reported descriptively: a median reduction of at least 30% in both minutes and adapter lines
  on every one of the three applications. "No lost detection" is assessed separately.
"""

from __future__ import annotations

import math
import random
import statistics
from pathlib import Path
from typing import Any

from . import session

VERSION = "onb-study/2"
APPS = ("X", "Y", "Z")
CONDITIONS = ("D", "M")
BASE = {"Q1": (("X", "D"), ("Y", "M")), "Q2": (("X", "M"), ("Y", "D")),
        "Q3": (("X", "D"), ("Z", "M")), "Q4": (("X", "M"), ("Z", "D")),
        "Q5": (("Y", "D"), ("Z", "M")), "Q6": (("Y", "M"), ("Z", "D"))}
MIRROR = {f"Q{int(q[1:]) + 6}": ((p2[0], p1[1]), (p1[0], p2[1])) for q, (p1, p2) in BASE.items()}
DESIGNS = {"min18": {"sequences": dict(BASE), "min_participants": 18, "min_per_sequence": 3},
           "pref24": {"sequences": {**BASE, **MIRROR}, "min_participants": 24, "min_per_sequence": 2}}
CLAIM_REDUCTION = 0.30
ALPHA = 0.05


def sequences(design: str) -> dict[str, tuple[tuple[str, str], tuple[str, str]]]:
    if design not in DESIGNS:
        raise ValueError(f"design is one of {sorted(DESIGNS)}")
    return DESIGNS[design]["sequences"]


def allocate(participant_ids: list[str], seed: str, design: str = "min18") -> dict[str, str]:
    """Randomized blocks of all the design's sequences, in enrolment order. Deterministic given the seed."""
    seqs = sequences(design)
    rng = random.Random(f"{VERSION}|{design}|{seed}")
    out: dict[str, str] = {}
    block: list[str] = []
    for p in participant_ids:
        if not block:
            block = list(seqs)
            rng.shuffle(block)
        out[p] = block.pop()
    return out


def balance(design: str, per_sequence: int = 1) -> dict[str, dict[str, int]]:
    """Counts per application × condition, condition × period and application × period, for a full design."""
    ac: dict[str, int] = {}
    cp: dict[str, int] = {}
    ap: dict[str, int] = {}
    for seq in sequences(design).values():
        for period, (app, cond) in enumerate(seq, 1):
            for d, key in ((ac, f"{app}/{cond}"), (cp, f"{cond}/P{period}"), (ap, f"{app}/P{period}")):
                d[key] = d.get(key, 0) + per_sequence
    return {"application_condition": ac, "condition_period": cp, "application_period": ap}


# ------------------------------------------------------------------------------------------------ timing

def timing(session_dir: Path) -> dict[str, Any]:
    ev = session.events(session_dir)
    if session.verify(session_dir):
        raise session.SessionRefused("the session log is broken")
    t0 = ev[0]["t"]
    s3 = next((e["t"] for e in ev if e["kind"] == "milestone" and e["name"] == "S3"), None)
    calls = [e for e in ev if e["kind"] == "model_call" and (s3 is None or e["t"] <= s3)]
    untimed = sum(1 for c in calls if c.get("call_seconds") is None)
    muse_s = round(sum(c.get("call_seconds") or 0 for c in calls), 3)
    e2e = (s3 - t0) if s3 is not None else None
    return {"end_to_end_seconds_to_S3": e2e, "muse_call_seconds_before_S3": muse_s, "untimed_muse_calls": untimed,
            "execution_only_seconds_to_S3": None if untimed or e2e is None else round(e2e - muse_s, 3),
            "execution_only_available": untimed == 0, "started_t": t0}


def measure(session_dir: Path) -> dict[str, Any]:
    return {**session.measure(session_dir), **timing(session_dir)}


# ------------------------------------------------------------------------------------------------ statistics

def sign_test_p(pos: int, neg: int) -> float:
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def critical_count(n: int, alpha: float = ALPHA) -> int | None:
    """The smallest k such that k or more of n pairs favouring one condition is significant (two-sided)."""
    for k in range(n // 2 + 1, n + 1):
        if sign_test_p(k, n - k) <= alpha:
            return k
    return None


def power(n: int, p: float, alpha: float = ALPHA) -> float:
    """Exact power of the two-sided sign test with n determined pairs, when each favours M with probability p."""
    k = critical_count(n, alpha)
    if k is None:
        return 0.0
    pmf = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    return sum(pmf[k:]) + sum(pmf[:n - k + 1])


def minimum_detectable(n: int, target: float = 0.8, alpha: float = ALPHA) -> float | None:
    """The smallest p (share of pairs favouring one condition) detected with at least ``target`` power."""
    for i in range(500, 1001):
        if power(n, i / 1000, alpha) >= target:
            return i / 1000
    return None


def sample_size_table(ns: list[int], ps: list[float], attrition: list[float]) -> dict[str, Any]:
    """Power by planned n and true p, and the effect of losing a share of pairs to ties, censoring or deviations."""
    rows = []
    for n in ns:
        k = critical_count(n)
        rows.append({"n": n, "critical": k, "critical_p": round(sign_test_p(k, n - k), 4) if k else None,
                     "minimum_detectable_p_80": minimum_detectable(n),
                     "power": {f"{p:.2f}": round(power(n, p), 3) for p in ps},
                     "attrition": {f"{a:.0%}": {"n_effective": math.floor(n * (1 - a)),
                                                "minimum_detectable_p_80": minimum_detectable(math.floor(n * (1 - a)))}
                                   for a in attrition}})
    return {"alpha_two_sided": ALPHA, "test": "exact sign test", "rows": rows}


# ------------------------------------------------------------------------------------------------ analysis

def _median_reduction(m: list[float], d: list[float]) -> float | None:
    if not m or not d or statistics.median(d) == 0:
        return None
    return round(1 - statistics.median(m) / statistics.median(d), 4)


def analyze(measures: list[dict[str, Any]], allocation: dict[str, str], budget_seconds: float,
            design: str = "min18") -> dict[str, Any]:
    """``measures``: ``measure`` dicts (``session.measure`` plus ``timing``)."""
    seqs, spec = sequences(design), DESIGNS[design]
    ind = [m for m in measures if m.get("operator_kind") == "independent"]
    by: dict[str, list[dict[str, Any]]] = {}
    for m in ind:
        by.setdefault(m["operator"], []).append(m)
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    deviations: list[str] = []
    for p, ss in sorted(by.items()):
        ss = sorted(ss, key=lambda m: m["started_t"])
        want = seqs.get(allocation.get(p, ""))
        got = tuple((m["app"], m["condition"]) for m in ss)
        if want is None or got != want:
            deviations.append(f"{p}: sessions {got} don't match allocated sequence {allocation.get(p)} {want}")
            continue
        pairs[p] = {m["condition"]: m for m in ss}

    def cens(m: dict[str, Any], key: str) -> tuple[float, bool]:
        v = m.get(key)
        return (budget_seconds, True) if v is None else (v, False)

    app_sessions = [m for c in pairs.values() for m in c.values()]
    medians = {a: statistics.median([cens(m, "end_to_end_seconds_to_S3")[0] for m in app_sessions
                                     if m["app"] == a]) for a in APPS if any(m["app"] == a for m in app_sessions)}

    def compare(key: str, meds: dict[str, float]) -> dict[str, Any]:
        pos = neg = ties = undetermined = unavailable = 0
        diffs = []
        for c in pairs.values():
            d, m = c["D"], c["M"]
            if key == "execution_only_seconds_to_S3" and not (d.get("execution_only_available") and
                                                               m.get("execution_only_available")):
                unavailable += 1
                continue
            (dv, dc), (mv, mc) = cens(d, key), cens(m, key)
            ds, ms = dv / meds[d["app"]], mv / meds[m["app"]]
            if dc and mc:
                ties += 1
                continue
            if mc or dc:  # one censored: its true time exceeds its lower bound
                if (ms < ds) if mc else (ds < ms):
                    undetermined += 1  # the lower bound doesn't settle the direction
                    continue
                diffs.append(ms - ds)
                neg, pos = neg + bool(mc), pos + bool(dc)
                continue
            diffs.append(ms - ds)
            pos, neg, ties = pos + (ms < ds), neg + (ms > ds), ties + (ms == ds)
        return {"m_faster": pos, "d_faster": neg, "ties_or_both_censored": ties, "undetermined": undetermined,
                "execution_only_unavailable": unavailable, "sign_test_p_two_sided": sign_test_p(pos, neg),
                "median_standardized_difference_m_minus_d": round(statistics.median(diffs), 4) if diffs else None}

    exec_meds = {a: statistics.median([cens(m, "execution_only_seconds_to_S3")[0] for m in app_sessions
                                       if m["app"] == a and m.get("execution_only_available")] or [1.0])
                 for a in medians}
    per_seq = {q: sum(1 for p in pairs if allocation[p] == q) for q in seqs}
    claim = {}
    for a in APPS:
        ms = [m for m in app_sessions if m["app"] == a and m["condition"] == "M"]
        ds = [m for m in app_sessions if m["app"] == a and m["condition"] == "D"]
        claim[a] = {"minutes_reduction": _median_reduction([cens(m, "end_to_end_seconds_to_S3")[0] for m in ms],
                                                           [cens(m, "end_to_end_seconds_to_S3")[0] for m in ds]),
                    "lines_reduction": _median_reduction([m.get("adapter_lines_final") or 0 for m in ms],
                                                         [m.get("adapter_lines_final") or 0 for m in ds])}
    claim_met = all(v["minutes_reduction"] is not None and v["minutes_reduction"] >= CLAIM_REDUCTION and
                    v["lines_reduction"] is not None and v["lines_reduction"] >= CLAIM_REDUCTION
                    for v in claim.values())
    n = len(pairs)
    enough = n >= spec["min_participants"] and all(v >= spec["min_per_sequence"] for v in per_seq.values())
    return {"version": f"{VERSION}-analysis", "design": design, "independent_participants_paired": n,
            "excluded_non_independent_sessions": len(measures) - len(ind), "protocol_deviations": deviations,
            "per_sequence": per_seq, "application_medians_seconds": medians,
            "primary_end_to_end": compare("end_to_end_seconds_to_S3", medians),
            "secondary_execution_only": compare("execution_only_seconds_to_S3", exec_meds),
            "model_cost_usd": {c: round(sum(p[c].get("est_usd") or 0 for p in pairs.values()), 6) for c in CONDITIONS},
            "claim_rule_descriptive": {"per_application": claim, "met_on_all_three": claim_met,
                                       "note": "no lost detection is assessed separately"},
            "verdict": "ANALYZABLE (predeclared test applies)" if enough else
            f"INSUFFICIENT SAMPLE ({n} paired independent participants; need >= {spec['min_participants']} and >= "
            f"{spec['min_per_sequence']} per sequence): descriptive only"}
