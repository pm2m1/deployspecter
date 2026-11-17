# Solo adoption of Amendment 003 r3: registered on OSF

This completes the two blanks at the end of `AMENDMENT_003_r3_SOLO_ADOPTION.md`, in a new file, so that the hashed
adoption record stays unchanged (`osf/REGISTER_NOW.md`, step 13).

OSF registration DOI or URL: **https://osf.io/34ytz** (DOI `10.17605/OSF.IO/34YTZ`)   Registered FREEZE.json sha256:
**`6fb0a189eb68c2351203bb5764387432c824e20d11a117a389dcdacb9aeced47`**

| | |
|---|---|
| Registration | https://osf.io/34ytz, template "OSF Preregistration" |
| Associated project | https://osf.io/9xdrb |
| Registered at | 2026-11-17T00:39:37.627039Z (OSF API, `attributes.date_registered`, UTC) |
| State when recorded | public, not archiving, not embargoed, not withdrawn; review state `accepted` |
| Frozen plan and code | branch `research/benchmark-prep`, commit `312d0af` |
| Label for every result | "preregistered, automation-blinded, single-investigator. Not independent" |

## What is on OSF

Checked on 2026-11-17 through the public OSF API, with no credentials. Each file was downloaded and hashed, and
matches both OSF's stored sha256 and the repository at `312d0af`.

| Attached to the registration (6 of the 11 files) | sha256 | Repository path |
|---|---|---|
| `FREEZE.json` | `6fb0a189…aeced47` | `osf/FREEZE.json` |
| `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md` | `af9b2205…13fe1c` | `protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md` (and its copy in `osf/`) |
| `AMENDMENT_003_r3_SOLO_ADOPTION.md` | `086ff3e5…bff1381` | `protocol/AMENDMENT_003_r3_SOLO_ADOPTION.md` |
| `SELECTION_RULE_SOLO.md` | `74ce07c3…be4eba3b` | `osf/SELECTION_RULE_SOLO.md` |
| `ANALYSIS_PLAN_SOLO.md` | `168daea9…0e758642` | `osf/ANALYSIS_PLAN_SOLO.md` |
| `MUTANTS.md` | `2c785419…85564ad1` | `benchmark_prep/MUTANTS.md` |

Not attached, and covered by their sha256 in the registered `FREEZE.json` at commit `312d0af`:
- `osf/SELECTION_RULE.md`;
- `osf/ANALYSIS_PLAN.md`;
- `protocol/OWNER_DECISIONS_r3_admission_2026-11-16.md`;
- `solo/CANDIDATES_FROZEN.json`.

Not attached and not hashed: `osf/R3_SHA256`. It holds only the r3 sha256 (`af9b2205…`), which `FREEZE.json`, the
registered text and the attached r3 file all carry.

The registered text carries the FREEZE.json sha256, both sealed-seed hashes and the r3 sha256, and no earlier FREEZE
hash. Every other frozen program and document (selection, mutant, oracle, analysis and admission code) is covered by
`FREEZE.json`.

## What this unlocks

`osf/REGISTRATION.json` records the above. `research_tools/solo/select_cases.py select` accepts it, because it names an
OSF URL and the sha256 of the committed `FREEZE.json`. From here on, nothing that `FREEZE.json` hashes may change. No
case had been selected and no arm run when this was recorded. The next steps are in `benchmark_prep/RUNBOOK.md`.

Recorded on the owner's instruction of 2026-11-17 (verbatim): "Record the OSF registration on research/benchmark-prep
(owner-authorized; same rules: no AI attribution, plain commits, frozen files untouched)."
