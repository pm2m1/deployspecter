# Sealed corpus registries (empty; no case has been submitted)

There are two append-only, hash-chained registries, kept disjoint by the intake tool:
- `REGISTRY_development.jsonl`: development cases. They may be NONBLIND, may tune budgets and prompts, and never count as confirmatory.
- `REGISTRY_confirmatory.jsonl`: sealed cases from an independent curator only. They are frozen (`corpus_intake.py freeze`) before any evaluation run.

Neither file exists yet, because no curator has submitted a case. The procedure is in `research_tools/corpus_intake.py` (module docstring) and `handoff/send/CURATOR_PACKAGE_v2.md`.

**Rules the tool enforces:**
- a registered case can't be replaced or edited, because any edit breaks the chain;
- nothing is added to a split after its freeze;
- a case, or any revision archive, can't appear in both splits;
- frozen development and held-out applications can never be confirmatory;
- the runner's case directory carries no commit id or buggy/fixed mapping, because the revisions are opaque `R1`/`R2` in random order;
- run identifiers are the sha256 of (manifest, registry head, protocol, code revision, policy), and need a frozen registry.
