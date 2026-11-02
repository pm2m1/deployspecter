# What this package leaves out

Some original evidence is withheld pending the owner's review. **All six trials can't be fully reproduced from these files alone.**

## Withheld

- **The three corrected-fixture model responses**, their validation details and their execution results.
- **The complete six-request ledger** (`requests.jsonl`), because it contains those responses. `derived/requests_buggy_only.jsonl` holds its buggy-fixture lines unchanged.
- **Corrected-fixture execution results** for Baseline B and Baseline C.
- **Baseline C's artifacts and per-test records.** Only aggregate figures are given. Its generator is public code in this repository.
- **The combined original summary, baseline file and report.** Derived files replace them for what is published.
- **The experiment's runner and tests**, which are not in the public repository.

These are withheld as one group, so a withheld item says nothing about what it shows. **This package makes no claim about how the corrected fixture behaved under these generators, and it isn't a safety result for the corrected trigger.** The hand-written reference's corrected-fixture result is public separately, in `results/external_public/gitlab_authorization_sync/20261101T134447Z/report.json`.

## What that limits

- Aggregate figures across all six requests and responses are stated here, but can't be recomputed from these files.
- The comparison figures for Baseline B and Baseline C can be re-run from public code, but their saved records aren't here.
- The experiment can't be re-run exactly as it was without the runner.

## Redactions

| File | Change |
|---|---|
| `redacted/protocol.redacted.json` | One counting rule is removed. Nothing else changed. Because of this, the experiment ID can't be recomputed from this copy |

The original's SHA-256 is in `MANIFEST.sha256`, marked `withheld-original`.

## Commitment to the complete record

A complete, unchanged archive is held privately. The SHA-256 of its file manifest is:

`1d0bb57093b2406d1cf0aa550e017e4d2b17a97037619f0e251da43a54ea7d6d`

If the withheld files are published later, anyone can check them against this value. It shows the record was fixed; it doesn't reveal the contents.
