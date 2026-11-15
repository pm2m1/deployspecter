# saq issue: posted

- **URL:** https://github.com/tobymao/saq/issues/316 (issue #316)
- **Posted by:** pm2m1, 2026-11-15T20:32:21Z (GitHub's `createdAt`; 2026-11-16 local time)
- **Title:** `max_burst_jobs` is exceeded when `concurrency > 1` (up to `max_burst_jobs + concurrency - 1` jobs run)
- **Body:** `UPSTREAM_ISSUE_saq.body.md` at commit `cc41bab`, sha256
  `9a3187695a0e632c66b3747997a3f1751af1d77e4649aa7a63de61f4ea0d937e` (6,348 bytes). The posted body read back
  through the API is identical apart from one trailing newline.
- **Rendering checked** through the API's HTML: 5 section headings, the results table, 2 collapsed blocks
  (`repro_max_burst.py`, `docker-compose.yml`), 4 code blocks, and permalinks to `worker.py` L73-L74, L152-L153 and
  L438-L466 at `4a9ecc2`.
- **Duplicate search just before posting** (`gh search issues --include-prs`, open and closed): "burst",
  "max_burst_jobs", "burst mode" matched #311, #253, #179 and #98, as on 2026-11-15. None reports this.
- **Status:** NOT confirmed by a maintainer. The verdict stays "likely defect, as a mismatch with the documented
  contract; a docs gap instead if the maintainers intend a soft cap".
- **Chancy:** the discussion draft was not posted (owner's decision).

Authorization (verbatim, from the owner's brief of 2026-11-16):
I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to: back up unpushed work to a private repo, submit the GitLab report as a confidential issue, post the saq issue, merge PR #2, publish the draft release, record my owner decisions, and re-plan the benchmark for a single investigator. I have no collaborators, curator, oracle writer or faculty reader. Record this text verbatim in each record you write on my behalf.
