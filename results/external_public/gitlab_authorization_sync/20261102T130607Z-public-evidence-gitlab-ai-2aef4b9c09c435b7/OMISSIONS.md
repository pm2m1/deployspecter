# What this package leaves out

This file stays until publication is complete. Some original evidence is withheld pending the owner's disclosure review, so **all six trials can't be fully reproduced from these files alone.**

## What you can and can't check

| Claim | Can you check it from public files? | How |
|---|---|---|
| Three buggy-fixture responses, as saved | Yes | `python verify_package.py` |
| Their validation results and the 0 of 3 | Yes, on PostgreSQL | `uv run deployspecter reproduce-public-evidence` |
| The documented serial defect, its replay and minimization | Yes, on PostgreSQL | The same command |
| Baseline B and Baseline C figures on the buggy fixture | Yes, by re-running public code | The same command |
| The hand-written test on the corrected fixture | Yes | The same command, and the separate `20261101T134447Z/report.json` |
| Six-request totals for tokens, cost and proposals | No: stated, not recomputable | `derived/evidence_summary.json` |
| Anything about the three corrected-fixture trials | No | Withheld |
| Repeating the run with the original runner | No | Withheld |

## Withheld

| Item | Why it isn't here |
|---|---|
| The three corrected-fixture model responses, with their validation details and execution results | Under disclosure review |
| The complete six-request ledger (`requests.jsonl`) | It contains those responses. `derived/requests_buggy_only.jsonl` holds its buggy-fixture lines, each unchanged |
| Corrected-fixture execution results for Baseline B and Baseline C | Under disclosure review |
| Baseline C's saved artifacts and per-test records | Withheld with the items above. Aggregate figures are given, and its generator is public code |
| The combined original summary, baseline file and report | They include the items above. Derived files replace them for what is published |
| The experiment's runner and its tests, and one counting rule in its protocol | Withheld with the items above. The public command in the main README is a separate harness, not this runner |

These items are withheld as a group, so a withheld item doesn't indicate what it shows either way. **This package makes no claim about how the corrected fixture behaved under these generators, and it isn't a safety result for the corrected trigger.** A pass or a withheld result on this fixture says nothing about GitLab's production systems.

## Redactions

| File | Change |
|---|---|
| `redacted/protocol.redacted.json` | One counting rule is removed. Nothing else changed. Because of this, the experiment ID can't be recomputed from this copy |

The original's SHA-256 is in `MANIFEST.sha256`, marked `withheld-original`.

## Commitment to the complete record

A complete, unchanged archive is held privately. The SHA-256 of its file manifest is:

`1d0bb57093b2406d1cf0aa550e017e4d2b17a97037619f0e251da43a54ea7d6d`

If the withheld files are published later, anyone can check them against this value. It shows the record was fixed; it doesn't reveal the contents.
