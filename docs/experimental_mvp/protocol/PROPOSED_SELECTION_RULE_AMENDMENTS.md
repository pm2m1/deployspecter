# PROPOSED amendments to the frozen corpus-selection rules (for human review; NOT applied)

The frozen rules are in `CORPUS_SHORTLIST.json` v2. `research_tools/corpus_select.py` applies them literally. Wherever they define no answer, it outputs `NEEDS_PROTOCOL_RULING` rather than inventing one (`results/corpus_selection_1`).

Each proposal below is an option for the reviewer to accept, change or reject before any candidate's migration content is read. None is in effect. If adopted, each must be applied identically to every candidate, including those already screened.

## A1. Django projects with several app-level `migrations/` directories (weblate: 22 apps)

The frozen text says "the core package's migrations/ directory". A Django project has no package-level one.

- **Option A1a: largest app.** The app whose `migrations/` holds the most numbered files counts as the core. For weblate this is decidable from file names alone (`trans`, 102 files).
- **Option A1b: root application.** The app whose label equals the distribution name, if any. Otherwise NONE.
- **Option A1c: exclude.** Multi-app Django projects are excluded until a per-app rule is preregistered.

**Rule fixed before reading any of it:** the highest-numbered file of the chosen app, plus Django's migration executor as "the module that applies migrations".

## A2. Alembic revision graphs (mlflow: 70 + 7 revisions)

"Highest-numbered" is undefined for hash-named revisions.

- **Option A2a: graph head.** The selected area is the single head of the main graph's revision chain, ordered by `down_revision` links (exactly what `alembic heads` reports). With several heads, or several graphs, the reviewer must name the graph. For mlflow, that means choosing between `mlflow/store/db_migrations` and `mlflow/server/auth/db/migrations`.
- **Option A2b: release-introduced revisions.** The revision(s) introduced by the pinned release relative to the previous one. This needs the previous sdist's file list, never its contents.

## A3. A driver declared only under an optional extra

This applies to weblate `postgres`, mlflow `db`, saq `postgres`, and pgqueuer `psycopg`. pgqueuer already entered the shortlist this way.

- **Option A3a: accept.** An extra whose name designates PostgreSQL counts as a declared dependency. Record the extra.
- **Option A3b: require.** Only a required dependency counts. pgqueuer would then leave the shortlist, which is a retroactive change to be recorded as such.

## A4. "The module that applies migrations", by framework

Proposal: record it by framework, as metadata rather than as read source:
- **Django:** `django.db.migrations.executor`.
- **Alembic:** `alembic.command.upgrade`, with the project's `env.py`.
- **Custom:** the module the tool finds by name (`migrate.py`, `*migrat*.py`), file name only.

## A5. Exposure recorded by the tool

The tool reads more than file names in exactly two places:
- `INSTALLED_APPS` literals, from settings modules;
- Alembic `revision` and `down_revision` headers.

Proposal: the reviewer accepts these two reads as metadata-level exposure that doesn't make a case non-blind, or forbids them. If they're forbidden, the tool must be switched to file names only before it runs on any new candidate.
