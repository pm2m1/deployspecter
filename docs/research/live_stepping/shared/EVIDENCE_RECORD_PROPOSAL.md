# Shared evidence record (proposed)

- **Status:** nonnormative, **PROPOSED, NOT ADOPTED.**
    - Model: `src/deployspecter/research/shared/evidence_record.py`, `evidence-record/0-proposed`.
    - Tests: `tests/research_tracks/test_shared_evidence_record.py`.
- **Neither track imports it** until it is reviewed, which a test enforces.
- **No frozen file is changed** to add it.

## Fields

| Group | Contents |
|---|---|
| `context` | `synthetic_demonstration`, `development_calibration` or `heldout_evaluation` |
| `identities` | Scenario and application; workload schema version 2; spec digest; `vplan1` plan identity; registry, contracts, context and snapshot digests; snapshot source (`supervised_export`, `dry_run_fixture` or `none`) |
| `attribution` | Arm, replicate, attempt, defect, response id and use (`primary` or `exploratory_reuse`). **Source is always `harness`** |
| `method` | `deterministic_planner`, `literal_translation` or `none` |
| `stages` | Validation, planning, literal translation, catalog verification, bridge, construction, verification, execution, replay, oracle. Each has an outcome, a code, deterministic operation counters, and wall time (descriptive only) |
| `independent_verification` | `VERIFIED`, `FAILED` or `NOT_RUN` |
| `oracle` | `violation`, `no_violation` or `not_run`, with requirement ids. Kept apart from verification |
| `safety` | Which safety timeout was reached, an infrastructure failure, and `retried`, which is always false |
| `hashes` | Source and configuration files by repository path, as SHA-256 |
| `self_reported_provenance` | The document's own claim, carried as data only |

## Rules the model enforces

1. **Only `heldout_evaluation` records carry an arm,** and they must have harness-recorded arm, replicate, attempt
   and defect, plus a supervised catalog export.
2. **Arm attribution is never taken from `self_reported_provenance`.**
3. **An oracle result requires a `VERIFIED` starting state.**
4. **`primary_analysis_records` refuses any synthetic or development record,** so synthetic demonstration results
   can't enter the held-out primary analysis.
5. **No retried or replaced unit can be recorded.**

## Review needed before either track depends on it

- **The field set.**
- **Whether wall time is recorded at all,** or kept outside the record.
- **Whether `development_calibration` records need their own corpus digest field.**

## Revision 0.2 (parallel milestone 2, PROPOSED, NOT ADOPTED)

`evidence-record/0.2-proposed` adds:

- **A `track` field,** which must agree with `context`:

  | `context` | `track` |
  |---|---|
  | `synthetic_demonstration` | `track_a_demonstration` |
  | `development_calibration` | `track_b_development` |
  | `heldout_evaluation` | `track_b_evaluation` |

  **The tracks share the format, never results.** `primary_analysis_records` still refuses every record that isn't
  held-out evaluation.
- **Execution authority.** A record may contain an `execution` stage only if it also has all of these:
    - a `supervised_export` snapshot;
    - a `catalog_verification` stage with `MATCH`;
    - a `bridge` stage with `bridged`;
    - `independent_verification = VERIFIED`.

  An unverified catalog or bridge result can't authorize execution.
- **Attempt accounting:**
    - `construction_attempts` lists every attempt, with method, outcome, code and whether it was admitted, and at most
      one is admitted;
    - `candidate_failures` lists every rejected, unsupported or refused candidate;
    - a construction stage without listed attempts is refused.
- **Unchanged:** harness-only arm attribution, provenance as data only, no retries.

**Neither track's code imports the record yet,** and a test enforces it. Wiring it in is the next shared review
milestone.
