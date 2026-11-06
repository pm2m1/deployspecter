# ds-p9x/1.1 Phase 9B technical assurance (A1–A4), 2026-11-06

Extension version **ds-p9x/1.1**, catalog **catalog/1.1**, attribution **same_v1.1**. The original Phase 9B files
(`state_construction/phase9b_source/`) are unchanged, and their pins are verified. Earlier evidence
(`runs_p9x/chancy_1..3`, `procrastinate_1..2`) is untouched.

## A1: the `_same` substitution

**Defect found and fixed.**
- v1 keyed jsonb semantics on the requested value's *text*: any string equal to a registered jsonb text got
  jsonb comparison.
- The constructor also compares stored-vs-stored values (`state_constructor.py:481` and `:489`). There, a stored
  text-column value that coincidentally equalled a registered jsonb text was compared by jsonb rules, not by the
  original rule.
- `test_v1_text_keyed_logic_diverged_from_the_original_on_stored_vs_stored_text` reproduces this with v1's logic,
  copied verbatim.

**same_v1.1 is identity-based.**
- A requested jsonb value is a `JsonbText` (a `str` subclass carrying its parsed value). It is placed into the seed
  with `model_construct`, so pydantic doesn't coerce it.
- The constructor passes seed rows by reference.
- Only `JsonbText` arguments get jsonb semantics. Every other call is the original `_same`, by construction.
- A lost marker is refused before construction.

**Tests:** `test_p9x_assurance_same.py`, hypothesis property tests.
- 6000 random pairs over ints, big ints, bools, floats (including -0.0, NaN and ±∞), Decimal, strings (numeric-,
  JSON-, uuid- and timestamp-looking) and nested lists and dicts: identical to the original, including raising.
- 3000 jsonb values in either argument position.
- Named adversarial cases: key order; `"1"` vs `1` vs `true` vs `"true"`; JSON null vs SQL NULL (attribution can't
  tell them apart, the readback does); ±(2^53−1); a JSON string `'{"a": 1}'` vs an object.
- Refusals: duplicates, ±2^53, fractions, exponents, NaN, malformed JSON.
- A live round trip of 13 adversarial jsonb values (`test_p9x_assurance_catalog.py`).

## A2: catalog-expression equivalence

The text-only allowance is no longer sufficient. Each expression-only difference must be established on
**PostgreSQL's parsed node trees** (`pg_index.indpred` and `indexprs`, `pg_constraint.conbin`, `pg_attrdef.adbin`)
in `expr_equiv.py`. The trees must be identical after only:
- **R0:** drop `:location` and `:relabelformat`;
- **R1:** `ArrayCoerceExpr(ArrayExpr, elemexpr = RelabelType(CaseTestExpr))` → an `ArrayExpr` of element-wise
  `RelabelType`. That is the same array value, because a relabel is binary-compatible and applying it per element
  is exactly what the array coercion does.

The object's non-expression attributes must also be identical (access method, keys, operator classes, collations,
options, uniqueness, NULLS NOT DISTINCT; for CHECK, the no-inherit and validated flags).

| Case | Result |
|---|---|
| Chancy's `IN (...)` and `NOT IN (...)` partial indexes (live, and in the real Chancy run) | established (R0, R1) |
| `s IN ('a','b')` vs `s IN ('a','c')` | **refused**: the parsed expressions differ |
| `CHECK (n > 0)` vs `(n > 1)`, and vs `(n >= 0)` | **refused** |
| `DEFAULT 'x'` vs `'y'`; `DEFAULT 1` vs `2 - 1` | **refused** (no constant folding is assumed) |
| The same predicate on a unique vs a non-unique index | **refused**: the non-expression attributes differ |
| An expression over a schema's own enum type (OIDs differ between schemas) | **refused** (supported-scope limit) |
| Function or trigger definition differences | **refused**: strict, unchanged |
| Unparsable node trees | never equivalent |

## A3: construction-session provenance

