# WorkloadSpec v2 freeze-readiness audit

**Recommendation: NOT READY to freeze.** Four blockers remain, B1 to B4. All are small, offline fixes, and two of them
change the schema, so they must land before the freeze, not after.

- **Date:** 2026-11-03
- **Audited commit:** `e1fe2fa02b879ae9e9dc2e63772489883d0a376e` on `research/live-stepping`, local only
- **Status:** WorkloadSpec v2 remains a candidate. This report isn't committed and freezes nothing.

## 1. Files and version identifiers

| Layer | Module | Version identifier | Last changed |
|---|---|---|---|
| Structural parser | `live/workload_spec.py` | `schema_version: 2`, `$id` `urn:deployspecter:workload-spec:2`, `x-status` candidate | `f1f4d70` |
| Candidate JSON Schema | `workload-spec-v2.candidate.schema.json` | as above, generated from the parser | `fa07b58` |
| v1 compatibility | `live/workload.py` (Gate 2A, frozen) plus `upgrade_v1` | v1 documents carry `schema_version: 1` | `cee464a` |
| Entrypoint registry | `live/registry.py` | `registry_version: 1`, pinned SHA-256 digest | `a26038d` |
| Argument validator | `live/argument_validation.py` | `contracts_version: 1`, pinned SHA-256 digest | `f1f4d70` |
| Plan identity | `live/workload_identity.py` | `vplan1:`, domain `deployspecter:validated-plan-identity:v1\0` | `e1fe2fa` |

**Environment:** CPython 3.12.14 and pydantic 2.14.2, in both the root environment and the isolated Gate 2B
environment.

## 2. Contract invariants

1. **One direction.** Each stage takes only the previous stage's typed output:
   `parse_workload → authorize → validate_arguments → validated_plan_identity`. Each refuses other Python types with
   `TypeError`.
2. **Nothing self-reported is authoritative.** Provenance, rationale, the specification's name, source references and
   carried digests decide nothing. Arm membership is recorded by the harness.
3. **Trust comes only from harness-built objects.** `TrustedRegistration` and `TrustedContracts` each hold a digest
   pinned from a separate source. A contract set binds to exactly one registry and one application.
4. **No coercion or repair.** A value is refused, never converted, clipped, defaulted or reordered.
5. **Nothing executes.** No layer imports application code, reads a database or network, or calls an oracle or model.
6. **No stage grants execution.** `execution_ready`, `execution_authorized` and `correctness_evaluated` stay `False`
   at every stage. Stage 4, execution authorization, doesn't exist.
7. **Three identities stay separate.** Full record (`workload_spec.digest`), validated plan (`vplan1`), and executed
   scenario, which is specified but not implemented.

## 3. Layer-by-layer results

| Layer | Input | Output | Error behaviour | Result |
|---|---|---|---|---|
| Parser | decoded JSON or text | `WorkloadSpec` | `SpecRejected` with every reason, or `UnsupportedSchemaVersion` | **Gap B1:** a lone surrogate raises `UnicodeEncodeError` |
| v1 upgrade | v1 document plus caller-stated provenance | `WorkloadSpec` | refuses without stated provenance; v1 rules apply first | Pass |
| Registry build | config plus `TrustedRegistration` | `Registry` | `RegistryError` | Pass |
| Authorization | `WorkloadSpec`, `Registry` | `AuthorizationResult` | structured rejections, 10 codes | **Gap B2:** trusts any `WorkloadSpec` instance |
| Contracts build | config, `Registry`, `TrustedContracts` | `Contracts` | `ContractError` | **Gap B1:** surrogate in an allowed value raises `UnicodeEncodeError` |
| Argument validation | `AuthorizedWorkload`, `Contracts` | `ValidationResult` | structured rejections, 14 codes | **Gap B2:** trusts any `AuthorizedWorkload` instance |
| Plan identity | `ValidatedWorkload`, `Registry`, `Contracts` | `IdentityResult` | explicit unsupported reason | Pass: re-runs stages 1 to 3 |

### Probes run for this audit

