# Phase 9B typed bridge (step 5C.3): implementation and evidence

`src/deployspecter/live/phase9b_bridge.py`, tested by `tests/test_phase9b_bridge.py`.

- **Status:** research-only, pure, **unfrozen and uncommitted**.
- **Not touched:** no Phase 9B code was imported, run, copied or changed, and no database was used.
- **Unchanged:** `AMENDMENT_003_DRAFT.md` stays DRAFT, NOT ADOPTED.

## 1. What Phase 9B's matcher compares (task A, read-only)

Phase 9B's `seed_feasibility._same(a, b)` decides whether two values are equal everywhere its constructor matches
rows:

| Inputs | Rule |
|---|---|
| Either side `None` | Equal only if both are `None` |
| Either side a `bool` | Identity, so `True` matches only `True` |
| Otherwise | `float(a) == float(b)`, and if either fails to parse, `str(a) == str(b)` |

**Where it's used:**

- `_matches(requested, row)` as `_same(stored, requested)`, in bipartite row assignment and the "already present"
  decision during search;
- primary-key conflicts between seed rows;
- foreign-key ordering edges between seed rows;
- trigger attribution and existing-key lookup in `seed_feasibility`;
- the final presence check;
- reorder-change detection, which compares **stored values with each other**.

**Stored rows** come from `SELECT to_jsonb(t)` and are decoded by psycopg's jsonb loader:

- integer columns decode as `int`, booleans as `bool`, and text as `str`;
- `float8` decodes as an `int` for integral values, a `float` otherwise, or the strings `"NaN"` and `"Infinity"`;
- −0 decodes as `0`.

**What follows:**

- **The stored side is arbitrary.** Requested values are compared against every row in the table, including setup
  rows and rows that triggers or defaults created. Restricting the values we send doesn't, by itself, make the
  comparison exact.
- **Cross-type numbers conflate:** `1` equals `1.0`.
- **Integers at or beyond 2^53 conflate:** `float(2^53 + 1) == float(2^53)`. So a requested `2^53` would match a
  stored `2^53 + 1`, and the safe bound is `|v| < 2^53`.
- **Numeric-looking strings conflate:** `"1.0"` equals `"1"`, and `"01"`, `"1e3"`, `" 1"`, `"1_000"`, `"١٢"`
  (Arabic-Indic digits) and `"１"` (full-width) all parse. `"nan"` never equals itself.
- **Exact cases:** a requested string that doesn't parse as a float compares by exact `str` equality against any
  stored value, because `float()` of it raises. Booleans and NULL compare exactly.
- **Columns the request omits are never compared,** so a trigger or default can set them to anything without the
  matcher noticing.
- **Reorder-change detection** compares stored values loosely in columns the request doesn't name. No input
  restriction can fix that.

## 2. API and trust boundary

```
bridge_to_phase9b(request, *, validated, registry, contracts, context, context_trust, snapshot, snapshot_trust,
                  verification_policy=None, planning_budget=None) -> Bridged | BridgeRefusal
```

**Nothing given is trusted as given.**

1. **The request is regenerated.** It must be a `StatePlanningRequest` or `LiteralRequest` whose digest recomputes. It's
   then regenerated with `plan_state` or `translate_literally`, from the validated workload, registry, contracts,
   pinned context and planning budget, and must equal the given request exactly. That re-runs `check_validated`, the
   context pin and the plan identity.
2. **The catalog verification is recomputed,** with `verify_context` from the pinned snapshot. There is **no
   parameter for a verification result**, so a forged `MATCH` can't be passed in.
3. **Bindings must agree:** the verification's context digest and snapshot digest must match the request's, and the
   request's application must be the registry's.

**`UntrustedBridgeInput` is raised for:**

- a forged or re-digested request, or a changed digest;
- a stale or altered plan identity, such as a request from another workload;
- mismatched contracts;
- a stale or re-pinned planning context;
- a mismatched or re-pinned snapshot, or a forged snapshot pin;
- inputs that no longer produce a candidate.

**`TypeError`** is raised for the wrong object types.

**Excluded:** hostile code in the same process, the authenticity and freshness of the snapshot's origin, and
anything about a live database.

## 3. Typed representation

`BridgeRepresentation`:

- **Identity and provenance:** `bridge_version`; `method` (`deterministic_planner` or `literal_translation`) and
  `method_version`; `request_digest` and `plan_identity`; the registry, contract, context and snapshot digests; and
  the application.
- **Order:** `candidate_order`, the request's canonical order, and `seed_order`, the dependency order.
- **`rows`**, in seed order. Each `BridgeRow` has:
    - its `row_id`, goal label and ordinal;
    - its `candidate_index` and `seed_position`;
    - its entity and table;
    - typed `values`, each with field, column, `pg_type`, type tag, exact value, origin and legacy status;
    - `omitted` columns with their reasons;
    - `depends_on`;
    - obligations.
