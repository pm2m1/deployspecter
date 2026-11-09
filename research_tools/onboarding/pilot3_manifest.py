"""Builds the frozen onb-pilot/3 manifests (D and M) and the exact per-condition checkpoint texts, before any session.

    python research_tools/onboarding/pilot3_manifest.py --sdists DIR --out docs/experimental_mvp/onboarding/pilot_3

onb-pilot/3 repeats onb-pilot/2's same-app design on the same two development applications. pgtq 0.5.0 and
postgres-tq 1.2.1 are not new: onb-pilot/2 exposed them. It corrects onb-pilot/2's main weakness, which was that
Muse was barely used. In M, Muse assistance is now a **mandatory call at a predetermined workflow point**
(CHECKPOINT A, right after the public documentation and before any source reading or code). Its response is shown
to the operator, who records whether it was accepted, modified or rejected. D has the same checkpoint without the
model, where it writes down its plan. Nothing forces adoption of a response.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "onboarding"))
import pilot2_manifest as p2  # noqa: E402

CHECKPOINT = {
    "M": ("   a. Ask Muse once: `.venv/bin/python research_tools/onboarding/pilot.py muse --session \"{SESSION}\" "
          "--manifest \"{MANIFEST}\" --sdists \"{SDISTS}\" --env-file \"{ENV}\" --label checkpoint_a`. Muse receives "
          "the library's PKG-INFO and the harness contract and returns a JSON proposal (a draft spec and workload, and "
          "notes). It is never executed.\n"
          "   b. Read the response and decide on its merits: **accepted** (use it as given), **modified** (use it with "
          "changes) or **rejected** (don't use it). Don't adopt a response you judge wrong.\n"
          "   c. Record the decision: `.venv/bin/python research_tools/onboarding/pilot.py note "
          "--session \"{SESSION}\" intervention --detail "
          "\"CHECKPOINT_A decision=<accepted|modified|rejected>: <one-line reason>\"`.\n"
          "   d. If you accept or modify a Muse-derived spec or workload, validate it with `--source muse`."),
    "D": ("   a. Write down your plan, based on the documentation you have read: `.venv/bin/python "
          "research_tools/onboarding/pilot.py note --session \"{SESSION}\" intervention --detail \"CHECKPOINT_A plan: "
          "<one-line plan>\"`. Condition D has no model assistance."),
}
MUSE_LATER = {
    "M": ("After CHECKPOINT A you may ask Muse again, at most 2 more times (for example after a failed attempt), with "
          "`--label muse_2` or `--label muse_3`. Record each decision the same way (`MUSE_N decision=...`)."),
    "D": "Condition D: no Muse and no model assistance of any kind.",
}
MUSE_BUDGET = {"M": "Muse: at most 3 calls and $0.12 estimated", "D": "no Muse (condition D)"}
AI_RULE = {"M": "Muse via pilot.py muse", "D": "none (you yourself are the operator)"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest(condition: str, sdists: Path) -> dict:
    m = p2.manifest(condition, sdists)
    m.update({
        "version": "onb-pilot-manifest/3",
        "label": "AI-OPERATOR, NONBLIND EXPLORATORY: fresh AI agent contexts with no task-specific memory; not "
                 "independent human participants; the two applications were already exposed by onb-pilot/2",
        "question": "On the same application, under identical pins, success criteria, resources and budgets, does a "
                    "mandatory Muse consultation at a fixed workflow point (M) change the automated work to reach S3 "
                    "compared with the same checkpoint without the model (D)?",
        "assistance_definition": "M: one mandatory `pilot.py muse` call at CHECKPOINT A (after pkginfo, before any "
                                 "source reading or code); the response is shown to the operator, who records "
                                 "accepted/modified/rejected; up to 2 further optional calls. D: the same checkpoint "
                                 "with a written plan and no model.",
        "checkpoint_text": CHECKPOINT[condition], "muse_later_text": MUSE_LATER[condition],
        "design": {"pairing": "each app under both D and M, separate fresh agent contexts, separate fresh worktrees "
                              "from a base commit that holds no earlier adapter for these apps",
                   "replicates": "2 per (app, condition); each replicate's four sessions start together",
                   "contamination_controls": "worktrees from the pre-onb-pilot/2 base f82e90d plus this manifest; "
                                             "an instruction not to read outside the worktree except the pinned "
                                             "sdists; identical frozen instructions apart from the checkpoint",
                   "contamination_limits": "the same model family as earlier operators; filesystem isolation by "
                                           "instruction only; the main repository (outside the worktree) holds "
                                           "onb-pilot/2's adapters for these apps"},
        "measures": {**m["measures"], "checkpoint": "CHECKPOINT_A notes (decision and reason); Muse calls, tokens, "
                                                     "USD; Muse-sourced proposals and their validation"},
        "analysis": {**m["analysis"], "code": "research_tools/onboarding/pilot2_analyze.py (unchanged) plus "
                                              "pilot3_checkpoints.py (checkpoint decisions)"},
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sprint_cost": "each M session's maximum ($0.12) is reserved in the ds-nbc/2 ledger (cap $1) before it starts",
    })
    m["budgets"]["muse"]["max_estimated_usd"] = 0.12
    m["code_sha256"] = {**m["code_sha256"],
                        "docs/experimental_mvp/onboarding/pilot_3/OPERATOR_INSTRUCTIONS.md":
                            sha(REPO / "docs/experimental_mvp/onboarding/pilot_3/OPERATOR_INSTRUCTIONS.md"),
                        "research_tools/onboarding/pilot3_manifest.py": sha(Path(__file__))}
    m.pop("selection", None)
    m["applications"] = "pgtq 0.5.0 and postgres-tq 1.2.1, development applications exposed by onb-pilot/2 (not new)"
    return m


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--sdists", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    for c in ("D", "M"):
        p = o.out / f"PILOT_MANIFEST_v3_{c}.json"
        if p.exists():
            raise SystemExit(f"refused: {p} is already frozen")
        p.write_text(json.dumps(manifest(c, o.sdists), indent=1, sort_keys=True) + "\n")
        print(p, sha(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
