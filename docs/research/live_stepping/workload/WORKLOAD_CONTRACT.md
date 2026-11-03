# Workload contract: version 1 as committed, version 2 as a candidate

Offline research, step 1 of `OFFLINE_WORKLOAD_PLAN.md`. Nothing here calls a model, and no fixture is model output.
The version 2 schema is a **candidate**, not frozen.

## Version 1 (`src/deployspecter/live/workload.py`, unchanged)

| Part | How version 1 handles it |
|---|---|
| Entrypoints | `WorkloadActor.entrypoint` names a key in `AppRegistry.entrypoints`. The pattern allows dots and hyphens, so `os.system` is valid in shape and refused only by the registry. Compiling resolves the key to a registered callable |
| Actor identity | `WorkloadActor.name`, the engine's actor name (`LiveActor.name`, used by `ExplicitSchedule`). Uniqueness is checked by the registry step, not the model |
| Application versions | `application_version`, a label that must be in the entrypoint's registered `versions` |
| Arguments | `name` plus an untyped JSON scalar. Types, ranges, patterns and choices are checked against `ArgumentSpec` by the registry step |
| Initial-state goals | `seed_goals` of `SeedGoal(entity, label, count, fields)`. Logical, unresolved; `compile_workload` returns them for a constructor |
| Scheduler | None in the proposal. Schedules are chosen by the harness (`ExplicitSchedule`, exploration), never by the proposal |
| Deterministic baselines | The release engine's `models.TestProposal` (Baseline B, the frozen Muse prompt and hand-written specs) is a different, statement-level format: it calls extracted statements by id with seed rows. Version 1 workloads call whole functions, and no baseline emits them yet |
| Versioning | `schema_version: Literal[1]`, defaulting to 1, so it may be omitted |

## Version 2 candidate (`src/deployspecter/live/workload_spec.py`)

```
WorkloadSpec
  schema_version  2, required and explicit (1 accepted only through an explicit upgrade)
  name            identifier
  application     identifier
  provenance      origin: hand_written | synthetic | deterministic_baseline | model_proposal
                  author: identifier; generator_version: optional label; note: safe text ≤ 400
  actors          2 to 4 ActorSpec
    name                 actor name (engine rule), unique
    entrypoint           registered identifier: lower-case, digits, underscore; no dot, colon or slash
    application_version  version label
    role                 request | background_job (required)
    arguments            ≤ 16, unique names, each a typed value:
                         {type: int (64-bit) | float (finite, written with a fraction or exponent, not -0.0)
                               | bool | str (safe text, ≤ 128 characters) | null, value}
  state_goals     ≤ 12, unique labels, ≤ 64 rows in total
    entity, label, count 0 to 20
    constraints   ≤ 16 of {field, op: eq ne lt le gt ge is_null not_null, value (typed; none for null checks)}
  source_refs     ≤ 24 of {relative path of "/"-separated segments, each starting with a letter, digit or "_"
                           (so no leading "/", empty, "." or ".." segment, trailing "/" or backslash),
                           start_line ≤ end_line, claim: safe text ≤ 400}
  rationale       ≤ 800 characters of inert safe text: one paragraph, no line breaks
Whole document ≤ 32 KiB, measured as compact UTF-8 JSON, however the original text was written.
Unknown fields refused at every level. Repeated JSON keys, NaN and Infinity refused.
Booleans are never integers, and integers are never floats.
Every key and string anywhere in the document follows the text policy below.
```

## Text policy

One rule for every string in the version 2 contract. It covers specification fields (rationale, notes, claims, string
arguments and goal values, and every object key) and trusted configuration (registry descriptions and contract
allowed values). Identifiers, names, labels and version labels have stricter ASCII patterns of their own.

**Refused code points:**

| Range | What |
|---|---|
| U+0000 to U+001F | C0 controls, including tab, newline and carriage return |
| U+007F | DEL |
| U+0080 to U+009F | C1 controls, including NEL and CSI |
| U+061C, U+200E, U+200F | directional marks |
| U+202A to U+202E | embeddings and overrides |
| U+2066 to U+2069 | isolates |
| U+2028, U+2029 | line and paragraph separators, which renderers treat as line breaks |
| U+D800 to U+DFFF | unpaired surrogates: not text, and impossible to encode as UTF-8 |

**Kept:** every script, including right-to-left letters; combining marks; emoji and other supplementary characters;
the joiners U+200C and U+200D that several scripts need; and other format characters such as U+00A0 and U+FEFF.

**How it's enforced:**

- **The schema pattern** (`SAFE_TEXT`) expresses everything except surrogates. A JSON Schema pattern can't state that
  exactly, so the surrogate rule is listed in `x-not-enforced-by-schema` and checked in Python.
