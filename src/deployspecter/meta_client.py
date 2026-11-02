"""Meta Model API client (Responses API through the OpenAI SDK). Never logs the key.

New requests use explicit medium reasoning and store=False. Pass both options as None
only to reproduce historical provider-default requests; never rewrite frozen records.
"""

from __future__ import annotations

import copy
import time
from typing import Any

from openai import OpenAI
from pydantic import BaseModel

from .config import Settings

# Standard rates from https://dev.meta.ai/docs/pricing-rate-limits, read 2026-11-01. The page does not
# say how reasoning tokens are billed; all output tokens, which include them, are costed at the output rate.
PRICE_INPUT_PER_MTOK = 1.25
PRICE_OUTPUT_PER_MTOK = 4.25
PRICING_NOTE = "estimated at Meta's published standard rates, $1.25 input and $4.25 output per MTok; not billing data"


class MissingApiKey(RuntimeError):
    pass


class ModelBudgetExceeded(RuntimeError):
    """Raised before sending a request that the configured limits do not allow."""


class ModelReply(BaseModel):
    text: str
    status: str | None = None
    latency_seconds: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    approx_cost_usd: float | None = None


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Inline $refs and mark every property required, as strict structured output expects."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]))
        # Keys inside "properties" are field names, not JSON Schema keywords, so a field
        # called "description" or "title" must survive.
        out = {
            k: ({name: walk(sub) for name, sub in v.items()} if k == "properties" else walk(v))
            for k, v in node.items()
            if k not in {"title", "default", "description"}
        }
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    return walk(schema)


class MetaClient:
    def __init__(
        self, settings: Settings, client: Any | None = None, timeout: float = 300.0,
        max_requests: int = 0, max_estimated_usd: float = 0.0,
        reasoning_effort: str | None = "medium", store: bool | None = False,
    ) -> None:
        self.model = settings.model_name
        if reasoning_effort not in {None, "low", "medium", "high"}:
            raise ValueError("reasoning_effort must be low, medium, high or None (legacy)")
        self.reasoning_effort = reasoning_effort
        self.store = store
        self.max_requests = max_requests
        self.max_estimated_usd = max_estimated_usd
        self.requests_sent = 0
        self.estimated_usd = 0.0
        if client is None:
            if not settings.model_api_key:
                raise MissingApiKey(
                    "MODEL_API_KEY is not set. Add it to .env (git-ignored); do not pass it on the command line."
                )
            client = OpenAI(
                api_key=settings.model_api_key, base_url=settings.model_base_url, timeout=timeout, max_retries=0
            )
        self._client = client

    def generate(
        self, instructions: str, prompt: str, schema_name: str, schema: dict[str, Any],
        max_output_tokens: int = 24000,
    ) -> ModelReply:
        # Characters / 2 overstates input tokens, so this bound is conservative.
        worst_case = (
            (len(instructions) + len(prompt)) / 2 / 1e6 * PRICE_INPUT_PER_MTOK
            + max_output_tokens / 1e6 * PRICE_OUTPUT_PER_MTOK
        )
        if self.requests_sent >= self.max_requests:
            raise ModelBudgetExceeded(f"request limit reached ({self.max_requests})")
        if self.estimated_usd + worst_case > self.max_estimated_usd:
            raise ModelBudgetExceeded(
                f"next request could exceed the ${self.max_estimated_usd} estimated limit "
                f"(spent ~${self.estimated_usd:.4f}, worst case ~${worst_case:.4f})"
            )
        self.requests_sent += 1
        # Charged up front so a failed request still counts against the limit.
        self.estimated_usd += worst_case
        started = time.perf_counter()
        request_options: dict[str, Any] = {}
        if self.reasoning_effort is not None:
            request_options["reasoning"] = {"effort": self.reasoning_effort}
        if self.store is not None:
            request_options["store"] = self.store
        response = self._client.responses.create(
            **request_options,
            model=self.model,
            instructions=instructions,
            input=prompt,
            max_output_tokens=max_output_tokens,
            text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        )
        latency = round(time.perf_counter() - started, 3)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        details = getattr(usage, "output_tokens_details", None)
        cost = None
        if input_tokens is not None and output_tokens is not None:
            cost = round(
                input_tokens / 1e6 * PRICE_INPUT_PER_MTOK + output_tokens / 1e6 * PRICE_OUTPUT_PER_MTOK, 6
            )
            self.estimated_usd += cost - worst_case
        return ModelReply(
            text=response.output_text or "",
            status=getattr(response, "status", None),
            latency_seconds=latency,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=getattr(details, "reasoning_tokens", None),
            approx_cost_usd=cost,
        )
