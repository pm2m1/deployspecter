"""Builds the UNSIGNED draft approval forms for revision candidate r1 in `approval_r1/`, from the unchanged forms in
`approval/`, and checks them. The original forms are never written.

    python docs/experimental_mvp/protocol/r1/build_forms_r1.py [--check]
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE.parent
SRC, OUT = PROTOCOL / "approval", PROTOCOL / "approval_r1"
OLD_SHA = "ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0"
CAND = "AMENDMENT_003_ADOPTION_CANDIDATE_r1.md"
ROW = "| {d} | [ ] approve  [ ] amend (attach text)  [ ] reject |{note}\n"
BANNER = ("> **DRAFT FORM FOR PROPOSED REVISION CANDIDATE r1. UNSIGNED.** It applies only to `{cand}` (sha256 below), "
          "never to the original candidate `ad5ea331…`. Nothing on it has been ticked or signed, and no reviewer has "
          "been named.\n\n")
NEW_ROWS = {  # form -> (insert the new rows after this existing row, the new decision ids)
    "OWNER_APPROVAL.md": ("| #22 |", ["#23", "#24", "#25", "#26", "#27", "#28", "#29", "#30"]),
    "CURATOR_ACCEPTANCE.md": ("| #19 |", ["#26", "#29"]),
    "ORACLE_REVIEWER_ACCEPTANCE.md": ("| #11 |", ["#23"]),
    "TRIAGE_REVIEWERS_ACCEPTANCE.md": ("| #8 |", ["#23"]),
}
NOTES = {"#23": " (PROPOSED in r1; co-decided with the oracle author, oracle reviewer and triage reviewers)",
         "#26": " (PROPOSED in r1; co-decided with the curator)",
         "#29": " (PROPOSED in r1; co-decided with the curator and the integrator)",
         "#30": " (PROPOSED in r1; the owner chooses option A, B or C)"}
INTEGRATOR = """# Integrator: acceptance of revision candidate r1's integration rules

**Candidate:** `docs/experimental_mvp/protocol/{cand}`
**sha256:** `{sha}`

**Check the hash before signing:** `shasum -a 256 docs/experimental_mvp/protocol/{cand}`. If it differs, don't sign.

I confirm that I have **not** seen any confirmatory case's fix, fix commit, fix tests, changelog entry for the fix,
or labels. I write or approve each case's adapter use, workload schema, Muse context and curated RA-DET workloads
from the public API, the documentation and the defective revision's pinned tree only. I record what I read in each
case's exposure ledger, and every input I produce is leak-checked with `seal_labels.py` before it is frozen.

| Decision | Your verdict |
|---|---|
""" + "".join(ROW.format(d=d, note=NOTES.get(d, " (PROPOSED in r1)")) for d in ("#25", "#27", "#29")) + """
| Role | Name | Signature | Date |
|---|---|---|---|
| Integrator (may be the curator, with this attestation) | | | |
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build() -> None:
    new_sha = sha(PROTOCOL / CAND)
    OUT.mkdir(exist_ok=True)
    for f, (anchor, ids) in NEW_ROWS.items():
        lines = (SRC / f).read_text().splitlines(keepends=True)
        i = next(n for n, line in enumerate(lines) if line.startswith(anchor))
        lines[i + 1:i + 1] = [ROW.format(d=d, note=NOTES.get(d, " (PROPOSED in r1)")) for d in ids]
        text = "".join(lines).replace(OLD_SHA, new_sha).replace("AMENDMENT_003_ADOPTION_CANDIDATE.md", CAND)
        title_end = text.index("\n") + 1
        (OUT / f).write_text(text[:title_end] + "\n" + BANNER.format(cand=CAND) + text[title_end:])
    (OUT / "INTEGRATOR_ACCEPTANCE.md").write_text(BANNER.format(cand=CAND) + INTEGRATOR.format(cand=CAND,
                                                                                                 sha=new_sha))
    chk = (SRC / "ADOPTION_CHECKLIST.md").read_text().replace(OLD_SHA, new_sha).replace(
        "AMENDMENT_003_ADOPTION_CANDIDATE.md", CAND)
    (OUT / "ADOPTION_CHECKLIST.md").write_text(BANNER.format(cand=CAND) + chk + (
        "\n**Added for r1:** the Integrator signs `INTEGRATOR_ACCEPTANCE.md` before any case is frozen (#29); the "
        "owner records the option chosen under #30; K and S (#24) are calibrated on development cases and hashed "
        "into the adoption commit.\n"))
    (OUT / "CANDIDATE_SHA256").write_text(f"{new_sha}  {CAND}\n")


def check() -> list[str]:
    new_sha = sha(PROTOCOL / CAND)
    p = []
    if (OUT / "CANDIDATE_SHA256").read_text().split()[0] != new_sha:
        p.append("CANDIDATE_SHA256 isn't the r1 candidate's hash")
    for f in sorted(OUT.glob("*.md")):
        t = f.read_text()
        hashes = set(re.findall(r"\b[0-9a-f]{64}\b", t))
        if hashes - {new_sha}:
            p.append(f"{f.name}: names a hash other than r1's")
        if "[x]" in t.lower():
            p.append(f"{f.name}: something is ticked")
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
    return p


if __name__ == "__main__":
    if "--check" not in sys.argv:
        build()
    problems = check()
    print("approval_r1:", "ok (unsigned, r1 hash only)" if not problems else problems)
    sys.exit(1 if problems else 0)