| Probe | Result |
|---|---|
| `schema_version` 0, 1 (v2 shape), 3, `"2"`, `true`, `2.0`, missing | All refused, by schema and parser alike |
| Stale registry pin | `RegistryError` |
| Stale contract pin | `ContractError` |
| Contract set bound to an older registry | `ContractError` |
| Workload from an older registry, validated with newer contracts | `registry_mismatch` |
| Identity with the registry and contracts mismatched | unsupported |
| Actor version not registered | `version_not_registered` |
| Contract set missing a registered version | `ContractError` |
| Application mismatch | `application_mismatch` |
| v1 and v2 actors of one entrypoint in one workload | Accepted, by design: mixed-version deployment is the research subject |
| Provenance with an unknown origin, extra field (`trusted: true`), missing author or invalid author | All refused |
| Extra top-level field, such as `arm` | Refused |
| v1 document without caller-stated provenance | Refused |
| v1 document upgraded twice | Same full-record digest |
| v1 dotted actor name | Refused by the v1 engine's own rule, so v2 introduces no regression |
| `model_construct` spec with an invalid actor name | **Authorized and argument-validated.** Only the identity refused it (B2) |
| `ValidatedWorkload` claiming `execution_authorized: True` through validation | Refused |
| The same claim through `model_construct` | The Python object holds `True` (B2) |

### Import isolation

Importing all four modules loads only `deployspecter.live.{workload_spec, registry, argument_validation,
workload_identity}`. It loads no psycopg, runner, driver, supervisor, worker, v1 `workload`, database, oracle, Meta
client, `live_app` or LangGraph module.

`socket` appears in `sys.modules`. It's loaded by pydantic's plugin loader, through `importlib.metadata` and
`email.utils` in the standard library. No connection is made, and the tests block `connect`.

## 4. Schema and parser consistency

Fifty-four generated boundary documents were run through the candidate schema and the parser. They cover every
numeric and length limit at the limit and one past it, type confusions, control characters, provenance and versions.

- **Agreement:** 46 documents.
- **Schema looser, all declared** in `x-not-enforced-by-schema`: 7 documents.
    - an int written for a float
    - negative zero
    - repeated actor names
    - repeated goal labels
    - more than 64 goal rows
    - `is_null` with a value
    - `eq` without a value
- **Schema looser, undeclared:** 1 document. A lone surrogate (`"\ud800"`, reachable from JSON text as an escape) passes
  the schema and crashes the parser with `UnicodeEncodeError`. That's B1.
- **Schema stricter than the parser:** none.

**Consistent on both sides:** C1 controls (U+0080 to U+009F, including CSI `0x9B`) and bidi overrides (U+202A to
U+202E, U+2066 to U+2069) are accepted in string values by both. The `rationale` field refuses only NUL, so ESC
sequences are accepted there. That's B3.

The registry, contracts and identity agree with the parser's representation. The type tags are `int`, `float`, `bool`,
`str` and `null` throughout. The int64 range, finite floats, the negative-zero refusal and the 128-character limit all
match. The identity re-parses through the same parser, so it can't drift from it.

## 5. Trust boundaries

- **Registry.** Trust needs a `TrustedRegistration` with a pinned digest from outside the configuration. The
  configuration's own `registered_by` must agree with it, and reported provenance is taken from the trusted object.
- **Contracts.** Trust needs a `TrustedContracts` with a pinned digest. The set is bound to a registry digest and an
  application, and must cover every registered version, argument and type. Comparisons are only between arguments of
  one numeric type.
- **Typed objects aren't trust tokens.** That's B2. Pydantic's `model_construct` and `model_copy(update=...)` build
  instances without validation. Stages 2 and 3 check only `isinstance`, so they accept a forged earlier-stage object.
  Today only the identity layer re-derives its input. A future stage 4 that trusted a `ValidatedWorkload` as given
  could dispatch a forged operation reference.

## 6. Identity correctness and deduplication limits

**What the identity guarantees:**

- **Typed distinctions are exact.** `1`, `1.0` and `true` stay distinct. So do signed 64-bit extremes, subnormal
  floats, and Unicode forms, since no normalization is applied. A missing argument and a null one stay distinct.
- **Order is canonical only where it's semantically irrelevant:** actors (scheduled by name), keyword arguments, and
  goals and constraints (conjunctions).
- **The identity is bound to the trusted registry and contract digests,** after re-running stages 1 to 3. Forged
  operations, entrypoints, versions, roles, names, applications and registrations get no identity. So do dropped,
  extra or retyped arguments, too many actors, and reordered actors.
- **Excluded metadata is inert.** No later stage reads `rationale`, `source_refs`, claims, `generator_version`, notes or
  the specification's name. The registry copies provenance once and never consults it.

**Its limits:**

