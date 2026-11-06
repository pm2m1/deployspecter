# Invitation package for a prospective label curator (v3: cover and bundle; not sent)

*This package names no one, and has not been sent. The owner sends it, after choosing a person who has never
contributed to DeploySpecter, its Muse integration or its deterministic generators.*

## Read in this order

1. `handoff/send/OVERVIEW.md`: what the project is, what is demonstrated and what is not, and every role.
2. `handoff/templates/participation_declaration.md`: **complete and return it before you receive any case
   material.**
3. `handoff/send/CURATOR_PACKAGE_v2.md`: your instructions, which are unchanged. The one correction is below.
4. `protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md`, sha256 `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`
   (`protocol/approval/CANDIDATE_SHA256`): the rules you would accept.
5. `protocol/approval/CURATOR_ACCEPTANCE.md`: your form, with rulings #1, #2, #6 and #19. Sign it only after
   checking the sha256.

## Correction to v2: normalize every archive before submitting it

`git archive <commit>` writes the commit id into the tar's pax header, and stamps each file with the commit time,
which tells the later (fixed) revision from the earlier one. The evaluator package builder refuses such archives.
Before `corpus_intake.py submit`, run:

    python research_tools/evaluator_sandbox.py normalize RAW.tar NORMALIZED.tar   # exit 0 and "remaining_metadata_leaks": []

Then reference `NORMALIZED.tar` in the submission's `revisions[].archive`. Keep the raw archives with your reveal
files, outside every repository.

## Tools you use (in the bundle)

| Tool | For |
|---|---|
| `research_tools/corpus_select.py`, `CORPUS_MANIFEST.json` | applying the frozen selection rules (file names only) |
| `research_tools/evaluator_sandbox.py normalize` | archive normalization (above) |
| `research_tools/corpus_intake.py submit / register / freeze / verify-registry` | sealed submission, the registry and the freeze |
| `research_tools/seal_labels.py leak-check` | the leak check over frozen runner inputs and model prompts: hits must be 0 |
| `handoff/templates/labels.json` | the label fields |

## What you return

- the signed declaration, before any case work;
- the signed acceptance form;
- per case: the submission JSON (it stays with you) and the committed case directory;
- the frozen registry's head hash;
- the corrected-version validation result ("holds" or "doesn't hold");
- the leak-check transcript (hits = 0), dated before the first evaluation run.

**What you must not share:** the reveal files and raw archives, with anyone, until the owner authorizes scoring.
The sealed workflow has been rehearsed on synthetic data only (`tests/experimental/test_sealed_workflow_offline.py`,
`test_evaluator_access_offline.py`). No real case exists.
