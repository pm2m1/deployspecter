"""EXPERIMENTAL onboarding study for independent participants (``onb-study/1``): allocation, blind-scoring bundles
and the predeclared analysis. **NOT RUN.** No participant has been recruited, and no participation record exists.

**Design.** Within-participant, with 2 conditions (D: deterministic and documentation-assisted; M: the same plus
Muse) and 2 apps (A and B, pinned). Each participant onboards both apps, one under each condition. Four sequences
counterbalance condition order and the app–condition pairing (a 2×2 Latin square, crossed):

    S1: (D, A) then (M, B)     S2: (M, A) then (D, B)     S3: (D, B) then (M, A)     S4: (M, B) then (D, A)

``allocate`` assigns participants to sequences in randomized blocks of four, from a seed recorded before
recruitment.

**Blind scoring.** ``blind_bundle`` copies each attempt's ``run.json`` into a scoring bundle under a random id, with
the condition, app, operator and every Muse trace removed. A scorer who isn't the participant or the experimenter
re-derives S2 and S3 with ``correctness.s2``/``s3`` from the bundle alone. The key mapping ids back to sessions is
written to a separate file, held by the experimenter.

**Analysis** (``analyze``), fixed here before any data:
* **Primary:** time to S3, per participant: the M session minus the D session. A session that didn't reach S3
  within the budget is censored at the budget.
* **Test:** an exact two-sided sign test on the pairs whose order is determined (one finished and the other didn't,
  or both finished). Pairs where both are censored are ties and are excluded and counted.
* **Secondary, descriptive only:** median paired differences in interventions, failed attempts, adapter lines
  revised, and proposals rejected. Model cost is reported per condition.
* **Sample rule:** with fewer than ``MIN_PARTICIPANTS`` (12, at least 3 per sequence) independent participants,
  the verdict is ``INSUFFICIENT SAMPLE`` and the results are descriptive only.
* **Exclusions:** developer sessions (``operator_kind != "independent"``) are always excluded.
"""

from __future__ import annotations

import json
import math
import random
import secrets
import shutil
import statistics
from pathlib import Path
from typing import Any

SEQUENCES = {"S1": (("D", "A"), ("M", "B")), "S2": (("M", "A"), ("D", "B")),
             "S3": (("D", "B"), ("M", "A")), "S4": (("M", "B"), ("D", "A"))}
MIN_PARTICIPANTS = 12
MIN_PER_SEQUENCE = 3
SECONDARY = ("interventions", "attempts_failed", "adapter_lines_revised", "proposals_rejected")


def allocate(participant_ids: list[str], seed: str) -> dict[str, str]:
    """Randomized blocks of four sequences, in the order participants enrol. Deterministic given the seed."""
    rng = random.Random(seed)
    out, block = {}, []
    for p in participant_ids:
        if not block:
            block = list(SEQUENCES)
            rng.shuffle(block)
        out[p] = block.pop()
    return out


def blind_bundle(session_dirs: list[Path], out: Path, key_file: Path) -> dict[str, str]:
    """A scoring bundle with every attempt's run evidence under a random id, and no condition, app or operator."""
    if out.exists() or key_file.exists():
        raise FileExistsError("the bundle and key are never overwritten")
    out.mkdir(parents=True)
    key: dict[str, str] = {}
    for s in session_dirs:
        for line in (s / "events.jsonl").read_text().splitlines():
            e = json.loads(line)
            if e["kind"] != "attempt" or not e.get("evidence"):
                continue
            run = Path(e["evidence"]) / "run.json"
            if not run.exists():
                continue
            rid = secrets.token_hex(6)
            r = json.loads(run.read_text())
            for k in ("app", "image", "schema", "pinned_sources", "stderr_tail", "network"):
                r.pop(k, None)
            (out / f"{rid}.json").write_text(json.dumps(r, indent=1, sort_keys=True))
            wl = s / "workload_for_scoring.json"
            if wl.exists():
                shutil.copyfile(wl, out / f"{rid}.workload.json")
            key[rid] = f"{s}#{e['seq']}"
    key_file.write_text(json.dumps(key, indent=1, sort_keys=True))
    return key


def _sign_test(pos: int, neg: int) -> float:
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def analyze(measures: list[dict[str, Any]], allocation: dict[str, str], budget_seconds: float) -> dict[str, Any]:
    """``measures``: ``session.measure`` dicts, each with ``participant`` set (its operator id) and ``condition``."""
    ind = [m for m in measures if m.get("operator_kind") == "independent"]
    excluded = len(measures) - len(ind)
    by: dict[str, dict[str, dict[str, Any]]] = {}
    for m in ind:
        by.setdefault(m["operator"], {})[m["condition"]] = m
    pairs = {p: c for p, c in by.items() if set(c) == {"D", "M"}}
    per_seq: dict[str, int] = {}
    for p in pairs:
        per_seq[allocation.get(p, "?")] = per_seq.get(allocation.get(p, "?"), 0) + 1
    pos = neg = ties = 0
    diffs = []
    for c in pairs.values():
        d, m = c["D"].get("seconds_to_S3"), c["M"].get("seconds_to_S3")
        if d is None and m is None:
            ties += 1
            continue
        dv = budget_seconds if d is None else d
        mv = budget_seconds if m is None else m
        diffs.append(mv - dv)
        if mv < dv:
            pos += 1  # M faster
        elif mv > dv:
            neg += 1
        else:
            ties += 1
    secondary = {k: statistics.median([c["M"].get(k, 0) - c["D"].get(k, 0) for c in pairs.values()])
                 if pairs else None for k in SECONDARY}
    cost = {cond: round(sum(c[cond].get("est_usd") or 0 for c in pairs.values()), 6) for cond in ("D", "M")}
    n = len(pairs)
    enough = n >= MIN_PARTICIPANTS and all(per_seq.get(s, 0) >= MIN_PER_SEQUENCE for s in SEQUENCES)
    return {"version": "onb-study-analysis/1", "independent_participants_paired": n,
            "excluded_non_independent_sessions": excluded, "per_sequence": per_seq,
            "primary": {"m_faster": pos, "d_faster": neg, "ties_or_both_censored": ties,
                        "sign_test_p_two_sided": _sign_test(pos, neg),
                        "median_difference_seconds_m_minus_d": statistics.median(diffs) if diffs else None},
            "secondary_median_differences_m_minus_d": secondary, "model_cost_usd": cost,
            "verdict": "ANALYZABLE (predeclared test applies)" if enough else
            f"INSUFFICIENT SAMPLE ({n} paired independent participants; need >= {MIN_PARTICIPANTS} and >= "
            f"{MIN_PER_SEQUENCE} per sequence): descriptive only"}
