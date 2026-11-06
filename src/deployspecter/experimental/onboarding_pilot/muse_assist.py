"""EXPERIMENTAL onboarding pilot, condition M: bounded Muse assistance. Muse proposes JSON only; nothing it returns
is executed.

``ask`` sends one frozen-instruction request built from the app's public package metadata (``PKG-INFO``: summary,
README) and the harness's adapter contract. It asks for a proposed app spec (without wheel hashes, which the harness
resolves from PyPI), the names of the library objects to use for each contract step, and a workload. The reply is
logged in full, validated deterministically, and given to the operator as advice. The operator still writes and
reviews the adapter module.

Hard limits come from ``MetaClient`` (``max_requests``, ``max_estimated_usd``) and the frozen pilot manifest.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

INSTRUCTIONS = (
    "You help onboard a Python PostgreSQL job-queue library into a test harness. Reply with JSON only, matching the "
    "schema. The harness gives the library one psycopg 3 AsyncConnection (or, if the library needs a pool, a pool "
    "built around that one connection) and calls three steps: install (create the library's schema), enqueue (add "
    "jobs for one trusted task taking an int argument n), work (run a worker until the jobs are done, then stop). "
    "Name the library's public API for each step, as dotted names with a one-line usage, using only what the "
    "documentation below states. Say what you don't know rather than guessing. Never include code to execute.")
PROMPT_VERSION = "onb-pilot-muse-1"
SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["package", "seam", "install_metadata", "steps", "task", "workload", "unknowns"],
    "properties": {
        "package": {"type": "string"}, "seam": {"type": "string", "enum": ["connection", "pool"]},
        "install_metadata": {"type": "boolean"},
        "steps": {"type": "object", "additionalProperties": False, "required": ["install", "enqueue", "work"],
                  "properties": {k: {"type": "string"} for k in ("install", "enqueue", "work")}},
        "task": {"type": "object", "additionalProperties": False, "required": ["name", "args"],
                 "properties": {"name": {"type": "string"},
                                "args": {"type": "array", "items": {"type": "string"}}}},
        "workload": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["key", "n"],
                                                 "properties": {"key": {"type": "string"},
                                                                "n": {"type": "integer"}}}},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "unknowns": {"type": "array", "items": {"type": "string"}}},
}
SCHEMA["required"].append("dependencies")


def prompt(pkg_info: str, contract: dict[str, Any]) -> str:
    return ("Harness contract:\n" + json.dumps(contract, indent=1) + "\n\nLibrary documentation (PKG-INFO):\n" +
            pkg_info[:24000])


def ask(client: Any, *, pkg_info: str, contract: dict[str, Any], log: Path, label: str) -> dict[str, Any]:
    p = prompt(pkg_info, contract)
    entry: dict[str, Any] = {"label": label, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "model": client.model, "prompt_version": PROMPT_VERSION,
                             "instructions_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
                             "prompt_sha256": hashlib.sha256(p.encode()).hexdigest()}
    try:
        reply = client.generate(INSTRUCTIONS, p, "onb_pilot_proposal", SCHEMA, max_output_tokens=6000)
    except Exception as exc:  # noqa: BLE001 - every outcome is logged
        entry.update(error=type(exc).__name__, detail=str(exc)[:300])
        _append(log, entry)
        raise
    entry.update(status=reply.status, tokens_in=reply.input_tokens, tokens_out=reply.output_tokens,
                 reasoning_tokens=reply.reasoning_tokens, est_usd=reply.approx_cost_usd,
                 cumulative_est_usd=round(client.estimated_usd, 6),
                 reply_sha256=hashlib.sha256(reply.text.encode()).hexdigest())
    (log.parent / f"{label}.reply.json").write_text(reply.text)
    try:
        entry["proposal"] = json.loads(reply.text)
    except ValueError:
        entry["parse_error"] = True
    _append(log, entry)
    return entry


def _append(log: Path, entry: dict[str, Any]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def to_workload(proposal: dict[str, Any]) -> dict[str, Any]:
    """Muse's workload in the harness format (validated afterwards like any other)."""
    t = proposal.get("task", {}).get("name")
    return {"jobs": [{"key": w.get("key"), "task": t, "args": {"n": w.get("n")}} for w in proposal.get("workload", [])]}
