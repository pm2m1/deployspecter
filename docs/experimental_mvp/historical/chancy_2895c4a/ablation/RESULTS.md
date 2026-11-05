# chancy-2895c4a: audit and code/schema ablations (NONBLIND development case, 2026-11-06)

**The 34-run record is intact.** Nothing under `../runs/` was changed. `audit_of_34_runs.json` (`research_tools/audit_historical.py`) re-checked every saved run against itself:
- frozen-file hashes, against the committed files;
- source-archive pins, and the version each container verified;
- the precondition schema;
- trace sha256 values, recomputed;
- corroboration;
- the case verdicts, recomputed by the frozen checker from the saved final states;
- teardown.

**34 runs, 0 problems.** All 12 forced runs share one statement-position sequence.

## Ablations

Every ablation run is at the clean code revision `97167f2`.

**How the ablations are applied.** Each one is a declared single-substring change to an **isolated build copy** of the fix tree `2895c4a`. The pinned archive is never modified. The mutated file's sha256 is declared to the adapter, and verified inside the container (`git-2895c4a+mutant:<id>`).

**The schema-only ablation** runs the parent's code on the schema the fix commit migrated (v8). That combination is compatible: v8 only adds a nullable `claim_id UUID` column, which the parent never references.

**Not run, by design:** the fix's code on the parent's v7 schema. That combination is incompatible, because the fix code reads and writes `claim_id`.

**The SQL check.** Every run also has an independent plain-SQL check: the cancelled job's row, read by id with `to_jsonb`, apart from the checker. It agrees with the frozen checker in **36/36 runs on H1**, and in **34/34 on H2** wherever H2 applies.

The fix's parts:
- **F1:** the claim query assigns `claim_id = gen_random_uuid()`.
- **F2:** the completion `UPDATE` is fenced, `AND claim_id IS NOT DISTINCT FROM %(claim_id)s`.
- **F3:** `cancel_job_ex` sets `claim_id = NULL`.
- **S:** the v8 column.

| Variant (forced schedule unless noted) | Runs | SOUND | H1 (cancel survives) | Final row (SQL) |
|---|---|---|---|---|
| control: parent `80f570f` | 2 | 2 | **violated 2/2** | `succeeded`, attempts 1 |
| control: fix `2895c4a` | 2 | 2 | holds 2/2 | `failed`, attempts 0, `claim_id` NULL |
| fix − F2 (`abl_no_fence`) | 3 | 3 | **violated 3/3** | `succeeded`, `claim_id` NULL (F3 cleared it; nothing fences the completion) |
| fix − F3 (`abl_cancel_keeps_claim`) | 3 | 3 | **violated 3/3** (and H2) | `succeeded`, `claim_id` still set (the fence still matches the old execution) |
| fix − F1 (`abl_no_claim_assignment`) | 3 | 3 | **violated 3/3** | `succeeded`, `claim_id` NULL (`NULL IS NOT DISTINCT FROM NULL` matches) |
| schema only: parent code on v8 | 3 | 3 | **violated 3/3** | `succeeded` |
| fix − F2, seeds 0–9 | 10 | 10 | violated 9/10 (seed 2: flush before cancel, as for the parent) | |
| fix − F3, seeds 0–9 | 10 | 10 | violated 10/10 (seed 2 by H2 only: the job is `failed`, its claim not cleared) | |

All 36 runs are SOUND, with 1,938 statements corroborated, 0 mismatched and 0 unchecked.

## The causal reading the data supports

**Within the fix commit's code, each of F1, F2 and F3 is necessary.** Removing any one restores the historical overwrite under the identical forced schedule. The mechanism is exactly the claim fence:
- the claim gives the execution an identity (F1);
- the completion writes only if that identity is still current (F2);
- the cancellation revokes it (F3).

**The schema change alone is not sufficient.** The parent's code on v8 still overwrites.

**Not shown:** that F1–F3 plus S, ported onto the parent, are *sufficient* without the commit's other changes. Those are `job.py` carrying `claim_id`, the executors passing it, and recovery and plugin hooks: 8 more files in `chancy/`. Building that port would mean writing a multi-file patch, which is no longer an ablation of the real code. So the precise statement is necessity of each part of the fence within `2895c4a`, plus the broader conclusion that the fix commit resolves the defect. It is not proven sufficiency of a minimal patch.

This is still NONBLIND and developer-encoded. Ablations are controls, not defects.

## Evidence (sha256 over all files, sorted)

| Directory | Digest |
|---|---|
| `control_parent` | `b41f3e2177536899` |
| `control_fix` | `e2f8d17fdd3e9668` |
| `fix_minus_abl_no_fence` | `6ad68761280f2296` |
| `fix_minus_abl_cancel_keeps_claim` | `8f6ad9395c575e11` |
| `fix_minus_abl_no_claim_assignment` | `d0387a052fc301bb` |
| `parent_code_on_v8_schema` | `6b7909d3f5feb2dd` |
| `fix_minus_abl_no_fence_explore` | `36dc581439229298` |
| `fix_minus_abl_cancel_keeps_claim_explore` | `436f80205f0a0293` |

## Reproduce (disposable PostgreSQL running; trees from `fetch_pinned.sh DIR --with-git`)

```bash
python research_tools/isolated/run_historical.py --source DIR/chancy-git-2895c4a.tar --mutant abl_no_fence --schedule forced --repeat 3 --out OUT/a
python research_tools/isolated/run_historical.py --source DIR/chancy-git-80f570f.tar --setup-source DIR/chancy-git-2895c4a.tar --schedule forced --out OUT/s
python research_tools/audit_historical.py docs/experimental_mvp/historical/chancy_2895c4a/runs
```
