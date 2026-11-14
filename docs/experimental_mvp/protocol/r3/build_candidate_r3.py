"""Builds AMENDMENT_003_ADOPTION_CANDIDATE_r3.md (PROPOSED) from revision candidate r2 (sha256 476d5c57…), by exact,
asserted text replacements. r3 changes only the proposal-admission rule (#24a) and its reporting (§5); every other
byte is r2's. It never writes the original, r1 or r2, and refuses unless all three match their recorded hashes.

    python docs/experimental_mvp/protocol/r3/build_candidate_r3.py [--check]
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE.parent
FROZEN = {"AMENDMENT_003_ADOPTION_CANDIDATE.md": "ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0",
          "AMENDMENT_003_ADOPTION_CANDIDATE_r1.md": "204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727",
          "AMENDMENT_003_ADOPTION_CANDIDATE_r2.md": "476d5c57013b12abfbe78c70db67a88603e095382dd409068561418869374e46"}
SRC = PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE_r2.md"
OUT = PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE_r3.md"
M = "[RULINGS 24a, PROPOSED in r3: see §19]"

ADMISSION = (
    "- **Proposal admission " + M + ":** for each case and Muse arm, in this order:\n"
    "  1. **Requests:** exactly R requests are attempted, by request index 0 … R − 1, and every reply is recorded "
    "(or its failure logged) before any workload of the case runs. A request that fails, times out, is refused "
    "before sending by the freeze check (§12), or returns a provider-reported model other than the accepted one is a "
    "failed request: it counts against R and is never retried or replaced.\n"
    "  2. **Order:** replies are processed in request-index order, and the proposals within a reply in the order "
    "the reply lists them. Nothing is reordered after any outcome.\n"
    "  3. **Denominator:** every generated proposal counts in the validity denominator, including those in a reply "
    "from a failed request whose proposals are never used (counted as not valid, reason \"provider failure\").\n"
    "  4. **Valid:** a proposal is valid when it passes the reply shape, the frozen workload schema, the "
    "integration's validator and the frozen eligibility rules (#28), all applied identically to every arm.\n"
    "  5. **Distinct:** valid workloads are deduplicated by their canonical representation (the sha256 of their "
    "canonical JSON: sorted keys, no insignificant whitespace), across the requests of the case and arm. A valid "
    "proposal equal to an earlier valid one is a duplicate: it counts as valid, never as a distinct admission.\n"
    "  6. **Admission:** the first K = 6 distinct valid workloads in that order are admitted, and only they run. "
    "Distinct valid workloads after the K-th are overflow: counted, never executed.\n"
    "  7. **Slots:** slot k runs admitted entry k mod the number admitted (#24). With 1 to K − 1 admitted workloads, "
    "the remaining slots repeat admitted workloads with their own seeds, and are counted as filled by repetition. A "
    "slot is unfilled only when no workload is admitted: every slot is then a REFUSED run (\"no valid workload\") "
    "that consumes its preallocated K × S schedules as a censored nondetection.\n"
    "  8. **Never:** an extra request, a replacement proposal, early stopping, or outcome-dependent reordering, "
    "substitution or admission. The admitted list is journaled by hash before the first run.\n"
    "  9. **Reported per case and arm:** requests attempted, failed and by status; proposals generated, valid, "
    "invalid (by reason), distinct valid, duplicate, admitted, overflow and executed (distinct workloads run); and "
    "slots filled first time, by repetition and unfilled.\n")

EDITS: list[tuple[str, str, str]] = [  # (what, old text in r2, new text)
    ("header",
     "# DRAFT amendment 003, REVISION CANDIDATE r2 (PROPOSED): prospective evaluation on real third-party "
     "applications\n\n"
     "**Status: PROPOSED REVISION CANDIDATE r2 (2026-11-15). Not in effect, not adopted, not registered, not "
     "signed.**",
     "# DRAFT amendment 003, REVISION CANDIDATE r3 (PROPOSED): prospective evaluation on real third-party "
     "applications\n\n"
     "**Status: PROPOSED REVISION CANDIDATE r3 (2026-11-15), the review candidate. Not in effect, not adopted, not "
     "registered, not signed.** It is revision candidate r2 (sha256 "
     "`476d5c57013b12abfbe78c70db67a88603e095382dd409068561418869374e46`) with one clarification: the "
     "proposal-admission rule (#24a, §4) and its reporting (§5), made by the asserted edits in "
     "`r3/build_candidate_r3.py`. The rule states recommended decisions as proposed text; it is not a recorded owner "
     "approval. The original candidate, r1 and r2 are unchanged and remain separate candidates. Its approval forms "
     "are in `approval_r3/`; no other form directory applies to it. The owner chooses at most one candidate to take "
     "forward.\n\n"
     "**r2's status paragraph, kept as a record:** PROPOSED REVISION CANDIDATE r2 (2026-11-15)."),
    ("#24a §4 requests",
     "each Muse arm makes exactly R requests per case, all scheduled before any run of that case, in a fixed order "
     "that doesn't depend on any reply's content or on any run.",
     "each Muse arm makes exactly R requests per case, all made before any run of that case, in a fixed order that "
     "doesn't depend on any reply's content or on any run."),
    ("#24a §4 admission",
     "Admission takes valid proposals in model order up to K. Every invalid proposal stays in the denominator with "
     "its reason. With between 1 and K − 1 valid workloads, the slots are filled by cycling, and the slots filled by "
     "repetition are counted and reported. With no valid workload, every slot is a REFUSED run (\"no valid "
     "workload\") that consumes all its schedules and is a censored nondetection.\n",
     "Admission follows the rule below.\n" + ADMISSION),
    ("§5 reporting",
     "**Reporting:** rejection rates are reported per arm, and never only for accepted proposals.\n",
     "**Reporting:** rejection rates are reported per arm, and never only for accepted proposals. For Muse arms, the "
     "proposal counts of §4's admission rule (generated, valid, invalid, distinct valid, duplicate, admitted, "
     "overflow, executed) are reported separately " + M + "; the validity rate is valid ÷ generated, and failed "
     "requests are reported beside it.\n"),
    ("§19 #24a",
     "- **#24a.** Exactly R requests per case per Muse arm, scheduled before any run; no additional, replacement or "
     "retry request after any outcome; invalid proposals stay in the denominator; slots filled by repetition are "
     "counted and reported; with no valid workload every slot is a REFUSED, schedule-consuming, censored "
     "nondetection; no slot is filled from another source.",
     "- **#24a (clarified in r3).** Exactly R requests per case per Muse arm, all made before any run; failed "
     "requests count against R and are never retried or replaced; replies processed in request-index order and "
     "proposals in reply order; every generated proposal in the validity denominator; valid workloads deduplicated "
     "by canonical JSON across the case's requests; the first K = 6 distinct valid workloads admitted and executed; "
     "overflow and duplicates counted, never executed; 1 to K − 1 admitted workloads fill the slots by repetition "
     "(#24), and with none admitted every slot is a REFUSED, schedule-consuming, censored nondetection; no extra "
     "request, replacement, early stopping or outcome-dependent reordering; generated, valid, distinct, duplicate, "
     "admitted, overflow and executed counts reported separately (§4, §5)."),
]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build() -> str:
    for name, want in FROZEN.items():
        if sha((PROTOCOL / name).read_bytes()) != want:
            raise SystemExit(f"refused: {name} isn't sha256 {want[:8]}…")
    text = SRC.read_bytes().decode()
    for what, old, new in EDITS:
        if text.count(old) != 1:
            raise SystemExit(f"refused: the text for {what} doesn't occur exactly once in r2")
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
