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

## E2 (2026-11-08, after `reproduction_2`): the v1 shape can't give an R1 verdict; v2 decides the same defect from state

**What happened.** `reproduction_2` (integration v1 with the E1 build fix): R2 HOLDS in 12 of 12 schedules
(3 replicates × 4). R1 was REFUSED in its first schedule of each replicate (3 runs), so no R1 verdict exists. In
every R1 run the second queue's `init_db` got `false` from `pg_try_advisory_lock`, skipped its migrations, and its
first `enqueue` failed with `UndefinedTable` (`saq_alt_jobs`, or `saq_jobs` in W2). That is the upstream failure,
but it is not evidence here: the run wasn't SOUND.

**Cause (instrument).** The failing INSERT is rejected while the server parses it (extended protocol), so the server
log has its ERROR row but no statement row. `corroborate.py` pairs an ERROR only with a traced error statement whose
own statement row it has just matched, so it reports "an ERROR the trace doesn't record" and "1 traced operation(s)
never reached the log". This is a limitation of the shared corroborator (not changed here; reported).

**Attempt 3 (`reproduction_3`): new files, nothing earlier edited.** Integration `saq_two_queues_addee31_v2.py`
(`saq-two-queues-addee31/2`, shape `two_queues` v2 in `saq_shapes.py`, added without changing v1), oracle
`oracle_v2.py`, spec `case_spec_v2.json`. The deployment, the gate, the pinned trees and the table-set grid are the
same. The one change: the second queue only connects and issues nothing else, so no statement can fail at parse.
The property moves from the downstream symptom (v1 Q1: its `enqueue` succeeds) to the skipped migration itself (v2
Q1: after its `connect()` returned, its documented `jobs_table`, `stats_table` and `versions_table` exist). Both come
from the same docstring and the same maintainers' statement.

**Disclosure.** v2 was written after seeing attempt 2. Attempt 2 showed what R1 does in this shape: it skips the
migration (and R2 holds). v2 is therefore not a pre-registered test of this case's R1 behaviour; it is a
re-encoding that makes the same predicted outcome corroborable. Attempts 1-2 stay as they are.
