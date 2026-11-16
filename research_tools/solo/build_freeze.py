"""Solo r3 design: build ``docs/experimental_mvp/osf/FREEZE.json`` from the frozen files, and check it.

    python research_tools/solo/build_freeze.py            # write it
    python research_tools/solo/build_freeze.py --check    # exit 1 if the committed file differs from a rebuild

Every entry is the sha256 of the file's bytes as committed. The sealed seeds are listed by their sha256 only, read
from ``~/.deployspecter-sealed/seeds.sha256.json`` on the first build and carried over from the existing FREEZE.json
afterwards (the seeds themselves are never read here). After registration the file must not change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs/experimental_mvp/osf/FREEZE.json"
SEED_HASHES = Path.home() / ".deployspecter-sealed" / "seeds.sha256.json"
R3 = "docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md"
E = "docs/experimental_mvp/"

GROUPS: dict[str, list[tuple[str, str]]] = {
    "protocol": [(R3, "Amendment 003 revision candidate r3: the binding protocol, unchanged"),
                 (E + "osf/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md", "byte-identical copy for upload")],
    "adoption": [(E + "protocol/AMENDMENT_003_r3_SOLO_ADOPTION.md", "single-investigator adoption record")],
    "owner_decisions": [(E + "protocol/OWNER_DECISIONS_r3_admission_2026-11-16.md", "recorded decisions 1-4"),
                        (E + "protocol/OWNER_DECISIONS_r3_admission.md", "the draft with options and rationale")],
    "selection_rule": [(E + "osf/SELECTION_RULE.md", "S1-S5, including S4 (Django on psycopg2)"),
                       (E + "osf/SELECTION_RULE_SOLO.md", "the curator replaced by frozen code"),
                       (E + "nonblind_corpus/SELECTION_RULE.md", "screening criteria E1-E6 (section 4)"),
                       (E + "CORPUS_SHORTLIST.json", "frozen selection_rules")],
    "candidate_list": [(E + "solo/CANDIDATES_FROZEN.json", "the 20 applications, window and limits"),
                       (E + "benchmark_prep/FEASIBILITY.md", "source of the list and the screening budget")],
    "selection_code": [("research_tools/solo/select_cases.py", "seeded selection"),
                       ("research_tools/nbc/scan.py", "frozen TOPIC, FIX and PSYCOPG3 expressions"),
                       ("research_tools/solo/sealed.py", "sealed seeds and store")],
    "mutant_generator": [("research_tools/solo/mutants.py", "operators RM1-RM6, seeded sample, sealed store"),
                         (E + "benchmark_prep/MUTANTS.md", "operators and reporting rules")],
    "oracle_code": [("research_tools/solo/maintainer_oracle.py", "maintainer-test adapter"),
                    ("research_tools/solo/auto_oracles.py", "serial reachability and differential oracles")],
    "analysis_plan": [(E + "osf/ANALYSIS_PLAN.md", "r3 sections 8 and 13 verbatim"),
                      (E + "osf/ANALYSIS_PLAN_SOLO.md", "what changes under the solo design"),
                      ("research_tools/paired_eval.py", "analysis code"),
                      ("research_tools/solo/detection_level.py",
                       "run-record schema ds-solo-run/1 and the detection-level sensitivity analysis")],
    "admission_code": [("research_tools/confirmatory_prep/prep.py", "canonical JSON (decision 2)"),
                       ("research_tools/confirmatory_prep/prep_r3.py", "r3 proposal admission (decision 1)"),
                       ("research_tools/solo/admission_solo.py", "missing request excludes the case (decision 3)"),
                       ("research_tools/confirmatory_prep/verified_request.py", "model and request verification")],
}
SECTIONS = ["8. Analysis (paired)", "13. Predeclared primary outcomes"]


def sha(rel: str) -> str:
    return hashlib.sha256((REPO / rel).read_bytes()).hexdigest()


def section_text(title: str) -> str:
    text = (REPO / R3).read_text()
    n = title.split(".", 1)[0]
    m = re.search(rf"^## {n}\. .*?(?=^## \d+\. )", text, re.S | re.M)
    assert m and m.group(0).startswith(f"## {title}")
    return m.group(0).rstrip("\n")


def seed_hashes() -> dict[str, str]:
    if OUT.exists():
        old = json.loads(OUT.read_text()).get("sealed_seeds")
        if old and old.get("selection_seed_sha256"):
            return {k: old[k] for k in ("selection_seed_sha256", "mutant_seed_sha256")}
    d = json.loads(SEED_HASHES.read_text())
    return {k: d[k] for k in ("selection_seed_sha256", "mutant_seed_sha256")}


def build() -> dict:
    doc: dict = {
        "freeze": "ds-ra-r3-solo-freeze/1",
        "prepared": "2026-11-16",
        "status": "DRAFT: not registered; the owner registers it on OSF (osf/REGISTER_NOW.md). After registration this "
                  "file must not change.",
        "label": "preregistered, automation-blinded, single-investigator. Not independent",
        "design": "docs/experimental_mvp/protocol/AMENDMENT_003_r3_SOLO_ADOPTION.md",
        "hash": "sha256 of the file's bytes as committed in this repository",
    }
    for group, entries in GROUPS.items():
        doc[group] = [{"path": p, "sha256": sha(p), "role": role} for p, role in entries]
    doc["analysis_plan"] += [{"path": R3, "section": t, "sha256": hashlib.sha256(section_text(t).encode()).hexdigest(),
                              "role": "section text, UTF-8, from its heading to the line before the next section"}
                             for t in SECTIONS]
    doc["accepted_model"] = {"string": "muse-spark-1.3",
                             "source": E + "protocol/OWNER_DECISIONS_r3_admission_2026-11-16.md item 4"}
    doc["sealed_seeds"] = {**seed_hashes(), "hash": "sha256 of the seed's lowercase hex string, UTF-8",
                           "location": "outside the repository (~/.deployspecter-sealed/seeds.json, mode 600); "
                                       "never committed or printed"}
    doc["superseded"] = [E + "protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md", E + "benchmark_prep/MESSAGES.md",
                         E + "benchmark_prep/CURATOR_QUICKSTART.md", E + "benchmark_prep/ORACLE_WRITER_QUICKSTART.md",
                         E + "benchmark_prep/FACULTY_NOTE.md"]
    return doc


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    text = render(build())
    if a.check:
        ok = OUT.exists() and OUT.read_text() == text
        print("FREEZE.json matches a rebuild" if ok else "FREEZE.json differs from a rebuild")
        return 0 if ok else 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(REPO)} sha256 {hashlib.sha256(text.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