- **Equal plan identities don't mean equal executed scenarios.** Code behind a reference, seed state, database
  configuration and schedule can all differ.
- **Some distinctions are deliberately false.** Renamed actors, relabelled goals and differently normalized text get
  different identities. That's safe, but it costs some deduplication.
- **The carried full-record digest can't be verified** without the original document. The identity never uses it.
- **Deduplication is a view.** It returns positions only and keeps every input. It has no arm, attempt, budget or
  replicate concept, so it can't change protocol v1.2 accounting. That belongs to the evaluation harness.

## 7. Offline test results

| Run | Result |
|---|---|
| Ruff | Clean |
| Root environment, contract-layer suites, `-m "not live"` | **393 passed**, 1 skipped (the adapter file, which needs LangGraph) |
| Isolated Gate 2B environment, same suites | **441 passed** |
| v1 offline tests (`test_live_workload.py`, not `TestExecution`) | 37 passed, 2 deselected |

`MODEL_API_KEY` was unset throughout.

## 8. Deliberate-breakage results

| Layer | Caught | Survivors |
|---|---|---|
| Parser (freeze-relevant rules, this audit) | 12 of 13 | **Repeated goal labels accepted.** Not equivalent: no test covers the rule. That's B4 |
| Registry (step 2) | 11 of 12 | Provenance read from the JSON. Equivalent, because the agreement check forces the fields equal |
| Argument validation (step 3) | 25 of 26, plus 4 of 4 new-rule breaks | `contracts_version` integer check. Equivalent: `StrictInt` already refuses |
| Plan identity and deduplication (step 4) | **38 of 38** | none |
| Frozen-file guard | 254 of 254 tracked files, 5 of 5 untracked probes | none |

## 9. Freeze decisions recorded

These are candidate decisions, applied when the freeze is approved:

- **Floats.** Finite binary64 only, with no NaN, infinity or negative zero, and floats must be written with a fraction
  or exponent. The plan identity encodes floats exactly, with `float.hex()`, and type-aware. The full-record digest
  uses Python's shortest round-trip `repr`, so it's Python-defined. That's acceptable because it identifies documents
  within this harness only.
- **Rationale.** Keep the 800-character maximum. It's semantically inert: no stage reads it and the identity excludes
  it. B3 must still make it display-safe.
- **Actors.** 2 to 4 for the live research contract. The historical release engine keeps its own limit of 3 and isn't
  changed.
- **Names.** Actor names and state-goal labels are identity-significant in `vplan1`. Making them semantics-free would
  need a new identity version.
- **Operation fingerprints.** They belong to trusted registry and dispatch provenance, recorded at registration and
  checked at dispatch. They're never argument constraints, and never taken from a proposal.
- **Digests.** The full-record digest and the validated-plan digest stay separate, and neither stands in for the other.
- **Executed-scenario identity.** It stays specified, in `WORKLOAD_IDENTITY.md`, and unimplemented.

## 10. Remaining blockers

1. **B1. Surrogate strings aren't rejected cleanly.** A lone surrogate, reachable from JSON text as `\ud800`, makes the
   parser raise `UnicodeEncodeError` instead of `SpecRejected`. The contract loader does the same for an allowed value.
   It still fails closed, but with the wrong error type, and callers that catch `SpecRejected` would crash. JSON
   Schema can't express the rule.
   **Fix:** refuse U+D800 to U+DFFF in every string field. Add fixtures, and add the rule to
   `x-not-enforced-by-schema`.
2. **B2. Stages 2 and 3 accept forged earlier-stage objects.** `authorize` and `validate_arguments` trust any instance
   of the right class, including one from `model_construct`.
   **Fix:** each stage re-validates its input, by a strict model round trip, before using it. Also record as an
   invariant that stage 4 must re-derive from the original document, as the identity layer already does.
3. **B3. Free text isn't display-safe.**
    - `rationale` accepts C0 escapes such as ESC `[2J`.
    - Every string field accepts C1 controls, including CSI `0x9B`.
    - Every string field accepts bidi overrides.

   This text is model-written and will appear in reports and terminals.
   **Fix:** a single-line rule that refuses C0 except, in `rationale` only, newline and tab. It also refuses DEL, C1,
   bidi controls and surrogates, with matching fixtures. This changes the schema's patterns, so it must happen before
   the freeze.
4. **B4. Repeated goal labels are untested.** Removing the parser's uniqueness check leaves every test passing. The
   rule matters for the identity, which orders goals by label.
   **Fix:** add a `repeated_goal_label` fixture.

