# Engineering release candidate rc1 (EXPERIMENTAL, 2026-11-05)

**Status: ENGINEERING RELEASE CANDIDATE.** It is frozen locally on branch `research/real-application-mvp`. It is not pushed, not merged and not tagged remotely.

It is **not** research evidence, **not** confirmatory, and **not** Gate A2 evidence. The Gate A2 demonstration guard stays blocked. Gate 2A, Gate A1, WorkloadSpec v2 and the frozen protocols are untouched.

| | |
|---|---|
| Code revision | `d9890f8b3e04` (`git rev-parse HEAD` at manifest time), clean |
| Manifest | `RELEASE_CANDIDATE.json`: schema `ds-mvp-release-candidate/1`, 72 tracked files under `src/deployspecter/experimental`, `tests/experimental`, `tests/thirdparty_isolated` and `research_tools` |
| `uv.lock` sha256 | `356ef882b363778afddc685d2c9ced92d5e67ca3603e0cc2e2cbd1b0555cc2a2` |
| PostgreSQL image | `postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c` (disposable, `127.0.0.1:55441`, tmpfs) |
| Isolated runner base | `python@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30` |
| Pinned sdists (sha256) | `pgqueuer-1.6.0.tar.gz` `ed130640…f156e`; `chancy-0.26.0.tar.gz` `ac98b9c5…aef06`; `chancy-0.25.1.tar.gz` `264ed4f7…122d7` (full hashes are in the manifest and in `research_tools/isolated/fetch_pinned.sh`) |

To check a checkout against the release candidate (no network, no database):

```bash
python research_tools/rc_manifest.py verify --manifest docs/experimental_mvp/RELEASE_CANDIDATE.json
```

## Regression results at the release candidate

| Suite | Command | Result |
|---|---|---|
| One-command demo, full | `research_tools/mvp_demo.sh docs/experimental_mvp/results/demo_full_4 full` | **exit 0** at `d42e369` (`results/demo_full_4`). The code is the same as rc1, whose commit added only the manifest tool and this record |
| Core experimental (live, disposable PostgreSQL) | `pytest tests/experimental` | **278 passed** |
| Optional isolated third-party | `DS_THIRDPARTY_SDIST_DIR=… pytest tests/thirdparty_isolated` | **9 passed** |
| Root and research suites, no database | `pytest tests --ignore=tests/experimental --ignore=tests/thirdparty_isolated` (with the 5 modules below ignored) | **1746 passed**, 1 skipped (`langgraph` not installed), 322 UNAVAILABLE |
| Root modules that call `load_settings()` at import | `test_baselines`, `test_documented_public`, `test_meta_client`, `test_scenarios`, `test_site` | UNAVAILABLE (not collectable without `DATABASE_URL`) |
| Ruff | `ruff check .` | clean |
| Frozen paths | `git diff --name-only 348b44c HEAD` outside the experimental trees | empty. Since the branch start (`ac6ff66`), only `pyproject.toml` and `uv.lock` (the `experimental` extra) changed outside them |

All 322 UNAVAILABLE tests fail for one reason: `ConfigError: DATABASE_URL is not set`. The only database that `.env` names is the protected `55432` one, which this work never uses. So they are reported as unavailable, not as passed.

**A failure on the way.** `demo_full_3` (at `5b146bf`) **exited 15**. The mixed-version pilot was refuted by the server log in its phase 3, because Chancy 0.25.1's `Worker.stop` cancelled a statement in flight. That run is kept with its note. The fix is `AsyncRun.quiesced`, which has a regression test that discriminates in 5 of 5 runs. After it, the pilot was reproduced in 5 of 5 repeated runs, and then in `demo_full_4`.

## Supported operations and refusals

See `CAPABILITY_MATRIX.md`. In summary:

