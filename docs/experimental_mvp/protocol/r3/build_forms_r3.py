"""Builds the UNSIGNED draft approval forms for revision candidate r3 in `approval_r3/`, from the unchanged forms in
`approval/`, and checks them. The forms in `approval/`, `approval_r1/` and `approval_r2/` are never written.

    python docs/experimental_mvp/protocol/r3/build_forms_r3.py [--check]
"""

# ruff: noqa: E501  (the markdown table rows of the checklist are kept on one line each)

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE.parent
SRC, OUT = PROTOCOL / "approval", PROTOCOL / "approval_r3"
OLD_SHA = "ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0"
R1_SHA = "204034600f9449ad68c5d17e252ca56fbc2101d09107c8c61be0445041bce727"
R2_SHA = "476d5c57013b12abfbe78c70db67a88603e095382dd409068561418869374e46"
CAND = "AMENDMENT_003_ADOPTION_CANDIDATE_r3.md"
ROW = "| {d} | [ ] approve  [ ] amend (attach text)  [ ] reject |{note}\n"
BANNER = ("> **DRAFT FORM FOR PROPOSED REVISION CANDIDATE r3 (review candidate). UNSIGNED.** It applies only to `{cand}` (sha256 below), "
          "never to the original candidate `ad5ea331…`, r1 `20403460…` or r2 `476d5c57…`. Nothing on it has been ticked "
          "or signed, and no reviewer has been named.\n\n")
NEW_ROWS = {  # form -> (insert after this existing row, the new decision ids)
    "OWNER_APPROVAL.md": ("| #22 |", ["#23", "#24", "#24a", "#25", "#26", "#27", "#28", "#29", "#30"]),
    "CURATOR_ACCEPTANCE.md": ("| #19 |", ["#26", "#29"]),
    "ORACLE_REVIEWER_ACCEPTANCE.md": ("| #11 |", ["#23"]),
    "TRIAGE_REVIEWERS_ACCEPTANCE.md": ("| #8 |", ["#23"]),
}
NOTES = {"#15": " (amended in r2: K = 6, S = 2; T = 900 s replaced by the §4 time rule)",
         "#23": " (PROPOSED in r3; co-decided with the oracle author, oracle reviewer and triage reviewers)",
         "#24": " (PROPOSED in r3: K = 6, S = 2 fixed)",
         "#24a": " (PROPOSED; clarified in r3: request-index order, canonical deduplication, first K distinct valid admitted, overflow and duplicates counted, never executed)",
         "#26": " (PROPOSED in r3; co-decided with the curator)",
         "#27": " (PROPOSED in r3: the provider-reported model must equal the accepted string recorded at adoption)",
         "#28": " (PROPOSED in r3: exclusions only under prospective, uniformly applied eligibility rules)",
         "#29": " (PROPOSED in r3: a separate Integrator; co-decided with the curator and the Integrator)",
         "#30": " (PROPOSED in r3: option A, 3 applications, at least 18 participants, 24 preferred)"}