**Freeze mechanics, not defects:** rename the schema file without `candidate`, change `x-status`, update the
schema-file test, then pin the hashes below again.

## 11. Proposed frozen inventory

These are the hashes at `e1fe2fa`. Fixing B1 to B4 will change the starred files, and the inventory must be hashed
again at the freeze.

| SHA-256 | File |
|---|---|
| `16c2e8c385659672c0d697f5521b0e0849e64eef253e326801c8052a494da7fe` | `src/deployspecter/live/workload_spec.py` * |
| `a3675fcc965d7cc04308b4ece300aac5221a7c91f6862813663f3cc82d1542c5` | `docs/research/live_stepping/workload/workload-spec-v2.candidate.schema.json` * |
| `b044efbbf8336fcb1cccbc264e6adb391c1981850a010648ceddf5c80513af11` | `docs/research/live_stepping/workload/WORKLOAD_CONTRACT.md` * |
| `f1be2b36170c024165f64c0ef34ca28423ebd0205990e6b5b9c1f86c6395b659` | `src/deployspecter/live/registry.py` * |
| `925ebcd8c20c441ab02913298dd74a38ab47ad7e472c748c943f9a8667a84b36` | `docs/research/live_stepping/workload/REGISTRY.md` |
| `0c531186310bbfe1e2d14a8d33b3b01a5e8b25be7b14d2532e1d6af71ea6fc19` | `src/deployspecter/live/argument_validation.py` * |
| `887d431a5dec86f04c7787cfcd144db14382caa7ae4875df4213de03a375467d` | `docs/research/live_stepping/workload/ARGUMENT_VALIDATION.md` |
| `1dd2233dfed889c322d3a55f3ee326f4f905e10605155f7e38e7e89954f7b9cc` | `src/deployspecter/live/workload_identity.py` |
| `d3ae08c6f9bbf65ad767749d1f015e99e067097aae7e12f2c4b8c4ef83497572` | `docs/research/live_stepping/workload/WORKLOAD_IDENTITY.md` |
| `290586312caab7c6df6220bde995980aa5284ea566dea24cc6a851bdcea2665c` | `tests/test_workload_spec.py` * |
| `ddb68365d7ff0ea1dff990b1eac8dab59ed708f505c83ea2430f7eb599d44b61` | `tests/live_app/workload_spec_fixtures.json` * |
| `96794fd0827cbb40e84d0b146da3229f8e56541120d2f264584483485ab65787` | `tests/test_live_registry.py` * |
| `adc5099297e67247195e76f6769ecc3cfd40462b71ca6505d6a2c9904d782656` | `tests/test_argument_validation.py` * |
| `ea47913a7787f0b4f20dcf1884d55fe952b46ad703351e0717db6348c1b54508` | `tests/test_workload_identity.py` |

`src/deployspecter/live/workload.py` (`cce1c54d…`) is the v1 dependency. It's already frozen with Gate 2A and stays
outside this inventory.

**Unchanged by this audit:**

- protocol v1.2, with hashes `31d82afd…`, `28add0e8…` and `c3e939bd…`;
- Gate 2A code and evidence;
- the release engine;
- every other worktree.

## 12. Recommendation

**NOT READY.** Fix B1 to B4 in one reviewed offline change, re-run this audit's probes, tests and breakage checks,
then hash the inventory again and request the freeze.

---

# Supplemental audit: resolution of B1 to B4

**Recommendation now: READY to freeze, on your approval.** Sections 1 to 12 above are the original audit at
`e1fe2fa`, kept unchanged as the record of why it was NOT READY. Their file hashes are superseded by section S7.

- **Date:** 2026-11-03
- **Resolution commit:** `a2b75898441e772aaaf94dbb7d10ab19867fe99f` on `research/live-stepping`, local only
- **Status:** WorkloadSpec v2 remains a candidate (`x-status: candidate, not frozen`). Nothing has been frozen.

## S1. Root causes and fixes

