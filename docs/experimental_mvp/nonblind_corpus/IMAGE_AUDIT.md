# Image-content audit (2026-11-08), after the stale-build-context finding

**Why.**
- `sealed_evaluation.normalize_archive` sets every file's mtime to 0.
- The saq agent found that Docker's build-context sync can then reuse a stale file of the same size from an
  earlier build. The result is an image whose tree isn't its archive's, with no error.

So every runner image used by a ds-nbc/1 case was compared, file by file (sha256), with **both** archives of its
case. The tool is `research_tools/nbc/image_audit.py`.

| Image | Used by | Exact match | Differs from the other tree in |
|---|---|---|---|
| `ds-mvp-iso:ca248e34599d411b` | chancy-2895c4a (Track A and B) | R1 (80f570f): 171 files | 9 files |
| `ds-mvp-iso:27f0400e25f9310b` | chancy-2895c4a (Track A and B) | R2 (2895c4a): 172 files | 9 files, +1 |
| `ds-mvp-iso:37649dfc8204a5e2` | chancy-b8a77df | R1 | 3 files |
| `ds-mvp-iso:7b1669be086fe25a` | chancy-b8a77df | R2 | 3 files |
| `nbcpgq-b5ee3715…`, `nbcpgq-69efb54d…`, `nbcpgq-d2e24069…` | pgqueuer-8dfadf4 (bring-up, Track A, Track B) | R1 | 3 files |
| `nbcpgq-2f876a0f…`, `nbcpgq-bf0fb106…`, `nbcpgq-7275f9b4…` | pgqueuer-8dfadf4 | R2 | 3 files |
| `saq-077207544fdc2e94` / `saq-e82feecb90222457` | saq-c570ba4 | R1 / R2 | |
| `saq-32e39673189eed1b` / `saq-d8eb672b70a5bf5e` | saq-addee31 | R1 / R2 | |
| `saq-bee03b617a6639a5` / `saq-f3dbe0bf5cf35533` | saq-ad8d1c8 | R1 / R2 | |
| `saq-f143851265a80bd6` | saq bring-up only (the released 0.26.5 tree, not a case archive) | none of the case trees (expected) | |

**Result.**
- Every image matches **exactly one** of its case's two trees: 0 differing, missing or extra files.
- Each case has one image per tree.
- So the stale-context hazard did not affect any case evidence here. The saq agent's earlier stale image (its
  erratum E1) produced only REFUSED runs, which are preserved in `saq-addee31/reproduction_1`.

**Not done.** The hazard itself is unfixed in the shared runner. Any future build from normalized trees should stamp
fresh mtimes, or verify its image the same way.
