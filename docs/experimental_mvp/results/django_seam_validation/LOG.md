# D3 Django seam validation: log

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Plan: `PLAN.md`, committed first (b1881ad), with addendum 1 (57a6455).

## Environment (2026-11-17)

- `research_tools/mvp_pg.sh start`: PostgreSQL 16, `postgres:16@sha256:1beaa308…`, 127.0.0.1:55441. Never 55432.
- The application container runs `ghcr.io/paperless-ngx/paperless-ngx@sha256:d704e84f…` with entrypoint `python3`, in
  the database container's network namespace. It mounts read-only `src/`, `research_tools/solo/django_seam/` and the
  password file (`/run/ds_secret`).

## Driver dry run, stock backend only (addendum 1): `dryrun/`

No stepping, no seam. Two driver errors, each fixed in the driver only:
1. `ImproperlyConfigured: PAPERLESS_SECRET_KEY is not set`. That is a documented variable the application requires.
   `ds_seam_settings.py` now sets a fresh random key per process. Nothing signed persists between processes.
2. `GeneratedAlways` on `value_monetary_amount` in the reset's `INSERT … SELECT *`. The reset now lists each table's
   non-generated columns from `pg_attribute`. Then a `format('public.%I', …)` needed `%%I` under psycopg's
   placeholders.

Result:
- stock `migrate` applied 109 migrations;
- `seed` built 74 application tables, with ids t-inbox 1, t-paid 2, doc-1..3 = 1..3;
- `stock` ran W1–W5 twice each. The statuses were W1 201/400, W2 200/200, W3 200/200, W4 204/400 and W5
  200/200.
- Columns that differ between the two stock repetitions: `auditlog_logentry.timestamp` (all), `.changes` (W5),
  `documents_document.modified` (W2, W5) and `documents_note.created` (W5).

The dry run is not evidence for P1–P4. Phase 1 drops `public` and `ds_seed` and starts from an empty schema.

## The validation runs (2026-11-17, UTC)

| Phase | Mode | Started | Result |
|---|---|---|---|
| 1. migrate | seam | 12:56:19 | 109 migrations; 1,117 statements in 2.7 s; outcome `completed`; corroboration ok, 0 mismatched |
| 2. seed | stock | ~12:56:30 | `showmigrations`: 109 applied, 0 unapplied; seed digest `162ac680…` over 74 tables |
| 3. ops | seam | 12:56:33 to 12:59:26 | 25 runs; 20 `completed`, 5 `timeout` (below) |
| 4. stock | stock | after phase 3 | W1 201/400, W2 200/200, W3 200/200, W4 204/400, W5 200/200 (both repetitions) |
| verdict | none | | **P1 pass, P2 FAIL, P3 pass, P4 FAIL: verdict FAIL** (`VERDICT.json`) |

Raw output: `phase1_migrate.out` … `phase4_stock.out`. Evidence: `evidence/`, hashed in `EVIDENCE.sha256`. Nothing
was re-run.

### Why P2 fails: 5 of 25 runs time out

W2_S3, W2_S4, W5_S3, W5_S4 and W5_S5 (two PATCHes of one document, and two notes on one document, under interleaved
schedules) end with outcome `timeout`. The diagnostic is "`<actor>` computed for more than 30 s". In each, both
actors' last statements were outside a transaction. One actor was waiting at the gate on
`SELECT "documents_document"."content" …` while the other stayed in Python with no statement for 30 s.

**Cause, from paperless-ngx's code at v3.4.2:**
- `documents/search/_backend.py` takes a `filelock.FileLock` on the search index (with sleep-and-retry), and reads
  from the database while holding it.
- The actor that holds the lock waits for its grant, while the other spins on the lock.
- DeploySpecter grants only database operations, so it can't order around a lock held outside the database.
- In production, the same file lock serializes paperless's separate processes.
- Under S1 and S2 (serial) and W2_S5, the lock is never contended and the runs complete.

### Why P4 fails: W3 differs from the stock backend

Under the seam, both W3 requests (`bulk_edit` `add_tag`, `remove_tag`) return 400 in all five schedules; on the
stock backend they return 200. W3_S1's final state differs from the stock reference in `auditlog_logentry`.
- **Cause, from the traceback in `phase3_ops.out`:** after its tag insert commits, `bulk_edit` starts the
  application's `bulk_update_documents` task, which runs inline because Celery is eager (`PLAN.md`).
- Celery's Django fixup closes the database connection at task start. Then paperless's `task_prerun_handler`
  queries, so Django opens a new connection. The seam refuses it, by design: "Django asked for a connection this
  thread wasn't given (a second or reopened connection)" (`django_backend/base.py:61`).
- The application catches the exception and answers 400.

### What passes

- **P1:** the application's full migration history runs through the seam, corroborated with 0 mismatched.
- **P3:** each workload's alternating schedule interleaves the two actors' statements.
- **P4:** W1, W2, W4 and W5 match the stock backend.
- **Corroboration:** ok with 0 mismatched in all 26 seam runs, including the five that timed out.
- **Unique-key and foreign-key races:** both surface as the application's own 500s under interleaving (W1_S3,
  W4_S3–S5: `IntegrityError`). That is the application's behaviour, not a seam fault.

## Consequence (owner's instruction at Gate 1, applied)

"If it fails, record the E3 refusals, stop, and report: the study ends at Stage 2 with 0 eligible historical cases.
Do not start integrations or mutants."

The 4 admitted cases are added to `solo/REFUSED.json` with reason `E3: r3 #12 Django seam not validated` (D3, D4):
`pretix-409e64d5f2`, `plane-b2717dc9f4`, `plane-4605f0078f` and `wger-bcfdde1a96`. No integration was started, and
no mutant was generated. The disposable database was stopped after the run.
