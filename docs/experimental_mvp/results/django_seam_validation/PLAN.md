# D3 validation of the Django seam (r3 #12) on paperless-ngx: plan

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Owner's instruction at Gate 1 (verbatim, 2026-11-17): "Do the Django seam validation first, and nothing else on the
cases. If it fails, record the E3 refusals, stop, and report: the study ends at Stage 2 with 0 eligible historical
cases. Do not start integrations or mutants."

Written and committed on 2026-11-17, **before any run through the seam**. Governing record: `../DEVIATIONS.md` D3.

## What is validated

Registered r3 #12: "The Django seam stays development-only until a development Django application of comparable scale
has run through it." The seam is DeploySpecter's trusted Django backend, `src/deployspecter/experimental/django_backend`
(Django's own PostgreSQL `DatabaseWrapper` over the actor's stepped psycopg 3 connection), used with the stepping
engine (`experimental/stepping.py`) and server-log corroboration (`experimental/harness.corroborated`).

The seam's code is used **as committed** and is not changed during this validation:
- `src/deployspecter` tree `889a7ae7d0afc839454c85c6c227eec2a826a2ac`;
- `src/deployspecter/experimental/django_backend` tree `016dedbbaafa9b8c32a0a87d51a15f3036848db5`.

If the seam fails, it is not fixed and re-run. That failure is the result.

## Application

- **paperless-ngx v3.4.2**. It is one of the two applications D3 names, and is not among the 20 candidates.
  - Tag commit `d939d873e11dd14896d1bfe23f65b1baa6513ba7`.
  - Released 2026-11-03. v3.5.0, released on 2026-11-17, is not used, because it is less than a day old.
- **Image:** the project's own published image, `ghcr.io/paperless-ngx/paperless-ngx@sha256:d704e84f88300eb6ab001ab15720a2330ff99169cf9f94d6b8fcc44b63a84c84`
  (arm64), unmodified.
  - Python 3.14.9;
  - Django 5.2.18;
  - psycopg 3.3.4;
  - Django REST framework 3.18.3.
- **Why paperless-ngx rather than InvenTree:** it targets psycopg 3 natively, and its image runs on this machine's
  architecture without emulation.

## Environment

- **Database:** `research_tools/mvp_pg.sh start`, a disposable PostgreSQL 16 (`postgres:16@sha256:1beaa308…`) with
  `cluster_name=ds_mvp_disposable`, database `ds_mvp`, port 55441 and csvlog statement logging. It is never 55432.
- **Application container:** the application runs in the image above, sharing the database container's network
  namespace, so it reaches the database at 127.0.0.1:5432.
  - The DeploySpecter `src/` and the validation driver are mounted read-only.
  - The database password is passed as an environment variable and is never printed.
  - Nothing else goes onto the network.
- **Configuration:** only the application's documented environment variables, plus one settings module that imports
  `paperless.settings` unchanged and then sets:
  - `DATABASES["default"]["ENGINE"] = "deployspecter.experimental.django_backend"` (seam mode only). Stock mode keeps
    `django.db.backends.postgresql`.
  - Environment variables:
    - `PAPERLESS_DBHOST=127.0.0.1`, `PAPERLESS_DBPORT=5432`, `PAPERLESS_DBNAME=ds_mvp`, `PAPERLESS_DBUSER=ds_mvp`
      and `PAPERLESS_DBPASS`;
    - no `PAPERLESS_DB_POOLSIZE`, so no pool;
    - `PAPERLESS_CACHE_BACKEND=django.core.cache.backends.locmem.LocMemCache`;
    - `PAPERLESS_CHANNELS_BACKEND=channels.layers.InMemoryChannelLayer`;
    - the database read cache (cachalot) left at its default, which is off.
  - `CELERY_TASK_ALWAYS_EAGER = True` and `CELERY_BROKER_URL = "memory://"`, so that no Redis is needed and the
    application's tasks run inline in the request thread.
- **Stepping limits:**
  - migration: `statement_ms=60000`, `python_step_s=120`, `run_s=3600`;
  - operations: `statement_ms=5000`, `lock_ms=3000`, `python_step_s=30`, `run_s=300`.
- **Binding mode:** the application's own setting, which is Django's default client-side binding.

## Phases (`research_tools/solo/django_seam/paperless_seam.py`)

1. **Migrate through the seam (seam mode).** Start from an empty `public` schema. Run the application's own
   `manage.py migrate` (`call_command("migrate")`, all apps) as one stepped actor that admits any SQL, corroborated
   against the server log.
