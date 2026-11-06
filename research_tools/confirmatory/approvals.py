"""EXPERIMENTAL: Amendment 003 decisions, the roles that must approve each one, and what the approval forms actually
say. Read-only over the repository: nothing here signs, ticks, adopts or registers anything.

**Sources, all read from the repository:**
* ``DECISIONS_003_v3.md``: the 24 decisions and their deciders;
* ``approval/*.md``: the four forms, their decision lines, tick boxes and signature rows;
* ``DECISIONS_003_v2.md``: the evidence blocks for #6, #7, #8, #9 and #14.

**Role binding (the point of this module).** A role is satisfied only by a signed signature row on *that role's
own form*:
* owner: ``OWNER_APPROVAL.md``;
* curator: ``CURATOR_ACCEPTANCE.md``;
* oracle author and oracle reviewer: ``ORACLE_REVIEWER_ACCEPTANCE.md``;
* triage reviewer 1, triage reviewer 2 and adjudicator: ``TRIAGE_REVIEWERS_ACCEPTANCE.md``.

The owner's form can therefore never satisfy a curator, oracle or triage requirement, whatever its text says. A
decision is ACCEPTED only when every required role's form ticks "approve" on that decision's line and is signed.
"amend" or "reject" on any line means a new candidate.

**Real mode** (``real_problems``) additionally refuses:
* anything marked SIMULATION;
* a signer who is a repository commit author (a developer);
* one person in two independent roles;
* a form gap: a required role whose form has no line for the decision, or no signature row of its own.

Mock approvals for rehearsals (``mock_approvals``) carry ``"SIMULATION": true`` and are rejected by real mode.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PROTOCOL = REPO / "docs/experimental_mvp/protocol"
ROLES = ("owner", "curator", "oracle_author", "oracle_reviewer", "triage_reviewer_1", "triage_reviewer_2",
         "adjudicator")
FORM_OF = {"owner": "OWNER_APPROVAL.md", "curator": "CURATOR_ACCEPTANCE.md",
           "oracle_author": "ORACLE_REVIEWER_ACCEPTANCE.md", "oracle_reviewer": "ORACLE_REVIEWER_ACCEPTANCE.md",
           "triage_reviewer_1": "TRIAGE_REVIEWERS_ACCEPTANCE.md", "triage_reviewer_2": "TRIAGE_REVIEWERS_ACCEPTANCE.md",
           "adjudicator": "TRIAGE_REVIEWERS_ACCEPTANCE.md"}
INDEPENDENT = ROLES[1:]  # every role but the owner must be independent of the developers and of each other
SIMULATION_MARK = "SIMULATION"


@dataclass
class Decision:
    id: str
    ruling: str
    decider_text: str
    roles: tuple[str, ...]
    register_line: int
    group: str
    candidate_line: int | None = None


@dataclass
class Form:
    name: str
    lines: dict[str, dict[str, Any]] = field(default_factory=dict)  # decision -> {line, ticked}
    signatures: list[dict[str, Any]] = field(default_factory=list)  # {line, role, name, signature, date}


def roles_for(decider: str) -> tuple[str, ...]:
    d = decider.lower()
    out = ["owner"] if "owner" in d else []
    if "curator" in d:
        out.append("curator")
    if "author and reviewer" in d:
        out += ["oracle_author", "oracle_reviewer"]
    elif "oracle reviewer" in d:
        out.append("oracle_reviewer")
    if "reviewers and adjudicator" in d:
        out += ["triage_reviewer_1", "triage_reviewer_2", "adjudicator"]
    return tuple(out)


def decisions(protocol: Path = PROTOCOL) -> list[Decision]:
    """The 24 decisions of the v3 register, with the decider column taken by table shape (a ``|`` inside a ruling,
    e.g. ``{pre|post}``, splits cells, so the decider is counted from the end of the row)."""
    text = (protocol / "DECISIONS_003_v3.md").read_text().splitlines()
    cand = (protocol / "AMENDMENT_003_ADOPTION_CANDIDATE.md").read_text().splitlines()
    out, width, group, after = [], 0, "", 0
    for i, line in enumerate(text, 1):
        if line.startswith("## "):
            group = line[3:].strip()
        if line.startswith("| # |"):
            width = len(line.strip().strip("|").split("|"))
            continue
        m = re.match(r"^\| (\w+)(?: \(new\))? \|", line)
        if not m or line.startswith("|---") or not width:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        decider = cells[-2] if width == 5 else cells[-1]
        ruling = "|".join(cells[1:-3] if width == 5 else cells[1:-1]).strip()
        d = Decision(m.group(1), ruling, decider, roles_for(decider), i, group)
        probe = re.sub(r"[\"*`]", "", ruling)[:50]
        for j, c in enumerate(cand, 1):  # §19 lists the rulings in register order: search forward
            if j > after and probe and probe in re.sub(r"[\"*`]", "", c):
                d.candidate_line = after = j
                break
        out.append(d)
    return out


def parse_form(path: Path) -> Form:
    f = Form(path.name)
    sig = False
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith("| Role |"):
            sig = True
            continue
        m = re.match(r"^\| #(\w+) \| (.*?) \|$", line)
        if m and not sig:
            ticks = re.findall(r"\[([ xX])\] (approve|amend|reject)", m.group(2))
            f.lines[m.group(1)] = {"line": i, "ticked": [v for t, v in ticks if t in "xX"]}
            continue
        if sig and line.startswith("|") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 4:
                f.signatures.append({"line": i, "role": cells[0], "name": cells[1], "signature": cells[2],
                                     "date": cells[3]})
        elif sig and not line.startswith("|"):
            sig = False
    return f


def forms(protocol: Path = PROTOCOL) -> dict[str, Form]:
    return {n: parse_form(protocol / "approval" / n) for n in sorted(set(FORM_OF.values()))}


def _row_roles(form: str, role_cell: str) -> list[str]:
    """Which roles a signature row on ``form`` can stand for. Roles come only from the form's own role set."""
    own = [r for r, f in FORM_OF.items() if f == form]
    cell = role_cell.lower()
    if form == "TRIAGE_REVIEWERS_ACCEPTANCE.md":
        return [r for r, key in (("triage_reviewer_1", "reviewer 1"), ("triage_reviewer_2", "reviewer 2"),
                                 ("adjudicator", "adjudicator")) if key in cell]
    if form == "ORACLE_REVIEWER_ACCEPTANCE.md":
        named = [r for r, key in (("oracle_author", "author"), ("oracle_reviewer", "reviewer")) if key in cell]
        return named or own  # one unlabeled row: one person for both (a gap real mode refuses)
    return own


