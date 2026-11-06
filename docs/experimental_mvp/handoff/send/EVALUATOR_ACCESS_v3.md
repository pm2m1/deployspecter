# What the sealed evaluator can and cannot reach (v3; verified on synthetic data, 2026-11-06)

Tool: `research_tools/evaluator_sandbox.py`. Tests: `tests/experimental/test_evaluator_access_offline.py`, 8 tests:
7 offline, plus 1 in a throwaway Docker container.

**This is the simulated workflow.** A `confirmatory` launch is refused unless Amendment 003 verifies as "adopted,
consistent" *and* registration evidence exists. Both tests confirm the refusal: with the real, unadopted state, and
with an adoption file present but forms unsigned.

| Must not reach the evaluator | How it is kept out | Host mode | Container mode |
|---|---|---|---|
| Buggy/fixed labels | not in the package (`R1`/`R2` in random order); label-token scan of the package with the reveal's labels | ✓ none in the package | ✓ unreachable |
| Fix or buggy commits | archives normalized (no pax header or mtimes); 40-hex scan; byte search of every package file, tars included | ✓ | ✓ |
| Curator secrets (the reveal, raw archives) | byte identity, contained text, and JSON values of each sensitive file are checked against every package file | ✓ not in the package. ⚠ **readable by path**: the same OS user | ✓ not mounted: unreachable |
| Oracle revisions not approved | sha256 allowlist; others refused and never copied | ✓ | ✓ |
| Hidden or unfrozen case registry | only a verified, frozen registry is packaged; an open registry is refused | ✓ | ✓ |
| Reveal-authorization material (the results ledger) | listed as sensitive; no package byte or text match | ✓ not in the package. ⚠ readable by path | ✓ unreachable |
| Model keys, `DATABASE_URL`, `DS_*` | environment reduced to `PATH`, `LANG` and an empty `HOME` | ✓ | ✓ |
| Network | — | not restricted | ✓ `--network none` (connect fails) |
| Writing to its inputs | — | not restricted | ✓ read-only mount and root |

**Container mode** uses `ds-mvp-evalbox-<nonce>`, with:
- the runner's pinned Python base image, `--pull never`;
- uid 65534, all capabilities dropped, `no-new-privileges`;
- only the package mounted, read-only.

It is removed after each run, and the test checks that no container is left behind.

**Remaining limits.**
- **Host mode** gives no filesystem separation, because the evaluator runs as the curator's OS user. Use
  container mode, or separate accounts or machines.
- **Container mode** shares Docker Desktop's daemon and VM: it is restricted, not hostile-grade.
- **Neither mode** hides what the revisions' contents show: diffing R1 against R2 shows the fix location, and
  public history and training data can't be excluded.
