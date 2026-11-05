# Package A: independent label curator (v2, ready to send; NOT sent; no recipient named)

**Your role:** choose historical upgrade-related defects, and hand the evaluator *sealed* cases. It must be able to run both revisions without knowing which is buggy. Estimated effort: 6–12 hours for 3–5 cases. You may stop at any time.

**Before you start:**
- check that the owner has signed `protocol/approval/OWNER_APPROVAL.md`, including rulings #1, #2, #3, #19 and #21;
- sign `protocol/approval/CURATOR_ACCEPTANCE.md` after checking the candidate's sha256 (`approval/CANDIDATE_SHA256`).

## 1. Selection (rules fixed; never changed after you see any result)

1. **Candidates.** Only from `CORPUS_MANIFEST.json`, admitted by the frozen selection tool (`research_tools/corpus_select.py`, sha256 `3eaa449e…`), as amended by the *accepted* rulings. Read no migration file of a candidate whose admission depends on a ruling not yet accepted.
2. **Excluded:** chancy, procrastinate, pgqueuer, inventree, paperless-ngx, langgraph-checkpoint-postgres, saleor, healthchecks, netbox and authentik. The intake tool refuses them for the confirmatory set.
3. **The defect.** It must involve a schema migration, a backfill, or code that runs across an upgrade, and have a public fix commit. Its upgrade path must be one the application **documents** (#19): single-version, rolling or blue-green. Cite the document.
4. **Order.** Choose cases before anyone runs anything on them. Never replace a registered case: the registry refuses it.

## 2. Exposure rules

- Record everything you read for each case: issues, diff, tests, changelog, discussions.
- Never pass to the developers, the Muse pipeline or the evaluation runner: fixes, commit ids, the buggy/fixed mapping, fix paths, keywords, or your reveal file.
- Never look at DeploySpecter outputs for your cases before scoring.
- You may not also be the oracle author for the same case.

## 3. Deliverable format: one submission per case (keep it private)

```json
{"case_id": "opaque-id", "application": "name", "repository": "https://…", "split": "confirmatory",
 "revisions": [{"archive": "path/to/rev.tar", "commit": "<40-hex>"}, {"archive": "…", "commit": "<40-hex>"}],
 "buggy": 0, "fix_paths": ["…"], "defect_summary": "one paragraph", "keywords": ["…"],
 "contract": {"kind": "single-version|rolling|blue-green", "citation": "doc path#anchor, at the pinned tag"},
 "oracle_spec_sha256": "<the oracle author's spec sha256, from the owner>",
 "exposure": {"curator": ["what you read"], "public": ["what the runner may be told"]}}
```

Each archive is `git archive --format=tar <commit> <package dir>`, built in a clean checkout. Then:

```bash
python research_tools/corpus_intake.py submit --submission SUB.json --case-dir CASES/<id> --reveal ~/private/<id>.reveal.json
python research_tools/corpus_intake.py verify --case-dir CASES/<id>
```

Give the owner `CASES/<id>/` only: `case.json`, `R1.tar` and `R2.tar`, in an order the tool randomizes. Keep the reveal file private and backed up. The tool refuses to write it inside the repository.

## 4. At the freeze, and after

- **Validation.** On the owner's request, trigger the corrected-version validation run on the revision you know is fixed, and report only "holds / doesn't hold" (Package B §3).
- **Leak check.** Run it over the runner's frozen inputs, and report the transcript:
  `python research_tools/seal_labels.py leak-check --reveal ~/private/<id>.reveal.json --bundle RUNNER_INPUTS/`
- **Scoring.** Release the reveal only at authorized scoring. The owner then runs `corpus_intake.py score`.

## 5. Acceptance checklist (the owner ticks it on receipt)

- [ ] `CURATOR_ACCEPTANCE.md` signed, with the sha256 matching `CANDIDATE_SHA256`
- [ ] each case directory passes `corpus_intake.py verify`
- [ ] each case is registered in `REGISTRY_confirmatory.jsonl`, and `verify-registry` reports the chain intact
- [ ] no excluded application; every contract is cited; every case's `oracle_spec_sha256` matches an oracle spec delivered under Package B
- [ ] the curator confirms each reveal is held outside every repository
- [ ] after the registry is frozen: the leak-check transcript shows hits = 0, dated before the first evaluation run