def signed(row: dict[str, Any]) -> bool:
    return bool(row["name"] and row["signature"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"] or ""))


def role_signers(fs: dict[str, Form]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {r: [] for r in ROLES}
    for name, f in fs.items():
        for row in f.signatures:
            if signed(row):
                for r in _row_roles(name, row["role"]):
                    out[r].append(row | {"form": name})
    return out


def status(d: Decision, fs: dict[str, Form]) -> dict[str, Any]:
    """PROPOSED unless every required role's own form ticks approve on this decision and is signed by that role."""
    signers = role_signers(fs)
    per_role, gaps = {}, []
    for r in d.roles:
        f = fs[FORM_OF[r]]
        line = f.lines.get(d.id)
        if line is None:
            gaps.append(f"{FORM_OF[r]} has no line for #{d.id} (role {r})")
        rows = [x for x in f.signatures if r in _row_roles(f.name, x["role"])]
        if not rows:
            gaps.append(f"{FORM_OF[r]} has no signature row for role {r}")
        elif r in ("oracle_author", "oracle_reviewer") and all(not x["role"] for x in rows):
            gaps.append(f"{FORM_OF[r]} has one unlabeled signature row standing for both the oracle author and the "
                        "oracle reviewer; ruling #7 needs two people (rows 'Oracle author' and 'Oracle reviewer')")
        per_role[r] = {"form": FORM_OF[r], "line": line["line"] if line else None,
                       "ticked": line["ticked"] if line else [], "signed": bool(signers[r]),
                       "signature_lines": [x["line"] for x in rows]}
    if any("amend" in v["ticked"] or "reject" in v["ticked"] for v in per_role.values()):
        st = "AMEND OR REJECT REQUESTED (a new candidate is needed)"
    elif per_role and all(v["ticked"] == ["approve"] and v["signed"] for v in per_role.values()) and not gaps:
        st = "ACCEPTED"
    else:
        st = "PROPOSED"
    return {"status": st, "roles": per_role, "gaps": list(dict.fromkeys(gaps))}


def developers(repo: Path = REPO) -> set[str]:
    out = subprocess.run(["git", "-C", str(repo), "log", "--format=%an%n%ae"], capture_output=True, text=True).stdout
    return {x.strip().lower() for x in out.splitlines() if x.strip()}


def real_problems(protocol: Path = PROTOCOL, repo: Path = REPO) -> list[str]:
    """Why the approvals don't authorize a confirmatory run (empty = they do)."""
    fs, ds = forms(protocol), decisions(protocol)
    p = []
    for d in ds:
        s = status(d, fs)
        if s["status"] != "ACCEPTED":
            p.append(f"#{d.id} is {s['status']} (needs {', '.join(d.roles)})")
        p += [f"#{d.id}: form gap: {g}" for g in s["gaps"]]
    signers = role_signers(fs)
    devs = developers(repo)
    names: dict[str, str] = {}
    for r in ROLES:
        for row in signers[r]:
            n = row["name"].strip().lower()
            if SIMULATION_MARK.lower() in (row["name"] + row["signature"]).lower():
                p.append(f"{r}: a SIMULATION signature is never an approval")
            if r in INDEPENDENT and n in devs:
                p.append(f"{r}: signer {row['name']!r} is a repository commit author (a developer)")
            if r in INDEPENDENT and n in names and names[n] != r:
                p.append(f"{row['name']!r} holds two independent roles ({names[n]}, {r})")
            names.setdefault(n, r)
    owner = {row["name"].strip().lower() for row in signers["owner"]}
    for r in INDEPENDENT:
        for row in signers[r]:
            if row["name"].strip().lower() in owner:
                p.append(f"{r}: signed by the owner; the owner can't approve for an independent role")
    return sorted(set(p), key=p.index)


def mock_approvals() -> dict[str, Any]:
    """SIMULATION ONLY: every role 'approves' every decision. Real mode rejects this object."""
    return {SIMULATION_MARK: True, "note": "mock approvals for an orchestration rehearsal; not signatures",
            "approvals": {d.id: {r: {"name": f"SIM-{r}", "verdict": "approve"} for r in d.roles}
                          for d in decisions()}}


def mock_problems(mock: dict[str, Any]) -> list[str]:
    """Simulation mode's own consistency check of mock approvals (complete, role-bound, marked)."""
    if mock.get(SIMULATION_MARK) is not True:
        return ["mock approvals must carry SIMULATION: true"]
    p = []
    for d in decisions():
        got = mock.get("approvals", {}).get(d.id, {})
        missing = [r for r in d.roles if got.get(r, {}).get("verdict") != "approve"]
        if missing:
            p.append(f"#{d.id}: no (mock) approval from {missing}")
    return p
