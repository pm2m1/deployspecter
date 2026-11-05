# Amendment 003 review package: addendum (2026-11-06)

**Governance status: unchanged.** Every decision (#6, #7, #8, #9, #14) is still **PROPOSED**. No curator, oracle author, triage reviewer or adjudicator has been named or contacted. Nothing is registered or timestamped. **Confirmatory evaluation is BLOCKED.**

Amendment 003 draft v3 is unadopted. The frozen protocol 1.2, amendments 001 and 002, and the A–G and R registrations are unchanged.

**New: ready-to-send packages (not sent).**
- `handoff/send/CURATOR_PACKAGE.md`;
- `handoff/send/ORACLE_REVIEW_PACKAGE.md`.

The owner may send them after choosing people. They carry the evidence needed to move #6 and #7 to ACCEPTED, IMPLEMENTED and VERIFIED. The oracle package's Part B asks for a review of the three developer-encoded development-case oracles, including errata E1 and E2.

## New issue

**21. SQL migration directories.** The frozen migration-area rule recognizes only a `.py` `migrations/` directory at the core package. procrastinate ships versioned SQL migrations (`procrastinate/sql/migrations/xx.yy.zz_ab_{pre|post}_*.sql`), with a documented blue-green procedure. The frozen tool reports NEEDS_PROTOCOL_RULING (`results/corpus_selection_2_procrastinate`).

Proposed:
- a directory of version-named migration files (`.py` or `.sql`) inside the core package counts as its migration area;
- the highest version's files, plus the documented procedure, are the area.

This would matter only for prospective candidates. procrastinate itself is a frozen development application, and stays development-only. **Decider:** the owner, before any candidate whose eligibility depends on it is read.
