# Publication readiness report for the r1 work (2026-11-10). NOT PUSHED

- **Remote:** `pm2m1/deployspecter` (public), branch `research/real-application-mvp`, remote HEAD `c251da0`.
- **Local:** 10 + 1 unpublished commits on top of `c251da0`. The 11th is this report and the verification evidence.
- **Push status:** no push in this sprint without explicit authorization. `main` is not affected.

## Unpublished commits (oldest first)

`2e88c96`, `e10a1d9`, `3d910e2`, `d3e781f`, `e8e9fc2`, `d794a13`, `90a9e89`, `0923448`, `0eeecfe`, `a705ea2`, and
the commit adding this report.

An earlier report said "8 local commits" for `c251da0..90a9e89`. The actual number was **7**: a miscount in that
report, not a missing commit.

## Audit of the unpublished content

| Check | Result |
|---|---|
| Secrets (exact values: `MODEL_API_KEY`, GitHub token, live disposable-database password; plus token and key patterns) | none |
| Database URLs with passwords | only test-fixture literals in JUnit files |
| GitLab material | none: the four mentions are exclusion rules ("anything GitLab-derived is never eligible") |
| Sealed, reveal or private files; archives; binaries | none |
| Size | 42 new blobs, 1.6 MB; the largest is a 0.58 MB JUnit file |
| Third-party material | none new; existing notices unchanged (`THIRD_PARTY_NOTICES.md`) |
| Provenance | every file was written in this project; no copied third-party code |
| Personal data | the local home-directory path in test logs, as accepted at earlier publications |
| Governance | nothing signed, adopted or registered; `approval_r1/` forms blank (`build_forms_r1.py --check`) |

**Verdict:** safe to publish as development material, if the owner authorizes a push (ordinary, non-force, this
branch only). Nothing in it is confirmatory evidence or an approval.
