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

### Reported per case: assertion coverage

Every admitted and every refused case gets its coverage record from `maintainer_oracle.py` (spec format
`ds-maintainer-spec/2`):
- the total number of assertions in the chosen regression test functions;
- how many are expressed over the final state;
- how many of those are expressed without DeploySpecter code: no handler table, model or helper output, only the
  application's own tables read with plain SQL and the workload's record of its own operations;
- not-expressible assertions by reason;
- how many reasons were verified mechanically (`mock`, `exception`, `in_memory_return_value`);
- how many rest on investigator judgement.

For every detection, the report also states whether it rests on at least one assertion that uses no DeploySpecter
code (`failed_without_deployspecter_code`), or only on assertions that do (`detected_only_through_deployspecter_code`).
The detection-level sensitivity analysis below counts with this.

### Preregistered sensitivity analysis: the strict subset

The **strict subset** is the cases where no expressed assertion uses DeploySpecter code, and no not-expressible
reason is investigator judgement (`coverage.strict_subset`).
- The primary comparison (McNemar, the Muse arm against RA-DET-G) and the secondary comparisons (against RA-DET,
  with Holm correction; time to detection) are repeated on the strict subset.
- They are reported whatever its size, with exact counts.
- With fewer than 10 strict cases, the result is **descriptive only**, and the report says so in those words.
- For reference, neither development case would qualify (`solo/DEV_CASE_CHECK.md`).

### Preregistered sensitivity analysis: detection level

The strict subset works per case, so it can't show a case that is detected only through DeploySpecter's own tables.
This analysis works per detection.
- **Recorded per run.** The maintainer oracle's verdict record for each schedule lists every VIOLATED assertion with
  its `uses_deployspecter_code` flag (`MaintainerOracle.evaluate`, key `violated_assertions`). Each run record uses
  the results schema `ds-solo-run/1` (`research_tools/solo/detection_level.py`). It carries this list for every
  defective-revision schedule, and, for the first detection, the VIOLATED lines with the flag true and with the flag
  false.
- **What counts.** A detection counts only if at least one VIOLATED assertion at that schedule has
  `uses_deployspecter_code: false`. A run that detects only through flag-true assertions detects, in this analysis,
  at its first schedule with a flag-false VIOLATED assertion. If there is none, it is a nondetection, censored at the
  budget.
- **What is repeated.** The primary comparison (exact McNemar, the Muse arm against RA-DET-G) and the secondary
  comparisons (against RA-DET, with Holm correction across the two McNemar tests; time to detection with the Gehan
  sign test and Kaplan–Meier medians) are run again by `paired_eval.py`'s own code, through `detection_level.py`.
  The cases, budgets and costs are the same. A corrected-revision violation still counts against the arm, whichever
  assertion it came from.
- **Reported per arm, beside the main result:** detected cases in the main analysis, detected cases at detection
  level, and the cases detected only through DeploySpecter code.
- **Status.** It is a sensitivity analysis and never replaces the main result. With fewer than 10 paired defect
  cases it is **descriptive only**, and the report says so in those words ("Descriptive only: N paired defect cases,
  fewer than 10.").
- **Development cases** (NONBLIND, never counted): it currently counts chancy-2895c4a, whose detections rest on
  line 164 (no DeploySpecter code), and not pgqueuer-8dfadf4, whose detections rest on line 258 only
  (`solo/DEV_CASE_CHECK.md`).

### Rule: an oracle may not depend only on the tool

A case whose expressed assertions **all** have `uses_deployspecter_code: true` is refused. It is logged as
`adapter_refused` with reason `oracle_depends_on_tool`, and the next candidate takes its place
(`SELECTION_RULE_SOLO.md`). `uses_deployspecter_code` is declared per assertion. `check-spec` refuses `false` when the
predicate reads a final-state key the spec classifies as DeploySpecter's, or a `raw_results` path outside the
workload's record.

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
