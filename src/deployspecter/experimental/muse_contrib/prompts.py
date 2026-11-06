"""EXPERIMENTAL: every prompt of the Muse-contribution experiment, by name, and the logged call.

Each prompt is (instructions, prompt text, response model). The experiment manifest freezes each prompt's text and
sha256 before any outcome is produced; a run refuses a prompt whose hash differs from the frozen one.

Every request is appended to the call ledger at once (time, purpose, model, settings, prompt sha256, tokens,
estimated USD at Meta's published rates, the reply's sha256), and the raw reply is written beside it. Validation
verdicts are appended afterwards as separate ``validation`` entries. No model output is executed.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ...meta_client import PRICING_NOTE, MetaClient, strict_json_schema
from .. import muse_apps as ma
from .. import muse_procrastinate as mp
from ..invariants import language as lg
from . import candidates as cand
from . import chancy as ch
from . import reservations as rs

PROMPT_VERSION = "ds-mc-1"
MAX_OUTPUT_TOKENS = 16000


def invariant_prompt(domain: str, n: int) -> str:
    return (f"Propose exactly {n} different invariant hypotheses for this application.\n\nApplication: "
            f"{cand.APPLICATION[domain]}\n\nGrammar and tables (JSON):\n"
            + json.dumps(lg.grammar_description(domain), indent=1, sort_keys=True)
            + f"\n\nSet domain to {domain!r} in every hypothesis, and give each a short unique id.")


def catalog(name: str, n: int) -> tuple[str, str, type[BaseModel]]:
    if name == "chancy_workloads":
        return ch.INSTRUCTIONS, ch.prompt(n), ch.ChancyProposalSet
    if name == "procrastinate_workloads":
        return mp.INSTRUCTIONS, mp.prompt(n), mp.ProposalSet
    if name == "reservations_planner":
        return ma.INSTRUCTIONS["planner"], ma.prompt("planner", n), ma.PlannerSet
    if name.startswith("reservations_state:"):
        s = name.split(":", 1)[1]
        return rs.STATE_INSTRUCTIONS, rs.state_prompt(s, n), rs.StateProposalSet
    if name.startswith("invariants:"):
        return cand.INVARIANT_INSTRUCTIONS, invariant_prompt(name.split(":", 1)[1], n), lg.HypothesisSet
    raise KeyError(name)


def prompt_sha256(name: str, n: int) -> str:
    instructions, text, model = catalog(name, n)
    schema = json.dumps(strict_json_schema(model), sort_keys=True)
    return hashlib.sha256("\n␞\n".join((PROMPT_VERSION, instructions, text, schema)).encode()).hexdigest()


def append(ledger: Path, entry: dict[str, Any]) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def call(client: MetaClient, name: str, n: int, *, label: str, purpose: str, ledger: Path,
         frozen_sha256: str) -> list[tuple[str, Any]]:
    """One request. Refuses a prompt that isn't the frozen one. Returns the raw proposals, unvalidated."""
    got = prompt_sha256(name, n)
    if got != frozen_sha256:
        raise RuntimeError(f"refused: prompt {name} n={n} has sha256 {got[:12]}, not the frozen {frozen_sha256[:12]}")
    instructions, text, model = catalog(name, n)
    key = "proposals" if model is not lg.HypothesisSet else "hypotheses"
    entry: dict[str, Any] = {"kind": "call", "call": label, "purpose": purpose, "prompt": name, "requested": n,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": client.model,
                             "prompt_version": PROMPT_VERSION, "prompt_sha256": got,
                             "reasoning_effort": client.reasoning_effort, "store": client.store,
                             "sampling": "provider defaults (no temperature or seed is set)",
                             "max_output_tokens": MAX_OUTPUT_TOKENS, "pricing_note": PRICING_NOTE}
    try:
        reply = client.generate(instructions, text, f"ds_mc_{name.split(':')[0]}", strict_json_schema(model),
                                max_output_tokens=MAX_OUTPUT_TOKENS)
    except Exception as exc:
        entry.update(error=type(exc).__name__, detail=str(exc)[:300])
        append(ledger, entry)
        raise
    (ledger.parent / f"{label}.reply.json").write_text(reply.text)  # kept at once: a later failure can't lose it
    entry.update(status=reply.status, latency_s=reply.latency_seconds, input_tokens=reply.input_tokens,
                 output_tokens=reply.output_tokens, reasoning_tokens=reply.reasoning_tokens,
                 estimated_usd=reply.approx_cost_usd, cumulative_estimated_usd=round(client.estimated_usd, 6),
                 raw_reply_sha256=hashlib.sha256(reply.text.encode()).hexdigest())
    try:
        raw = json.loads(reply.text).get(key, [])
    except ValueError:
        raw, entry["parse_error"] = [], True
    entry["returned"] = len(raw) if isinstance(raw, list) else 0
    append(ledger, entry)
    return [(f"{label}#{i}", p) for i, p in enumerate(raw if isinstance(raw, list) else [])]


def record_validation(ledger: Path, label: str, valid: bool, reasons: list[Any], **extra: Any) -> None:
    append(ledger, {"kind": "validation", "proposal": label, "valid": valid, "reasons": reasons, **extra,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
