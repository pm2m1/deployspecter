# Case selection rule for the r3 evaluation (predeclared; part of `FREEZE.json`)

This file states, in one place, the rule the Label Curator applies. Where it quotes or points to a frozen file, that
file governs. It adds one owner-declared eligibility rule (S4), made on 2026-11-15 before any candidate's fix,
issue or test was read by anyone in the study.

## S1. Frozen rules that still apply unchanged

- **r3 §3 and ruling #26:** one case per upstream defect; at most 3 cases per application; at least 5 distinct
  applications among the eligible cases. Development applications and anything GitLab-derived are never eligible.
- **r3 ruling #5:** a case is a commit-level fix pair, the fix commit and its first parent.
- **`docs/experimental_mvp/CORPUS_SHORTLIST.json` `selection_rules`:** OSI licence and a public repository not on
  GitLab; versioned schema migrations; not a held-out application (saleor, healthchecks, netbox, authentik); not a
  development application; not GitLab-derived. Its psycopg 3 rule is widened only by S4 below.
- **Screening criteria E1–E6:** `docs/experimental_mvp/nonblind_corpus/SELECTION_RULE.md` §4, applied in order;
  the first failing criterion is recorded. E3 (the code path runs under DeploySpecter's existing stepping seam) is
  unchanged by S4.

## S2. Pool (from `benchmark_prep/FEASIBILITY.md` §2)

Python applications (not libraries) whose own PostgreSQL access goes through psycopg 3, either directly, through
SQLAlchemy 2 with the `postgresql+psycopg` dialect, or through Django with its psycopg 3 backend, as shown by a
declared dependency **or by S4**.

**Excluded:** every application already seen (chancy, pgqueuer, saq, procrastinate, taskiq-postgresql, pgtq,
postgres-tq, InvenTree, paperless-ngx, langgraph-checkpoint-postgres); the held-out applications; nautobot (it shares
code history with netbox); anything GitLab-derived.

## S3. Order and limits

- **Application order:** ascending `sha256("research-2026-11-15:<owner>/<repo>")` over the candidate list in
  `benchmark_prep/FEASIBILITY.md` §3. Candidates may be appended to the end of that list only before screening
  starts, and the list is committed when they are.
- **Within an application:** fixes from the most recent back, at most 12 screened per application.
- **Screening budget:** about 100 screens in total (`benchmark_prep/FEASIBILITY.md` §1).
- **Stop:** when at least 10 eligible cases from at least 5 applications are sealed, or the budget is spent. With
  fewer than 10, RA-Q3 is descriptive.

## S4. Django applications pinned to psycopg2 (owner's predeclared eligibility rule)

**Rule.** A Django application whose declared dependencies pin psycopg2 (or `psycopg2-binary`) is eligible on the
driver rule if it **runs unmodified with psycopg 3 installed, under Django 4.2 or later**. Django 4.2 and later use
psycopg 3 when it is importable. No application source file may be changed. Only the installed packages differ.

**Verification, by the curator, per application, before any of its fixes is read:**
1. Use the application's revision at the start of its screening (its default-branch head on the screening date),
   and record the commit.
2. In a disposable environment with a disposable PostgreSQL 16, install its declared dependencies, then
   `psycopg[binary]` 3. Record both versions and Django's version, which must be 4.2 or later.
3. Confirm the backend in use is psycopg 3: `python manage.py shell -c "from django.db import connection;
   connection.ensure_connection(); print(type(connection.connection).__module__)"` prints a module starting with
   `psycopg` and not `psycopg2`.
4. Apply every migration from an empty database: `python manage.py migrate` exits 0.
5. Run the application's own test suite, or a database-backed subset fixed **before** running it and recorded. It
   must show no failure that doesn't also occur with psycopg2 in the same environment.
6. Record the verdict, the commands and their exit codes in the screening log, with the date and the curator's
   initials. The verdict is final for that application.

**Per case:** each pinned revision (R1 and R2) must still declare Django 4.2 or later, checked from its dependency
file alone. A revision that doesn't fails this rule, which is recorded as a driver failure. E3 still applies:
a case whose Django version the stepping seam can't run fails E3, and the engine isn't extended to raise the count.

**Status.** This widens `CORPUS_SHORTLIST.json`'s psycopg 3 rule for one class of application. r3 §3 expects such a
change to come from a written reviewer ruling. With no reviewer under this staffing, it is an **owner ruling**,
declared here before selection and registered with the rest of this package. Results report how many cases were
admitted under S4.

## S5. Who reads what

- **The curator** reads an application's fixes only while screening them under E1–E6, and only after that
  application's driver verdict (including S4) is recorded.
- **The curator never passes** a fix, its tests, its changelog entry, its issue or PR text, or the revision labels
  to the owner or the oracle writer (`benchmark_prep/CURATOR_QUICKSTART.md`,
  `benchmark_prep/ORACLE_WRITER_QUICKSTART.md`).
