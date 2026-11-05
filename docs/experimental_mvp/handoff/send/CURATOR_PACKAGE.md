# DeploySpecter: independent label-curator package (ready to send; NOT sent)

**Status.** Prepared 2026-11-06. **No recipient has been chosen, contacted or agreed to anything.** The owner sends this package, if and when they choose a curator. Nothing here is a commitment by anyone named, because nobody is named.

---

## Cover note (for the owner to complete and send)

> Dear [CURATOR NAME],
>
> We'd like to ask you to act as the independent label curator for a prospective evaluation of DeploySpecter, a tool that tests PostgreSQL application upgrades by running old and new code together under a controlled scheduler. Your role is to choose historical upgrade-related defects from a fixed candidate list, record their buggy and fixed commits, and **seal** those labels before any part of the evaluation runs, so that the tool's developers and its AI component never see them.
>
> The attached package describes the work (an estimated 6–12 hours for 3–5 cases), what you receive, what you must keep private, and how to return your results. You may decline at any point.
>
> [OWNER NAME], [DATE]

---

## 1. What you receive

| File | Why |
|---|---|
| `docs/experimental_mvp/CORPUS_MANIFEST.json` | the pinned candidate applications |
| `docs/experimental_mvp/results/corpus_selection_1/selection_report.json` (tool `3eaa449e…`) | the frozen selection rules applied: which candidates are eligible, and which need a ruling |
| `docs/experimental_mvp/protocol/PROPOSED_SELECTION_RULE_AMENDMENTS.md` and `DECISIONS_003_v2.md` (#1–#3, #19) | the rulings needed before you may read some candidates' migration files |
| `research_tools/seal_labels.py` | the sealing and leak-check tool |
| `docs/experimental_mvp/handoff/templates/labels.json` | the label template |
| this package | the procedure |

Verify the tool's integrity before use: `shasum -a 256 research_tools/seal_labels.py`, and compare it with the value the owner sends you separately.

## 2. What you must not see, and what you must never pass on

- Any DeploySpecter evaluation output: the Muse proposals, run reports or traces for your cases, before the reveal.
- Never give the developers, the Muse pipeline or the evaluation runner any of: fix commits, fix diffs, defect reports, issue links, keywords, or your reveal file.
- You may read anything public about the candidate applications. Record what you read (§5).

## 3. Before you start

1. **Declarations.** Sign the conflict-of-interest and independence declaration (§6).
2. **Rulings.** Confirm with the owner which protocol rulings (#1–#3, #19) have been ACCEPTED. A candidate marked `NEEDS_PROTOCOL_RULING` may be used only after its ruling is accepted, and **before** you read any of its migration files.

## 4. The work

For each case, from candidates with role `PROSPECTIVE_CANDIDATE` and an admitted migration area:

1. **Find one historical defect** that involves a schema migration, a backfill, or code that runs across an upgrade, and that has a public fix commit.
   - Prefer a defect whose upgrade path the application **documents as supported** (#19), such as a rolling or blue-green deploy, or a single-version upgrade.
   - Record the documentation citation.
2. **Record**, in `labels.json`:
   - `case_id`, `repository`;
   - `buggy_commit` (the fix's first parent), `fixed_commit`, `fix_paths`;
   - a one-paragraph `defect_summary`;
   - `keywords`: words whose presence in the runner's inputs would reveal the defect;
   - `exposure`: what you read.
3. **Seal**, keeping the reveal file outside any repository:
   ```bash
   python research_tools/seal_labels.py seal --labels LABELS.json --seal SEAL.json --reveal ~/private/REVEAL.json
   ```
4. **Return only `SEAL.json`** to the owner. Keep `REVEAL.json` private and backed up.
5. **When the owner tells you the runner's inputs are frozen,** run the leak check over the bundle they give you:
   ```bash
   python research_tools/seal_labels.py leak-check --reveal ~/private/REVEAL.json --bundle RUNNER_INPUTS/
   ```
   Report "hits: 0", or the hit list. A hit blocks the evaluation. The inputs are fixed, never the labels.
6. **At authorized scoring only:** release `REVEAL.json`. The owner runs `seal_labels.py verify`.

A changed label after sealing is a new seal, and is reported as one.

## 5. Exposure record (per case, inside `labels.json`)

Record what you read: the issue tracker, the fix diff, tests, the changelog, and discussions. This tells the analysis which cases might have leaked through public material.

## 6. Declaration (sign and return with `SEAL.json`)

> I am not a developer of DeploySpecter, its Muse integration or its deterministic generators. I have not seen any DeploySpecter evaluation output for these cases. I will not disclose my labels, my reveal file or my keywords to them before authorized scoring. I have recorded my exposure for each case.
>
> Name: ____________ Signature: ____________ Date: ____________

## 7. What makes this decision ACCEPTED, IMPLEMENTED and VERIFIED (`DECISIONS_003_v2.md` #6)

| Status | Evidence |
|---|---|
| ACCEPTED | your signed declaration, and the owner's written acceptance of this procedure |
| IMPLEMENTED | a committed `SEAL.json` per case, and your statement that the reveal is held outside every repository |
| VERIFIED | a leak-check transcript (hits 0) dated before each arm's first run, and `verify` matching at scoring |
