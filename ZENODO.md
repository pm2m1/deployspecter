# Archiving the research snapshot on Zenodo (steps for the owner; not done)

Goal: a DOI for the snapshot `research-2026-11-15` and its 30 evidence bundles. Zenodo deposits are permanent:
after you publish, the files can't be changed or removed, only superseded by a new version. Do these steps
yourself, signed in with your own account.

## Choose a route

| | A. GitHub integration | B. Manual upload (recommended) |
|---|---|---|
| What is archived | the source archive GitHub makes for the tag (the whole repository at `493b9ae`) | the 30 `.tar.zst` bundles and `SHA256SUMS` from the release |
| Includes the evidence bundles? | **no**: release assets aren't archived, only the source archive | yes |
| When it triggers | when a release is **published** after the integration is switched on | when you press Publish |
| Metadata | from the release, or from a `.zenodo.json` / `CITATION.cff` file in the repository | entered in the form |

Route B archives exactly what `EVIDENCE.md` describes. Route A can be added for the source; if you want it, switch
it on **before** you publish the draft release, because only releases published afterwards are archived.

## Route B: manual upload

1. Sign in at https://zenodo.org (log in with GitHub or ORCID).
2. Download the release assets: publish the draft release first, or download them from the draft while signed in
   to GitHub. Check them: `shasum -a 256 -c SHA256SUMS` must print OK for all 30 bundles.
3. Zenodo → **New upload**. Drag in the 30 `evidence-*.tar.zst` files and `SHA256SUMS`.
4. Under the DOI field, choose **"Reserve DOI"** if you want to put the DOI into the GitHub release notes before
   publishing either.
5. Fill in the metadata:
   - **Resource type:** Dataset.
   - **Title:** `DeploySpecter research evidence, snapshot 2026-11-15`.
   - **Creators:** Parth Mishra (add your ORCID and affiliation if you have them).
   - **Description:** the first paragraph of the release notes, including "All results are NONBLIND or
     EXPLORATORY", plus: "Each bundle's FILES.tsv lists the git blob id of every file; `tools/package_evidence.py
     verify` checks them against tag research-2026-11-15 of https://github.com/pm2m1/deployspecter."
   - **Version:** `2026-11-15`.
   - **Licence:** Apache License 2.0. Add to the description: "Third-party material inside the bundles keeps its
     own licence (MIT); see THIRD_PARTY_NOTICES.md in the repository."
   - **Related works:** "Is supplement to" `https://github.com/pm2m1/deployspecter/tree/research-2026-11-15`
     (URL); "Is documented by" the `RESEARCH.md` URL on `main`.
   - **Keywords:** PostgreSQL, database migrations, concurrency, rolling deployment, testing, language models.
   - **Funding, communities:** optional.
6. Press **Preview**, check the files and the text, then **Publish**. The DOI is now permanent.
7. Record the DOI:
   - in the GitHub release notes (edit the release);
   - in `RESEARCH.md` and `EVIDENCE.md`, in a new commit;
   - on OSF, if the registration cites the evidence.

## Route A: GitHub integration (optional, for the source)

1. On Zenodo: your account menu → **GitHub**. Sync, then switch on `pm2m1/deployspecter`.
2. Optionally commit a `.zenodo.json` or `CITATION.cff` to set the title, creators and licence; otherwise Zenodo
   takes them from the release.
3. Publish the draft release on GitHub. Zenodo archives the tag's source archive within a few minutes and shows
   the DOI under **GitHub** on Zenodo.
4. Later releases of the repository are archived too, as new versions, until you switch the integration off.

## Afterwards

- Zenodo gives two DOIs: one for this version, and a "concept" DOI for all versions. Cite the version DOI for
  this snapshot.
- To correct something, make a **new version** on Zenodo. The published files of this one stay as they are.
