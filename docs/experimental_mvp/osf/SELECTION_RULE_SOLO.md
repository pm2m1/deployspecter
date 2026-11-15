# Case selection for the single-investigator design (predeclared; part of `FREEZE.json`)

This replaces the human curator of `SELECTION_RULE.md` with frozen code. Everything in `SELECTION_RULE.md` S1, S2 and
S4 still applies (r3 §3 limits, the pool and exclusions, and the Django/psycopg2 rule S4). Its S3 ordering and S5
("who reads what") are replaced below.

## What runs

`research_tools/solo/select_cases.py select`, after `fetch` mirrors the 20 repositories of
`docs/experimental_mvp/solo/CANDIDATES_FROZEN.json`. It refuses to run until `osf/REGISTRATION.json` names the OSF
registration and the sha256 of the `FREEZE.json` that was registered. It refuses a selection seed whose sha256 isn't
the one in `FREEZE.json`.

## The rule (the code is authoritative; this restates it)

1. **Order of applications:** ascending `sha256("<selection seed>:solo-selection:app:<owner>/<repo>")`.
2. **Candidates per application:** non-merge, first-parent commits on the default branch between 2023-04-03 and
   2026-11-16 whose message matches the frozen topic and fix expressions of `research_tools/nbc/scan.py`, and that
   change a non-test `.py` or `.sql` file. Ordered by `sha256("<seed>:solo-selection:commit:<repo>:<commit>")`.
3. **Screening,** at most 12 per application, recording the first failing check:
   - `root_commit`;
   - `driver`: the parent declares neither psycopg 3, nor psycopg2 with Django 4.2 or later;
   - `s4_failed`: an S4 application whose S4 check failed;
   - `no_regression_test`: the fix adds or changes no `test_*` function that contains an assertion;
   - `adapter_refused`: `maintainer_oracle.py` refused the case, with its reason. The reasons include
     `oracle_depends_on_tool`: every expressed assertion uses DeploySpecter code;
   - `app_cap`: the application already has 3 admitted cases.
4. **Stop** at 10 admitted cases from at least 5 applications, or after 100 screens. With fewer than 10, RA-Q3 is
   descriptive.
5. **S4 checks come first.** The selection stops (exit 4) at the first S4 application without a recorded verdict.
   The investigator runs that application's S4 check (`SELECTION_RULE.md` S4, steps 1–6), records `pass` or `fail`
   in `S4.json`, and re-runs `select`. Because the order is fixed, earlier decisions don't change.
6. **Adapter refusals.** For each admitted case, the investigator writes the maintainer spec (format
   `ds-maintainer-spec/2`) and runs `maintainer_oracle.py check-spec`. A refusal goes into `REFUSED.json` as
   `{"repo", "fix_commit", "reason"}`, and `select` is re-run; the next candidate in the fixed order takes the place.
   `check-spec` refuses when:
   - the spec leaves out or invents an assertion or a changed test function;
   - an expressed assertion lacks `uses_deployspecter_code`, or claims `false` while reading DeploySpecter data;
   - a `mock`, `exception` or `in_memory_return_value` reason fails mechanical verification;
   - nothing is expressible;
   - every expressed assertion uses DeploySpecter code (`oracle_depends_on_tool`).

   The coverage record of every admitted or refused case is committed with its spec.

## What the investigator reads, and when

- **Before registration:** nothing of any candidate's history beyond what `FEASIBILITY.md` already records.
- **During `select`:** nothing. The code reads messages and diffs, but stores and prints only commit ids, paths,
  function names and skip codes.
- **After a case is admitted:** its regression test, to write the spec, and its public API and documentation, to
  write the integration. The fix commit itself is known to the investigator from this point (see the solo adoption
  record: labels aren't sealed from the runner).
