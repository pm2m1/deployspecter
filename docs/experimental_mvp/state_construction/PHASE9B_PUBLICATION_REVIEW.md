# Phase 9B constructor: publication review (2026-11-07)

`PHASE9B_PROVENANCE.json` records the 2026-11-06 approval: retain the copied constructor for local experimental
development only, without publishing or pushing. That record is historical and is left unchanged. On 2026-11-07 the
owner authorized pushing completed project work to the existing GitHub repository, on condition that redistribution
rights for these files be verified first and that no restricted material is included. This file records that check.

## Files

| File in this branch | sha256 | Origin |
|---|---|---|
| `src/deployspecter/experimental/state_construction/phase9b_source/seed_feasibility.py` | `2684e57fbce8e594…` | `src/deployspecter/seed_feasibility.py`, untracked, in the owner's `trigger-aware-validation` worktree of this repository |
| `src/deployspecter/experimental/state_construction/phase9b_source/state_constructor.py` | `bb3fec6c4efa4733…` | `src/deployspecter/state_constructor.py`, same worktree |
| `src/deployspecter/experimental/state_construction/phase9b_source/triggers.py` | `de970dd5d313a2ea…` | `src/deployspecter/triggers.py`, same worktree |

Each file is still byte-identical to its origin (rechecked 2026-11-07 with `cmp`).

## Ownership and redistribution

- **Authorship:** the three files were written on 2026-11-02 in the owner's own development session, inside the
  owner's local checkout of this repository. No third party contributed to them.
- **No external source:** there is no third-party copyright header, licence text or attribution in any of the files.
  They import only the standard library, psycopg, pydantic, python-dotenv and this repository's own modules.
- **Conclusion:** they are the owner's own project work. The owner's 2026-11-07 authorization covers publishing them.

## Restricted material

- **No GitLab or private terms:** the files contain no occurrence of "gitlab" or of private paths. Every match of a
  broader search for application-specific vocabulary is PostgreSQL's `pg_namespace` catalog.
- **No protected evaluation material:** no reference to held-out applications, sealed cases, external scenarios or
  the GitLab experiment outputs (as in `PHASE9B_PROVENANCE.json`'s audit).
- **What stays local:** the origin worktree's tests and tools, which do name private terms, were not copied and are
  not published.
- **No new GitLab-fixture results:** the retrospective use of this constructor on the GitLab-derived fixture is
  described only in the evidence package already published on `hackathon-release`. Nothing about that fixture is
  added here.

## What this does not change

The constructor is published as EXPERIMENTAL research code, not as part of any release. Gates A1, 2A and A2 are
unchanged. This is not an upstream merge, and Amendment 003 is not adopted.
