"""Builds AMENDMENT_003_ADOPTION_CANDIDATE_r2.md (PROPOSED) from revision candidate r1 (sha256 20403460…), by exact,
asserted text replacements, so every difference from r1 is reviewable here. It never writes r1 or the original
candidate (sha256 ad5ea331…), and refuses unless both are byte-identical to their recorded hashes.

    python docs/experimental_mvp/protocol/r2/build_candidate_r2.py [--check]

``--check`` rebuilds in memory and compares with the committed r2 file without writing anything.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE.parent
ORIGINAL = PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE.md"
SRC = PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE_r1.md"
OUT = PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE_r2.md"
ORIGINAL_SHA = "ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0"
SRC_SHA = "204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727"
M = "[RULINGS {}, PROPOSED in r2: see §19]"

EDITS: list[tuple[str, str, str]] = [  # (decision, old text in r1, new text)
    ("header",
     "# DRAFT amendment 003, REVISION CANDIDATE r1 (PROPOSED): prospective evaluation on real third-party "
     "applications\n\n"
     "**Status: PROPOSED REVISION CANDIDATE r1 (2026-11-10). Not in effect, not adopted, not registered, not "
     "signed.**",
     "# DRAFT amendment 003, REVISION CANDIDATE r2 (PROPOSED): prospective evaluation on real third-party "
     "applications\n\n"
     "**Status: PROPOSED REVISION CANDIDATE r2 (2026-11-15). Not in effect, not adopted, not registered, not "
     "signed.** It is derived from revision candidate r1 (sha256 "
     "`204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727`) by the asserted edits in "
     "`r2/build_candidate_r2.py`, and states as proposed text the study design recommended for #23–#30 plus #24a "
     "and the time rule replacing #15's T. Those recommendations are **not** a recorded owner approval. r1 and the "
     "original candidate are unchanged and remain separate candidates. Its approval forms are in `approval_r2/`; "
     "the forms in `approval/` and `approval_r1/` don't apply to it. The owner chooses at most one candidate to take "
     "forward.\n\n"
     "**r1's status paragraph, kept as a record:** PROPOSED REVISION CANDIDATE r1 (2026-11-10)."),
    ("#24 #24a #15",
     "- **Execution [RULINGS 24, PROPOSED: see §19]:** every arm gets K workload slots × S schedules per revision "
     "(equal schedules, not equal workloads), with K and S fixed on development cases and hashed into the adoption "
     "commit.",
     "- **Execution [RULINGS 24, PROPOSED: see §19]:** every arm gets K workload slots × S schedules per revision "
     "(equal schedules, not equal workloads), with **K = 6 and S = 2** " + M.format("24") + ", fixed before any "
     "confirmatory run from development feasibility only (`r2/TIME_BUDGET_r2.md`) and hashed into the adoption "
     "commit."),
    ("#24a #15",
     "fewer, and the number of distinct workloads executed is reported. This replaces the three-replicate shape for "
     "RA arms.\n",
     "fewer, and the number of distinct workloads executed is reported. This replaces the three-replicate shape for "
     "RA arms.\n"
     "- **Proposals and slots " + M.format("24a") + ":** each Muse arm makes exactly R requests per case, all "
     "scheduled before any run of that case, in a fixed order that doesn't depend on any reply's content or on any "
     "run. No additional, replacement or retry request is ever made, and no slot is ever filled from another arm, "
     "another request, a later generation or a different generator. Admission takes valid proposals in model order "
     "up to K. Every invalid proposal stays in the denominator with its reason. With between 1 and K − 1 valid "
     "workloads, the slots are filled by cycling, and the slots filled by repetition are counted and reported. With "
     "no valid workload, every slot is a REFUSED run (\"no valid workload\") that consumes all its schedules and is a "
     "censored nondetection.\n"
     "- **Time " + M.format("15, 24") + ":** no time limit truncates an arm's K × S schedules.\n"
     "  - **Per run:** every run of every arm has the same frozen limits: the coordinator's stop at 45 s, after "
     "which an uncovered run is INCOMPLETE; the host's container limit of 240 s with a kill; and an executor limit "
     "L = 300 s of wall-clock per run, under which a run whose recorded wall-clock exceeds L is INCOMPLETE whatever "
     "its verdict. A run that reaches any limit consumes its schedule and is a nondetection.\n"
     "  - **Per case and arm:** a safety ceiling of 7,500 s of wall-clock time, above the bound the per-run limit "
     "implies (2 × K × S = 24 runs × L = 7,200 s, plus at most 1 s between runs). It can be reached only if runs "
     "overrun L, which is itself an instrument failure. Reaching it is an infrastructure failure: the case is "
     "excluded for **every** arm, with the reason reported. It is never censoring of one arm, and nothing is "
     "rerun.\n"
     "  - **Excluded from these clocks:** image builds and pinned-tree preparation, done once per revision before "
     "any arm runs and shared by every arm. Lock waits are inside the safety clock but excluded from execution "
     "seconds.\n"
     "  - **Planning figure, not a cap:** at most 1,200 s per case and arm is expected from development evidence "
     "(observed worst case 1,175 s if every run stopped at 48.1 s).\n"),
    ("#24a accounting",
     "  - A failed or timed-out request counts against R, with its unknown usage recorded.\n"
     "  - Every reply is saved when it arrives.\n"
     "  - No retry is allowed except a logged transport failure.\n",
     "  - A failed or timed-out request counts against R, with its unknown usage recorded.\n"
     "  - Every reply is saved when it arrives.\n"
     "  - No retry is allowed " + M.format("24a") + ": a logged transport failure counts against R, keeps its "
     "worst-case charge, and is not retried.\n"),
    ("#28 eligibility",
     "- **Negative control:** a violation of any clause on the corrected version voids that case for that arm (the "
     "primary rule).",
     "- **Eligibility rules " + M.format("28") + ":** an integration may reject a workload shape (for example one "
     "known to stop INCOMPLETE on both revisions) only under an eligibility rule that is written by the Integrator "
     "without outcome knowledge, fixed and hashed into the adoption commit, and applied identically by every arm's "
     "validator, including RA-DET-G's generator and the curated RA-DET workloads. A rejected proposal counts as "
     "invalid in the denominator, and a rejected generator draw is counted. No rule is added, removed or changed "
     "after any confirmatory run. Outside such a rule, INCOMPLETE and REFUSED runs are nondetections.\n"
     "- **Negative control:** a violation of any clause on the corrected version voids that case for that arm (the "
     "primary rule)."),
    ("#28 censoring",
     "  - Report Kaplan–Meier curves per arm, and the paired median difference with censored values at B.\n",
     "  - Report Kaplan–Meier curves per arm, and the paired median difference with censored values at K × S.\n"),
    ("#30 §9",
     "The application count and participant minimum follow the owner's choice in ruling #30.\n",
     "The application count and participant minimum follow the owner's choice in ruling #30.\n\n"
     "**RA-Q4 design " + M.format("30") + ":** option A. Three applications never exposed in development, selected "
     "by a written rule fixed before recruitment by the study's data owner or an independent coordinator; each "
     "participant onboards two different applications, one under each condition, in one of six sequences (3 "
     "application pairs × 2 condition orders); at least 18 participants (3 per sequence), 24 preferred (adding the "
     "six mirror sequences, which balance application order); a common unscored calibration task first; "
     "end-to-end time primary and execution-only time (excluding logged Muse call durations) secondary; two blind "
     "scorers and an adjudicator; an exact two-sided sign test on application-standardized paired differences. "
     "Protocol and sample-size justification: `onboarding/participant_study/PROTOCOL_r2_3APP.md` and "
     "`SAMPLE_SIZE_r2.md`. Below the minimum, the verdict is INSUFFICIENT SAMPLE. Consent, ethics review, data "
     "ownership and independent scoring are preconditions of recruitment.\n"),
    ("#27 §12",
     "A model or prompt change after adoption is a new numbered amendment and starts a new evaluation. Every request "
     "logs the model the provider reports. A reported model that differs from the frozen one makes that request a "
     "provider failure.\n",
     "A model or prompt change after adoption is a new numbered amendment and starts a new evaluation. Every request "
     "logs the model the provider reports. A reported model that differs from the frozen one makes that request a "
     "provider failure.\n\n"
     "**Model identity " + M.format("27") + ":** the adoption commit records the exact provider-reported model "
     "string that is accepted (by default the frozen model identifier). Before every request, the client verifies "
     "the requested model, effort, `store`, `max_output_tokens`, and the proposer, instructions, context and schema "
     "hashes against the freeze, and refuses without sending on any mismatch. After it, a provider-reported model "
     "other than the accepted string makes the request a provider failure: the reply is saved, never used, and the "
     "request counts against R with its cost; no replacement request is made (#24a). Client: "
     "`research_tools/confirmatory_prep/verified_request.py`.\n"),
    ("#29 §15",
     "- **Integration [RULINGS 29, PROPOSED: see §19]:** a named Integrator without fix exposure (the curator may "
     "serve, with an attestation) writes",
     "- **Integration " + M.format("29") + ":** a named Integrator without fix exposure, who is a different person "
     "from the Label Curator, the Oracle Author, the Oracle Reviewer, the Triage Reviewers, the Adjudicator and the "
     "tool developers, writes"),
    ("#29 §15 exposure",
     "The Integrator's exposure is ledgered, and every frozen input is leak-checked with `seal_labels.py`.\n",
     "The Integrator's exposure is ledgered (an independent input-exposure record per case, committed before the "
     "freeze), and every frozen input is leak-checked with `seal_labels.py`.\n"),
    ("§19 #15",
     "- **#15 (amended by #24 in r1).** The execution budget is K × S schedules per revision per arm (#24; "
     "recommended K = 6, S = 2, i.e. 12, the former B), with T = 900 s per case per arm; R = 2 requests, P = 6 "
     "proposals, C = $1 per case per arm (`PROSPECTIVE_MUSE_PLAN.md` §5). The three-replicate shape no longer "
     "applies to RA arms.",
     "- **#15 (amended by #24 in r1, and in r2).** The execution budget is K × S schedules per revision per arm "
     "(#24: K = 6, S = 2, i.e. 12, the former B); R = 2 requests, P = 6 proposals, C = $1 per case per arm "
     "(`PROSPECTIVE_MUSE_PLAN.md` §5). The three-replicate shape no longer applies to RA arms. **r2 replaces r1's "
     "T = 900 s per case per arm**, which could truncate an all-INCOMPLETE arm (worst case 1,154 s of run time), "
     "with §4's time rule: uniform per-run limits (45 s coordinator stop, 240 s container limit, L = 300 s executor "
     "limit), no truncation of any arm's schedules, and a 7,500 s per case and arm safety ceiling whose breach "
     "excludes the case for every arm as an infrastructure failure."),
    ("§19 #24",
     "K and S are calibrated on development cases (recommended K = 6, S = 2).\n",
     "K = 6 and S = 2, fixed in r2 from development feasibility only (`r2/TIME_BUDGET_r2.md`), not from any "
     "outcome.\n"
     "- **#24a.** Exactly R requests per case per Muse arm, scheduled before any run; no additional, replacement or "
     "retry request after any outcome; invalid proposals stay in the denominator; slots filled by repetition are "
     "counted and reported; with no valid workload every slot is a REFUSED, schedule-consuming, censored "
     "nondetection; no slot is filled from another source.\n"),
    ("§19 #27",
     "every request verifies them; the provider-reported model is recorded (client change required before "
     "adoption).\n",
     "every request verifies them before it is sent; the provider-reported model must equal the exact accepted "
     "string recorded at adoption, or the request is a provider failure (saved, never used, counted against R, not "
     "replaced). Client implemented, development-only: `confirmatory_prep/verified_request.py`.\n"),
    ("§19 #28",
     "INCOMPLETE and REFUSED runs consume schedules, count as nondetections and are never retried.\n",
     "INCOMPLETE and REFUSED runs consume schedules, count as nondetections and are never retried, unless a workload "
     "shape is excluded by a prospective eligibility rule fixed before the freeze and applied identically to every "
     "arm (§6); excluded proposals count as invalid.\n"),
    ("§19 #29",
     "- **#29.** A named fix-blind Integrator (the curator may serve) writes or approves",
     "- **#29.** A named fix-blind Integrator, a different person from the curator, the oracle author and reviewer, "
     "the triage reviewers, the adjudicator and the developers, writes or approves"),
    ("§19 #30",
     "the owner chooses (A) 3 applications and at least 18 participants, (B) 2 applications and at least 12 with a "
     "2-application claim rule, or (C) descriptive only.\n",
     "r2 proposes option (A): 3 applications, six crossover sequences, at least 18 participants, 24 preferred "
     "(`PROTOCOL_r2_3APP.md`, `SAMPLE_SIZE_r2.md`). Options (B) and (C) of r1 are not part of r2.\n"),
]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build() -> str:
    if sha(ORIGINAL.read_bytes()) != ORIGINAL_SHA:
        raise SystemExit("refused: the original candidate isn't sha256 ad5ea331…")
    raw = SRC.read_bytes()
    if sha(raw) != SRC_SHA:
        raise SystemExit("refused: the source candidate isn't r1 (sha256 20403460…)")
    text = raw.decode()
    for decision, old, new in EDITS:
        if text.count(old) != 1:
            raise SystemExit(f"refused: the text for {decision} doesn't occur exactly once in r1")
        text = text.replace(old, new)
    return text


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        ok = OUT.exists() and OUT.read_text() == text
        print(OUT.name, "rebuild byte-identical" if ok else "DIFFERS from the rebuild")
        return 0 if ok else 1
    OUT.write_text(text)
    print(OUT.name, sha(OUT.read_bytes()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
