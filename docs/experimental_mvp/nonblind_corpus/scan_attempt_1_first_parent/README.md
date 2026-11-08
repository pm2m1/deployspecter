# Scan attempt 1 (superseded): scanner defect, preserved

The first scanner run used `git log --first-parent`. That walks only the default branch's first-parent chain, so it
misses every fix commit merged through a pull request. procrastinate, which merges pull requests, showed 0
candidates in 118 first-parent commits.

`SELECTION_RULE.md` §2 says "commits reachable from the default branch", so this was a scanner defect, not a rule
choice. The scanner was corrected to walk every reachable non-merge commit, and the scan re-run, **before any
candidate was screened** or any diff read. This attempt's output is kept unchanged.
