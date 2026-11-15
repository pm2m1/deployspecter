# Analysis plan, single-investigator design (predeclared; part of `FREEZE.json`)

`ANALYSIS_PLAN.md` (r3 §8 and §13 verbatim, and `research_tools/paired_eval.py`) still governs the statistics. This
file states what changes under `protocol/AMENDMENT_003_r3_SOLO_ADOPTION.md`.

## Label

Every result: **"preregistered, automation-blinded, single-investigator. Not independent".** It is never called
confirmatory, whatever the sample size.

## Historical cases

- **Oracle:** the case's maintainer-test spec, compiled by `research_tools/solo/maintainer_oracle.py`, whose sha256
  is in `FREEZE.json`. Each spec is committed, and its hash recorded, before any arm runs on that case.
- **Oracle-error exclusion:** a case is excluded for every arm if its oracle is VIOLATED on the corrected revision
  under the serial schedule. This is checked once, before any arm runs, and not repeated.
- **Detection** for an arm: a SOUND, corroborated VIOLATED on the defective revision, and no violation on the
  corrected revision for that arm (r3's rule).
- **Primary:** exact two-sided McNemar, the Muse workload arm against RA-DET-G. **Secondary:** against RA-DET, with
  Holm correction; time to detection (#10), from the same runs, right-censored at 12 schedules, with Kaplan–Meier per
  arm and the paired censored median difference. Leave-one-application-out sensitivity for every comparison.
- With fewer than 10 eligible cases, RA-Q3 is descriptive.

## Mutants (a second source, reported separately and never pooled)

- **Generation:** `research_tools/solo/mutants.py generate` per application, after its integration is frozen and
  before any arm runs. The sample uses the sealed mutant seed.
- **Verdicts:** `research_tools/solo/auto_oracles.py`. A mutant is VIOLATED (killed) if any SOUND schedule's final
  state or results differ from the unmutated build's under the identical schedule. It is EQUIVALENT-SUSPECT if
  reached with no difference, and UNREACHABLE if never reached. Serial-killed mutants (different without
  concurrency) are reported in their own column.
- **Score per arm:** killed / (sampled − invalid − unreachable), reported with and without EQUIVALENT-SUSPECT
  mutants in the denominator, per operator and per application, with exact counts.
- A McNemar test on mutants is separate and secondary.
- Mutants are decoded (`mutants.py decode`) only after `RESULTS.json` holds their final verdicts.

## Reported but not tested

The number of cases refused by the adapter and the reasons; adapter refusals by reason; integration time per
application; Muse cost per case; and every corrected-revision violation, as observed, without classification.
