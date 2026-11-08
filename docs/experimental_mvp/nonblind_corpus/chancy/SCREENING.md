# ds-nbc/1 chancy: screening (rule §4, NONBLIND development, 2026-11-08)

All 5 scan candidates (`CANDIDATES.json`) were screened, most recent first; none is NOT SCREENED (capacity 12).
Details, what was read, and every criterion's reason: `SCREENING.json`.

| # | Commit | Subject (short) | E1 | E2 | E3 | E4 | E5 | E6 | First failing | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `b8a77df` | Acquire job row locks in a consistent order to prevent deadlocks (#89) | PASS | PASS | PASS (declared variant adapter) | PASS | PASS | PASS | — | **ELIGIBLE**: case `chancy-b8a77df` |
| 2 | `ea44e76` | race: cancellation before the process executor registers the PID | PASS | **FAIL** | | | | | E2 | in-process/multiprocess signalling (also the unsupported process executor) |
| 3 | `eba1eb0` | don't duplicate logging handlers | PASS | **FAIL** | | | | | E2 | logging |
| 4 | `c71a266` | simplify plugins, shutdown timeout, fix duplicated logging, add AsyncExecutor | PASS | **FAIL** | | | | | E2 | the named defect is logging; the rest are features |
| 5 | `8b8cbb4` | v1 migration down() fix, improved atomic transactions | PASS | **FAIL** | | | | | E2 (E4 for the transactional change) | down(): a Python `KeyError` before any SQL; atomic migrations: no documented contract |

E1 for candidates 2-5: both package trees are pinnable from the public mirror (`SCREENING.json` gives the package tree
hashes); no image was built, since a later criterion fails.

**Cases in ds-nbc/1 for chancy:** `chancy-2895c4a` (the existing case, prior exposure; not a scan candidate because its
subject matches no fix expression) and `chancy-b8a77df` (screened here).

**What was read** (per candidate in `SCREENING.json`): commit messages and diffs of all five; for `b8a77df` also its
test, the CHANGELOG entry and the parent's code path; for `8b8cbb4` also the docs diff and the parent's `app.py`. No
issue or PR discussion was read.
