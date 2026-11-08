"""The sprint's Meta API spending ledger (ds-nbc/1): one cap, shared by every process and worktree.

Before a request, ``reserve(label, worst_usd)`` takes an exclusive file lock, adds up every settled cost and every
open reservation, and refuses (``BudgetRefused``) if the new worst case would pass the cap. After the request,
``settle(rid, ...)`` records the provider-reported usage. A reservation that is never settled keeps its worst case
counted. The worst case comes from ``MetaClient``'s own pre-call bound (input characters / 2 as tokens, plus the
request's ``max_output_tokens``), so no request can start unless its whole worst case fits.

The ledger is append-only JSONL at ``$DS_NBC_META_LEDGER``, by default inside the primary worktree, so it is shared by
every worktree and committed as evidence.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

CAP_USD = 5.00
LEDGER = Path(os.environ.get("DS_NBC_META_LEDGER", str(
    Path.home() / "Desktop/deployspecter-real-app-mvp/docs/experimental_mvp/nonblind_corpus/META_LEDGER.jsonl")))
PRICE_IN, PRICE_OUT = 1.25, 4.25  # per million tokens: meta_client.PRICE_*_PER_MTOK


class BudgetRefused(RuntimeError):
    pass


def worst_case(instructions: str, prompt: str, max_output_tokens: int) -> float:
    return (len(instructions) + len(prompt)) / 2 / 1e6 * PRICE_IN + max_output_tokens / 1e6 * PRICE_OUT


@contextmanager
def _locked():
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(str(LEDGER) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _entries() -> list[dict]:
    return [json.loads(x) for x in LEDGER.read_text().splitlines()] if LEDGER.exists() else []


def committed_usd(entries: list[dict] | None = None) -> float:
    """Settled costs plus the worst case of every open reservation (an unsettled one is never assumed free)."""
    es = _entries() if entries is None else entries
    settled = {e["rid"]: e for e in es if e["kind"] == "settle"}
    total = 0.0
    for e in es:
        if e["kind"] == "reserve":
            s = settled.get(e["rid"])
            cost = s.get("est_usd") if s else None
            total += e["worst_usd"] if cost is None else cost
    return round(total, 6)


def _append(e: dict) -> None:
    with LEDGER.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")


def reserve(label: str, worst_usd: float) -> str:
    with _locked():
        have = committed_usd()
        if have + worst_usd > CAP_USD:
            _append({"kind": "refused", "label": label, "worst_usd": round(worst_usd, 6), "committed_usd": have,
                     "cap_usd": CAP_USD, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            raise BudgetRefused(f"{label}: worst case ${worst_usd:.4f} on ${have:.4f} committed would pass the "
                                f"${CAP_USD:.2f} sprint cap")
        rid = uuid.uuid4().hex[:16]
        _append({"kind": "reserve", "rid": rid, "label": label, "worst_usd": round(worst_usd, 6),
                 "committed_before_usd": have, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return rid


def settle(rid: str, *, est_usd: float | None, tokens_in: int | None, tokens_out: int | None, ok: bool,
           detail: str = "") -> None:
    with _locked():
        _append({"kind": "settle", "rid": rid, "est_usd": est_usd, "tokens_in": tokens_in, "tokens_out": tokens_out,
                 "ok": ok, "detail": detail[:300], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def summary() -> dict:
    es = _entries()
    return {"cap_usd": CAP_USD, "committed_usd": committed_usd(es),
            "requests_reserved": sum(e["kind"] == "reserve" for e in es),
            "requests_settled": sum(e["kind"] == "settle" for e in es),
            "refusals": sum(e["kind"] == "refused" for e in es),
            "tokens_in": sum(e.get("tokens_in") or 0 for e in es if e["kind"] == "settle"),
            "tokens_out": sum(e.get("tokens_out") or 0 for e in es if e["kind"] == "settle")}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=1))
