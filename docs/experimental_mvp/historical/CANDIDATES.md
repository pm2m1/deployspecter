# Historical upgrade-defect candidates (2026-11-05)

**Rules.** The frozen corpus-selection rules (`CORPUS_SHORTLIST.json` v2), applied by `research_tools/corpus_select.py` (sha256 `3eaa449e…`, `results/corpus_selection_1`), unchanged. No eligibility rule was changed or applied after looking at any defect label, and no new rule is applied here.

**This sprint's screening criteria** come from the owner. They are recorded as screens, not as frozen rules, and the proposed rule for them is issue #19 in `protocol/DECISIONS_003_v2.md`:
- a historical migration or backfill change;
- a supported old/new overlap or a documented compatibility contract;
- runnable entrypoints;
- pinned sources;
- clear properties;
- an isolated route;
- a documented historical fix.

**Runner-context restriction.** For any candidate that could become blind-eligible, this context did **not** read changelogs, issues, fixes or upgrade documentation, because they can expose defect history. Assessing their compatibility contract is assigned to the curator (#6).

## A. BLIND_ELIGIBLE: none

No case is blind-eligible, because no curator exists, no labels are sealed, and two candidates need protocol rulings.

| Candidate | Pinned | Frozen-rule verdict | What would make it eligible |
|---|---|---|---|
| weblate 2026.11 | sdist `ca7babad…`, tag commit `08b6dd96…` | **NEEDS_PROTOCOL_RULING**: 22 app-level Django `migrations/` directories (#1); psycopg 3 only under an extra (#3) | rulings on #1 and #3; a curator; the Django seam validated at this scale (#12) |
| mlflow 3.18.0 | sdist `04e912d5…`, tag commit `ba41106d…` | **NEEDS_PROTOCOL_RULING**: hash-named Alembic revisions (#2); a driver under an extra (#3) | rulings on #2 and #3; a curator; an Alembic/SQLAlchemy path (the SQLAlchemy seam exists for development fixtures only) |

Their compatibility contracts are **not assessed** here; that is curator work.

## B. NONBLIND_DEVELOPMENT

| Candidate | Why NONBLIND | Upgrade contract (cited) | Historical fix | Runnable here? |
|---|---|---|---|---|
| **Chancy** (0.25.1 / 0.26.0, commits `80f570f` / `2895c4a`) | prototype target; adapter written from source; changelog and fix read | **Single-version.** "Stop old workers, run migrations, then start updated workers" (`2895c4a` CHANGELOG); "Stop your workers" (0.26.0). Mixed-version operation is **not** supported | `2895c4a`: execution claims (migration v8, the frozen-rule-selected area). The maintainers' regression test is `test_cancellation_fences_execution_completion` | **Yes, executed**: `historical/chancy_2895c4a` |
| **procrastinate** (frozen protocol development application, 3.10.0 / `8b639832…`) | frozen development application; its fix history was enumerated by the main repository's Corpus v3 triage (`evaluation/corpus_v3/unsupported.json`) | **Documented blue-green overlap for adjacent versions:** apply the `pre` migration, deploy the new version, apply the `post` migration, "stop at every version that provides migrations" (`howto/production/migrations`). The page gives no explicit compatibility matrix | `8fa4ab5` (2020-06-15), "Close race condition in procrastinate_fetch_job", migration `delta_0.9.0_001_close_fetch_job_race_condition.sql`. Corpus v3 classified it "unsupported: no test file changed with the fix" | **Not now.** (1) The 0.8/0.9 era runs on aiopg/psycopg2, and the gate is psycopg 3 only. (2) The race is inside one plpgsql call, so a statement scheduler can't interleave it. (3) psycopg 3 versions (3.x) would need a new connector adapter, built only for a selected case (P5) |
| **pgqueuer** 1.6.0 | prototype target; adapter written from source | not assessed | none identified | the lifecycle runs. Migration area NONE_BY_RULE (no core `migrations/` directory) |

## C. INELIGIBLE (under the frozen rules, or for lack of executable evidence)

| Candidate | Reason |
|---|---|
| dbos 3.4.0, saq 0.26.5, pgmq 1.1.4 | **NONE_BY_RULE**: no `migrations/` directory in the core package, so the frozen migration-area rule selects nothing. (Their schemas are created in code. Changing that would be a new rule, and it isn't proposed silently) |
| saleor, healthchecks, netbox, authentik | frozen protocol **held-out** applications: excluded by name, and nothing fetched |
| inventree, paperless-ngx, langgraph-checkpoint-postgres | frozen protocol development applications: development only, never prospective cases. Not screened further this sprint |

## Chancy's simultaneous-worker results are not an upgrade defect

The P7 cells of `simultaneous/RESULTS.md` (cancels lost when 0.25.1 and 0.26.0 run together) fall **outside** Chancy's documented contract. They are a mixed-deployment hazard, not a confirmed upgrade defect. The historical case below is a single-version defect with a documented fix.
