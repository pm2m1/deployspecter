# Results

| Path | Contents |
|---|---|
| `benchmark/heldout_2026-11-01T105013Z.*` | The held-out evaluation: 6 model requests, one per case, with a frozen prompt |
| `benchmark/heldout_ai_preflight.json` | Fixture, prompt and baseline hashes taken before that run |
| `benchmark/dev_2026-11-01T102413Z.*` | Development results, scored from 10 recorded responses; 2 repeats are missing |
| `benchmark/latest_{dev,heldout}.json` | Copies of the two reports above, shown by the interface |
| `benchmark/posthoc_wallet_corrected_actor_names.json` | Post-hoc check of one response rejected in the held-out run; not a held-out result |
| `benchmark/failures/` | Saved failures that `deployspecter replay` can re-execute |
| `ai_responses/v2/` | Raw model responses with token counts and validation errors |
| `ai_responses/v1/` | Void responses: a bug in the response schema made them all fail parsing. Kept as a usage record |
| `audits/` | Offline audit of the model's candidate invariants |

Earlier `benchmark/dev_*` and `heldout_2026-11-01T102438Z.*` reports are superseded interim runs, kept unchanged. `dev_2026-11-01T095408Z.*` used the void responses.
