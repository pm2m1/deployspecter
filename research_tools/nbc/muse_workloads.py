"""The frozen Muse workload proposer for ds-nbc/1 arm RA-MD (``nbc-muse-workloads/1``).

``Proposer(integration, journal_dir)(case, arm, request_index) -> (text, usage)``, the ``real_executor`` model
interface. Each request:
* sends the frozen instructions, plus the integration's ``MUSE_CONTEXT`` (its handler registry and the application
  schema; never the fix, tests, oracle or results) and the workload JSON schema;
* asks for ``P`` workloads, as strict structured output;
* uses the plan's §4 settings: ``muse-spark-1.3`` by default, reasoning effort medium, store=False;
* reserves its worst case in the sprint ledger (``budget.py``, $5 cap) before sending, and settles it after;
* saves the raw reply on arrival.

Proposals are returned as a JSON list. The integration's ``validate`` decides each one, and the executor counts the
invalid ones.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))
import budget  # noqa: E402

VERSION = "nbc-muse-workloads/1"
MAX_OUTPUT_TOKENS = 16000
INSTRUCTIONS = (
    "You propose test workloads for a database-backed application. A workload is data only: it names registered "
    "operations and their arguments, exactly as the JSON schema allows. You never write SQL, code, schedules or "
    "timing; a separate deterministic explorer chooses every interleaving. Aim for workloads that could expose a "
    "correctness problem under concurrency (for example lost updates, duplicate processing, a cancellation or "
    "completion overwritten, an incorrect state transition), using only what the context documents. Each "
    "workload must be valid against the schema and differ from the others."
)


def prompt(context: dict[str, Any], p: int) -> str:
    return (f"Propose exactly {p} different workloads.\n\nContext (JSON):\n"
            + json.dumps(context, indent=1, sort_keys=True))


def response_schema(workload_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["workloads", "rationale"],
            "properties": {"workloads": {"type": "array", "items": workload_schema},
                           "rationale": {"type": "string"}}}


def prompt_sha256(integration: Any, p: int) -> str:
    return hashlib.sha256((VERSION + INSTRUCTIONS + prompt(integration.MUSE_CONTEXT, p)
                           + json.dumps(integration.WORKLOAD_JSON_SCHEMA, sort_keys=True)).encode()).hexdigest()


class Proposer:
    def __init__(self, integration: Any, journal: Path, *, p: int, env_file: Path, model_name: str | None = None):
        self.integ, self.journal, self.p, self.env_file = integration, journal, p, env_file
        self.model_name = model_name

    def __call__(self, case: dict[str, Any], arm: str, i: int) -> tuple[str, dict[str, Any]]:
        from deployspecter.experimental import muse
        label = f"{case['case_id']}__{arm}__req{i}"
        pr = prompt(self.integ.MUSE_CONTEXT, self.p)
        worst = budget.worst_case(INSTRUCTIONS, pr, MAX_OUTPUT_TOKENS)
        rid = budget.reserve(label, worst)  # BudgetRefused propagates: no request is sent
        client = muse.client_from_env_file(self.env_file, max_requests=1, max_estimated_usd=worst + 1e-9)
        self.journal.mkdir(parents=True, exist_ok=True)
        entry = {"proposer": VERSION, "label": label, "model": client.model, "reasoning_effort": "medium",
                 "store": False, "prompt_sha256": prompt_sha256(self.integ, self.p), "worst_usd": round(worst, 6),
                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        try:
            reply = client.generate(INSTRUCTIONS, pr, "ds_nbc_workloads",
                                    response_schema(self.integ.WORKLOAD_JSON_SCHEMA),
                                    max_output_tokens=MAX_OUTPUT_TOKENS)
        except Exception as exc:  # a transport or provider failure: counted against R by the executor
            budget.settle(rid, est_usd=None, tokens_in=None, tokens_out=None, ok=False,
                          detail=f"{type(exc).__name__}: {exc}")
            entry.update(error=type(exc).__name__, detail=str(exc)[:300])
            self._log(entry)
            raise ConnectionError(str(exc)) from exc
        (self.journal / f"{label}.reply.json").write_text(reply.text)  # kept on arrival
        budget.settle(rid, est_usd=reply.approx_cost_usd, tokens_in=reply.input_tokens,
                      tokens_out=reply.output_tokens, ok=True)
        entry.update(status=reply.status, latency_s=reply.latency_seconds, tokens_in=reply.input_tokens,
                     tokens_out=reply.output_tokens, reasoning_tokens=reply.reasoning_tokens,
                     est_usd=reply.approx_cost_usd, reply_sha256=hashlib.sha256(reply.text.encode()).hexdigest())
        self._log(entry)
        try:
            workloads = json.loads(reply.text).get("workloads")
        except (ValueError, AttributeError):
            workloads = None
        text = json.dumps(workloads) if isinstance(workloads, list) else reply.text  # unparseable: counted invalid
        return text, {"tokens_in": reply.input_tokens or 0, "tokens_out": reply.output_tokens or 0,
                      "est_usd": reply.approx_cost_usd if reply.approx_cost_usd is not None else worst}

    def _log(self, entry: dict[str, Any]) -> None:
        with (self.journal / "calls.jsonl").open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