INTEGRATOR = """# Integrator: acceptance of revision candidate r3's integration rules

**Candidate:** `docs/experimental_mvp/protocol/{cand}`
**sha256:** `{sha}`

**Check the hash before signing:** `shasum -a 256 docs/experimental_mvp/protocol/{cand}`. If it differs, don't sign.

I confirm that:
- I am **not** the Label Curator, the Oracle Author, the Oracle Reviewer, a Triage Reviewer, the Adjudicator, or a
  DeploySpecter or Muse developer, for any case I integrate (#29);
- I have **not** seen any confirmatory case's fix, fix commit, fix tests, changelog entry for the fix, labels or
  outcomes;
- I write or approve each case's adapter use, workload schema, Muse context, curated RA-DET workloads and any
  eligibility rule (#28) from the public API, the documentation and the defective revision's pinned tree only, before
  the freeze, and apply each eligibility rule identically to every arm;
- I record what I read in each case's exposure record before the freeze, and every input I produce is leak-checked
  with `seal_labels.py` before it is frozen.

| Decision | Your verdict |
|---|---|
""" + "".join(ROW.format(d=d, note=NOTES.get(d, " (PROPOSED in r3)")) for d in ("#24a", "#25", "#27", "#28", "#29")) + """
| Role | Name | Signature | Date |
|---|---|---|---|
| Integrator (a separate person; never the curator) | | | |
"""
CHECKLIST_ADD = """
**Added for r2 and kept in r3 (nothing here has been done):**

| Step | Who | Evidence it was done | Status |
|---|---|---|---|
| r2-a. Choose a separate Integrator (#29), different from the curator, the oracle author and reviewer, the triage reviewers, the adjudicator and the developers. Send `handoff/send_r3/REVIEWER_PACKAGE_r3.md` | owner | the sent package, with its date | **not done** |
| r2-b. The Integrator signs `INTEGRATOR_ACCEPTANCE.md` before any case is frozen | Integrator | the signed form | **not done** |
| r2-c. Hash into the adoption commit: K = 6, S = 2 (#24); the time rule's L = 300 s and 7,500 s ceiling (§4); R, P, C (#15) | owner | the adoption commit | **not done** |
| r2-d. Record the exact accepted provider-reported model string (#27) | owner, Integrator | the adoption commit | **not done** |
| r2-e. Freeze every eligibility rule (#28) with its hash, or record that there are none | Integrator | the rule hashes in the freeze | **not done** |
| r2-f. Commit each case's exposure records (#29) and the admission journal before its first run | Integrator, curator | the committed records | **not done** |
| r2-g. For #30 option A: the ethics or owner review, a named data owner, the application selection rule, and the study manifest (design `min18` or `pref24`, seed, code hashes) frozen before recruitment | owner, data owner, coordinator | the frozen study manifest | **not done** |
| r3-h. For each case and Muse arm, after its R requests and before its first run, commit the admission record (`prep_r3.admit_replies`: request statuses; generated, valid, invalid, distinct valid, duplicate, admitted and overflow counts; the per-proposal log) with the admission journal's hash (#24a) | Integrator, evaluator | the committed admission records | **not done** |
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _note(d: str) -> str:
    return NOTES.get(d, " (PROPOSED in r3)")


def build() -> None:
    new_sha = sha(PROTOCOL / CAND)
    OUT.mkdir(exist_ok=True)
    for f, (anchor, ids) in NEW_ROWS.items():
        lines = (SRC / f).read_text().splitlines(keepends=True)
        if f == "OWNER_APPROVAL.md":
            lines = [line.rstrip("\n") + _note("#15") + "\n" if line.startswith("| #15 |") else line for line in lines]
        i = next(n for n, line in enumerate(lines) if line.startswith(anchor))
        lines[i + 1:i + 1] = [ROW.format(d=d, note=_note(d)) for d in ids]
        text = "".join(lines).replace(OLD_SHA, new_sha).replace("AMENDMENT_003_ADOPTION_CANDIDATE.md", CAND)
        title_end = text.index("\n") + 1
        (OUT / f).write_text(text[:title_end] + "\n" + BANNER.format(cand=CAND) + text[title_end:])
    (OUT / "INTEGRATOR_ACCEPTANCE.md").write_text(BANNER.format(cand=CAND) + INTEGRATOR.format(cand=CAND,
                                                                                                 sha=new_sha))
    chk = (SRC / "ADOPTION_CHECKLIST.md").read_text().replace(OLD_SHA, new_sha).replace(
        "AMENDMENT_003_ADOPTION_CANDIDATE.md", CAND)
    (OUT / "ADOPTION_CHECKLIST.md").write_text(BANNER.format(cand=CAND) + chk + CHECKLIST_ADD)
    (OUT / "CANDIDATE_SHA256").write_text(f"{new_sha}  {CAND}\n")


def check() -> list[str]:
    new_sha = sha(PROTOCOL / CAND)
    p = []
    if (OUT / "CANDIDATE_SHA256").read_text().split()[0] != new_sha:
        p.append("CANDIDATE_SHA256 isn't the r3 candidate's hash")
    for f in sorted(OUT.glob("*.md")):
        t = f.read_text()
        if set(re.findall(r"\b[0-9a-f]{64}\b", t)) - {new_sha}:
            p.append(f"{f.name}: names a hash other than r3's")
        if "[x]" in t.lower():
            p.append(f"{f.name}: something is ticked")
        if "may be the curator" in t:
            p.append(f"{f.name}: lets the curator serve as Integrator (#29)")
        in_sig = False
        for line in t.splitlines():  # only rows of tables whose header has a Signature column
            if line.startswith("|") and "Signature" in line:
                in_sig = True
                continue
            if not line.startswith("|"):
                in_sig = False
                continue
            if in_sig and "---" not in line and any(c.strip() for c in line.split("|")[2:-1]):
                p.append(f"{f.name}: a name, signature or date is filled in")
    if sha(PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE.md") != OLD_SHA:
        p.append("the original candidate changed")
    if sha(PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE_r1.md") != R1_SHA:
        p.append("r1 changed")
    if sha(PROTOCOL / "AMENDMENT_003_ADOPTION_CANDIDATE_r2.md") != R2_SHA:
        p.append("r2 changed")
    return p


if __name__ == "__main__":
    if "--check" not in sys.argv:
        build()
    problems = check()
    print("approval_r3:", "ok (unsigned, r3 hash only)" if not problems else problems)
    sys.exit(1 if problems else 0)
