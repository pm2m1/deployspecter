"""EXPERIMENTAL confirmatory arm executor (``ds-confirm-exec/1``), behind ``run.py``'s seven real-mode gates.

It runs as the **evaluator role**. It reads only:
* the evaluator package (``cases/*/case.json``, ``R1.tar``, ``R2.tar``, built and leak-scanned by the sealed tools);
* the locked budgets, the approved oracle set and the reviewed application integrations.

It never receives a reveal path, and it writes only through the orchestrator's ``Writer``. On one machine the role
separation is procedural; real separation needs the curator and the evaluator on different accounts or machines.

**Per case × arm × replicate × slot** (R1, R2), schedules ``1..B/replicates``:
* the seeded explorer uses the **same seeds for every arm**, derived from (case, replicate, schedule index);
* a run ends at the first detection (a SOUND, corroborated run whose approved oracle reports VIOLATED), or at the
  schedule budget, or at the time cap T per case and arm (a censored nondetection);
* the first non-SOUND schedule ends it as REFUSED or INCOMPLETE. The plan's retry policy is "runs never retried";
  model requests are retried only on transport failures, counted against R.
* Deterministic arms have a model budget of zero, enforced. A Muse arm needs a frozen model and prompt manifest
  (plan §4), refused otherwise, and is held to R requests and the cost caps.

**Persistence and interruption.**
* Each finished run is appended at once to the hash-chained results ledger, which refuses anything after an
  authorization.
* Muse proposals are journaled, so a resumed run reuses them instead of asking again.
* An interruption (KeyboardInterrupt, SIGINT) writes ``INTERRUPTED.json`` with the committed keys, then re-raises.
  A restart resumes after the last committed run, with no duplicates.

**Reveal.** The executor never authorizes. ``authorize_real`` needs every expected run committed and an owner record
that isn't SIMULATION, whose authorizer is the owner who signed ``OWNER_APPROVAL.md``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

VERSION = "ds-confirm-exec/1"
DETERMINISTIC = ("RA-DET", "RA-DET-C")
MUSE = ("RA-MD", "RA-MS", "RA-MDS")


class ExecRefused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(msg if msg.startswith("refused") else f"refused: {msg}")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def seed_for(case_id: str, replicate: int, i: int) -> int:
    """The same seed for every arm (plan §3): the explorer never favours an arm."""
    return int(hashlib.sha256(f"{case_id}|{replicate}|{i}".encode()).hexdigest()[:8], 16)


def key(r: dict[str, Any]) -> tuple[str, str, int, str]:
    return (r["case_id"], r["arm"], int(r["replicate"]), r["slot"])


class Executor:
    """``integrations``: application name → integration (``prepare(archive, sha256, workdir)`` and
    ``run(handle, workload, seed, out)``, plus ``workloads(arm)`` and ``validate(workload)``).
    ``oracles``: case_id → (oracle code path, sha256), approved and frozen.
    ``append``: commits one run to the results ledger (``run._append_result`` bound).
    ``model``: the Muse proposer, ``model(case, arm, request_index) -> (text, usage)``, or None.
    ``model_manifest``: the frozen §4 model and prompt manifest, required for any Muse arm."""

    def __init__(self, *, package: Path, lock: dict[str, Any], arms: list[str], oracles: dict[str, tuple[Path, str]],
                 integrations: dict[str, Any], append: Callable[[dict[str, Any]], None], committed: Callable[[], list],
                 workdir: Path, write_json: Callable[[Path, Any], str], model: Any = None,
                 model_manifest: dict[str, Any] | None = None, replicates: list[int] | None = None,
                 simulation: bool = False) -> None:
        self.package, self.lock, self.arms, self.oracles = package, lock, arms, oracles
        self.integrations, self.append, self.committed = integrations, append, committed
        self.workdir, self.write_json, self.model, self.model_manifest = workdir, write_json, model, model_manifest
        self.b = lock["budgets"]
        self.replicates = replicates if replicates is not None else list(range(self.b["replicates"]))
        self.simulation = simulation

    # ---------------------------------------------------------------------------------------------- preflight
    def cases(self) -> list[tuple[Path, dict[str, Any]]]:
        out = []
        for d in sorted(p for p in (self.package / "cases").iterdir() if p.is_dir()):
            out.append((d, json.loads((d / "case.json").read_text())))
        return out

    def preflight(self) -> list[str]:
        p = []
        cases = self.cases()
        if not cases:
            p.append("the evaluator package holds no case")
        for d, c in cases:
            if c.get("application") not in self.integrations:
                p.append(f"{c['case_id']}: no reviewed integration for application {c.get('application')!r}")
            for slot in ("R1", "R2"):
                want = (c.get("revisions") or {}).get(slot, {}).get("sha256")
                if not (d / f"{slot}.tar").exists() or sha(d / f"{slot}.tar") != want:
                    p.append(f"{c['case_id']}/{slot}: the archive isn't the frozen manifest's ({str(want)[:12]})")
            o = self.oracles.get(c["case_id"])
            if o is None:
                p.append(f"{c['case_id']}: no approved oracle")
            elif not o[0].exists() or sha(o[0]) != o[1]:
                p.append(f"{c['case_id']}: the oracle code isn't the approved one")
        if any(a in MUSE for a in self.arms):
            if self.model is None:
                p.append("a Muse arm without a model client")
            if not self.model_manifest or not self.model_manifest.get("frozen_at_adoption"):
                p.append("a Muse arm without the frozen model and prompt manifest (plan §4)")
        for a in self.arms:
            if a not in DETERMINISTIC + MUSE:
                p.append(f"unknown arm {a}")
        return p

    def expected(self) -> list[tuple[str, str, int, str]]:
        return [(c["case_id"], a, r, s) for _, c in self.cases() for a in self.arms for r in self.replicates
                for s in ("R1", "R2")]

    # ---------------------------------------------------------------------------------------------- workloads
    def workloads(self, integ: Any, case: dict[str, Any], arm: str) -> tuple[list[Any], dict[str, Any]]:
        usage = {"tokens_in": 0, "tokens_out": 0, "est_usd": 0.0, "invalid_proposals": 0, "requests": 0}
        if arm in DETERMINISTIC:
            return integ.workloads(arm), usage
        journal = self.workdir / "proposals" / f"{case['case_id']}__{arm}.json"
        if journal.exists():  # resumed: reuse, never ask again
            j = json.loads(journal.read_text())
            return j["valid"], j["usage"]
        valid: list[Any] = []
        attempts = 0
        while usage["requests"] < self.b["R"] and attempts < self.b["R"] * 3:
            attempts += 1
            try:
                text, u = self.model(case, arm, usage["requests"])
            except ConnectionError:
                usage["requests"] += 1  # a transport failure: retried, counted against R
                continue
            usage["requests"] += 1
            for k in ("tokens_in", "tokens_out", "est_usd"):
                usage[k] += u.get(k, 0)
            if usage["est_usd"] > self.b["C_usd_per_case_arm"]:
                raise ExecRefused(f"{case['case_id']}/{arm}: model cost would exceed the per case and arm cap")
            try:
                proposals = json.loads(text)
            except ValueError:
                usage["invalid_proposals"] += 1
                continue
            for w in proposals if isinstance(proposals, list) else [proposals]:
                if integ.validate(w):
                    usage["invalid_proposals"] += 1
                else:
                    valid.append(w)
        self.write_json(journal, {"valid": valid, "usage": usage, "executor": VERSION})
        return valid, usage

    # ---------------------------------------------------------------------------------------------- execution
    def run(self) -> dict[str, Any]:
        if why := self.preflight():
            raise ExecRefused("; ".join(why))
        done = {key(e["run"]) for e in self.committed() if e["kind"] == "result"}
        per_rep = self.b["B"] // self.b["replicates"]
        handles: dict[tuple[str, str], Any] = {}
        cost = 0.0
        try:
            for d, c in self.cases():
                integ = self.integrations[c["application"]]
                for arm in self.arms:
                    todo = [(r, s) for r in self.replicates for s in ("R1", "R2")
                            if (c["case_id"], arm, r, s) not in done]
                    if not todo:
                        continue
                    work, usage = self.workloads(integ, c, arm)
                    if arm in DETERMINISTIC and (usage["tokens_in"] or usage["est_usd"]):
                        raise ExecRefused(f"{arm} used the model (#16)")
                    cost += usage["est_usd"]
                    if cost > self.b["C_usd_per_evaluation"]:
                        raise ExecRefused("the evaluation's model cost cap would be exceeded")
                    spent = 0.0
                    for rep, slot in todo:
                        h = handles.get((c["case_id"], slot))
                        if h is None:
                            h = handles[(c["case_id"], slot)] = integ.prepare(
                                d / f"{slot}.tar", c["revisions"][slot]["sha256"], self.workdir / "build")
                        rec = self.one(integ, h, c, arm, rep, slot, work, usage, per_rep, spent)
                        spent += rec["wall_seconds"]
                        self.append(rec)
                        done.add(key(rec))
        except KeyboardInterrupt:
            self.write_json(self.workdir / "INTERRUPTED.json", {
                "executor": VERSION, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "committed": sorted(map(list, done)), "remaining": len(set(self.expected()) - done),
                "note": "the committed runs are final; restarting resumes after them"})
            raise
        return {"executor": VERSION, "runs_committed": len(done), "expected": len(self.expected()),
                "complete": set(self.expected()) <= done, "model_cost_usd": round(cost, 6)}

    def one(self, integ: Any, handle: Any, c: dict[str, Any], arm: str, rep: int, slot: str, work: list[Any],
            usage: dict[str, Any], per_rep: int, spent: float) -> dict[str, Any]:
        cid = c["case_id"]
        rec: dict[str, Any] = {"case_id": cid, "arm": arm, "replicate": rep, "slot": slot, "executor": VERSION,
                               "first_violation_schedule": None, "schedules_used": 0, "wall_seconds": 0.0,
                               "verdict": "HOLDS", "corroboration": {"ok": True, "params_mismatched": 0,
                                                                     "params_unchecked": 0},
                               "tokens_in": usage["tokens_in"], "tokens_out": usage["tokens_out"],
                               "est_usd": usage["est_usd"], "invalid_proposals": usage["invalid_proposals"],
                               "schedules": [], **({"SIMULATION": True} if self.simulation else {})}
        if not work:
            rec["verdict"] = "REFUSED"
            rec["reason"] = "no valid workload for this arm"
            return rec
        workload = work[rep % len(work)]
        rec["workload_sha256"] = hashlib.sha256(json.dumps(workload, sort_keys=True).encode()).hexdigest()
        oracle_code, oracle_sha = self.oracles[cid]
        for i in range(1, per_rep + 1):
            if spent + rec["wall_seconds"] >= self.b["T_seconds"]:
                rec["censored_by"] = "T"
                break
            out = self.workdir / "runs" / f"{cid}__{arm}__r{rep}__{slot}__s{i:02d}"
            t0 = time.monotonic()
            r = integ.run(handle, workload, seed_for(cid, rep, i), out, oracle_code=oracle_code,
                          oracle_sha256=oracle_sha)
            rec["wall_seconds"] += round(time.monotonic() - t0, 3)
            rec["schedules_used"] = i
            rec["schedules"].append({k: r.get(k) for k in ("verdict", "oracle", "corroboration", "seed")})
            if r["verdict"] != "SOUND":
                rec["verdict"] = r["verdict"]  # REFUSED or INCOMPLETE: final, never retried (plan policy)
                rec["corroboration"] = r.get("corroboration") or {"ok": False}
                break
            if r.get("oracle") == "VIOLATED":
                rec["verdict"], rec["first_violation_schedule"] = "VIOLATED", i
                rec["corroboration"] = r["corroboration"]
                break
        return rec


def authorize_real(entries: list[dict[str, Any]], expected: list[tuple[str, str, int, str]], record: Path,
                   owner_signers: list[dict[str, Any]]) -> dict[str, Any]:
    """The real reveal authorization's checks. Returns the authorizer, or raises ExecRefused."""
    have = {key(e["run"]) for e in entries if e["kind"] == "result"}
    if missing := sorted(set(expected) - have):
        raise ExecRefused(f"results are incomplete ({len(missing)} expected runs missing); no reveal")
    try:
        rec = json.loads(record.read_text())
    except (OSError, ValueError) as exc:
        raise ExecRefused(f"no readable authorization record: {exc}") from exc
    if rec.get("SIMULATION") or "simulation" in json.dumps(rec).lower():
        raise ExecRefused("a SIMULATION authorization can't reveal real results")
    names = {s["name"] for s in owner_signers}
    if rec.get("role") != "owner" or rec.get("authorizer") not in names:
        raise ExecRefused("the authorizer isn't the owner who signed OWNER_APPROVAL.md")
    return {"authorizer": rec["authorizer"], "record_sha256": sha(record)}
