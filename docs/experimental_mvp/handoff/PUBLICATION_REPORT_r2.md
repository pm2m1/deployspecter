# Publication readiness report for the r2 work (2026-11-15). NOT PUSHED

- **Remote:** `pm2m1/deployspecter` (public), branch `research/real-application-mvp`, remote HEAD `c251da0`.
- **Local:** 17 unpublished commits on top of `c251da0`: the 12 audited in `PUBLICATION_REPORT_r1.md` (through
  `c7b74fe`), the 3 r2 commits, the commit adding this report and the verification evidence, and a final manifest
  commit.
- **Push status:** no push without a separate, explicit authorization. Nothing is merged; `main` is not affected.

## Unpublished commits (oldest first)

- **Audited before (r1 sprint):** `2e88c96`, `e10a1d9`, `3d910e2`, `d3e781f`, `e8e9fc2`, `d794a13`, `90a9e89`,
  `0923448`, `0eeecfe`, `a705ea2`, `a6f2bdf`, `c7b74fe`. (That report said "10 + 1" because it was written before its
  own manifest commit; the r1 range was 12.)
- **This sprint:** `9efda6a` (r2 candidate, forms, comparison, time budget, package, study protocol and sample size),
  `9e9bcec` (`prep_r2.py`), `cfd5477` (onb-study/2 code), the commit adding this report and the verification
  evidence, and the manifest commit `MANIFEST_final_6`.

## Audit of the whole unpublished range `c251da0..HEAD`

Scanned blob by blob at HEAD, and every intermediate blob in the range, with a scanner that prints counts and file
names only.

| Check | Result |
|---|---|
| Secrets by exact value (`MODEL_API_KEY`, the GitHub token, the disposable-database password) | none, at HEAD or in any intermediate blob |
| Key and token patterns (private keys, AWS, GitHub, `sk-`, bearer) | none |
| Database URLs with passwords | only the fixture literal `s3cret` in JUnit files, and the wrapper's `{pw}` template |
| GitLab material | none: the mentions are the exclusion rule "anything GitLab-derived is never eligible" |
| The owner's email address in content | none (commit metadata uses the GitHub noreply address) |
| Sealed, reveal or private files; archives; binaries | none |
| Licence headers or copied third-party code | none; the sdists and mirrors used for testing are outside the repository, and `THIRD_PARTY_NOTICES.md` is unchanged |
| Size | the largest file is a 0.58 MB JUnit report; no binaries |
| Personal data | the local home-directory path in test logs, as accepted at earlier publications |
| Governance | nothing signed, adopted or registered; `approval_r1/` and `approval_r2/` forms blank (`build_forms_r*.py --check`) |

**Verdict:** safe to publish as development material, if the owner authorizes a push (an ordinary, non-force push
of this branch only). Nothing in it is confirmatory evidence, an approval or a registration. All results stay
NONBLIND.
