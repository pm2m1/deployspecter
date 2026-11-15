# Solo adoption record for Amendment 003, revision candidate r3

**Label for every result under this record: "preregistered, automation-blinded, single-investigator. Not
independent".**

**Candidate:** `docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`, sha256
`af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`, **unchanged**. This record states how one person
runs r3's design. Wherever r3 requires an independent person, this record names the automated or mechanical
replacement and **what independence is lost**. r3 §15's confirmatory conditions are **not** met, and no result
under this record is called confirmatory.

Related records, all new files:
- `OWNER_DECISIONS_r3_admission_2026-11-16.md`: the owner's admission decisions and the accepted model string;
- `../osf/FREEZE.json`: the hashes of everything frozen, including the sealed seeds' hashes;
- `../osf/SELECTION_RULE_SOLO.md` and `../osf/ANALYSIS_PLAN_SOLO.md`;
- `../solo/DEV_CASE_CHECK.md`: the adapter and the generator, checked on the development cases.

## Superseded (left unedited)

These files assumed collaborators. They are **superseded by this record** and stay in the repository unchanged, as
a record of the earlier plan:
- `AMENDMENT_003_r3_OWNER_ADOPTION.md` (the four-person adoption record);
- `../benchmark_prep/MESSAGES.md`;
- `../benchmark_prep/CURATOR_QUICKSTART.md`;
- `../benchmark_prep/ORACLE_WRITER_QUICKSTART.md`;
- `../benchmark_prep/FACULTY_NOTE.md`.

## Each r3 role: the solo replacement, and what is lost

| r3 role (rule) | Solo replacement | Independence lost |
|---|---|---|
| **Label Curator** (#6) | `research_tools/solo/select_cases.py`: the frozen candidate list (`solo/CANDIDATES_FROZEN.json`) and frozen rules, ordered by a **sealed seed** whose sha256 alone is committed. It runs only after the OSF registration, and records every skip by code. | **Labels aren't sealed from the runner.** The investigator who runs the arms also ran the selection, and knows which commit is the fix. r3's random R1/R2 order protects nothing here. The *choice* of cases is mechanical and can't be steered after the freeze, but the candidate list and rules were written by the developer. |
| **Oracle Author** (#7) | **Historical cases:** the maintainers' own regression test from the fix commit. The investigator transcribes it into a spec (`solo/maintainer_specs/`), and `maintainer_oracle.py` checks that the spec accounts for **every** assertion of the chosen test functions, and every changed test function. It refuses the case when no assertion can be expressed over the final database state. **Mutants:** automatic oracles (`auto_oracles.py`): serial reachability, and a differential against the unmutated build under the identical schedule. | **The oracle isn't fix-blind,** and its transcription is the developer's judgment. That covers which test functions match the workload's scenario, which assertions are expressible, and translations through the integration's own tables (for example pgqueuer's line 258). The maintainers wrote the property, not the developer, but the developer maps it. Made measurable before
registration: each spec (format 2) flags assertions that use DeploySpecter code, verifies the mechanically checkable
reasons, and yields a coverage record; a case whose oracle rests only on tool code is refused; and a strict-subset
sensitivity analysis is preregistered (`ANALYSIS_PLAN_SOLO.md`). |
| **Oracle Reviewer** (#7) | None. `check_spec` is a mechanical completeness check only. Before any arm runs, each spec is committed and hashed, and run on the **corrected** revision under the serial schedule. A violation there excludes the case for every arm as an oracle error. | A second person's check of each transcription. |
| **Mutant secrecy** (MUTANTS.md) | `mutants.py` samples sites with the **sealed mutant seed**. It writes each mutant encrypted to a sealed store, and prints only counts and hashes. `decode` refuses until the results file gives that mutant a final verdict. | Secrecy holds against accidental reading only: the investigator holds the seed. Every `materialize` and `decode` is logged, but nothing technical stops a deliberate read. |
| **Fix-blind Integrator** (#29) | **The owner.** Integrations, workload schemas, Muse's context, RA-DET's curated workloads and RA-DET-G's schema are written by the person who selected the cases and transcribed their tests. | **The main residual threat.** The integrator knows each defect, so workloads and schemas can be shaped to reach it. Leak checks (`seal_labels.py`) catch copied fix text in Muse's inputs, but not tailoring. Mitigations: integration time is logged per application; RA-DET-G is outcome-blind by construction; mutants on the same integrations measure detection on defects nobody chose. |
| **Triage Reviewers A, B, Adjudicator** (#8) | None. No triage is needed for the primary outcome: under r3's rule, any corrected-revision violation voids the case for that arm. An oracle error (the only cross-arm exclusion triage could cause) is decided **mechanically**, by the corrected-revision serial check above, before any arm runs. | A judgment on whether a corrected-revision violation is a new finding, a pre-existing behaviour or an oracle error. They are reported as observed, without classification. |
| **Owner** (#9, §15) | The owner. | none |

## What adopting this record means

- **Commits to:** r3's design (arms, K = 6 × S = 2 budgets, the admission rule as amended by the owner's
  decisions, the analysis, the stopping rules), the selection, oracle and mutant code frozen by hash in
  `osf/FREEZE.json`, and the sealed seeds whose hashes it lists. This happens before any case is selected, and is
  registered on OSF with that file's hash.
- **Does not make:** the evaluation independent or confirmatory under r3. Historical cases and mutants are reported
  separately and never pooled. Development cases (ds-nbc/1, ds-nbc/2) are never counted.

## Owner

Owner: **Parth Mishra** (sole owner, GitHub pm2m1). Recorded on 2026-11-16 by the working session, on the owner's
written authorization below. This line records that authorization; it is not a handwritten signature.

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to: back up unpushed work to a private repo, submit the GitLab report as a confidential issue, post the saq issue, merge PR #2, publish the draft release, record my owner decisions, and re-plan the benchmark for a single investigator. I have no collaborators, curator, oracle writer or faculty reader. Record this text verbatim in each record you write on my behalf.

Amendment before registration, 2026-11-16: maintainer spec format 2 (the tool-code flag, mechanical verification of reasons, coverage records), the strict-subset sensitivity analysis and the `oracle_depends_on_tool` refusal were added and FREEZE.json was rebuilt, on the owner's written authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to: encrypt my confidential backup, remove the `backup` git remote and the local GitLab snapshot branch after the encrypted backup is verified, copy the encrypted backup to iCloud Drive, add assertion-coverage reporting and a sensitivity analysis to the solo benchmark before OSF registration and rebuild FREEZE.json, and submit the GitLab report as a confidential issue. Record this text verbatim in each record you write on my behalf.

OSF registration DOI or URL: ______________________   Registered FREEZE.json sha256: ______________________