| Area | Supported (live-tested) | Refused or unsupported (tested) |
|---|---|---|
| Sync stepping (psycopg 3) | execute, executemany, commit, rollback, `transaction()`, savepoints, lock waits, deadlocks, timeouts, exploration | named cursors, COPY, `stream`, pipelines, non-allowlisted SQL, a second connection, use from another thread |
| Async stepping | the same on `AsyncConnection`, causal task identity, raw `$n` cursors, recorded NOTIFY, `serialize_tasks`, a task cancelled at the gate, `quiesced` stops | the same refusals; a statement cancelled in flight is refuted by the server log |
| SQLAlchemy 2.0.57 (sync and asyncio) | ORM through `creator` with `StaticPool` | a shared engine across actors; a second connection |
| Django 5.2 | both binding modes, migrations, `select_for_update`, `atomic`, savepoints | server-side cursors, reopened connections, `OPTIONS["pool"]` |
| PGQueuer 1.6.0 | install, enqueue, drain-mode worker (Driver-port adapter) | `from_psycopg_connection` (refuted); asyncpg drivers (not built) |
| Chancy 0.26.0 / 0.25.1 | migrate, declare, push, worker, stop; concurrent workers; a four-phase rolling deploy; the declared claim-lock mutant | LISTEN listener (refuted); sync APIs; process executor |
| WorkloadSpec v2 / Phase 9B | the unmodified chain to a bridged, typed initial state, on Track A's dry-run snapshot or on the live catalog | trigger with unknown writes (`indeterminate`), CHECK (`unsupported`), drifted schema (`invalid` or refused) |
| Corroboration | statements, sessions, errors, order and typed values (int, NULL, bool, str, bytea, 1-D arrays, json/jsonb, date/time types, interval, uuid, server-coerced str-to-uuid) | anything else: UNVERIFIED and counted, never accepted |
| Muse | proposals through deterministic validators only (`muse.py`, `muse_apps.py`); no model-generated Python or SQL executes | proposals that fail validation are rejected and counted |
| Isolation | restricted runc container for trusted and pinned-reviewed code | hostile-grade isolation: BLOCKED |

## Limitations

- **Not hostile-grade isolation.** The runner shares the Docker Desktop VM kernel. It is suitable only for trusted fixtures and pinned, reviewed releases.
- **NONBLIND only.** Every application case so far was selected and run by the same developer who wrote the harness. No result here is confirmatory.
- **Cross-version scheduling.** In the Chancy pilot, each phase is its own container and process, so a 0.25.1 worker and a 0.26.0 worker are never concurrently schedulable against each other.
- **Exploration is bounded.** "Exhausted" means the decision frontier emptied within the budget. It is not a completeness proof, and decision points are not every asyncio interleaving.
- **Reviewed statement allowlists.** A new query shape needs a reviewed entry.
- **Root suite.** It was not run against a database in this release candidate (see above).

## Reproduction commands

```bash
cd ~/Desktop/deployspecter-real-app-mvp
uv sync --frozen --extra experimental
research_tools/mvp_demo.sh OUT full                     # everything, with its own disposable PostgreSQL and teardown
# or, step by step, with research_tools/mvp_pg.sh start / stop around it:
.venv/bin/python -m pytest tests/experimental
research_tools/isolated/fetch_pinned.sh SDIST_DIR
DS_THIRDPARTY_SDIST_DIR=SDIST_DIR .venv/bin/python -m pytest tests/thirdparty_isolated
.venv/bin/python research_tools/isolated/run_isolated.py chancy-mixed \
  --chancy-old-sdist SDIST_DIR/chancy-0.25.1.tar.gz --chancy-sdist SDIST_DIR/chancy-0.26.0.tar.gz --out OUT/mixed
.venv/bin/python research_tools/isolated/run_isolated.py chancy-runtime --concurrent-workers \
  --mutant no_skip_locked_claim --chancy-sdist SDIST_DIR/chancy-0.26.0.tar.gz --out OUT/mutant
```

Environment rules for every command:
- `MODEL_API_KEY` and `DATABASE_URL` are unset (`env -u`), except in the Muse comparison runners, which read only the `MODEL_*` keys.
- The protected `deployspecter-pg` on 55432 is never contacted.
