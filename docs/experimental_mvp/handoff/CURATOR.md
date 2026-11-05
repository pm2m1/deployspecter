# Curator brief

**Purpose:** choose eligible historical mixed-version migration defects, and seal their ground truth before any evaluation arm runs.

1. **Eligibility.** Start from `CORPUS_MANIFEST.json` and `research_tools/corpus_select.py`, with the frozen rules unchanged.
   - Use only cases with `PROSPECTIVE_CANDIDATE` and a selected migration area, or with a written reviewer ruling on `NEEDS_PROTOCOL_RULING` (see `protocol/PROPOSED_SELECTION_RULE_AMENDMENTS.md`).
   - Development cases (pgqueuer, chancy) are NONBLIND and are never labelled for confirmatory use.
2. **Select defects.** Record per case, with `templates/labels.json`:
   - the exact buggy and fixed commits;
   - the fix's file paths;
   - a one-paragraph defect summary;
   - keywords whose presence in runner inputs would leak the defect;
   - your own prior exposure.
3. **Seal:**
   `python research_tools/seal_labels.py seal --labels LABELS.json --seal SEAL.json --reveal <outside-the-repo>/REVEAL.json`
   - Give the runner only `SEAL.json`.
   - Keep `REVEAL.json` private until authorized scoring.
4. **Leak check before the freeze:**
   `python research_tools/seal_labels.py leak-check --reveal REVEAL.json --bundle <runner's frozen inputs>`
   - Any hit blocks the run. Fix the inputs, never the labels.
5. **Never:** give fixes, defect reports or labels to the Muse pipeline, the deterministic generators or the runner; or change a label after sealing. A change is a new seal, reported.