2. **Seed (stock mode, not through the seam).** Create, with the application's ORM:
   - one superuser;
   - tags `t-inbox` and `t-paid`;
   - correspondent `ACME`;
   - three documents, `doc-1` to `doc-3`.

   Then copy every application table into a snapshot schema `ds_seed`. A reset restores the snapshot: in one
   transaction, `TRUNCATE` every application table, then insert the snapshot rows with foreign-key triggers off and
   set each sequence to its table's maximum. Every run checks the restored state's digest against the snapshot's
   (`harness.run_app`). The application tables are those in `public`, except `django_migrations`.
3. **Operation workloads through the seam (seam mode).** Five workloads, each with two actors `a` and `b`. Each actor
   makes one documented REST API request, through DRF's `APIClient` authenticated as the seeded superuser, inside the
   application's full middleware stack. The handler returns the HTTP status.

   | Workload | Actor `a` | Actor `b` |
   |---|---|---|
   | W1 | `POST /api/tags/ {"name": "dup"}` | the same |
   | W2 | `PATCH /api/documents/<doc-1>/ {"title": "A"}` | `PATCH /api/documents/<doc-1>/ {"title": "B"}` |
   | W3 | `POST /api/documents/bulk_edit/` `add_tag` `t-paid` on doc-1 and doc-2 | `POST /api/documents/bulk_edit/` `remove_tag` `t-inbox` on doc-1 and doc-2 |
   | W4 | `DELETE /api/tags/<t-paid>/` | `PATCH /api/documents/<doc-2>/ {"tags": [<t-paid>]}` |
   | W5 | `POST /api/documents/<doc-1>/notes/ {"note": "from a"}` | `POST /api/documents/<doc-1>/notes/ {"note": "from b"}` |

   Five schedules per workload, so 25 runs, each corroborated:
   - S1: `a` serially, then `b`;
   - S2: `b` serially, then `a`;
   - S3: strict alternation `a, b, a, b, …`;
   - S4 and S5: `random.Random(1)` and `random.Random(2)` choices over `{a, b}`, 400 entries each.

   Each schedule's remainder continues in the engine's default order.
4. **Stock reference (stock mode, not through the seam).** For each workload, from the same reset:
   - `a`'s request then `b`'s, in one thread on Django's stock backend, twice;
   - the final state is read with D5's generic read (`SELECT to_jsonb(t) FROM t ORDER BY 1`, TimeZone UTC).

## Pass criteria, all required

- **P1.** Phase 1 completes:
  - the migrate actor finishes without an exception;
  - the trace outcome is `completed`;
  - corroboration is `ok`, with `params_mismatched == 0`;
  - afterwards, `showmigrations` (stock mode) lists every migration as applied.
- **P2.** Each of the 25 runs:
  - has trace outcome `completed`;
  - no actor ends with an exception raised by DeploySpecter (`Refused`, `RunAborted`);
  - each actor returns an HTTP status. Any status is the application's own behaviour, including 4xx and 500.
  - corroboration is `ok`, with `params_mismatched == 0`, and no actor backend outlives the run.
- **P3.** In each workload's S3 run, the two actors' granted statements interleave: at least one of `b`'s
  statements is granted between two of `a`'s.
- **P4, fidelity.** For each workload, the S1 seam run's final state equals the stock reference's final state.
  - Columns of type `timestamp`, `timestamptz` and `date` are masked.
  - So are columns whose values differ between the two stock reference runs (nondeterministic by construction).
  - Every masked column is listed in the evidence.

## Failure, and what is not a failure

- **Seam failure.** Any P1–P4 criterion fails on a run made as planned. The result is "FAIL", and the owner's
  instruction applies: each admitted case gets an E3 refusal in `solo/REFUSED.json`, with reason
  `E3: r3 #12 Django seam not validated`, and the study stops at Stage 2. Under D3, the owner is told before those
  refusals are recorded.
- **Environment or driver failure** before or outside a seam run is recorded as an attempt under `attempts/` with an
  addendum to this plan, committed before the next attempt, as for the S4 checks. Examples: an image pull, a typo in
  the driver, or the stock-mode seed failing. Neither the seam nor the application is ever changed.
  - A driver fix may change only how the driver sets up or calls the application.
  - It may not change P1–P4, the workloads or the schedules.
  - A seam run's evidence is kept even if a later attempt follows.

## Records

Each run's trace, corroboration and final-state digest go in `evidence/`, hashed in `EVIDENCE.sha256`.
`LOG.md` holds the commands and times. `VERDICT.json` holds P1–P4 and "PASS" or "FAIL".