| Blocker | Root cause | Fix |
|---|---|---|
| B1 | Text was first touched by `document_size`, which encodes to UTF-8. A lone surrogate, including one from a `\ud800` escape in JSON text, raised `UnicodeEncodeError`. The contract digest had the same failure | `text_problems` walks every key and string before anything measures, encodes or validates. It runs in `parse_workload` (so in `parse_workload_json`), `build_registry`, `load_registry_json` and `build_contracts`. The JSON entry points accept only `str` |
| B2 | `authorize` and `validate_arguments` checked only `isinstance`. Pydantic's `model_construct` and `model_copy(update=...)` skip validation, and `Registry` and `Contracts` are plain classes with reassignable attributes | Each stage re-derives its input from content. `authorize` verifies the registry and re-parses the specification. `validate_arguments` now **requires the registry**, verifies both trusted objects, and checks the workload with `check_authorized`. The identity uses `check_validated` |
| B3 | `SINGLE_LINE` refused only C0 and DEL. `rationale` refused only NUL | One policy, `SAFE_TEXT` plus the walker, for every free-text field, including rationale |
| B4 | No fixture or test exercised the goal-label uniqueness rule | A direct fixture whose goals differ in everything but the label, plus forged-object tests at every later stage |

**Found and fixed during the resolution:**

1. **Size contradiction, introduced by the B2 fix.** Re-validation dumped the specification with its defaults. A
   valid document at 32,766 bytes re-parsed at 34,227 bytes and was refused by `authorize`. Re-validation now
   serializes without defaults, and the rebuilt document uses the one-character name `r`. Neither can be larger than
   the original.
2. **v1 upgrade size.** Typed values make the version 2 form larger, so a version 1 document within its limit could
   exceed version 2's. The upgrade now applies version 2's limit to its own result.
3. **Recursion.**
    - My first walker was recursive, so a 5,000-deep document raised `RecursionError`. It was rejected cleanly at
      `e1fe2fa`, so this was a regression. The walker now uses an explicit stack.
    - A 50,000-deep JSON text made `json.loads` raise `RecursionError`. That predates this work. Both JSON entry points
      and `document_size` now turn it into a structured rejection.
4. **Test harness.** The schema-agreement validator used Python's `$`, which also matches before a final newline, unlike
   ECMA-262. It now uses end-of-input semantics.

## S2. API changes

| Change | Before | After |
|---|---|---|
| `validate_arguments` | `(workload, contracts)` | `(workload, *, registry, contracts)` |
| `authorize` | trusted its arguments' classes | may raise `RegistryError` for a tampered registry; forged specs get `structure_invalid` |
| `parse_workload_json`, `load_registry_json` | any input | text only, else a structured rejection |
| `SINGLE_LINE` | exported | replaced by `SAFE_TEXT` |
| New | | `ws.text_problems`, `ws.forbidden_code_point`, `ws.safe_repr`, `rg.verify_registry`, `rg.check_authorized`, `rg.revalidate_model`, `av.verify_contracts`, `av.check_validated` |
| New rejection codes | | registry: `structure_invalid`, `not_genuine_authorization`; validation: `not_authorized` |
| `AuthorizedWorkload` | free-form digests | `spec_digest` and `registry_digest` must be 64 lower-case hex digits |
| `rationale` | any text except NUL | safe text: one paragraph, no line breaks |
| v1 upgrade | | refused if the text policy or version 2's size limit would be broken |

`vplan1` identities are unchanged. The pinned identity in the tests still holds.

## S3. Schema and parser consistency

The 54 original boundary documents were re-run, plus 33 new B1 to B4 cases.

| Set | Agree | Schema looser, declared Python-only | Undeclared | Schema stricter |
|---|---|---|---|---|
| Original 54 | 46 | 8 | 0 | 0 |
| New 33 | 27 | 6 | 0 | 0 |

Three original cases changed outcome as intended: a C1 control, a bidi override and a newline in the rationale are
now refused by both the schema and the parser. The lone-surrogate case is now a declared Python-only rule.
No case raised an unexpected exception.

The only text rule left to Python is the unpaired-surrogate rule, declared in `x-not-enforced-by-schema` alongside
key policy. A test checks the Python ranges and the schema pattern describe the same code points across U+0000 to
U+2FFF and beyond, surrogates aside.

## S4. The chain

parse → authorize → validate_arguments → validated_plan_identity:

- **No stage refuses an earlier stage's genuine output.** Every valid fixture and a v1 upgrade pass structural
  re-validation. Genuine outputs pass `check_authorized`, `check_validated` and the identity. A document at the exact
  size limit, omitting every default, passes every stage.
- **No forged object advances.** These are all refused, at the first stage that sees them:
    - forged nested models, and altered roles, versions, entrypoints, operations or registrations;
    - missing, extra or retyped arguments, and reordered actors;
    - invalid or repeated state goals;
    - stale registry or contract pins, and tampered `Registry` or `Contracts` objects;
    - post-validation edits to claimed status;
    - a lookalike `WorkloadSpec` subclass.
