# Invitation package for a prospective oracle author and oracle reviewer (v3: cover and bundle; not sent)

*This package names no one, and has not been sent. The author and the reviewer are two different people. Neither
may be a DeploySpecter developer, or the curator for the same case.*

## Read in this order

1. `handoff/send/OVERVIEW.md`: the project, its roles, and what is and isn't demonstrated.
2. `handoff/templates/participation_declaration.md`: **complete and return it first.** Any prior exposure to a
   case (its fix, tests, issue thread or changelog entry) excludes you from that case.
3. `handoff/send/ORACLE_REVIEW_PACKAGE_v2.md`: your instructions, which are unchanged. They cover the property
   format, corrected-version validation and the disagreement table.
4. `handoff/templates/oracle_spec.md`: the specification template.
5. Worked **development** examples, which are not blind: `historical/chancy_2895c4a/CASE.md`,
   `rolling_upgrade/procrastinate_3.3_3.4/CASE.md` with its `ERRATA.md` (two errors in the developer's own oracle,
   which is why this role exists), and `simultaneous/PROPERTIES.md`.
6. `protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md`, sha256 `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`.
7. `protocol/approval/ORACLE_REVIEWER_ACCEPTANCE.md`: rulings #7 and #11. Sign it only after checking the sha256.

## How your work is protected after delivery (implemented; rehearsed on synthetic data)

- **Approval.** The reviewer approves each oracle file by its sha256, in an allowlist. The evaluator launcher
  (`research_tools/evaluator_sandbox.py`) refuses any oracle revision that isn't on it.
- **Freezing.** Results are committed to a hash-chained ledger with the oracle's hash. Scoring refuses if the
  oracle changed afterwards, or if a result was produced with a different oracle.

## What you return

- the declarations;
- the acceptance form;
- per case, `oracle_spec.md`, citing documentation only;
- the reviewer's sign-off and the disagreements table;
- the approved-oracle allowlist (file name → sha256).
