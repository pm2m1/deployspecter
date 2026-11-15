# Runbook: the single-investigator r3 evaluation (NOT STARTED; nothing here has been run)

Design: `../protocol/AMENDMENT_003_r3_SOLO_ADOPTION.md`. Frozen inputs: `../osf/FREEZE.json`. Label for everything:
"preregistered, automation-blinded, single-investigator. Not independent". Each step's outputs are committed before
the next step starts, so the history shows the order. Never re-run a step to get a different result. A failure is
recorded and the plan continues.

## 1. Register

Follow `../osf/REGISTER_NOW.md` to the end. Gate: `osf/REGISTRATION.json` exists and names FREEZE.json's sha256.

## 2. Select the cases

```sh
python research_tools/solo/select_cases.py fetch  --mirrors ~/ds-mirrors
python research_tools/solo/select_cases.py select --mirrors ~/ds-mirrors --registration docs/experimental_mvp/osf/REGISTRATION.json \
    --out docs/experimental_mvp/solo/selection_1 [--s4-verdicts S4.json] [--refused REFUSED.json]
```
- **Exit 4** names an S4 application. Run its S4 check (`osf/SELECTION_RULE.md` S4, steps 1–6) before reading any of
  its fixes, record `{"<owner>/<repo>": "pass"|"fail"}` in `solo/S4.json` with a log of the commands, commit, and
  re-run into a new `selection_<n>`.
- **For each admitted case:** read its regression test(s), write `solo/maintainer_specs/<case>.json`, then run
  ```sh
  python research_tools/solo/maintainer_oracle.py check-spec --spec SPEC --test-file FILE
  ```
  Exit 3 means refused: add the case to `solo/REFUSED.json`, commit, and re-run `select`.
- Commit each spec, with its sha256 in `solo/SPECS.sha256`, **before** step 3 starts on that case.

## 3. Build the integrations (log time per application)

For each application, record the start and end time and lines of code in `solo/INTEGRATION_LOG.jsonl`
(`{"app", "start", "end", "minutes", "lines"}`). For each case:
- pin both revisions (the pattern of `research_tools/nbc/pin_*.py`);
- write the integration, the RA-DET-G schema, Muse's context and the RA-DET workloads, from the public API and
  documentation;
- run `research_tools/seal_labels.py` on Muse's inputs;
- **oracle-error check:** the maintainer oracle on the corrected revision, serial schedule only. VIOLATED excludes the
  case for every arm; record it.

Then generate the mutants once per application, from the **corrected** revision's tree of one of its cases
(MUTANTS.md: a kill is judged against the unmutated corrected revision):
```sh
python research_tools/solo/mutants.py generate --app <app> --archive <corrected revision .tar>   # counts and hashes only
```
Commit the printed counts and hashes.

## 4. Run every arm at equal budgets

- **Model arms:** admission through `research_tools/solo/admission_solo.py` (a missing request excludes the case for
  every arm) and `verified_request.py` with the accepted model `muse-spark-1.3`.
- **Every arm:** K = 6 × S = 2 schedules per revision, under the frozen arm order, with
  `research_tools/nbc/run_nbc.py run` per case, wrapped by `research_tools/solo/capture_final_states.py` so that
  each final state is kept, and the maintainer oracle as the case oracle.
- **Mutants:** for each mutant, `mutants.py materialize`, then the same arms, schedules and seeds on the mutated
  build, paired with the unmutated build. Classify with `auto_oracles.py classify`, and write `RESULTS.json`
  (`{"<mutant id>": {"verdict": ...}}`).
- **#10 (time to detection)** comes from these same runs. No extra run is made.

## 5. Decode the mutants

Only after `RESULTS.json` is committed:
```sh
python research_tools/solo/mutants.py decode --app <app> --id <id> --results RESULTS.json
```
for each mutant. Commit the decoded records and `ACCESS.log`.

## 6. Analyse per the frozen plan

`../osf/ANALYSIS_PLAN.md` and `../osf/ANALYSIS_PLAN_SOLO.md`, with `research_tools/paired_eval.py`:
- historical cases: McNemar (primary and secondary), leave-one-application-out, Kaplan–Meier (#10);
- mutants: separately, per operator and application;
- report refusals, exclusions, integration time and cost with exact counts.

Never pool mutants with historical cases. Label every table.
