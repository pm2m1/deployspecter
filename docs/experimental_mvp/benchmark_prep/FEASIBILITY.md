# Feasibility of 10 blind historical cases from 5 or more unexposed applications (prepared 2026-11-15)

For the owner and the curator. **No candidate's fix commits, issues or bug details were opened.** For each candidate
repository, only GitHub metadata (name, language, archived flag, stars) and a commit-search **count** were read.
No commit message was displayed. Case selection is the curator's job, and it must stay blind to the developers.

## 1. Screening rate from ds-nbc/1

Source: `nonblind_corpus/CORPUS_RESULTS.md`. There were 68 candidates, of which 34 were screened. 5 were eligible,
not counting the prior-exposure case. Criterion E2 (database-mediated behaviour) failed 22 of the 34.

| | Eligible rate p | Screens for 10 eligible: median | 80th percentile | 90th percentile |
|---|---|---|---|---|
| Point estimate, 5/34 (1 in 6.8) | 0.147 | 66 | 84 | 94 |
| Lower 95% bound (Clopper–Pearson) | 0.050 | 195 | 251 | 285 |
| Upper 95% bound | 0.311 | 31 | 39 | 43 |

These are negative-binomial quantiles for the number of screens needed to reach 10 eligible cases.

**Plan:** budget **about 100 screens**, which covers the point estimate's 90th percentile. Use at most 12 per
application, most recent first, as in ds-nbc/1. That means **at least 9 applications**, and in practice 12–15,
because:
- r3 #26 caps each application at 3 cases and requires at least 5 applications;
- some applications will fail on their driver before any screening.

ds-nbc/1's pool was job-queue libraries, where most fixes are in-process (E2). Applications with migrations and
ORM-level concurrency may do better or worse; the 95% interval above covers that uncertainty only roughly.

## 2. A widened, predeclared pool (inside the frozen selection rules)

`CORPUS_SHORTLIST.json`'s frozen rules stay unchanged:
- OSI licence, not on GitLab;
- psycopg 3 shown by a **declared dependency**;
- versioned schema migrations;
- not held out, not a development application, not GitLab-derived.

The widening only states which access paths count, all of which still need psycopg 3:

> **Pool:** Python applications (not libraries) whose own PostgreSQL access goes through psycopg 3, either
> directly, through SQLAlchemy 2 with the `postgresql+psycopg` dialect, or through Django 5.2 with its psycopg 3
> backend, as shown by a declared dependency.
>
> **Excluded:**
> - every application already seen: chancy, pgqueuer, saq, procrastinate, taskiq-postgresql, pgtq, postgres-tq,
>   InvenTree, paperless-ngx, langgraph-checkpoint-postgres;
> - the frozen protocol's held-out applications: saleor, healthchecks, netbox, authentik;
> - nautobot, because it shares code history with held-out netbox;
> - anything GitLab-derived.
>
> **Screening order** is fixed by `sha256("research-2026-11-15:<owner>/<repo>")`, ascending, not chosen from memory.

An application on psycopg2 or asyncpg fails E3 (our stepping seam is psycopg 3) and goes to the exclusion log.
Whether that widening needs a reviewer's ruling under r3 §3 is on the owner's decision list.

## 3. Candidate applications (ALL UNVERIFIED)

The ORM, driver and PostgreSQL columns come from general knowledge, **not from reading the repositories**. The
curator verifies each from the declared dependencies. "Keyword commits" is the GitHub commit-search `total_count`
for `deadlock OR race OR concurrency OR "for update" OR "select_for_update"` in the default branch's history, read
2026-11-15. It's a rough upper bound with many false positives, and says nothing about eligibility.

| # | Repository | ORM / driver (unverified) | PostgreSQL use (unverified) | Keyword commits |
|---|---|---|---|---|
| 1 | https://github.com/zulip/zulip | Django; psycopg2 historically | PostgreSQL only | 396 |
| 2 | https://github.com/getsentry/sentry | Django; psycopg2 | primary store | 636 |
| 3 | https://github.com/PostHog/posthog | Django; psycopg2 and/or psycopg 3 | app DB (events in ClickHouse) | 510 |
| 4 | https://github.com/WeblateOrg/weblate | Django; possibly psycopg 3 | PostgreSQL recommended | 144 |
| 5 | https://github.com/makeplane/plane | Django (in a TypeScript monorepo); driver unknown | primary store | 18 |
| 6 | https://github.com/HumanSignal/label-studio | Django; psycopg2 | PostgreSQL or SQLite | 52 |
| 7 | https://github.com/TandoorRecipes/recipes | Django; driver unknown | PostgreSQL recommended | 1 |
| 8 | https://github.com/wger-project/wger | Django; driver unknown | PostgreSQL or SQLite | 6 |
| 9 | https://github.com/rafalp/Misago | Django; driver unknown | PostgreSQL only | 1 |
| 10 | https://github.com/kiwitcms/kiwitcms | Django; driver unknown | PostgreSQL, MariaDB | 7 |
| 11 | https://github.com/mathesar-foundation/mathesar | Django; possibly psycopg 3 | PostgreSQL only, heavy SQL | 18 |
| 12 | https://github.com/apache/airflow | SQLAlchemy; psycopg2 | metadata DB | 398 |
| 13 | https://github.com/PrefectHQ/prefect | SQLAlchemy 2 async; asyncpg (would fail E3) | server DB | 392 |
| 14 | https://github.com/dagster-io/dagster | SQLAlchemy; psycopg2 (dagster-postgres) | run and event storage | 381 |
| 15 | https://github.com/apache/superset | SQLAlchemy; psycopg2 | metadata DB | 54 |
| 16 | https://github.com/mealie-recipes/mealie | SQLAlchemy 2; driver unknown | PostgreSQL or SQLite | 6 |
| 17 | https://github.com/mlflow/mlflow | SQLAlchemy 2; driver chosen by the user | tracking store | 64 |
| 18 | https://github.com/ckan/ckan | SQLAlchemy; psycopg2 | primary store | 37 |
| 19 | https://github.com/taigaio/taiga-back | Django; driver unknown | PostgreSQL only | 26 |
| 20 | https://github.com/pretix/pretix | Django; psycopg2-binary (the frozen protocol's exclusion log, X2) | PostgreSQL recommended | 30 |

**Expectation, to be honest about:** several of these will fail the psycopg 3 rule, most likely the psycopg2
ones (1, 2, 6, 12, 14, 15, 18, 20) and Prefect (asyncpg). If fewer than about 9 survive the driver check, the
psycopg 3 rule makes 10 cases unlikely without a further widening, which would need a reviewer's ruling. Mutants
(`MUTANTS.md`) are a second, separately reported source, never a substitute.