The construction session (`application_name = ds_p9x_construct`) is **journaled and reconciled with PostgreSQL's
csvlog** between two markers, written by a separate audit session (`ds_p9x_audit`), in `provenance.py`:
- **Identity:** the backend pid, and the connection authorized with the expected user, database and application.
- **Statements:** every one exactly, in order (text normalized for whitespace, placeholders and identifier quotes).
- **Boundaries:** `BEGIN`, `COMMIT` and `ROLLBACK`.
- **Kinds:** admin (settings, locks, fingerprint, snapshots) and construction (the planned INSERTs) are
  distinguished.
- **Parameters:**
  - str, int, bool and NULL as the server renders them;
  - float bit-exact;
  - timestamptz as the same instant to the microsecond. The server logs a cast parameter through the type's output
    function. This was found when the first real Chancy run was refused (`runs_p9x/chancy_4_assurance`, exit 1).
  - Anything else is counted as unchecked, which **fails** the audit.
- **The window:** no other session and no unallowlisted audit statement may appear in it. An unaccounted session
  makes the outcome `refused_provenance`, and the state is not used.

**Not audited by the log:** row effects such as trigger outputs and the final state. Those are checked by the
in-transaction change comparison and the independent readback, which are database reads, **not** a server-log
audit. The readback session and the Phase 9B sandbox sessions are not log-audited either.

**Real Chancy (`runs_p9x/chancy_5_assurance`, at `b14c6d7`):**
- single: 44/44 statements, 38 parameters checked, 0 unchecked;
- multi: 48/48 statements, 110 parameters checked, 0 unchecked;
- worker corroboration (47 and 191 statements, 0 mismatched, 0 unchecked) and the oracle are unchanged, and the
  final state equals the direct constructor's.

## A4: failure atomicity (`assurance/run_2`, `run_3`; `test_p9x_assurance_atomic.py`)

| Injected at | Raised | Target after | Server-log record of the attempt |
|---|---|---|---|
| Before writes (after the fingerprint check) | `Injected` | 0 rows, 0 audit rows, 0 sessions, 0 locks | reconciled: the BEGIN … ROLLBACK journal matches the log |
| During writes (after the 2nd insert) | `Injected` | empty | reconciled |
| Before verification | `Injected` | empty | reconciled |
| After verification, before COMMIT | `Injected` | empty | reconciled |
| ROLLBACK impossible (backend terminated by another session) | `Injected` (psycopg logs "error ignored in rollback") | empty: the server aborted the transaction | **not reconciled, correctly**: the terminating session is unexplained in the window |
| SIGINT (default handler) | `KeyboardInterrupt` | empty | reconciled |

Interruption during the sandbox stage and an interrupted write were already covered (`test_p9x_live.py`). Sandbox
teardown dropped the constructor's database and role after every run.

## Limits (fail-closed)

- A requested timestamptz must be in the session's `to_jsonb` form (UTC, `+00:00`). Phase 9B's attribution compares
  that text, so another offset is refused as IMPOSSIBLE.
- Expression equivalence covers only built-in OIDs and rules R0–R1.
- Parameter types other than str, int, bool, NULL, float and timestamptz fail the audit.

## Evidence

| Path | Revision | Exit | Result |
|---|---|---|---|
| `assurance/run_1_uncommitted_tree` | `5a380d2` + uncommitted A3/A4 tree | 0 | as expected (kept; superseded) |
| `assurance/run_2` | `29d9381` | 0 | as expected |
| `assurance/run_3` | `b14c6d7` | 0 | as expected |
| `runs_p9x/chancy_4_assurance` | `29d9381` | **1** | refused_provenance (the timestamptz reconciler defect) |
| `runs_p9x/chancy_5_assurance` | `b14c6d7` | 0 | as expected |
| `runs_p9x/procrastinate_3_assurance` | `29d9381` | 0 | refused_triggers (reproduces `procrastinate_2`) |