- **A post-validation edit to a value** that yields valid content gets the identity of the plan it now describes,
  never the original's.

## S5. Offline results

| Run | Result |
|---|---|
| Ruff | Clean |
| Root environment, contract suites, `-m "not live"` | **749 passed**, 1 skipped: the LangGraph adapter file, because `langgraph` isn't installed in the root environment |
| Isolated Gate 2B environment, same suites including the adapter | **797 passed**, 0 skipped |
| v1 offline tests, both environments | 37 passed, 2 deselected (`TestExecution`, which needs PostgreSQL; not run) |
| `-m live` across these suites | 0 tests collected, so no live model test can run |
| Frozen-file guard (adapter suite, isolated environment) | Passed: Gate 2A, the release engine, tools, dependencies, results and scenarios unchanged |

`MODEL_API_KEY` was unset. No database, network, external application or model was used.

## S6. Deliberate-breakage results

One harness over all four contract suites: **91 of 91 caught**.

| Group | Caught |
|---|---|
| B1 (walker, surrogates, keys, non-string keys, nesting, entry-point types, contract pattern, message escaping) | 13 of 13 |
| B3 (each refused class in table and pattern, each field's pattern, over-broad refusal of ZWJ and of non-ASCII) | 11 of 11 |
| B2 (each re-validation, verification, comparison and binding check) | 23 of 23 |
| Size consistency and recursion | 8 of 8 |
| B4 (goal-label uniqueness) | 1 of 1 |
| Parser, registry, validation and identity rules | 35 of 35 |

Three survivors appeared during development, and each was a missing test, not an equivalent mutant:

- non-string keys;
- the contract model's own allowed-value pattern;
- the re-parse comparison, reachable by a lookalike subclass.

Each now has a direct test. A fourth mutant, which made `check_validated` ignore a failed re-validation, changed only
the reason text, because the next check also refused. It's now caught by asserting the reason.

## S7. Proposed frozen inventory at `a2b7589`

| SHA-256 | File |
|---|---|
| `42dd98f27be6a33f708d2b7d51915067fd81427a3644faa38c95729f6d01fd5f` | `src/deployspecter/live/workload_spec.py` |
| `e0687cb9271280e4424eac7576bcd53b06ca53ed0db7f72f10131060a39b9702` | `docs/research/live_stepping/workload/workload-spec-v2.candidate.schema.json` |
| `17245b0900e0063afede9551b95c56967ce7608d58808e0378a07de962ed9676` | `docs/research/live_stepping/workload/WORKLOAD_CONTRACT.md` |
| `abdfd4edb7bd2d3467054abe12814e36ba7789d652bd506d519bafcbb407dcf3` | `src/deployspecter/live/registry.py` |
| `3bacc21ade1ec2735d9250d18cdcae30eea58f30b99eb9e2a2f3a63e820ba262` | `docs/research/live_stepping/workload/REGISTRY.md` |
| `a03231f5a60ab17e64230a064ec2fef1222e676ba74c8ee9217c2cb131cbffb4` | `src/deployspecter/live/argument_validation.py` |
| `511a7a3d5498fab07b2222f1f41aafc575e85eb58b1b5ca6a59e0848361278fc` | `docs/research/live_stepping/workload/ARGUMENT_VALIDATION.md` |
| `53e05335b0035c2b390676c5f51be8c1262fa0441b4ce08f525aee467aa7db08` | `src/deployspecter/live/workload_identity.py` |
| `62eb4fdd2db98010c7cc91e78bd6c249ab930253b03facf329f4516f17c042c0` | `docs/research/live_stepping/workload/WORKLOAD_IDENTITY.md` |
| `cee68ab0215c2450c115e368df4cfa99c640b2ef9a5bf0817f08c3a7be99f162` | `tests/test_workload_spec.py` |
| `b8e53a43e441ab2c731bf57a69f8afd1ef740401dfa2fc5f9f195cc5b1454e0d` | `tests/live_app/workload_spec_fixtures.json` |
| `5debaffd092b9ad5b6c7ad027804684e1f69f93cadf71e9c89fe9b8b185793e1` | `tests/test_live_registry.py` |
| `3c53ba6d6ab06ae059f21d253ace26651c8c7a5b2fd01a5b0da352d59a90d6af` | `tests/test_argument_validation.py` |
| `2a6e347fd011de5203e7296064fdcdaca98d8bb0c404feb69ecd66aee3e3b287` | `tests/test_workload_identity.py` |

`src/deployspecter/live/workload.py` (`cce1c54d6b5830a3837879347ef728c810fcfa405314475be9cbfdc13402268d`) is
unchanged and stays outside this inventory, frozen with Gate 2A. Protocol v1.2 is unchanged:

- `31d82afd7bed1c38aa4870c064ca5b0827e72211ca6804a4b98370c54aef1725`
- `28add0e8f69aaa3dd96be205b2c62dcf1b7a7b27d1da963256b9e818a6b06480`
- `c3e939bd87fe89ae2e04c43d23aa1156e441e2c20be12a44c8f5939909c47075`

A freeze changes the schema file's name and `x-status`, and the schema-file test with them, so that step produces its
own final hashes.

## S8. Remaining limits, none blocking

- **In-process code is out of scope.** The checks stop forged data objects, not hostile code in the same process.
  Such code could subclass `Registry` or `Contracts` and override `lookup`, call `object.__setattr__` on a frozen
  model, or replace these functions outright. The contract documents say so.
- **The text policy isn't display safety.** Confusable letters, long words and markup-like text remain, so renderers
  must still escape untrusted text.
- **Section 9's recorded decisions stand.** The full-record digest is Python-defined. The executed-scenario identity is
  specified only.
- **One compatibility change:** a version 1 rationale with a line break, or a version 1 document too large once typed,
  is now refused on upgrade. No committed v1 fixture is affected.

## S9. Recommendation

**READY to freeze, on your approval.** B1 to B4 are resolved, and no undeclared schema mismatch, stage bypass by forged
data objects, or chain contradiction remains in these checks. The freeze itself has three steps, which need your
approval:

1. rename the schema file without `candidate`, and set `x-status`;
2. update the schema-file test;
3. hash the inventory again and commit.

---

# Final freeze decision

**FROZEN: WorkloadSpec v2 contract, 2026-11-03, on the owner's approval.**

The record of this document, in order:

1. **Sections 1 to 12:** the original audit at `e1fe2fa`, which said NOT READY.
2. **Sections S1 to S9:** the supplemental audit at `a2b7589`, which resolved B1 to B4 and said READY.
3. **This section:** the freeze. It was committed in the same commit as the schema rename.

The manifest, `V2_FROZEN_MANIFEST.json`, is committed separately so it can name that commit. Earlier sections
haven't been edited: their failed tests, intermediate findings and deviations stand as written. Their hash tables are
superseded by F3.

## F1. What the freeze changed

| Change | Detail |
|---|---|
| Schema file name | `workload-spec-v2.candidate.schema.json` → `workload-spec-v2.schema.json`, a git rename. One line differs: `x-status` |
| `x-status` | `candidate, not frozen` → `frozen` |
| Kept | `$schema` (draft 2020-12), `$id` `urn:deployspecter:workload-spec:2`, `schema_version: 2`, every pattern, bound and `x-not-enforced-by-schema` entry |
| Code | Docstrings and the `x-status` constant only. No validation, limit, field, predicate or coercion change |
| Tests | The schema-file path and the status assertion. The new assertions check draft 2020-12 and that the candidate file is gone |
| Documents | Status headers, plus the *Freeze and compatibility* section of `WORKLOAD_CONTRACT.md` |

## F2. Findings and their resolution

| Finding | Where found | Resolution |
|---|---|---|
| B1: unpaired surrogates crash with `UnicodeEncodeError` | §10 | Resolved, `a2b7589` |
| B2: stages 2 and 3 trust any object of the right class | §10 | Resolved, `a2b7589`. `validate_arguments` now requires the registry |
| B3: free text not display-safe (ESC, C1, bidi) | §10 | Resolved, `a2b7589` |
| B4: repeated goal labels untested | §10 | Resolved, `a2b7589` |
| Re-validation with defaults refused a valid 32,766-byte document | S1, introduced by the B2 fix | Resolved, `a2b7589` |
| v1 document within v1's limit but over v2's | S1 | Resolved: refused at upgrade |
| Recursive walker raised `RecursionError` at 5,000 levels | S1, introduced during the fix | Resolved: iterative walker |
| `json.loads` raised `RecursionError` at 50,000 levels | S1, predates this work | Resolved: structured rejection |
| Test validator used Python `$` semantics | S1 | Resolved in the test harness |
| Four survivors during development | S6 | Each now has a direct test |
| Surviving registry and contract mutants assessed as equivalent | §8, steps 2 and 3 | Unchanged. Equivalent, so not counted as caught |
| Decisions in §9 (floats, rationale, actors, names, fingerprints, digests, executed-scenario identity) | §9 | Adopted as frozen |
| No protection against in-process hostile code | S8 | Accepted limitation, documented, not a blocker |

## F3. Frozen inventory

| SHA-256 | File |
|---|---|
| `a187058f2aad174f0a9764dd29733c5061f63aa49028ee5cac4622d9d3fd1ece` | `src/deployspecter/live/workload_spec.py` |
| `abdfd4edb7bd2d3467054abe12814e36ba7789d652bd506d519bafcbb407dcf3` | `src/deployspecter/live/registry.py` |
| `a03231f5a60ab17e64230a064ec2fef1222e676ba74c8ee9217c2cb131cbffb4` | `src/deployspecter/live/argument_validation.py` |
| `53e05335b0035c2b390676c5f51be8c1262fa0441b4ce08f525aee467aa7db08` | `src/deployspecter/live/workload_identity.py` |
| `3744bb083d3630be62b5f0a8507e69e5a3a81d02029c49558c66a0567fbabf0d` | `docs/research/live_stepping/workload/workload-spec-v2.schema.json` |
| `d099faa28018d95a1eb000a60ba2500ffd04ed329a6a01b7b08dd1c73c98f272` | `tests/test_workload_spec.py` |
| `5debaffd092b9ad5b6c7ad027804684e1f69f93cadf71e9c89fe9b8b185793e1` | `tests/test_live_registry.py` |
| `3c53ba6d6ab06ae059f21d253ace26651c8c7a5b2fd01a5b0da352d59a90d6af` | `tests/test_argument_validation.py` |
| `2a6e347fd011de5203e7296064fdcdaca98d8bb0c404feb69ecd66aee3e3b287` | `tests/test_workload_identity.py` |
| `b8e53a43e441ab2c731bf57a69f8afd1ef740401dfa2fc5f9f195cc5b1454e0d` | `tests/live_app/workload_spec_fixtures.json` |
| `c0988c327e16b49090cb55dd02d38baac3a5069d13b51c4429789a2ae9d7c785` | `docs/research/live_stepping/workload/WORKLOAD_CONTRACT.md` |
| `f35ba92e5c87606cdad3dfb24a378dd8777f2679f7c48efcb522948c2146bcc1` | `docs/research/live_stepping/workload/REGISTRY.md` |
| `6e413c38d93b688b497a6715570807baf7cbf61993368e3d39c1331af497ec66` | `docs/research/live_stepping/workload/ARGUMENT_VALIDATION.md` |
| `d3cbbbebbedc8ea7ef2600f300e0c9f9feab4ec0024d3c9e1bc9c26fd9e7218e` | `docs/research/live_stepping/workload/WORKLOAD_IDENTITY.md` |

This report is audit evidence, not a frozen contract file.

## F4. Final verification, after the rename and status change

| Check | Result |
|---|---|
| Task 1 review | B1 to B4 resolved; one text policy, documented in one place and referenced elsewhere; every stage re-derives its input; the four layers load no engine, database, oracle, application or LangGraph module; `vplan1` encoding and hashing code byte-identical to `e1fe2fa`, with the pinned identity unchanged |
| Ruff | Clean |
| Contract suites, root environment, `-m "not live"` | 749 passed, 1 skipped: the adapter file, because `langgraph` isn't installed in the root environment |
| Same plus the adapter, isolated environment | **797 passed**, 0 skipped |
| v1 offline tests, both environments | 37 passed; 2 deselected (`TestExecution`, needs PostgreSQL, not run) |
| B1 to B4 direct regressions | 382 passed |
| Gate 2A frozen-file guard (`TestNothingFrozenChanged`) | 5 passed |
| Live tests in these suites (`-m live`; `live` is the only paid-API marker) | 0 collected |
| Schema/parser agreement | Original 54: 46 agree, 8 declared. New 33: 27 agree, 6 declared. 0 undeclared, 0 unexpected exceptions |
| Deliberate breakage | **91 of 91 caught** |

`MODEL_API_KEY` was unset. No PostgreSQL, external application, network service or model endpoint was used.
