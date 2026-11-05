# Typed-parameter verification: fresh disposable PostgreSQL runs at `02bf736` (clean)

**Purpose.** Each of the original rolling-upgrade runs (`../runs/`, unchanged) left **20 parameter values unchecked**: values the server-log reconciler counted but couldn't compare.
- **15** were the `jobs` argument of `procrastinate_defer_jobs_v1(%(jobs)s::procrastinate_job_to_defer_v1[])`: a 1-D array of composite records, one per defer.
- **5** were the `seconds_since_heartbeat` float of `procrastinate_prune_stalled_workers_v1`.

**What PostgreSQL logs** (probed live on PostgreSQL 16, captured verbatim in `tests/experimental/fixtures/pg16_typed_params.json`):
- **The composite array** is the server's text rendering of an array of record literals. procrastinate passes a list of tuples `(varchar, varchar, int, text, text, Jsonb, timestamptz)`.
  - Inside a record, an empty unquoted field is NULL, an unquoted `NULL` is the 4-character string, and `""` is the empty string.
  - jsonb fields appear in their canonical form, and timestamptz fields in UTC.
- **float8** is the server's shortest round-trip output: `'30'`, `'0.1'`, `'1e-300'`, `'-0'`, `'NaN'`, `'Infinity'`.

**Comparison now implemented** (`corroborate.py`). Nothing was loosened:
- **Arrays:** a strict 1-D array tokenizer, where quoted elements may contain braces and a nested or malformed array is refused (unchecked).
- **Records:** a record-literal parser following `record_in`'s rules. Each field is compared by the existing exact scalar rules: string digest, exact int, NULL, jsonb by canonical value, timestamptz by instant. A field-count mismatch is a mismatch.
- **float8:** compared bit for bit after parsing, including the sign of zero, NaN and the infinities.
- **Anything unparseable** stays UNVERIFIED and is counted, never accepted.

**Adversarial offline tests.** `tests/experimental/test_typed_params_offline.py`, 26 tests:
- every captured vector compares True;
- every single-field mutation compares False: an int ±1, a string digest, NULL↔value, jsonb, a timestamp +1 µs;
- an ulp step, a zero sign and NaN-vs-number compare False;
- unterminated quotes, nested arrays, unquoted parentheses and dimension decorations are refused.

The 57 existing reconciliation vectors still pass.

## Results (fresh disposable PostgreSQL)

| Run | Phases SOUND | Statements | Values compared | Mismatched | Unchecked | Same schedule's original (`../runs`) |
|---|---|---|---|---|---|---|
| `alternate_1` | 7/7 | 282 | **214** | **0** | **0** | 282 statements; 194 compared, 0 mismatched, 20 unchecked |
| `seeded_2` | 7/7 | 315 | **236** | **0** | **0** | 315 statements; 216 compared, 0 mismatched, 20 unchecked |

**Every one of the formerly unchecked values now compares exactly, and none mismatches.** The properties are as before:
- R1, R3, R4 and R6 hold;
- the R2 equality entries are erratum E1;
- `seeded_2` again shows the same non-atomic R5 read (erratum E2): the same seed gives the same interleaving.

**Digests**, computed from the repository root (the digest hashes `shasum` lines, which include paths):
- `alternate_1`: `b495746d497c7c06`;
- `seeded_2`: `a5c62dd127e26547`.
