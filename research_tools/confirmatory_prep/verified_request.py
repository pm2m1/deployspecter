"""DEVELOPMENT-ONLY client-side protection for proposed decision #27 (revision candidate r1; PROPOSED). It wraps
the unchanged ``MetaClient`` and fails closed.

**Before a request,** the frozen record (``prep.freeze_record``) must match exactly:
- the requested model;
- the effort, ``store`` and ``max_output_tokens``;
- the proposer, instructions, context and schema digests.

Any difference refuses the request, and nothing is sent.

**After a request,** the provider-reported model (``response.model``) must equal the frozen model, or, under r2's
#27, the exact accepted string recorded at adoption (``frozen["accepted_provider_model"]``) when one is frozen. The
proxy captures it from the response object; ``MetaClient`` itself is not modified.
- **On a mismatch:** the reply is kept for the record but marked a **provider failure**. Its proposals are never
  used, and it counts against R with its cost.
- **On a transport or provider error:** the worst-case cost that ``MetaClient`` charged before the call stays
  counted, and the error is recorded. It is never retried here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep  # noqa: E402


class ProviderModelMismatch(RuntimeError):
    pass


class _Capture:
    def __init__(self, inner: Any) -> None:
        self.inner, self.last = inner, None

    def create(self, **kw: Any) -> Any:
        self.last = self.inner.create(**kw)
        return self.last


class _ClientProxy:
    def __init__(self, inner: Any) -> None:
        self.responses = _Capture(inner.responses)


def verified_generate(client: Any, frozen: dict[str, Any], *, proposer_source: bytes, instructions: str,
                      context: Any, schema: Any, prompt: str, schema_name: str, max_output_tokens: int
                      ) -> dict[str, Any]:
    current = prep.freeze_record(proposer_source=proposer_source, instructions=instructions, context=context,
                                 schema=schema, model=client.model, reasoning_effort=client.reasoning_effort,
                                 store=client.store, max_output_tokens=max_output_tokens)
    accepted = frozen.get("accepted_provider_model", frozen["model"])  # r2 #27: the exact string recorded at adoption
    prep.verify_freeze({k: v for k, v in frozen.items() if k != "accepted_provider_model"}, current)  # before sending
    inner = client._client
    proxy = _ClientProxy(inner)
    client._client = proxy
    try:
        reply = client.generate(instructions, prompt, schema_name, schema, max_output_tokens=max_output_tokens)
    except prep.PrepRefused:
        raise
    except Exception as exc:  # transport or provider error: worst case stays charged by MetaClient
        return {"status": "request_failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "charged_usd": client.estimated_usd, "usable": False}
    finally:
        client._client = inner
    reported = getattr(proxy.responses.last, "model", None)
    out = {"status": "ok", "reply_text": reply.text, "est_usd": reply.approx_cost_usd,
           "provider_model": reported, "usable": True}
    out["accepted_provider_model"] = accepted
    if reported != accepted:
        out.update(status="provider_model_mismatch", usable=False)  # kept for the record, never used
    return out
