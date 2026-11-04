# Gate A2.3: operator checklist for the one-run supervisor characterization

**Not authorized yet.** Nothing below may be run until the owner gives a separate authorization and signs a **new** approval file. No earlier approval (Gate A1 versions 1 to 4, Gate A2) is valid for this package.

## 0. What is pinned

| | |
|---|---|
| Commit | the local commit that adds this checklist (`git rev-parse HEAD` must print it) |
| Manifest | `docs/research/live_stepping/track_a/gate_a2_supervisor_characterization_manifest.json` |
| Manifest SHA-256 | `ef6cdc2f74eab99d3d4f2ab3c010fc27b70735ea92abebdef5593a6cab80a867` |
| Phase | `gate_a2_supervisor_characterization` |
| Image | `postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c` |
| Target | container `deployspecter-live-pg`, `127.0.0.1:55433`, database `deployspecter_test`, user `deployspecter`, scram-sha-256 |
| Forbidden | `deployspecter-pg` on 55432: never stopped, connected to, modified or used as a fallback |
| Workload | Gate 2A's synthetic `live_app.reservations`, variant `fixed`, schedule `a, b` (scenario digest in the manifest) |
| Source hashes | the manifest's `source_sha256`. The driver refuses if any differs, including every frozen Gate 2A file it uses |

## 1. Read-only checks before anything starts

All of these must pass; any failure stops everything.

1. **Repository:**
   - `git rev-parse HEAD` matches the commit above;
   - `git status --porcelain` shows no tracked change;
   - `shasum -a 256 <manifest>` matches.
2. **Approval:** the owner's signed approval names exactly that SHA-256 and the phase. `ssh-keygen -Y verify -f <allowed_signers> -I <signer> -n file -s approval.json.sig < approval.json` reports a good signature.
3. **Environment:**
   - `MODEL_API_KEY` unset, and no `*_API_KEY`, `MODEL_*` or `PG*` variable set;
   - `DATABASE_URL` not set in the shell;
   - no `.env` present yet.
4. **The protected database:** `deployspecter-pg` is running with its known ID and start time. Port 55433 is free, and no container is named `deployspecter-live-pg`.
5. **The package's own checks:**
   - `env -u MODEL_API_KEY .venv/bin/python research_tools/gate_a2_supervisor_characterize.py plan --manifest <manifest> --manifest-sha256 <sha>` prints the steps, stop conditions and commands.
   - `.venv/bin/python -m pytest -q -m "not live" tests/research_tracks` passes.

## 2. Start (operator only; the driver runs no command)

`plan` prints these from the manifest:

1. Generate a random password and export it as `DS_PG_PASSWORD` in this shell only. Never print it.
2. `docker run -d --rm --name deployspecter-live-pg -p 127.0.0.1:55433:5432 --tmpfs /var/lib/postgresql/data:rw,size=512m --memory 1g --cpus 2 --pids-limit 256 -e POSTGRES_USER=deployspecter -e POSTGRES_DB=deployspecter -e POSTGRES_PASSWORD="$DS_PG_PASSWORD" postgres:16@sha256:1beaa308… -c cluster_name=ds_live_disposable … -c max_prepared_transactions=5` (the full command is printed by `plan`)
3. `docker exec deployspecter-live-pg pg_isready -h 127.0.0.1 -p 5432 -U deployspecter -d deployspecter` until it is ready (bounded).
4. `docker exec deployspecter-live-pg psql -U deployspecter -d deployspecter -c 'CREATE DATABASE deployspecter_test'`.
5. Write the git-ignored `.env` with exactly one line, `DATABASE_URL=postgresql://deployspecter:<password>@127.0.0.1:55433/deployspecter`. Then `unset DS_PG_PASSWORD`.

## 3. Run once, in the foreground

`env -u MODEL_API_KEY .venv/bin/python research_tools/gate_a2_supervisor_characterize.py characterize --manifest <manifest> --manifest-sha256 <sha> --approval <signed approval.json>`

- One attempt only. No retry, whatever the outcome.
- Expected output: `Gate A2 supervisor characterization: CHARACTERIZED_WITH_DISCLOSED_GAPS (OBSERVABLE_SQL_VERIFIED_WITH_DISCLOSED_GAPS)`, exit 0.
- Anything else is a stop. `NOT_CHARACTERIZED`, or a refusal with `failure.json`, is a result to report, not something to fix and rerun.

**Stop conditions** (the manifest's `stop_conditions`, enforced by the driver):

- a refused precondition or preflight;
- a parent session or segment audit that doesn't reconcile;
- the B5 policy failing;
- a REFUTED or INDETERMINATE gap claim;
- the call raising or being flagged;
- an interrupt;
- the wall-clock ceiling.

## 4. Evidence

1. Copy the server log into the run directory as `operator_server_log_copy.csv`: `docker exec deployspecter-live-pg cat /var/lib/postgresql/data/log/ds.csv > …`.
2. Hash every file in `docs/research/live_stepping/evidence/gate_a2_supervisor/<run>/`, and check that none contains the password.
3. Don't stage or commit any of it. Back it up privately, as with Gate A1.

## 5. Mandatory teardown, even after a stop

1. `docker stop deployspecter-live-pg`. It was started with `--rm` on tmpfs, so nothing persists.
2. Remove `.env`, and confirm that no password variable remains.
3. Confirm that port 55433 is free, and that `deployspecter-pg` has the same ID and start time and is still running.
4. Confirm that no driver or Gate 2A worker process remains, and that there are no tracked changes.

## 6. What the report must keep apart

- **Log-template agreement**, as the admin-session phases' statuses, against **database state**: the reconciled reads, the role catalog and the final read.
- **What was VERIFIED**, against what is UNOBSERVABLE by design: role creation, the snapshot values, the state between the snapshot and the worker, and what the `DO` block executed.
- **A successful run establishes characterization only,** on one synthetic instance. It doesn't establish Gate A2's demonstration, independent post-reset state, plain_serial attribution or Amendment 002's manual reproduction.
