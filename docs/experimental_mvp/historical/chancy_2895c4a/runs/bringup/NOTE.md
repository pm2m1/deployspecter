# Bring-up of the chancy-2895c4a case runner (both runs kept; made at the uncommitted state just before `run_historical.py` was committed)

| Run | Revision | Verdict | Case oracle | Final state |
|---|---|---|---|---|
| `parent_80f570f_forced` | buggy parent | SOUND, 53 statements corroborated, 0 mismatched | **H1 VIOLATED**: the cancelled job ends `succeeded` | `succeeded`, attempts 1 |
| `fix_2895c4a_forced` | fix commit | SOUND, 53 statements, 0 mismatched | **HOLDS** (H1, H2) | `failed`, attempts 0, `claim_id` NULL, `completed_at` set |

Both runs had the identical statement schedule:
- grant 17: push;
- grant 23: the worker's claim (1 row);
- grant 24: the cancel (1 row), committed at 26;
- grant 43: the completion flush, committed at 44.

The final state differs. The full set of runs is in `../final_*`.
