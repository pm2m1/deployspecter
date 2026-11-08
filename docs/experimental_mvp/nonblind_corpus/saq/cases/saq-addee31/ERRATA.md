# Errata (NONBLIND development case)

## E1 (2026-11-08, before any SOUND run of this case): stale file in the runner image; integration build fixed

**What happened.** In `reproduction_1` (of `saq-addee31`, the first case run), every schedule was REFUSED by the
instrument: the container's pin check (`saq_coord_adapter.verify_pinned`) found `saq/__init__.py` with the v0.26.5
content (`__version__ = "0.26.5"`, sha256 `67e07915…`) instead of the tree's (`0.26.4`, `4d21215e…`). No saq code ran
and no oracle was consulted.

**Cause.** `normalize_archive` gives every file mtime 0. Docker's build-context sync reused an earlier build's
same-size, same-mtime file (`0.26.4` and `0.26.5` have the same length) instead of sending the new one. The bring-up
build (v0.26.5) had seeded it. The in-image check was the only thing that caught it.

**Fix** (`research_tools/nbc/integrations/saq_common.py`, harness only): every context file gets the build's own mtime,
and the built image's `/app/src/saq` tree is streamed out of a created, never-started container and compared file by
file with the archive before the image is used (`verify_image_tree`). Verified offline on all six case trees and the
bring-up tree; the stale image fails the check (negative control). The stale images were removed.

**What did not change:** the workloads, the oracle, `CASE.md`, the case spec, the pinned trees, the adapter.
`saq-addee31/reproduction_1` was interrupted (SIGINT, `INTERRUPTED.json`) after 3 REFUSED replicate-slots and is kept
as is. `saq-ad8d1c8` and `saq-c570ba4` never started a `reproduction_1`. The next attempt of every case is
`reproduction_2` (reason: this erratum).
