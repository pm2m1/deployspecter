"""EXPERIMENTAL: the independent oracle step (ruling #7), as a hash-chained ledger ``ORACLE_LEDGER.jsonl``.

Entry kinds, in order per case:
1. ``spec``: the oracle author's specification file (its sha256), for a case's sealed manifest. The author attests
   that it was written from documentation and the public API only.
2. ``review``: a reviewer, who must be a different role identifier from the author, approves or rejects that exact
   spec sha256. The approval also binds the oracle **code** file's sha256 that encodes the spec.
3. ``validation``: the curator's corrected-version validation. The curator, who holds the reveal, runs **only the
   fixed revision**, under the oracle code, through an executor, and records "holds" or "doesn't hold". The record
   says nothing about which slot (R1 or R2) is fixed.
4. ``freeze``: no spec, review or validation is accepted after it. An oracle file whose sha256 isn't in the frozen,
   approved set is never given to an evaluator (``allowlist``, the format ``evaluator_sandbox`` reads).

**Post-outcome edits** are refused twice:
- here, a ``spec`` or ``review`` after the freeze, or after a results ledger exists, is refused;
- at scoring, ``run.py`` compares the oracle code's sha256 with the frozen approval.

The reveal and the reveal authorization are held by the curator and the owner, outside the evaluator's package.
Only ``validation`` touches the reveal, and it runs on the curator's side.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus_intake as ci  # noqa: E402

SIM = "SIMULATION"


class OracleRefused(SystemExit):
    pass


def _chain(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _h(e: dict[str, Any]) -> str:
    return hashlib.sha256(ci.canonical({k: v for k, v in e.items() if k != "hash"})).hexdigest()


def verify(ledger: Path) -> list[str]:
    p, prev = [], "0" * 64
    for i, e in enumerate(_chain(ledger)):
        if e.get("seq") != i or e.get("prev") != prev or e.get("hash") != _h(e):
            p.append(f"oracle ledger entry {i}: the chain is broken")
        prev = e.get("hash", "")
    return p


def _append(ledger: Path, entry: dict[str, Any], *, results_ledger: Path | None = None) -> dict[str, Any]:
    entries = _chain(ledger)
    if verify(ledger):
        raise OracleRefused(f"refused: {verify(ledger)}")
    if entry["kind"] != "freeze" and any(e["kind"] == "freeze" for e in entries):
        raise OracleRefused("refused: the oracle set is frozen; no spec, review or validation after the freeze")
    if entry["kind"] in ("spec", "review") and results_ledger is not None and results_ledger.exists():
        raise OracleRefused("refused: results exist; an oracle can't be written or approved after outcomes")
    e = {"seq": len(entries), **entry, "prev": entries[-1]["hash"] if entries else "0" * 64,
         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    e["hash"] = _h(e)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def add_spec(ledger: Path, case_dir: Path, spec: Path, *, author: str, simulation: bool = False,
             results_ledger: Path | None = None) -> dict[str, Any]:
    m = json.loads((case_dir / "case.json").read_text())
    h = ci.sha(spec)
    if h != m["oracle_spec_sha256"]:
        raise OracleRefused(f"refused: the spec's sha256 {h[:12]} isn't the one sealed in the case manifest")
    return _append(ledger, {"kind": "spec", "case_id": m["case_id"], "spec_sha256": h, "author": author,
                            "attests": "written from documentation and the public API only",
                            **({SIM: True} if simulation else {})}, results_ledger=results_ledger)


def add_review(ledger: Path, case_id: str, *, spec_sha256: str, oracle_code: Path, reviewer: str, verdict: str,
               simulation: bool = False, results_ledger: Path | None = None) -> dict[str, Any]:
    specs = [e for e in _chain(ledger) if e["kind"] == "spec" and e["case_id"] == case_id
             and e["spec_sha256"] == spec_sha256]
    if not specs:
        raise OracleRefused("refused: no such spec for this case")
    if reviewer == specs[-1]["author"]:
        raise OracleRefused("refused: the reviewer must not be the spec's author")
    if verdict not in ("approve", "reject"):
        raise OracleRefused("refused: verdict is approve or reject")
    return _append(ledger, {"kind": "review", "case_id": case_id, "spec_sha256": spec_sha256,
                            "oracle_code": oracle_code.name, "oracle_code_sha256": ci.sha(oracle_code),
                            "reviewer": reviewer, "verdict": verdict, **({SIM: True} if simulation else {})},
                   results_ledger=results_ledger)


def validate_corrected(ledger: Path, case_dir: Path, reveal: Path, oracle_code: Path,
                       executor: Callable[[Path, Path], bool], *, simulation: bool = False) -> dict[str, Any]:
    """Curator side. ``executor(fixed_archive, oracle_code) -> holds`` sees the fixed revision only (a copy, in a
    fresh directory). The ledger records only whether the oracle holds there."""
    roles = ci.score(case_dir, reveal)["revision_roles"]
    fixed_slot = next(s for s, r in roles.items() if r == "fixed")
    with tempfile.TemporaryDirectory(prefix="ds_corrected_") as d:
        copy = Path(d) / "revision.tar"
        copy.write_bytes((case_dir / f"{fixed_slot}.tar").read_bytes())
        with tarfile.open(copy):  # a readable archive, nothing else is passed
            pass
        holds = bool(executor(copy, oracle_code))
    m = json.loads((case_dir / "case.json").read_text())
    return _append(ledger, {"kind": "validation", "case_id": m["case_id"],
                            "oracle_code_sha256": ci.sha(oracle_code),
                            "result": "holds" if holds else "doesn't hold", **({SIM: True} if simulation else {})})


def freeze(ledger: Path, *, simulation: bool = False) -> dict[str, Any]:
    return _append(ledger, {"kind": "freeze", **({SIM: True} if simulation else {})})


def approved(ledger: Path) -> dict[str, dict[str, Any]]:
    """Per case: the frozen, reviewer-approved, validated oracle (or why not)."""
    entries = _chain(ledger)
    frozen = any(e["kind"] == "freeze" for e in entries)
    out: dict[str, dict[str, Any]] = {}
    for e in entries:
        c = out.setdefault(e.get("case_id", ""), {"problems": []}) if e["kind"] != "freeze" else None
        if c is None:
            continue
        if e["kind"] == "spec":
            c.update(spec_sha256=e["spec_sha256"], author=e["author"], simulation=bool(e.get(SIM)))
        elif e["kind"] == "review" and e["verdict"] == "approve" and e["spec_sha256"] == c.get("spec_sha256"):
            c.update(oracle_code=e["oracle_code"], oracle_code_sha256=e["oracle_code_sha256"],
                     reviewer=e["reviewer"])
            c["simulation"] = c.get("simulation") or bool(e.get(SIM))
        elif e["kind"] == "validation" and e["oracle_code_sha256"] == c.get("oracle_code_sha256"):
            c["validation"] = e["result"]
    out.pop("", None)
    for c in out.values():
        if not frozen:
            c["problems"].append("the oracle set isn't frozen")
        if "oracle_code_sha256" not in c:
            c["problems"].append("no reviewer approval of the current spec")
        if c.get("validation") != "holds":
            c["problems"].append(f"corrected-version validation: {c.get('validation', 'not run')}")
    return out


def allowlist(ledger: Path) -> dict[str, str]:
    """``{oracle file name: sha256}`` of the approved, validated, frozen oracles (for evaluator_sandbox)."""
    return {c["oracle_code"]: c["oracle_code_sha256"] for c in approved(ledger).values()
            if not c["problems"] and "oracle_code" in c}
