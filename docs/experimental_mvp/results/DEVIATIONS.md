# Deviations from the registered plan (https://osf.io/34ytz)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Every entry is written before the departure takes effect. Entries are only ever added.

## D1. The PostHog mirror is cloned in steps (2026-11-17)

- **Plan:** `benchmark_prep/RUNBOOK.md` §2 mirrors the 20 candidate repositories with
  `select_cases.py fetch`, which runs one `git clone --mirror` per repository.
- **What happened:** `PostHog/posthog` failed five times in a row (2026-11-17, 01:5x–03:05 UTC). Each failure was a
  connection reset or HTTP/2 stream cancel partway through the single pack (`curl 56`, `curl 92`, `early EOF`). No
  partial mirror was left behind. Zulip and Sentry were mirrored. `fetch` stops at its first failure, so the 17
  repositories after PostHog weren't mirrored yet.
- **Departure:** the PostHog mirror is built at the same path (`~/ds-mirrors/PostHog__posthog.git`) in steps:
  1. `git clone --mirror --depth 1` from the same URL;
  2. repeated `git fetch --deepen=<n>`, each step retried on a network error;
  3. `git fetch --unshallow`;
  4. `git fsck --connectivity-only`, which must pass. The repository must not be shallow at the end.

  Then the frozen `select_cases.py fetch` is run unchanged. It skips existing mirrors, so it mirrors the remaining
  17. `select` does not run until all 20 mirrors exist.
- **Expected effect:** none on what counts as a case, an arm or a budget. The result is a complete mirror clone
  with the same refspec (`+refs/*:refs/*`, `mirror = true`) and the full history. `select` reads only the default
  branch's history and trees, which are identical to a one-shot mirror clone's. No frozen file is changed.

### D1, addendum (2026-11-17, before it takes effect)

The first stepwise attempt left a depth-1 mirror at the path. Its `--deepen` steps all hit a client-side
low-speed timeout (`curl 28`) while the server was still building the pack. The deepening continues with smaller
steps and no low-speed limit. Because the PostHog path now exists, the frozen `fetch` skips it, and it runs for the
other 17 repositories at the same time instead of afterwards. `select` still does not run until the PostHog mirror is
complete (not shallow) and `git fsck --connectivity-only` passes. Expected effect: none.

### D1, addendum 2 (2026-11-17, before it takes effect)

From 04:04 to 05:50 UTC, no `--deepen=500` step on PostHog completed. The failures were connection resets, timeouts,
two DNS outages and a bad pack header. The reason is the mirror refspec: it includes GitHub's `refs/pull/*` (Zulip's
mirror, for comparison, has 36,129 of them), and every deepen step deepens all of those refs.
- **Departure:** the PostHog mirror is rebuilt in a separate scratch directory under `~/ds-mirrors/`. It fetches
  `refs/heads/*` and `refs/tags/*` only, without `refs/pull/*`:
  1. depth 1 first;
  2. then `--deepen` steps;
  3. then `--unshallow`.

  `HEAD` points at the remote's default branch (`master`). It must end up not shallow and pass
  `git fsck --connectivity-only`. Then it replaces the depth-1 directory at `~/ds-mirrors/PostHog__posthog.git`. The
  other 19 mirrors are one-shot `select_cases.py fetch` mirrors, all verified not shallow.
- **Expected effect:** none on selection. `select_cases.py` reads only the default branch:
  - `git log --no-merges --first-parent … HEAD`;
  - `diff-tree`, `diff`, `show` and `ls-tree` of those commits and their parents.

  These are fully present. PostHog's mirror lacks only the pull-request refs, which `select` never reads. This is
  recorded so that a re-run of `select` on a one-shot mirror can be compared.