- **`text_problems`** walks every key and string of a decoded document before anything measures, encodes or validates
  it. It runs in `parse_workload`, so in `parse_workload_json` too, and in `build_registry`, `load_registry_json` and
  `build_contracts`. A lone surrogate, including one written as a `\ud800` escape in JSON text, is therefore a
  structured rejection, never an encoding error.
- **Depth can't turn a rejection into a crash.** The walker uses an explicit stack, and a recursion overflow while
  decoding or measuring JSON becomes a structured rejection (`SpecRejected`, or `RegistryError` for registry JSON).
- **Nothing is repaired.** Offending text is refused, never replaced, removed or escaped into a different value.
- **Messages are safe.** Rejections name code points (`U+202E`) and render untrusted keys with escapes, so no
  refused character reaches a message.

**Renderers must still escape untrusted text.** This policy removes control and directional characters. It doesn't
make every string safe to display: confusable letters, very long words and markup-like text all remain.

**What version 2 decides, and what it leaves to later steps.** Structure only: types, bounds, identifiers, uniqueness,
size. Whether an entrypoint is registered, whether an argument suits it, and whether a goal is feasible belong to the
registry (step 2), the argument validator (step 3) and state construction. So a well-formed but unregistered entrypoint,
or a string argument containing SQL, passes structure here and is kept as inert data. No code path interprets argument
text.

**Separation.** State goals carry no seed rows, requirement or oracle, and the module imports none of those (tested).
Parsing takes only the document.

**Canonical form.** `canonical()` sorts only collections whose order means nothing:

- actors by name, because the engine refers to and schedules actors by name;
- arguments by name, because they become keyword arguments;
- state goals by label, because they're a set of facts and construction decides physical order;
- constraints within a goal, because they're a conjunction;
- source references, because they're citations.

No value is changed. Floats canonicalize to Python's shortest round-trip form, so decimal texts that round to the same
double (`0.1` and `0.10000000000000001`) have one form. Canonical floats are deterministic within Python, not promised
across languages. `canonical_json()` is compact, key-sorted UTF-8. One digest is pinned in the tests, so changing the
canonical form must be deliberate.

**Two kinds of digest.** `digest()` is a **full-record digest**: every field, including provenance, rationale and source
references. It identifies a document. A future **execution-semantic digest** would cover only what changes execution
(application, actors, entrypoints, versions, roles, arguments and state goals), so two proposals that differ only in
their explanation would share it. That digest isn't implemented, and must not be inferred from the full-record one.

**Provenance is self-reported.** `provenance.origin` and `author` are what the document claims. They grant no trust and
no authority to run anything. Evaluation-arm membership, and who actually produced a specification, are recorded by
the harness that requested it and never taken from these fields.

**JSON Schema.** `json_schema()` produces `workload/workload-spec-v2.candidate.schema.json` (draft 2020-12,
`$id` `urn:deployspecter:workload-spec:2`, `x-status: candidate, not frozen`). The schema is necessary, not
sufficient: the rules it can't express are listed in its `x-not-enforced-by-schema` field and enforced by the Python
validators. A test fails if the file drifts from the model.

Every fixture is also checked against the schema with a small validator for the keywords it uses. The schema must
accept everything the parser accepts. It must refuse everything the parser refuses, except rejections by the
declared Python-only rules.

**Compatibility.** `parse_workload` accepts version 1 or 2 and returns version 2.

- A version 1 document must state its version, and its caller must state its provenance; nothing is guessed.
- Values take the type of the JSON value they had.
- Anything version 2 can't express faithfully, such as a dotted entrypoint name, is refused rather than renamed,
  because a rename could point at a different function.
- The version 2 text policy applies to version 1 documents too, so a version 1 rationale with a line break is refused
  on upgrade.
- Typed values make the version 2 form larger. The upgraded form must fit version 2's own 32 KiB limit, measured
  without defaults, or the upgrade is refused. Later stages re-validate the version 2 form, so otherwise they would
  refuse what the upgrade accepted.
- `workload.py` is unchanged, so existing version 1 code and tests are unaffected.

## Fixtures

`tests/live_app/workload_spec_fixtures.json`: 77 hand-written synthetic fixtures, each labelled so, none attributed to
a model. Each has an expected class: valid, structurally valid (for the registry to judge later), invalid with a
reason, or unsupported version. Each also says whether the JSON Schema can see its rule. They cover simple and multi-actor workloads, old and new version selection, typed
arguments, goals, unknown and malformed entrypoints, malformed arguments, unexpected fields, versions, resource limits,
and code or SQL placed where it doesn't belong. Eighteen were added for the freeze blockers: unpaired surrogates in
values, keys and nested goals; each class of control and directional character; international text that must be
kept; and repeated state-goal labels that differ in everything else.