- **Request-level obligations.**
- **`typed_digest`:** SHA-256 over `b"deployspecter:phase9b-bridge:v1\0"` plus canonical JSON, with tag-encoded values
  (decimal integers, `float.hex()`, `true` and `false`, raw strings, `null`). It's pinned in a test.

## 4. Legacy projection matrix

| Value | Column | Typed encoding | Legacy equivalence | Projection |
|---|---|---|---|---|
| int, \|v\| < 2^53 | integer types | Preserved | **Equivalent** under the stated assumptions | Python `int` |
| int, \|v\| ≥ 2^53 | integer types | Preserved | **Refused**: conflates with neighbouring stored integers | `matcher_ambiguous_integer` |
| finite float, not −0.0 | `double precision` | Preserved | **Equivalent**: stored float8 decodes to an exact number or a NaN or Infinity string | Python `float` |
| −0.0, NaN, ±∞ | — | — | **Refused** (unreachable through regeneration) | `uncastable_value` |
| bool | `boolean` | Preserved | **Equivalent**: identity | Python `bool` |
| str, not float-parsable | `text` or `varchar`, deterministic collation | Preserved, no normalization | **Equivalent**: exact `str` equality | Python `str` |
| str, float-parsable | text | Preserved | **Refused** | `matcher_ambiguous_text` |
| str with a nondeterministic collation | text | Preserved | **Refused** | `uncastable_value` |
| any value of the wrong type for its column | — | — | **Refused** | `uncastable_value` |
| explicit NULL | nullable | Preserved | **Equivalent**: `None` matches only `None` | Listed with `None` |
| omitted column (default, generated, nullable unset) | any | Preserved as omitted | **Unverified**: never compared | Not listed, and named in `not_compared` |

**Assumptions for "equivalent"**, recorded in every projection:

1. Phase 9B decodes stored rows with `to_jsonb` and psycopg's default jsonb loader.
2. Stored values have their column's type, as the MATCHed snapshot states.
3. Text collations are deterministic.

**Legacy behaviour the bridge can't verify,** also recorded:

- loose stored-to-stored reorder-change detection;
- Phase 9B counting a requested row as already present when a trigger or setup row matches it.

**A projection's status is one of two:**

- **`equivalent`:** no omitted columns;
- **`equivalent_for_listed_columns_only`:** some columns are omitted, and those aren't compared.

If any value is refused, there's **no projection**. A refusal still carries the typed representation, except a
dependency cycle, which has no order. `BridgeRefusal` has no projection field at all.

**`Bridged` is never execution authorization.** It carries `execution_authorized: False` and
`feasibility: candidate_pending_postgresql_validation`, and neither can be set otherwise. Its validator refuses
construction unless:

- every value in the representation is equivalent;
- there are at most 10 rows;
- the projection is exactly the representation's own, with a legacy digest that recomputes;
- the typed digest recomputes.

So a `Bridged` result can't be assembled from a refused representation or a foreign projection, except by bypassing
validation in the same process.

**Digests:**

- **The typed digest** identifies the representation.
- **`legacy_seed_sha256`** is Phase 9B's own formula, `sha256(json.dumps([SeedRow.model_dump()], sort_keys=True))`.
  A golden test recomputes it independently through this branch's release `SeedRow`, which is byte-identical, and
  pins it.
- **A matching legacy digest doesn't establish equivalence.** The planner and the translator produce identical
  legacy digests for the same rows, but different typed digests, because the method differs. That's tested.

## 5. Refusals, ordering and limits

| Refusal code | When |
|---|---|
| `context_not_matched` | The recomputed verification isn't `MATCH` |
| `dependency_cycle` | The foreign-key dependencies form a cycle. It's refused, never broken |
| `matcher_ambiguous_integer`, `matcher_ambiguous_text` | The legacy matcher can't compare the value exactly |
| `uncastable_value` | A type mismatch, a non-finite or negative-zero float, or a nondeterministic collation |
| `too_many_rows_for_phase9b` | More than 10 rows. Never truncated or split; the full typed representation is returned |

- **Order:** Kahn's algorithm, parents first, with ties kept in candidate order. A self-reference needs no edge.
- **Identity:** each row records both its candidate index and its seed position.
- **Obligations added:**
    - `explicit_value_with_database_default`: a sequence behind the column isn't advanced, so its state must be
      verified later;
    - `trigger_rows_may_satisfy_or_change_requested_rows`.

## 6. Tests and breakage

