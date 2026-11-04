# Gate A1 version 4: evidence index

A non-sensitive index of the fourth Gate A1 run, the only one so far that the driver classified `CHARACTERIZED`. It holds digests and a directory reference only. The raw evidence is local and uncommitted: the approval and its signature, the operator's copy of the server log, and the driver's JSON records. This file contains none of their contents.

## The run

| | |
|---|---|
| Commit | `d8274a5034bf55669d2a1f22ddfa238648c2914e` |
| Manifest | `docs/research/live_stepping/track_a/gate_a1_manifest.json`, version 4 |
| Manifest SHA-256 | `1590d23ea9dec1e9e7ca903daf2b52b15c0404e3b496c4e5d7d714bcb2b02fb5` |
| Run date | 2026-11-04 (UTC), one attempt, not retried |
| Server | PostgreSQL 16.15 (`server_version_num` 160015), disposable container `deployspecter-live-pg` on `127.0.0.1:55433`, tmpfs storage, torn down afterwards |
| Driver result | exit status 0, outcome `CHARACTERIZED` |
| Findings | `catalog_match`, `state_reads_exact`, `bindings_hold`, `every_parent_session_reconciled` and `every_session_attributed` are all true |
| Recorded, non-relied-on findings | `to_jsonb` gives float8 `-0.0` as `0`, and NaN and ±Infinity as strings |
| Evidence kind | synthetic characterization; not an evaluation, not a detection, not held-out evidence |

## Evidence directory

`docs/research/live_stepping/evidence/gate_a1/20261104T144603Z-9df2a36b/`

| File | Bytes | SHA-256 |
|---|---:|---|
| `approval.json` | 187 | `3b31e9b3714f5610878d3984fb3d1baca23d9657d28c7c8bbf48975bc97ada78` |
| `approval_provenance.json` | 510 | `ca5f96d19a98e83e50b8cee37111d71d0421372683263e67f7778a85ac94d5d2` |
| `preflight.json` | 1054 | `18048dc943c13cdbe28cc4b8596745a4cc9b636e4f9def312bcbaab63b06f170` |
| `catalog.json` | 11258 | `22752c084a9f282e3c83d4d19b7129a66feb78975ffdb1447487a751edbcb29b` |
| `states.json` | 685 | `6eddbe02a76290deba333238ca0847dc8ecc36c4fbe6a4d7cd4c98eae24b81fb` |
| `bindings.json` | 274 | `272672ff2eca5ece77988680a779e25dda9c4c7f96a61d34a1699d80aa69fb3b` |
| `parent_reconciliation.json` | 1631 | `61af1bd7985cc62eedaee4ec0345cb827da0306a6f7eb9ea7adb14067c03aa68` |
| `batch_audit.json` | 881 | `74f2f08d7598da0a2da294403a49653858e27478c5c792064e75a68a7e031d02` |
| `summary.json` | 349 | `832bca86c6a2dae3bb91461f3530027cd5e57309b909f5a442cd28f8485be882` |
| `operator_server_log_copy.csv` | 96700 | `d7fa9279b81c4f1b057dc8700cc191bfa15fe8cbbd01a2685e65ebc6500361e4` |

The detached signature over `approval.json` is kept with the owner's approval files, outside the repository, with SHA-256 `ec9ddc04cd6ae9c71bb48a175b585131fd22af9c65905b018104b87e7905c2c2`. The evidence copy of `approval.json` is byte-identical to the signed file.

## What the recorded files say

- Parent reconciliation covers 13 tagged sessions, all reconciled, with these statement counts:

  | Operation | Statements |
  |---|---:|
  | op0p | 3 |
  | op0r (empty reset) | 6 |
  | op2a | 12 |
  | op2b | 12 |
  | op2c | 7 |
  | op0r (seeded reset), each of two | 7 |
  | op6i, each of two | 9 |
  | op6f, each of two | 5 |
  | op7b | 32 |
  | op9c | 2 |

- The batch audit passed with no problems, over 14 sessions: the log reader and the 13 tagged sessions.
- The two exports have the same digest, `bf09fc51…`, and the re-read agrees. `verify_context` gave `MATCH`, and there were no time-typed columns.
- Both typed-state repeats had no problems. Both reads have digest `35e330f8…`.

## Earlier runs (unchanged, kept as stopped)

| Run | Directory | Result |
|---|---|---|
| Version 1 | `20261104T113256Z-d0b21a60` | refused at op0p |
| Version 2 | `20261104T132842Z-1c432509` | refused at the seeded op0r reset: psycopg auto-prepared the log reader's poll |
| Version 3 | `20261104T140952Z-58f6e18e` | refused at op7b: float8 `-0` against an expected `-0.0` |

On 2026-11-04 all their file digests still matched the operator reports.

## Handling

- The evidence directory is untracked and **not ignored** by Git: `git check-ignore` matches nothing. A broad `git add` would stage the raw server logs and the personal approvals, so stage exact paths only.
- After the run, every file in this directory was checked for the database password, and none contains it.
- The evidence exists only in this worktree. The owner should keep an independently verified private backup (a copy checked against the SHA-256 values above), because nothing else preserves it.

## What it supports

It supports characterization only, on this synthetic PostgreSQL 16 instance, with psycopg 3.3.6, automatic preparation disabled and `extra_float_digits=1`. It doesn't establish:

- Gate A2 correctness;
- production isolation;
- a real rolling-deployment defect;
- Muse superiority;
- any preregistered result.
