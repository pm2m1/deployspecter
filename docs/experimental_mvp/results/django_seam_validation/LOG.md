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
