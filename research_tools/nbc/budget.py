"""The sprint's Meta API spending ledger (ds-nbc/1): one cap, shared by every process and worktree.

Before a request, ``reserve(label, worst_usd)`` takes an exclusive file lock, adds up every settled cost and every
open reservation, and refuses (``BudgetRefused``) if the new worst case would pass the cap. After the request,
``settle(rid, ...)`` records the provider-reported usage. A reservation that is never settled keeps its worst case
counted. The worst case comes from ``MetaClient``'s own pre-call bound (input characters / 2 as tokens, plus the
request's ``max_output_tokens``), so no request can start unless its whole worst case fits.

The ledger is append-only JSONL. Its location is resolved at every use, never cached at import:
* ``$DS_NBC_META_LEDGER``, when set, must be an absolute path whose parent directory already exists;
* otherwise it is ``docs/experimental_mvp/nonblind_corpus/META_LEDGER.jsonl`` under **this file's own git worktree
  root**, verified with ``git rev-parse --show-toplevel``.

A missing directory is never created. A relocated or stale location is refused (``LedgerPathError``) instead of
silently starting a new, empty ledger. (Before 2026-11-10 the default was a fixed home-directory path, which after the
2026-11-08 folder move pointed at a directory that no longer existed: ds-nbc/1 DEVIATION_1.)

The cap is ``$DS_NBC_META_CAP_USD`` when set, else 5.00, and every reservation records the cap it was checked against.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DEFAULT_CAP_USD = 5.00
DEFAULT_RELATIVE = Path("docs/experimental_mvp/nonblind_corpus/META_LEDGER.jsonl")
PRICE_IN, PRICE_OUT = 1.25, 4.25  # per million tokens: meta_client.PRICE_*_PER_MTOK


class BudgetRefused(RuntimeError):
    pass


class LedgerPathError(RuntimeError):
    pass


def repo_root(module_file: str | None = None) -> Path:
    """This file's git worktree root, verified: the directory two levels above research_tools/nbc must be the
    top level git reports for it."""
    here = Path(module_file or __file__).resolve().parents[2]
    try:
        top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LedgerPathError(f"{here} is not a git worktree; set DS_NBC_META_LEDGER explicitly") from exc
    if Path(top).resolve() != here:
        raise LedgerPathError(f"{here} is not its git worktree's root ({top}); set DS_NBC_META_LEDGER explicitly")
    return here


def ledger_path(env: dict | None = None, module_file: str | None = None) -> Path:
    e = os.environ if env is None else env
    v = e.get("DS_NBC_META_LEDGER")
    p = Path(v) if v else repo_root(module_file) / DEFAULT_RELATIVE
    if not p.is_absolute():
        raise LedgerPathError(f"DS_NBC_META_LEDGER must be absolute, not {v!r}")
    if not p.parent.is_dir():
        raise LedgerPathError(f"the ledger directory {p.parent} doesn't exist; refusing to create it (a relocated or "
                              "stale path)")
    return p


def cap_usd(env: dict | None = None) -> float:
    e = os.environ if env is None else env
    return float(e.get("DS_NBC_META_CAP_USD") or DEFAULT_CAP_USD)


def worst_case(instructions: str, prompt: str, max_output_tokens: int) -> float:
    return (len(instructions) + len(prompt)) / 2 / 1e6 * PRICE_IN + max_output_tokens / 1e6 * PRICE_OUT


@contextmanager
def _locked():
    with open(str(ledger_path()) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _entries() -> list[dict]:
    p = ledger_path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


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
    with ledger_path().open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")


def reserve(label: str, worst_usd: float) -> str:
    cap = cap_usd()
    with _locked():
        have = committed_usd()
        if have + worst_usd > cap:
            _append({"kind": "refused", "label": label, "worst_usd": round(worst_usd, 6), "committed_usd": have,
                     "cap_usd": cap, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            raise BudgetRefused(f"{label}: worst case ${worst_usd:.4f} on ${have:.4f} committed would pass the "
                                f"${cap:.2f} sprint cap")
        rid = uuid.uuid4().hex[:16]
        _append({"kind": "reserve", "rid": rid, "label": label, "worst_usd": round(worst_usd, 6),
                 "committed_before_usd": have, "cap_usd": cap,
                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return rid


def settle(rid: str, *, est_usd: float | None, tokens_in: int | None, tokens_out: int | None, ok: bool,
           detail: str = "") -> None:
    with _locked():
        _append({"kind": "settle", "rid": rid, "est_usd": est_usd, "tokens_in": tokens_in, "tokens_out": tokens_out,
                 "ok": ok, "detail": detail[:300], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def summary() -> dict:
    es = _entries()
    return {"ledger": str(ledger_path()), "cap_usd": cap_usd(), "committed_usd": committed_usd(es),
            "requests_reserved": sum(e["kind"] == "reserve" for e in es),
            "requests_settled": sum(e["kind"] == "settle" for e in es),
            "refusals": sum(e["kind"] == "refused" for e in es),
            "tokens_in": sum(e.get("tokens_in") or 0 for e in es if e["kind"] == "settle"),
            "tokens_out": sum(e.get("tokens_out") or 0 for e in es if e["kind"] == "settle")}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=1))
