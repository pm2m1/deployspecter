"""Builds AMENDMENT_003_ADOPTION_CANDIDATE_r1.md (PROPOSED) from the unchanged candidate (sha256 ad5ea331…), by
exact, asserted text replacements, so every difference is reviewable here. It never writes the original.

    python docs/experimental_mvp/protocol/r1/build_candidate_r1.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE.parent
SRC = PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE.md"
OUT = PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE_r1.md"
SRC_SHA = "ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0"

EDITS: list[tuple[str, str, str]] = [  # (decision, old, new)
    ("header",
     "# DRAFT amendment 003, ADOPTION CANDIDATE: prospective evaluation on real third-party applications\n",
     "# DRAFT amendment 003, REVISION CANDIDATE r1 (PROPOSED): prospective evaluation on real third-party "
     "applications\n\n"
     "**Status: PROPOSED REVISION CANDIDATE r1 (2026-11-10). Not in effect, not adopted, not registered, not "
     "signed.** It is derived from the adoption candidate sha256 "
     "`ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`, which is unchanged and remains a "
     "separate candidate. It adds proposed decisions #23–#30 (§19), drawn from the NONBLIND development experiments "
     "(`nonblind_corpus/track_b/AUDIT_1.md`, `nonblind_corpus/nbc2/AUDIT_2.md`, `onboarding/pilot_3/RESULTS.md`). "
     "Its approval forms are in `approval_r1/`; the original candidate's forms in `approval/` don't apply to it. The "
     "owner chooses at most one candidate to take forward.\n"),
    ("#25",
     "| RA-DET-C | RA-DET's drivers | **Constructor-isolation control:** RA-DET's goals with the constructor replaced "
     "by literal translation, with no search. An infeasible goal is counted, not repaired | none |\n",
     "| RA-DET-C | RA-DET's drivers | **Constructor-isolation control:** RA-DET's goals with the constructor replaced "
     "by literal translation, with no search. An infeasible goal is counted, not repaired | none |\n"
     "| RA-DET-G [RULINGS 25, PROPOSED: see §19] | **Outcome-blind generator:** K distinct workloads drawn from the "
     "case's frozen workload schema only, seeded from the case id; it sees no fix, oracle, result or Muse context "
     "prose | The deterministic constructor over RA-DET's goals | none |\n\n"
     "**Who writes RA-DET [RULINGS 25, 29, PROPOSED: see §19]:** RA-DET's curated workloads are written by the "
     "fix-blind Integrator (§15), never by anyone who has seen the fix. Fix-informed workloads are never a "
     "confirmatory arm.\n"),
    ("#26",
     "- **Corpus:** cases come only from `CORPUS_MANIFEST.json`, selected under the frozen rules in "
     "`CORPUS_SHORTLIST.json`.\n",
     "- **Corpus:** cases come only from `CORPUS_MANIFEST.json`, selected under the frozen rules in "
     "`CORPUS_SHORTLIST.json`.\n"
     "- **Independence [RULINGS 26, PROPOSED: see §19]:** one case per upstream defect; at most 3 cases per "
     "application; at least 5 distinct applications among the eligible cases. Applications exposed during "
     "development (chancy, pgqueuer, procrastinate, saq, taskiq-postgresql, pgtq, postgres-tq, InvenTree, "
     "paperless-ngx, langgraph-checkpoint-postgres) and anything GitLab-derived are never eligible.\n"),
    ("#24",
     "- **Execution:** B schedules per workload per build, and T seconds per case. These are fixed on the development "
     "cases and hashed into the adoption commit. The shape is three replicates, as frozen §8 prescribes.\n",
     "- **Execution [RULINGS 24, PROPOSED: see §19]:** every arm gets K workload slots × S schedules per revision "
     "(equal schedules, not equal workloads), with K and S fixed on development cases and hashed into the adoption "
     "commit. Slot k runs the arm's workload list entry k mod its length; seeds are seed(case, k, j), identical "
     "across arms; there is no early stopping; every admitted proposal is executed; nothing is substituted or "
     "reordered after outcomes. RA-DET-G always supplies K distinct workloads; other arms cycle when they have "
     "fewer, and the number of distinct workloads executed is reported. This replaces the three-replicate shape for "
     "RA arms.\n"),
    ("#23",
     "- **Detected:** an oracle violation on the defective version, in at least one replicate, that the server log "
     "corroborates, with no oracle violation on the corrected version under the same budget.\n"
     "- **Negative control:** a violation on the corrected version voids that case for that arm. Two reviewers "
     "triage it as a false flag, an oracle error or a new finding, under frozen §9.\n",
     "- **Target clauses [RULINGS 23, PROPOSED: see §19]:** each oracle lists its clauses, and the Oracle Author "
     "declares before sealing which clauses encode the target defect. The oracle reports the violated clause ids.\n"
     "- **Detected:** a SOUND, server-log-corroborated violation of a **target** clause on the defective version, "
     "with no oracle violation of any clause on the corrected version under the same budget.\n"
     "- **Negative control:** a violation of any clause on the corrected version voids that case for that arm (the "
     "primary rule). Two reviewers triage it as (a) a false flag, (b) an oracle error (the case drops for every "
     "arm), (c) a pre-existing violation of a non-target clause (reported; the arm's case stays void), or (d) a new "
     "finding, under frozen §9. A slot-level analysis that disregards slots whose corrected runs violate only "
     "non-target clauses is secondary.\n"),
    ("#25 #26 #28",
     "- **RA-Q3 detection:**\n"
     "  - Exact two-sided McNemar on discordant cases, for RA-MDS against RA-DET (primary) and RA-MD against RA-DET "
     "(secondary), with Holm correction.\n",
     "- **RA-Q3 detection [RULINGS 25, 26, PROPOSED: see §19]:**\n"
     "  - Exact two-sided McNemar on discordant cases, for the Muse workload arm against **RA-DET-G** (primary: "
     "RA-MDS where the case's integration has a state stage, otherwise RA-MD), and against RA-DET (secondary), "
     "with Holm correction.\n"
     "  - A leave-one-application-out sensitivity analysis is reported for every comparison.\n"),
    ("#28",
     "- **Time to detection:** schedules to first detection, right-censored at B.\n",
     "- **Time to detection [RULINGS 28, PROPOSED: see §19]:** defective-revision schedules to the first target-"
     "clause detection, in slot order, right-censored at K × S (primary). Execution seconds (run time only, "
     "excluding image builds and lock waits) are secondary; end-to-end seconds including each arm's generation time "
     "(model latency, generator time) are tertiary. INCOMPLETE and REFUSED runs consume their schedules, count as "
     "nondetections, are never retried, and are reported per arm and per workload.\n"),
    ("#30",
     "## 9. Manual work and cost\n",
     "## 9. Manual work and cost\n\n"
     "**RA-Q4 protocol [RULINGS 30, PROPOSED: see §19]:** the independent-participant study in "
     "`onboarding/participant_study/` (onb-study/1), with Muse assistance defined as a mandatory consultation at a "
     "fixed workflow point (after the public documentation, before any source reading or code), its response "
     "recorded as accepted, modified or rejected and never forced; the control condition has the same checkpoint "
     "without the model. Time is reported with and without the checkpoint's model latency. Applications are never "
     "ones exposed in development. The application count and participant minimum follow the owner's choice in "
     "ruling #30.\n"),
    ("#27",
     "- every JSON schema the model is constrained to.\n",
     "- every JSON schema the model is constrained to;\n"
     "- [RULINGS 27, PROPOSED: see §19] the proposer module and its instructions, each case's Muse context and "
     "workload schema (sha256 of canonical JSON), and `max_output_tokens`. Each request verifies these hashes before "
     "it is sent and is refused on any mismatch. Provider sampling is unseeded; every raw reply is saved on "
     "arrival.\n"),
    ("#25 primary",
     "- **Primary:** RA-Q3. A paired case-level detection difference, RA-MDS against RA-DET, with an exact McNemar "
     "test (§8).",
     "- **Primary [RULINGS 25, PROPOSED: see §19]:** RA-Q3. A paired case-level detection difference, the Muse "
     "workload arm against RA-DET-G, with an exact McNemar test (§8)."),
    ("#29 #23",
     "- **Triage [RULINGS 8, PROPOSED: see §19]:**",
     "- **Integration [RULINGS 29, PROPOSED: see §19]:** a named Integrator without fix exposure (the curator may "
     "serve, with an attestation) writes or approves each case's adapter use, workload schema, Muse context and "
     "curated RA-DET workloads, from the public API, documentation and the defective revision's pinned tree only. "
     "The Integrator's exposure is ledgered, and every frozen input is leak-checked with `seal_labels.py`.\n"
     "- **Target clauses [RULINGS 23, PROPOSED: see §19]:** the Oracle Author declares each oracle's target clauses "
     "before sealing; the Oracle Reviewer confirms the declaration.\n"
     "- **Triage [RULINGS 8, PROPOSED: see §19]:**"),
    ("#15 amended by #24",
     "- **#15.** B = 12 schedules per workload per build (forced plus seeds 0–10), T = 900 s per case per arm, 3 "
     "replicates; R = 2 requests, P = 6 proposals, C = $1 per case per arm (`PROSPECTIVE_MUSE_PLAN.md` §5).",
     "- **#15 (amended by #24 in r1).** The execution budget is K × S schedules per revision per arm (#24; "
     "recommended K = 6, S = 2, i.e. 12, the former B), with T = 900 s per case per arm; R = 2 requests, P = 6 "
     "proposals, C = $1 per case per arm (`PROSPECTIVE_MUSE_PLAN.md` §5). The three-replicate shape no longer "
     "applies to RA arms."),
    ("§19 #23-#30",
     "- **#22.** **Server-log corroboration of typed parameters.**",
     "- **#23.** Oracles declare target clauses before sealing. Detection requires a target-clause violation on the "
     "defective version. Any corrected-version violation voids the case for that arm (primary); triage adds category "
     "(c), pre-existing violation of a non-target clause. Slot-level non-target filtering is secondary only.\n"
     "- **#24.** Equal budgets of K slots × S schedules per revision for every arm; slot k runs entry k mod length; "
     "identical seeds; no early stopping; every admitted proposal executed; RA-DET-G supplies K distinct workloads, "
     "other arms cycle. K and S are calibrated on development cases (recommended K = 6, S = 2).\n"
     "- **#25.** New arm RA-DET-G (outcome-blind schema generator) is the primary comparator for the Muse workload "
     "arm; RA-DET (curated by the fix-blind Integrator) is secondary; fix-informed workloads are never confirmatory.\n"
     "- **#26.** One case per upstream defect; at most 3 cases per application; at least 5 applications; a "
     "leave-one-application-out sensitivity analysis; development-exposed applications are never eligible.\n"
     "- **#27.** §12's frozen set adds the proposer, each case's Muse context and workload schema, and "
     "`max_output_tokens`; every request verifies them; the provider-reported model is recorded (client change "
     "required before adoption).\n"
     "- **#28.** Primary time is defective-revision schedules to first target-clause detection, censored at K × S; "
     "execution seconds secondary; end-to-end seconds tertiary. INCOMPLETE and REFUSED runs consume schedules, count "
     "as nondetections and are never retried.\n"
     "- **#29.** A named fix-blind Integrator (the curator may serve) writes or approves each case's workload schema, "
     "Muse context and curated workloads; exposure ledgered; inputs leak-checked.\n"
     "- **#30.** RA-Q4 uses onb-study/1 with a mandatory Muse checkpoint (recorded accepted/modified/rejected, never "
     "forced) and the same checkpoint without the model as control; the owner chooses (A) 3 applications and at "
     "least 18 participants, (B) 2 applications and at least 12 with a 2-application claim rule, or (C) descriptive "
     "only.\n"
     "- **#22.** **Server-log corroboration of typed parameters.**"),
]


def build() -> str:
    text = SRC.read_text()
    if hashlib.sha256(text.encode()).hexdigest() != SRC_SHA:
        raise SystemExit("refused: the source candidate isn't sha256 ad5ea331…")
    for decision, old, new in EDITS:
        if text.count(old) != 1:
            raise SystemExit(f"refused: the text for {decision} doesn't occur exactly once in the source")
        text = text.replace(old, new)
    return text


def main() -> int:
    OUT.write_text(build())
    print(OUT.name, hashlib.sha256(OUT.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
