# Muse Spark on the GitLab-derived fixture: public evidence

**Result: Muse Spark did not detect the defect. 0 of 3 buggy-fixture trials.**

**This is a partial record.** Some original evidence from experiment `gitlab-ai-2aef4b9c09c435b7` is withheld, and **all six trials can't be fully reproduced from these files alone.** Three buggy-fixture trials are published with their original files. `OMISSIONS.md` lists what isn't here.

The experiment ran once on 2026-11-02, starting at 13:06:07 UTC, from commit `2dd347b`.

## What this was

A targeted, post-hoc experiment. The fixture and its defect were already known, so it is neither blind nor independent. The fixture is a small, isolated PostgreSQL model of a defect GitLab published in MR !246505. It isn't GitLab's schema, services, locks or workload, and nothing here is evidence about GitLab's production systems.

The question was whether Muse Spark could construct a useful test for this fixture without seeing the oracle, the known failing test or any failing schedule.

## Proposals are not findings

The model proposes tests, and proposing a test proves nothing. A **verified detection** needs all of these on the buggy fixture: an oracle flag on a valid test, a reproduced replay, an independent PostgreSQL check of the flagged schedule, a failing serial order, and no failing serial order of that test on the corrected fixture.

All three buggy-fixture responses described the delete behaviour in text and proposed a test aimed at it. All three of those tests were rejected at seeding, so they never ran. Written analysis doesn't count as detection.

## Configuration

| Item | Value |
|---|---|
| Model | `muse-spark-1.3` |
| Prompt | Version `v2`, unchanged from the earlier benchmark |
| Instructions SHA-256 | `5d38e0f2d6f18d0da7e72769c25d826a338f210666a5521a39657f64b19294c7` |
| Response schema SHA-256 | `7dfa99ba042414b428135d417a1e1290c4f19d1d95a4d294c860db9800f2e4ee` |
| Prompt identity, buggy fixture | `f2b8f708df908bc392b90b03635c5d1e98b34d74f07b8384c0e6eab84fc2f0af` |
| Fields sent | `model`, `instructions`, `input`, `max_output_tokens` (24,000), `text` |
| Not sent | `reasoning`, `store` |
| Reported by the provider in each published response | Reasoning effort `high`, `store: true`, temperature 1, top_p 1 |
| Budget | 6 requests, no retries or repairs, $1.00 estimated cap |
| Execution | 300 schedules and 60 s per response |

Later versions of this project's client send medium reasoning and `store=False` by default, so matching these settings needs its legacy mode.

## Results

| Measure | Result |
|---|---|
| Verified detections | **0 of 3** |
| Buggy-fixture trials | 5 of 12 proposed tests valid; 40, 20 and 40 schedules; no violations; no valid test deleted a seeded row with a seeded sibling |
| All six responses, aggregate | 24 proposed: 11 valid, 12 rejected for seeding a destination row the trigger had already created, 1 missing a required parameter |
| All six requests, aggregate | 6 sent, 6 returned, 0 failed; 16,020 input and 80,363 output tokens; 438.3 s; **$0.36157 estimated at published rates, not billing data** |

Only buggy-fixture figures can be recomputed from these files. The aggregates are stated, not independently checkable here.

## Comparisons on the buggy fixture

Same budget: 300 schedules and 60 s. These rows cover the buggy fixture only and make no statement about the corrected trigger.

| Generator | Result | Schedules | First failure | Blind? |
|---|---|---|---|---|
| Muse Spark, 3 responses | Not detected | 40, 20, 40 | None | Yes, but on a known fixture |
| Baseline B | Missed; 100 schedules inconclusive | 200 | None | Written before this defect was known |
| Baseline C | Detected, serial | 300 | 56 | No: written with this defect known |
| Hand-written reference | Detected, serial | 20 | 1 | No |

## Later retrospective observation

A deterministic state constructor was developed afterwards. It searches orders of a proposal's own seed rows, without changing their values or the test's operations. Applied offline to the saved responses, it made the three defect-aimed proposals executable, and all three then reproduced the original serial defect.

This was post-hoc development on a known fixture, using a tool built after these failures were seen. It isn't a blind or independent evaluation, it doesn't change the 0 of 3, and the constructor isn't in this release. Details are in `derived/retrospective_state_construction.json`.

## Contents

| Path | Status |
|---|---|
| `original/responses/buggy_r*/analysis.json` | **Byte-identical.** The three parsed buggy-fixture model responses |
| `original/responses/buggy_r*/system_run.json` | **Byte-identical.** Their validation and execution results |
| `original/baselines/reference/` | **Byte-identical.** The documented serial-defect reproduction and its minimized form |
| `derived/requests_buggy_only.jsonl` | **Filtered copy.** The original ledger's lines for the three buggy-fixture requests, each line unchanged, including the full provider responses |
| `derived/evidence_summary.json` | **Derived.** The figures above |
| `derived/retrospective_state_construction.json` | **Derived and post-hoc.** Not part of the frozen experiment |
| `redacted/protocol.redacted.json` | **Redacted copy**, not the original |
| `MANIFEST.sha256` | Hash and status of every file |
| `verify_package.py` | Checks hashes and recomputes buggy-fixture usage with the Python standard library |

## Check it yourself

Run `python verify_package.py` here to check hashes without a database. From the repository root, with PostgreSQL running as in the main README's Setup section, run `uv run deployspecter reproduce-public-evidence` to re-execute the three buggy-fixture responses, the documented defect and the buggy-fixture baselines. It makes no model requests. To rebuild the buggy-fixture prompt, check out commit `2dd347b`, load the case with `deployspecter.scenarios.load_case` and call `analyzer.build_prompt` with the fixture's schema. `analyzer.prompt_identity` should give the identity above.
