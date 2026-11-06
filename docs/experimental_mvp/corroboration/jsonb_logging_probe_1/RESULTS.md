# jsonb parameter logging (2026-11-06): CONFIRMED, and fixed narrowly

**The claim.** The onboarding pilot (saq attempt 2) reported that the server logs a jsonb parameter with its keys
reordered. It is confirmed on PostgreSQL 16.15 (`log_statement=all`, `log_parameter_max_length_on_error=0`), and
the raw rows are in `raw_csvlog_rows.csv` and `probe.json`.
- **jsonb parameters:** logged through jsonb's output function, whether sent as an untyped `str`, a `str` cast with
  `::jsonb`, or psycopg's `Jsonb`. Keys are reordered, a duplicate key keeps its last value, and `1e2` is logged as
  `100`.
- **json parameters:** logged verbatim.

**The impact.**
- **Affected:** an application that binds JSON *text* to a jsonb parameter. The trace keeps only a digest of the
  exact text, so corroboration reported a false mismatch: `before_fix/` shows 3 mismatched out of 5 (exit 0, raw
  log rows kept).
- **Not affected:** `Jsonb` and `Json` values were already compared by value.

**The fix.**
- **Trace side (`stepping.sanitize`):** a `str` that is a JSON object or array, and whose PostgreSQL jsonb
  normalization differs from it, also records `jsonb_sha256`, the digest of that normalization. Every other string's
  trace entry is unchanged.
- **Comparator side (`corroborate._value_matches`):** after the exact-text comparison fails, a logged value is
  accepted only if it is itself jsonb output (a normalization fixpoint) **and** its digest is `jsonb_sha256`.
- **Never accepted:**
  - a different value;
  - `1` vs `1.0` (numeric scale is kept);
  - a non-fixpoint text;
  - a string where a number was sent;
  - scalar JSON texts that another type would also rewrite, such as `' 5 '` logged as the integer 5.

**The normalizer (`jsonb_text.normalize`)** reproduces `jsonb_in` and `jsonb_out`. It agrees with PostgreSQL 16's own
output on all 60 captured vectors (`tests/experimental/fixtures/pg16_jsonb_out.json`, via
`research_tools/capture_jsonb_out.py`), including 16 inputs PostgreSQL rejects.

**After the fix (`after_fix/`):** 5 of 5 compared, 0 mismatched, 0 unchecked.
**Tests:** `test_jsonb_log_corroboration_offline.py`, plus `test_jsonb_log_corroboration_live.py`, which runs live.

The pilot's own evidence is unchanged. Its saq attempt 2 stays as recorded (failed, then passed with a declared
adapter deviation).
