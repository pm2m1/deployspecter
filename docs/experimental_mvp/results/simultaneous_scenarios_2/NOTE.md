# simultaneous_scenarios_2: the full scenario set at the fixed revision (NONBLIND), `fd592d7`

One run per scenario under `alternate`. **All 9 SOUND**: isolated, coordinated to completion, corroborated. Together: 1720 statements, 0 parameters mismatched, 0 unchecked; teardown clean; the protected container unchanged.

| Scenario | Distinct sessions | Window (grants by version) | Final state | Properties |
|---|---|---|---|---|
| `old_to_new` | 3 | 65 / 65, 129 version switches | jobs 0–2 pushed by 0.25.1, run by 0.26.0 | clean |
| `new_to_old` | 3 | 63 / 63 | jobs 0–2 pushed by 0.26.0, run by 0.25.1 | clean |
| `distinct` | 4 | 48 / 48 | each version ran its own queue | clean |
| `compete` | 4 | 35 / 35, 4 claim queries each | 0.25.1 ran jobs 0 and 1, 0.26.0 ran 2 and 3; each once | clean |
| `retry_handoff` | 3 | 28 / 29 | failed under 0.25.1, retried and succeeded under 0.26.0 (attempts 2, errors 1) | clean (P8 holds) |
| `cancel_new_new` | 2 (0.26.0 only) | n/a | `failed`, attempts 0: the cancel held | clean (**G** holds) |
| `cancel_old_old` | 2 (0.25.1 only) | n/a | `succeeded`: the cancel was overwritten | **P7 (X)** |
| `cancel_new_old` | 2 | 23 (0.25.1) / 4 (0.26.0) | `succeeded`: the 0.26.0 cancel was overwritten by the 0.25.1 completion | **P7 (X)** |
| `cancel_old_new` | 2 | 4 (0.25.1) / 23 (0.26.0) | `succeeded`: the 0.25.1 cancel was overwritten by the 0.26.0 completion | **P7 (X)** |

The interpretation of the P7 results is in `../simultaneous_scenarios_1/NOTE.md`, "What the P7 results mean". This set reproduces every outcome of the first one, and adds sound results for `compete` and `retry_handoff`.

To see a run's statement-level interleaving, in global grant order with its version, actor, pid and lock waits:

```bash
python research_tools/coordinated_summary.py --interleaving <run dir>
```
