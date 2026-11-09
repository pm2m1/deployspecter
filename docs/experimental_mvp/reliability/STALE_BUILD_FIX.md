# Stale build-context hazard: fixed in the shared runner (2026-11-10)

**The hazard** (`nonblind_corpus/IMAGE_AUDIT.md`, saq erratum E1).
- `sealed_evaluation.normalize_archive` sets every file's mtime to 0.
- Docker's build-context transfer can then reuse an earlier file of the same size and mtime, so an image can hold a
  tree other than its archive's, with no error.
- Image tags are content hashes of the staged files and don't depend on mtimes.

**The fix** (`research_tools/isolated/run_isolated.build_image`).
1. **Cache isolation by timestamp:** after the staging step, and after the content-hash tag is computed, every
   context file and directory gets this build's own mtime (`stamp_fresh_mtimes`). A stale file can no longer match by
   size and mtime. The tag stays the same, so existing tags and evidence are unaffected.
2. **Pinned-source verification:**
   - After `docker build`, a network-less container lists the sha256 of every file under `/app`
     (`image_tree`).
   - It is compared with the staged context (`context_tree`). Every staged file must be present with the same
     content, and nothing extra may appear (`image_tree_problems`).
   - On any difference, the tag is removed and the build refused.
   - The archive's own sha256 is still checked before staging, as before.

**Tests.**
- `tests/experimental/test_isolated_image_verify_offline.py` (4 offline tests).
- `tests/thirdparty_isolated` (14 live tests): every image it builds goes through the verification.

**Scope.**
- Images built through `build_image` are covered: chancy sdists and commit trees, pgqueuer 1.6.0, procrastinate,
  and the confirmatory `chancy-sv/1`.
- `research_tools/nbc/integrations/saq_common.py` already stamped mtimes and verified its images.
- **Residual:** `research_tools/nbc/integrations/pgqueuer_cancel.py` builds its own image without these steps. It is
  hash-pinned in the frozen ds-nbc/2 manifest, so it is left unchanged. Its images were audited after the fact and
  match their archives (`nonblind_corpus/IMAGE_AUDIT.md`, `nbc2/AUDIT_2.md`). Any future use must go through
  `build_image`, or be followed by `research_tools/nbc/image_audit.py`.

**Historical evidence:** unchanged. No frozen manifest, ledger or run directory was modified.
