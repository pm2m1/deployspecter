"""EXPERIMENTAL: Meta Muse proposes application workloads; the deterministic validator decides what runs.

Muse sees only what DeploySpecter itself could capture about the trusted fixture: each handler's app version, name,
argument types and ranges, the exact statements the handler may send, the schema and migration DDL, and the
validator's rules. It is never shown the fixture's docstrings, its defect, or any oracle result. It returns JSON in
the ``ds-mvp-workload/1`` schema; nothing it returns is executed except through ``workloads.validate`` and the trusted
registry. No generated SQL or code is ever run.

Every call is recorded in a local ledger: model, prompt hash, settings, token usage, estimated cost (Meta's published
rates, not billing data) and the validator's verdict on each proposal.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..meta_client import PRICING_NOTE, MetaClient, strict_json_schema
from .apps import reservations_mixed as app
from .workloads import MAX_EVENTS, MAX_INVOCATIONS, MAX_RESERVATIONS, RANGES, SCHEMA_VERSION, Proposal, validate

PROMPT_VERSION = "mvp-muse-workload-1"
INSTRUCTIONS = (
    "You design concurrency test workloads for an application that is being deployed while a schema migration is "
    "in its expand phase, so old (v1) and new (v2) application code and a background job run at the same time "
    "against one PostgreSQL database. A deterministic scheduler will run your workload's invocations concurrently "
    "and try different interleavings of their database operations; an independent checker then inspects the final "
    "state. Propose workloads that are likely to expose data-integrity problems specific to mixed-version operation "
    "and the migration. You may only invoke the listed handlers with the listed argument types; you cannot write "
    "SQL or code. Every proposal must satisfy the stated constraints exactly, or it is rejected. Return JSON only."
)


class ProposalSet(BaseModel):
    proposals: list[Proposal]


def context() -> dict[str, Any]:
    """The bounded, sanitized description Muse receives. Built from the registry and schema, not from docstrings."""
    handlers = []
    for (version, name), h in sorted(app.registry("buggy").items()):
        handlers.append({"app_version": version, "handler": name,
                         "args": {k: ("int" if t is int else "str") for k, t in h.args.items()},
                         "may_send_sql": sorted(h.allowed_sql)})
    return {
        "schema_version": SCHEMA_VERSION, "migration_phase": "expand",
        "schema_ddl": [" ".join(app.SCHEMA_V1.split()), " ".join(app.MIGRATION_EXPAND.split())],
        "handlers": handlers,
        "constraints": {
            "invocations": f"1 to {MAX_INVOCATIONS}, each with a unique lowercase actor name",
            "argument_ranges": {k: list(v) for k, v in RANGES.items()},
            "strings": "holder values are lowercase identifiers matching ^[a-z][a-z0-9_]{0,31}$",
            "args_encoding": "each arg is {name, kind: int|str, int_value, str_value}; the unused value is null",
            "seed": (f"1 to {MAX_EVENTS} events and at most {MAX_RESERVATIONS} reservations; 0 <= reserved <= "
                     "capacity; each event's reserved equals the sum of its seeded reservations' qty; reserved_v2 is "
                     "null or equal to reserved; invocations may only reference seeded events and reservations"),
            "migration_phase": "must be 'expand'",
        },
    }


def prompt(n: int) -> str:
    return (f"Propose exactly {n} different workloads.\n\nContext (JSON):\n"
            + json.dumps(context(), indent=1, sort_keys=True)
            + f"\n\nSet schema_version to {SCHEMA_VERSION!r}. Use invariant_hypotheses for the properties you expect "
              "a correct system to keep; they are recorded, not trusted.")


def prompt_hash(n: int) -> str:
    return hashlib.sha256((PROMPT_VERSION + INSTRUCTIONS + prompt(n)).encode()).hexdigest()


def propose(client: MetaClient, *, n: int, ledger: Path, call_label: str) -> list[tuple[str, dict[str, Any]]]:
    """One request. Returns the raw proposals (still to be validated) and appends a ledger entry."""
    entry: dict[str, Any] = {"call": call_label, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "model": client.model, "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_hash(n),
                             "reasoning_effort": client.reasoning_effort, "store": client.store,
                             "sampling": "provider defaults (the Responses API call sets no temperature or seed)",
                             "requested_proposals": n, "pricing_note": PRICING_NOTE}
    try:
        reply = client.generate(INSTRUCTIONS, prompt(n), "ds_mvp_workloads", strict_json_schema(ProposalSet),
                                max_output_tokens=16000)
    except Exception as exc:
        entry.update(error=type(exc).__name__, detail=str(exc)[:300])
        _append(ledger, entry)
        raise
    entry.update(status=reply.status, latency_s=reply.latency_seconds, input_tokens=reply.input_tokens,
                 output_tokens=reply.output_tokens, reasoning_tokens=reply.reasoning_tokens,
                 estimated_usd=reply.approx_cost_usd, cumulative_estimated_usd=round(client.estimated_usd, 6))
    try:
        raw = json.loads(reply.text).get("proposals", [])
    except ValueError:
        raw = []
        entry["parse_error"] = True
    out = [(f"{call_label}#{i}", p) for i, p in enumerate(raw)]
    entry["verdicts"] = []
    for label, p in out:
        v = validate(p, source="muse", label=label)
        entry["verdicts"].append({"label": label, "valid": not hasattr(v, "reasons"),
                                  "reasons": getattr(v, "reasons", [])})
    _append(ledger, entry)
    return out


def _append(ledger: Path, entry: dict[str, Any]) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def client_from_env_file(path: Path, *, max_requests: int, max_estimated_usd: float) -> MetaClient:
    """Reads only MODEL_API_KEY, MODEL_API_BASE_URL and MODEL_NAME from the file; never prints them, never reads a
    database URL from it."""
    from dotenv import dotenv_values

    from ..config import DEFAULT_BASE_URL, DEFAULT_MODEL, Settings
    values = dotenv_values(path)
    settings = Settings(admin_dsn="", test_dsn="", model_api_key=values.get("MODEL_API_KEY") or None,
                        model_base_url=values.get("MODEL_API_BASE_URL") or DEFAULT_BASE_URL,
                        model_name=values.get("MODEL_NAME") or DEFAULT_MODEL)
    return MetaClient(settings, max_requests=max_requests, max_estimated_usd=max_estimated_usd,
                      reasoning_effort="medium", store=False)
