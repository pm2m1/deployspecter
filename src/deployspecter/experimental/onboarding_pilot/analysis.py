"""EXPERIMENTAL onboarding pilot: the frozen analysis (``onb-pilot-analysis/1``).

The pilot has one operator and one app per condition, so it is descriptive only. The table is per
condition: the measures from ``session.measure``, with a censored milestone (not reached within budget) reported as
``">= budget"``, never imputed.

The verdict is always ``"NONBLIND EXPLORATORY PILOT: descriptive only"`` for operator kinds other than
``independent``. With independent participants, ``participant_study.analyze`` applies instead.

No direction is claimed: two apps differ in difficulty, and the operator built the harness and knows the codebase.
"""

from __future__ import annotations

from typing import Any

MEASURES = ("seconds_to_S1", "seconds_to_S2", "seconds_to_S3", "seconds_total", "interventions", "proposals",
            "proposals_rejected", "muse_proposals_rejected", "attempts", "attempts_failed", "adapter_lines_final",
            "adapter_lines_revised", "model_calls", "tokens_in", "tokens_out", "est_usd", "s3_pass_rate_on_s2_runs")


def table(measures: list[dict[str, Any]], budget_seconds: float) -> dict[str, Any]:
    rows = {}
    for m in measures:
        r = {}
        for k in MEASURES:
            v = m.get(k)
            if k.startswith("seconds_to_") and v is None:
                r[k] = f">= {budget_seconds:.0f} (censored: not reached)" if m.get("ended") else "running"
            else:
                r[k] = v
        rows[f"{m['condition']}:{m['app']}"] = r
    kinds = {m["operator_kind"] for m in measures}
    verdict = ("NONBLIND EXPLORATORY PILOT: descriptive only (single operator, one app per condition; no comparison "
               "of conditions is supported)") if kinds != {"independent"} else "use participant_study.analyze"
    return {"version": "onb-pilot-analysis/1", "rows": rows, "verdict": verdict}
