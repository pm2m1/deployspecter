# Historical case `chancy-2895c4a`: results (NONBLIND development case, 2026-11-05)

**Result: the historical defect is reproduced on real code, at commit level, and its real fix removes it.**

All 34 runs are SOUND: isolated, coordinated to completion and corroborated against PostgreSQL's server log. There are 0 typed parameters mismatched and 0 unchecked.

This is **not** blind, confirmatory or independent evidence:
- the case was chosen after reading the fix;
- the oracle is maintainer-sourced but developer-encoded;
- nothing here counts toward RA-Q3 or Gate A2.

## Provenance

| | |
|---|---|
| Code revision of every final run | `602c6ed` (clean) |
| Frozen before any run (`cf4b303`) | `CASE.md` `a342de43…`, `historical_oracles.py` `db9bc5fe…`; the same hashes are recorded in every report |
| Buggy parent `80f570f` | tree `a5a389c4…`, `git archive` tar `8747cdc8…`, image `ds-mvp-iso:7b5a482a8182b4f5` (`sha256:ca95c8cd…`). Source verified inside the container (`git-80f570f`) |
| Fix commit `2895c4a` | tree `bdf46b6b…`, tar `cbc461d2…`, image `ds-mvp-iso:ccb9a4b362ffe4f7` (`sha256:a646e1de…`) |
| Releases | 0.25.1 (sdist `264ed4f7…`) and 0.26.0 (sdist `ac98b9c5…`) |
| Schema | migrated by **each revision alone**, fresh. The host checked the version before every run: 0.25.1 → 6, `80f570f` → 7, `2895c4a` and 0.26.0 → 8. No mixed-version operation is involved |
| Reproduce the trees | `research_tools/isolated/fetch_pinned.sh DIR --with-git` (it verifies the tree hashes and the tar sha256) |

## Results

| Revision | Schedule | Runs | SOUND | Case oracle (H1/H2) | Final state of the cancelled job |
|---|---|---|---|---|---|
| `80f570f` (buggy parent) | forced (the maintainers' test order) | 3 | 3 | **VIOLATED 3/3** (H1) | `succeeded`, attempts 1: the cancel was overwritten |
| `2895c4a` (fix commit) | forced | 3 | 3 | **HOLDS 3/3** | `failed`, attempts 0, `claim_id` NULL, `completed_at` set |
| 0.25.1 (last release before the fix) | forced | 3 | 3 | **VIOLATED 3/3** | `succeeded`, attempts 1 |
| 0.26.0 (first release with the fix) | forced | 3 | 3 | **HOLDS 3/3** | `failed`, attempts 0, `claim_id` NULL |
| `80f570f` | forced, `max_attempts = 3` (the test's parameter) | 1 | 1 | **VIOLATED** | `succeeded` |
| `2895c4a` | forced, `max_attempts = 3` | 1 | 1 | **HOLDS** | `failed` |
| `80f570f` | seeded exploration, cancel after claim (seeds 0–9) | 10 | 10 | **VIOLATED 9/10**; seed 2 HOLDS | 9 × `succeeded`; seed 2 `failed`, attempts 1 |
| `2895c4a` | seeded exploration (seeds 0–9) | 10 | 10 | **HOLDS 10/10** | 9 × `failed` with attempts 0; seed 2 `failed` with attempts 1 |

**Forced schedule.** It was the same in all 12 forced runs across the four revisions: push at grant 17, the worker's claim at 23 (1 row), the cancel at 24 (1 row), and the completion flush at 43. Only the revision changes the outcome. The commit pair, which differs by a 10-file change, carries the attribution; the release pair (137 files) agrees, but is confounded.

**Seeded exploration, paired by seed.** There are 9 discordant pairs (parent violated, fix held) and 0 the other way. The first detection was at seed 0, which in time-to-detection terms is the first schedule.

Seed 2 is not a miss. Its schedule placed the completion flush before the cancel, so the race window never opened: the job succeeded, then the cancel marked it `failed`, in both revisions. H0 held, so the handler ran once, but this schedule doesn't exercise the defect.

This is one case, so no test statistic is reported (draft §8 requires at least 10).

## What this shows, and what it doesn't

**It shows:**
- On the real historical code, a cancellation followed by the already-queued completion flush makes the job end `succeeded`. The maintainers' own regression test (`test_cancellation_fences_execution_completion`, added with the fix) asserts that this must not happen.
- The same statement schedule on the fix commit leaves the job `failed` with its claim invalidated.
- All of it was executed through Chancy's public `push`, `cancel_job` and `Worker` APIs, in a restricted container, with every statement reconciled against the server log.

**It doesn't show:**
- **Independent discovery.** The fix was read first.
- **Independent oracle authorship.** The oracle was encoded by the harness developer from the maintainers' test.
- **The notifications path.** Notifications are disabled. Code reading argues that the overwrite would occur with them too, for this schedule: see CASE.md, competing explanation 1. That is not executed evidence.
- **Anything about Muse.** No model was used.

## Evidence (sha256 over all files, sorted)

| Directory | Digest | Size |
|---|---|---|
| `runs/bringup` | `87755514b75043e5` | 432K |
| `runs/final_forced_git-80f570f` | `1a6a410c306ad093` | 628K |
| `runs/final_forced_git-2895c4a` | `7f11029bf5d78fbd` | 640K |
| `runs/final_forced_0.25.1` | `d57bfab7793d66da` | 736K |
| `runs/final_forced_0.26.0` | `786a141f84a46c26` | 640K |
| `runs/final_forced_max3_git-80f570f` | `effdf68032b36588` | 212K |
| `runs/final_forced_max3_git-2895c4a` | `ddfc978f702c7426` | 216K |
| `runs/final_explore_git-80f570f` | `8c0cca4681271a3c` | 2.1M |
| `runs/final_explore_git-2895c4a` | `4f759f4bdd4dd2b0` | 2.1M |

## Reproduce

```bash
cd ~/Desktop/deployspecter-real-app-mvp && research_tools/mvp_pg.sh start
research_tools/isolated/fetch_pinned.sh SDISTS --with-git
python research_tools/isolated/run_historical.py --source SDISTS/chancy-git-80f570f.tar --schedule forced --out OUT/buggy
python research_tools/isolated/run_historical.py --source SDISTS/chancy-git-2895c4a.tar --schedule forced --out OUT/fixed
python research_tools/isolated/run_historical.py --source SDISTS/chancy-git-80f570f.tar --schedule explore --seeds 0-9 --out OUT/explore
python research_tools/coordinated_summary.py --interleaving OUT/buggy/00_alternate
research_tools/mvp_pg.sh stop
```
