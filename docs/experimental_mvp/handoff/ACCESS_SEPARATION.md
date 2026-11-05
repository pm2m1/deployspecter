# Access and artifact separation

1. **Locations.**
   - Curator material (reveal files, defect reports, fix diffs) lives outside this repository. `seal_labels.py` refuses to write a reveal file inside it.
   - Oracle sources live in their own location until the freeze, and enter the repository only as hashes plus the frozen oracle files.
2. **Runner inputs are one frozen directory.** The curator's `leak-check` runs over exactly that directory. Any later change to it voids the check.
3. **One-way flow.** Curator → (seal) → runner. Oracle reviewer → (hashes) → freeze. Runner → (result bundle) → scoring. No reverse flow before scoring.
4. **Triage of corrected-version violations (proposed decision #8).**
   - Reviewers A and B each classify every violation independently, as a false flag, an oracle error or a new finding, using `templates/triage_record.json`.
   - The adjudicator, named before the freeze, rules on each disagreement.
   - All three verdicts are kept.
5. **Exposure ledger.** Every person records, per case, what they read, when (draft v2 §10). A case where a runner-side person saw a label is NONBLIND.
6. **Independence check.** A role held by someone who has seen the fix, Muse outputs or the tool's internals is reported as such. Without independent people the evaluation is exploratory only.
