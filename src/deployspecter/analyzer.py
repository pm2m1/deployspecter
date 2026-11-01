"""Muse Spark analysis: code in, typed test proposals out. Output is data, never code.

The prompt contains only the submitted code, the extracted statement catalog and
the schema. It never contains scenario names, variant labels, requirements or
failing schedules; ``assert_no_leak`` enforces this at call time.
"""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field, ValidationError

from .analyzer_support import TableInfo
from .meta_client import MetaClient, ModelReply, strict_json_schema
from .models import TestProposal
from .source_model import SourceModel

PROMPT_VERSION = "v2"  # v1 results are void: the response schema omitted a required field
MAX_TESTS = 4

INSTRUCTIONS = """\
You analyze PostgreSQL rolling deployments. During a rollout, an old application release, a new
release, and background data jobs run at the same time against the same migrated schema.

Read the submitted code and propose concurrency tests that could expose lost or corrupted data if
such a defect exists. The code may be entirely correct: do not assume a defect is present.
An engine will run each test over many statement-level interleavings on a disposable database.

How tests work:
- An actor is one invocation of one source function. You choose the actors, the input values, and
  the seed rows. You do not choose a schedule, and you never write SQL.
- `calls` lists the statements that invocation executes, in source order, using only statement_id
  values from the catalog. BEGIN and COMMIT are derived from the source automatically.
- Every statement marked required must appear. Statements marked in_loop run once per row you bind;
  list them once per iteration you want. Statements marked conditional may be left out.
- Provide exactly one param per placeholder. Use source "literal" with a value, or source "row_ref"
  with bind (a bind_as name from an earlier call in that actor), row (0-based), and column.
- Follow the data flow in the code. When the catalog gives a param's flow, you must match it:
  row[x] needs a row_ref to column x; arg needs a literal; const needs that literal.
- If the code computes a value from a row, use transform on the row_ref: strip, lower, upper, title,
  or add/multiply with operand. Otherwise use transform "none" and operand null.
- If a statement reads rows other statements use, set bind_as; otherwise set it to null.
- For a param that is not a row_ref, set bind, column and operand to null, row to 0, transform "none".
  For a row_ref, set value to null.
- Seed rows must satisfy the schema constraints. Use 2 or 3 actors per test.

Invariants are hypotheses about the final database state that you expect to hold after correct
behaviour. Use the kinds in the schema. key_column and key_value select one row. Set unused fields
to null, and use an empty list for expected when it does not apply. An invariant must hold under
every correct execution order of that test, not merely the one you have in mind.

Propose at most 4 tests, most promising first, with inputs that make the actors' effects
distinguishable in the final state. Explain the risk in `rationale` and in `conflicts`.
Respond only with JSON matching the schema.
"""


class OperationNote(BaseModel):
    function: str
    summary: str
    tables_read: list[str]
    tables_written: list[str]


class ConflictNote(BaseModel):
    functions: list[str]
    table: str
    column: str
    risk_explanation: str


class AnalysisOutput(BaseModel):
    operations: list[OperationNote]
    conflicts: list[ConflictNote]
    tests: list[TestProposal] = Field(max_length=MAX_TESTS)


class AnalysisAttempt(BaseModel):
    kind: str  # "initial" or "repair"
    reply: ModelReply
    parse_error: str | None = None


class AnalysisRecord(BaseModel):
    prompt_version: str = PROMPT_VERSION
    prompt_sha256: str
    model: str
    attempts: list[AnalysisAttempt] = Field(default_factory=list)
    output: AnalysisOutput | None = None

    @property
    def total_input_tokens(self) -> int:
        return sum(a.reply.input_tokens or 0 for a in self.attempts)

    @property
    def total_output_tokens(self) -> int:
        return sum(a.reply.output_tokens or 0 for a in self.attempts)


_FORBIDDEN = re.compile(r"buggy|corrected|hand_spec|ground.?truth|\bBR-|scenario\.json", re.I)


class PromptLeak(RuntimeError):
    pass


def assert_no_leak(prompt: str) -> None:
    match = _FORBIDDEN.search(prompt)
    if match:
        raise PromptLeak(f"prompt contains withheld term {match.group(0)!r}")


def build_prompt(files: dict[str, str], source: SourceModel, schema: dict[str, TableInfo]) -> str:
    parts = ["# Submitted files"]
    for name, text in sorted(files.items()):
        parts.append(f"## {name}\n```\n{text.rstrip()}\n```")
    parts.append("# Schema after migration (read from PostgreSQL)")
    for table in schema.values():
        cols = ", ".join(
            f"{c.name} {c.data_type}{'' if c.nullable else ' NOT NULL'}{' DEFAULT' if c.has_default else ''}"
            for c in table.columns
        )
        extra = f" primary key {table.primary_key}" if table.primary_key else ""
        checks = f" checks {table.checks}" if table.checks else ""
        fks = f" foreign keys {table.foreign_keys}" if table.foreign_keys else ""
        parts.append(f"- {table.name}({cols}){extra}{checks}{fks}")
    parts.append("# Statement catalog")
    for fn in source.functions.values():
        parts.append(f"## function {fn.ref}({', '.join(fn.args)})")
        for st in fn.statements:
            marks = [m for m, on in (("in_loop", st.in_loop), ("conditional", st.conditional)) if on]
            flows = {k: f"{kind}:{detail}" for k, (kind, detail) in st.flows.items() if kind != "unknown"}
            parts.append(
                f"- statement_id={st.statement_id} [{', '.join(marks) or 'required'}]\n"
                f"  sql: {st.sql}\n  params: {st.placeholders}\n  known flows: {flows or 'none'}"
            )
    if source.warnings:
        parts.append("# Extraction warnings\n" + "\n".join(source.warnings))
    prompt = "\n\n".join(parts)
    assert_no_leak(prompt)
    return prompt


def prompt_identity(prompt: str) -> str:
    return hashlib.sha256((INSTRUCTIONS + "\0" + prompt).encode()).hexdigest()


def analyze(client: MetaClient, prompt: str, repair_context: list[str] | None = None) -> AnalysisAttempt:
    """One model call. ``repair_context`` carries validator rejections for a single retry."""
    text = prompt
    if repair_context:
        text += (
            "\n\n# Your previous answer was rejected\n" + "\n".join(f"- {r}" for r in repair_context)
            + "\nReturn a complete new answer that resolves these problems."
        )
        assert_no_leak(text)
    reply = client.generate(
        INSTRUCTIONS, text, "deployspecter_analysis", strict_json_schema(AnalysisOutput)
    )
    return AnalysisAttempt(kind="repair" if repair_context else "initial", reply=reply)


def parse_output(attempt: AnalysisAttempt) -> AnalysisOutput | None:
    try:
        return AnalysisOutput.model_validate(json.loads(attempt.reply.text))
    except (json.JSONDecodeError, ValidationError) as exc:
        attempt.parse_error = str(exc)[:2000]
        return None