| Check | Result |
|---|---|
| Ruff | Clean |
| Bridge tests | **80 passed** |
| All contract, planner, verifier, translator and bridge suites, root environment, `-m "not live"` | **1,121 passed**, 1 skipped (the LangGraph adapter file; `langgraph` isn't installed there) |
| Same, isolated environment | **1,169 passed** |
| v1 offline tests | 37 passed |
| Frozen-contract integrity | Passed; committed steps 5B, 5C.1 and 5C.2 unchanged |
| Live tests | 0 collected |

`MODEL_API_KEY` was unset throughout.

**Coverage:**

- **Values:** int64 and binary64 preservation; the 2^53 boundary in both directions; boolean against numeric; twelve
  numeric-looking strings, including Unicode digits, and seven safe ones; NULL against missing; signed zero and
  special floats; Unicode without normalization.
- **Ordering and limits:** foreign-key ordering, ties, self-references and cycles; 10 and 11 rows.
- **Obligations:** trigger uncertainty and sequence-key flags.
- **Trust:** forged planner and literal requests; changed digests; stale workloads, contracts, contexts and snapshots;
  forged snapshot pins; a non-MATCH snapshot; no verification parameter at all; wrong types.
- **Determinism and identity:** pinned typed and legacy digests; byte-identical output; no mutation of inputs.
- **Isolation:** no database, engine, oracle, Phase 9B or release-models import in the module, checked through imports,
  the AST and the network.

**Deliberate breakage: 39 of 41 caught.** The mutants cover the trust checks, type preservation and legacy
classification, refusals, ordering, identity, NULL handling and obligations. The two survivors are equivalent on
every reachable input:

- **Removing the application check.** A regenerated request always carries the registry's application.
- **Removing the type check at the top.** Regeneration raises the same `TypeError`.

**Five earlier survivors were test gaps, now closed:**

- a forged literal request;
- a regeneration that yields no candidate;
- a boolean posing as an integer;
- bytes posing as a string;
- tie ordering between independent rows.

## 7. Assumptions that need database-backed verification

Each needs a supervised, disposable PostgreSQL instance and separate approval. None has been tested.

| Assumption | What would establish it |
|---|---|
| D1. Stored rows read through `to_jsonb` decode as `int` for integer columns, `bool` for booleans, `str` for text, and an exact number or a NaN or Infinity string for float8 | A decoding test with psycopg's default jsonb loader at the pinned driver version |
| D2. psycopg binds projected `int`, `float`, `bool`, `str` and `None` so the stored value equals the typed value exactly, at 2^53 − 1, the binary64 extremes, the smallest subnormal and in Unicode | Round-trip insert and select tests per column type |
| D3. No string is ever bound to a non-text column (enforced offline), and how strings are bound to text columns doesn't alter them | A driver binding test |
| D4. A MATCHed snapshot describes the live database at the time of the run | The supervised exporter, with re-verification immediately before each run |
| D5. Triggers fire as under `session_replication_role = origin` | A session-setting check in the supervised environment |
| D6. Text columns compare by code point under their deterministic collations | A collation test on the snapshot's collations |
| D7. Omitted columns, trigger-produced rows, "already present" decisions, sequence state after explicit keys, and stored-to-stored reorder checks behave as each obligation describes | Phase 9B construction under supervision. A separate, later integration |

## 8. Remaining PostgreSQL verification obligations

- **Omitted columns:** defaults, generated values and unset nullable columns are never compared by the legacy
  matcher.
- **Triggers:** trigger-produced rows and BEFORE-trigger changes; Phase 9B's "already present" decisions.
- **Sequences:** their state after explicit key inserts.
- **Driver behaviour:** psycopg's binding of the projected scalars, and its default jsonb decoding (assumption 1).
- **Unverifiable legacy behaviour:** Phase 9B's loose stored-to-stored reorder checks.

## 9. Is a safe SeedRow projection possible without changing Phase 9B?

**Yes, within the restricted domain in section 4, and with limits:**

- **Exact cases:** every projected value's comparison by the legacy matcher agrees with PostgreSQL equality under the
  stated assumptions.
- **Refused cases:** integers at or beyond 2^53, numeric-looking strings, nondeterministic collations, and anything
  that would need a cast.
- **What this doesn't make sound:**
    - Phase 9B's checks of omitted columns;
    - its stored-to-stored reorder detection;
    - its "already present" semantics.

  Those stay PostgreSQL obligations.

Type safety wasn't weakened to fit the legacy matcher. Values it can't compare exactly are refused.

## 10. Integration blockers (unchanged from the design)

- **Amendment 003 isn't adopted.**
- **Oracle reach:** Phase 9B's transitive import of oracle-bearing `Case` objects.
- **Supervision:** it runs outside Gate 2A's supervision.
- **Schema setup:** it needs `setup_sql`, while live applications use migrations.
- **Shared schema names:** its fixed schema names would collide between concurrent runs.
- **Time budget:** it uses a wall-clock budget.
- **The row cap:** it accepts at most 10 rows.
- **No exporter:** there's no live catalog exporter yet.
- **Unconfirmed driver behaviour:** psycopg binding and decoding.
- **Provenance:** Phase 9B is uncommitted, with no frozen hash.
- **The legacy matcher itself.** Any fix to it must be a new, separately reviewed module, so historical Phase 9B
  results stay reproducible.
