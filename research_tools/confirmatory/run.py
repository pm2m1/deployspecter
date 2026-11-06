"""EXPERIMENTAL confirmatory orchestration: the path from a frozen corpus to a paired analysis.

    python research_tools/confirmatory/run.py real --registry REG --oracle-ledger LEDGER --registration REC \\
        --policy POLICY --results-dir DIR
    python research_tools/confirmatory/run.py simulate --sim-dir DIR --private-dir DIR \\
        [--scenario difference|null] [--cases N]

    frozen case registry → approved oracle set → fixed budgets (PROSPECTIVE_MUSE_PLAN.md §5) → arm execution →
    corroboration per run → immutable result ledger → authorized reveal → independent scoring → paired analysis

**real** refuses to start unless every prerequisite holds (``real_refusals``):
1. every Amendment 003 decision ACCEPTED by its own roles' signed forms, none of them SIMULATION, no developer and
   no one person holding two independent roles (``approvals.real_problems``);
2. the protocol adopted: ``verify_amendment`` reports "adopted, consistent", byte-identical to the candidate;
3. a prospective registration record, not SIMULATION, binding the candidate's and the adopted file's sha256,
   with its timestamp token present, dated in the past and before any result;
4. a frozen confirmatory registry (intact chain) of at least one case, each independently curated: an intake-v2
   envelope and an intact intake ledger entry, and not a SIMULATION case;
5. an approved, validated, frozen independent oracle for every case, none of them SIMULATION;
6. a complete resource and retry policy equal to the frozen plan's budgets;
7. no leakage: no development or held-out application, no archive also in the development registry, and no case id
   or archive sha256 anywhere in the development evidence.
Even with all seven, the real arm executor is **not wired**: ``RealExecutor`` refuses. Wiring it to the existing
runners is a reviewed step for after adoption.

**simulate** runs the whole path on synthetic cases with mock approvals:
* every artifact is stamped SIMULATION (ids ``sim-…``, the application name ``simulation-app``, a ``SIMULATION``
  field in every JSON record, a banner file);
* every write goes through ``Writer``, which refuses any path outside ``--sim-dir`` and ``--private-dir``. The
  private directory, for the reveals, must be outside the repository.

**Analysis** (``analyse``), frozen by the plan (§6–7):
* every run counts: refusals, unsuccessful schedules, nondetections right-censored at B, fixed-version false
  positives (which void the case for that arm and go to triage), invalid proposals and cost;
* primary: RA-MDS vs RA-DET; secondary: RA-MD vs RA-DET, Holm-corrected;
* anything else is labelled POST-HOC (exploratory);
* a null or negative result for Muse is a valid result and is reported as such;
* the budgets are locked (``BUDGET_LOCK.json``, with the plan's sha256) before execution, and any change
  afterwards is refused.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import io
import json
import re
import secrets
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TOOLS))
import approvals as ap  # noqa: E402
import corpus_intake as ci  # noqa: E402
import evaluator_sandbox as es  # noqa: E402
import intake_v2  # noqa: E402
import oracle_review as orc  # noqa: E402
import paired_eval as pe  # noqa: E402
import verify_amendment as va  # noqa: E402

PLAN = REPO / "docs/experimental_mvp/protocol/PROSPECTIVE_MUSE_PLAN.md"
PROTOCOL = REPO / "docs/experimental_mvp/protocol"
FROZEN = REPO / "docs/research/live_stepping"
EVIDENCE = REPO / "docs/experimental_mvp"
ARMS = ("RA-DET", "RA-MD", "RA-MS", "RA-MDS", "RA-DET-C")
PRIMARY = ("RA-MDS", "RA-DET")
SECONDARY = [("RA-MD", "RA-DET")]
SIM = "SIMULATION"
BANNER = ("SIMULATION: synthetic cases, mock approvals and a simulated executor. This is an orchestration "
          "rehearsal, not an evaluation; nothing here is evidence about DeploySpecter or Muse.")


class Refused(SystemExit):
    pass


# ------------------------------------------------------------------------------------------------ budgets

def plan_budgets(plan: Path = PLAN) -> dict[str, Any]:
    """The frozen plan's §5 budgets, read from the table (never edited here)."""
    text = plan.read_text()
    sec = text[text.index("## 5. Budgets"):text.index("## 6.")]

    def cell(prefix: str) -> str:
        m = re.search(rf"^\| {re.escape(prefix)}[^|]*\| ([^|]+) \|", sec, re.M)
        if not m:
            raise Refused(f"refused: the plan's §5 has no row {prefix!r}")
        return m.group(1).strip()
    b = {"B": int(re.match(r"(\d+)", cell("B (")).group(1)), "T_seconds": int(cell("T (")),
         "replicates": int(re.match(r"(\d+)", cell("Replicates")).group(1)), "R": int(cell("R (")),
         "P": int(cell("P (")), "C_usd_per_case_arm": float(re.search(r"\$([\d.]+) per case", cell("C (")).group(1)),
         "C_usd_per_evaluation": float(re.search(r"\$([\d.]+) per evaluation", cell("C (")).group(1)),
         "max_grants": int(re.match(r"(\d+)", cell("Maximum schedules")).group(1)),
         "deterministic_model_budget": int(re.match(r"(\d+)", cell("Deterministic arms")).group(1))}
    return {"plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(), "budgets": b}


def policy_from_plan(plan: Path = PLAN) -> dict[str, Any]:
    pb = plan_budgets(plan)
    return {"plan_sha256": pb["plan_sha256"], "budgets": pb["budgets"], "arms": list(ARMS),
            "retry": {"runs": "never (SOUND, REFUSED or INCOMPLETE are final)",
                      "requests": "transport failures only, logged, counted against R"}}


def policy_problems(policy: Any, plan: Path = PLAN) -> list[str]:
    want = policy_from_plan(plan)
    if not isinstance(policy, dict):
        return ["no resource and retry policy"]
    p = []
    for k in ("plan_sha256", "budgets", "arms", "retry"):
        if policy.get(k) != want[k]:
            p.append(f"policy.{k} isn't the frozen plan's ({k} differs or is missing)")
    return p


def lock_budgets(writer: Writer, path: Path, plan: Path = PLAN) -> dict[str, Any]:
    lock = {SIM: writer.simulation, **plan_budgets(plan), "locked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                                      time.gmtime())}
    writer.json(path, lock)
    return lock


def check_lock(lock_path: Path, *, plan: Path = PLAN, budget_override: int | None = None) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text())
    now = plan_budgets(plan)
    if now["plan_sha256"] != lock["plan_sha256"] or now["budgets"] != lock["budgets"]:
        raise Refused("refused: the plan or its budgets changed after the budgets were locked")
    if budget_override is not None and budget_override != lock["budgets"]["B"]:
        raise Refused("refused: a schedule budget other than the locked B after results is a post-hoc change")
    return lock


# ------------------------------------------------------------------------------------------------ real mode

def _registration_problems(rec_path: Path | None, results_dir: Path | None, *, protocol: Path = PROTOCOL,
                           frozen: Path = FROZEN) -> list[str]:
    if rec_path is None or not rec_path.exists():
        return ["no prospective registration record (ruling #9; checklist step 6)"]
    try:
        r = json.loads(rec_path.read_text())
    except ValueError:
        return ["the registration record isn't JSON"]
    p = []
    if r.get(SIM) or SIM.lower() in json.dumps(r).lower():
        p.append("the registration record is a SIMULATION")
    cand = protocol / "AMENDMENT_003_ADOPTION_CANDIDATE.md"
    if r.get("candidate_sha256") != ci.sha(cand):
        p.append("the registration doesn't bind the candidate's sha256")
    adopted = frozen / "EVALUATION_PROTOCOL_AMENDMENT_003.md"
    if not adopted.exists() or r.get("adopted_file_sha256") != ci.sha(adopted):
        p.append("the registration doesn't bind the adopted file's sha256")
    token = rec_path.parent / str(r.get("timestamp_token", ""))
    if not r.get("timestamp_token") or not token.is_file() or ci.sha(token) != r.get("timestamp_token_sha256"):
        p.append("the registration's timestamp token is missing or doesn't match its sha256")
    try:
        t = calendar.timegm(time.strptime(r["registered_at"], "%Y-%m-%dT%H:%M:%SZ"))
        if t > time.time():
            p.append("the registration is dated in the future")
        ledger = (results_dir / "RESULTS_LEDGER.jsonl") if results_dir else None
        if ledger and ledger.exists():
            first = json.loads(ledger.read_text().splitlines()[0])["at"]
            if calendar.timegm(time.strptime(first, "%Y-%m-%dT%H:%M:%SZ")) <= t:
                p.append("results exist from before the registration")
    except (KeyError, ValueError):
        p.append("registered_at: UTC 'YYYY-MM-DDTHH:MM:SSZ'")
    if not r.get("registration_id"):
        p.append("no registration identifier")
    return p


def _registry_cases(registry: Path | None) -> tuple[list[dict[str, Any]], list[str]]:
    if registry is None or not registry.exists():
        return [], ["no confirmatory registry"]
    p = [f"registry: {x}" for x in ci.verify_registry(registry)]
    entries = [json.loads(x) for x in registry.read_text().splitlines()]
    cases = [e for e in entries if e["kind"] == "case"]
    if registry.name != "REGISTRY_confirmatory.jsonl":
        p.append("the registry isn't the confirmatory split's")
    if not any(e["kind"] == "freeze" for e in entries):
        p.append("the confirmatory registry isn't frozen")
    if not cases:
        p.append("the confirmatory registry has no cases")
    return cases, p


def _leakage_problems(cases: list[dict[str, Any]], cases_root: Path, registry: Path) -> list[str]:
    p = []
    dev = registry.with_name("REGISTRY_development.jsonl")
    dev_archives = {a for x in (dev.read_text().splitlines() if dev.exists() else [])
                    for a in json.loads(x).get("archives", [])}
    needles = {}
    for e in cases:
        m = json.loads((cases_root / e["case_id"] / "case.json").read_text())
        if m["application"].lower() in ci.NOT_CONFIRMATORY:
            p.append(f"{e['case_id']}: {m['application']} is a development or held-out application")
        if set(e["archives"]) & dev_archives:
            p.append(f"{e['case_id']}: a revision is also in the development registry")
        needles[e["case_id"]] = e["case_id"]
        for a in e["archives"]:
            needles[a] = e["case_id"]
    skip = {cases_root.resolve(), registry.parent.resolve()}
    for f in EVIDENCE.rglob("*"):
        if not f.is_file() or f.stat().st_size > 2_000_000 or any(f.resolve().is_relative_to(s) for s in skip):
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for n, cid in needles.items():
            if n in text:
                where = f.relative_to(REPO) if f.resolve().is_relative_to(REPO) else f
                p.append(f"{cid}: appears in development evidence {where}")
    return sorted(set(p))


def real_refusals(*, registry: Path | None, cases_root: Path | None, oracle_ledger: Path | None,
                  registration: Path | None, policy: Path | None, results_dir: Path | None,
                  protocol: Path = PROTOCOL, frozen: Path = FROZEN) -> list[str]:
    """Every reason a real confirmatory run may not start (empty = all prerequisites hold)."""
    why = [f"approvals: {x}" for x in ap.real_problems(protocol)]
    v = va.verify(protocol, frozen)
    if v["integrity_problems"] or v["adoption"] != "adopted, consistent":
        why.append(f"protocol: Amendment 003 is {v['adoption']} {v['integrity_problems'] or ''}".strip())
    why += [f"registration: {x}" for x in _registration_problems(registration, results_dir, protocol=protocol,
                                                                 frozen=frozen)]
    cases, rp = _registry_cases(registry)
    why += [f"corpus: {x}" for x in rp]
    root = cases_root or (registry.parent / "cases" if registry else None)
    ledger = root / "INTAKE_LEDGER.jsonl" if root else None
    for e in cases:
        if e["case_id"].startswith("sim-") or "simulation" in e["case_id"]:
            why.append(f"corpus: {e['case_id']} is a SIMULATION case")
        env = root / f"{e['case_id']}.envelope.json" if root else None
        if env is None or not env.exists():
            why.append(f"corpus: {e['case_id']} has no curator envelope (independent curation unrecorded)")
        elif ledger is None or intake_v2.verify_ledger(ledger) or not any(
                json.loads(x).get("manifest_sha256") == e["manifest_sha256"] and
                json.loads(x).get("envelope_sha256") == ci.sha(env) for x in ledger.read_text().splitlines()):
            why.append(f"corpus: {e['case_id']}'s envelope isn't bound in an intact intake ledger")
    if oracle_ledger is None or not oracle_ledger.exists():
        why.append("oracles: no oracle ledger (ruling #7)")
    else:
        why += [f"oracles: {x}" for x in orc.verify(oracle_ledger)]
        got = orc.approved(oracle_ledger)
        for e in cases:
            c = got.get(e["case_id"])
            if c is None:
                why.append(f"oracles: {e['case_id']} has no oracle")
                continue
            why += [f"oracles: {e['case_id']}: {x}" for x in c["problems"]]
            if c.get("simulation"):
                why.append(f"oracles: {e['case_id']}'s oracle is a SIMULATION")
    try:
        pol = json.loads(policy.read_text()) if policy and policy.exists() else None
    except ValueError:
        pol = None
    why += [f"policy: {x}" for x in policy_problems(pol)]
    if cases and root is not None and registry is not None:
        why += [f"leakage: {x}" for x in _leakage_problems(cases, root, registry)]
    return why


class RealExecutor:
    """Not wired. A real arm execution must call the reviewed per-case runners (``research_tools/isolated``) under
    the locked budgets; that wiring is a reviewed step for after adoption, so it refuses."""

    def __call__(self, *a: Any, **k: Any) -> Any:
        raise Refused("refused: the real arm executor isn't wired (a reviewed step after adoption)")


# ------------------------------------------------------------------------------------------------ simulation

class Writer:
    """Every simulation write goes through here: inside the allowed roots only, stamped SIMULATION."""

    def __init__(self, roots: list[Path], simulation: bool) -> None:
        self.roots = [r.resolve() for r in roots]
        self.simulation = simulation

    def check(self, path: Path) -> Path:
        p = path.resolve()
        if not any(p == r or p.is_relative_to(r) for r in self.roots):
            raise Refused(f"refused: {path} is outside the simulation directories")
        return path

    def json(self, path: Path, obj: Any) -> str:
        self.check(path).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(obj, dict) and self.simulation:
            obj = {SIM: True, "banner": BANNER, **obj}
        path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")
        return ci.sha(path)

    def text(self, path: Path, text: str) -> str:
        self.check(path).parent.mkdir(parents=True, exist_ok=True)
        path.write_text((f"<!-- {BANNER} -->\n\n" if self.simulation else "") + text)
        return ci.sha(path)


def _archive(path: Path, fenced: bool, docs: bytes) -> None:
    """A synthetic, normalized revision: the simulated application, its upgrade documentation, a banner."""
    buf = io.BytesIO()
    files = {"SIMULATION.txt": BANNER.encode() + b"\n", "app/__init__.py": b"VERSION = '1.0'\n",
             "app/queue.py": (b"def finish(job):\n    claim_guard(job)\n    update(job)\n" if fenced
                              else b"def finish(job):\n    update(job)\n"), "docs/upgrade.md": docs}
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(files[name]), 0
            t.addfile(info, io.BytesIO(files[name]))
    path.write_bytes(buf.getvalue())


def _violable(archive: Path, oracle: dict[str, Any]) -> bool:
    with tarfile.open(archive) as t:
        code = "".join(t.extractfile(m).read().decode() for m in t.getmembers()
                       if m.isfile() and m.name.startswith(oracle["scan"]) and m.name.endswith(".py"))
    return oracle["required"] not in code


def simulate(sim_dir: Path, private_dir: Path, *, scenario: str = "difference", n_cases: int = 12) -> dict[str, Any]:
    if "simulation" not in sim_dir.name.lower() or "simulation" not in private_dir.name.lower():
        raise Refused("refused: simulation directories must be named as such (…simulation…)")
    if private_dir.resolve().is_relative_to(REPO):
        raise Refused("refused: the private directory (reveals) must be outside the repository")
    for d in (sim_dir, private_dir):
        if d.exists() and any(d.iterdir()):
            raise Refused(f"refused: {d} isn't empty (evidence is never overwritten)")
    w = Writer([sim_dir, private_dir], simulation=True)
    w.text(sim_dir / "SIMULATION.md", f"# {BANNER}\n")
    summary: dict[str, Any] = {"scenario": scenario, "cases": n_cases}
    # 1. curator: synthetic cases through the intake-v2 envelope, a frozen confirmatory registry
    cases_root, raw = sim_dir / "corpus" / "cases", private_dir / "curator_raw"
    registry = sim_dir / "corpus" / "REGISTRY_confirmatory.jsonl"
    intake_ledger = cases_root / "INTAKE_LEDGER.jsonl"
    for d in (cases_root, raw):
        w.check(d).mkdir(parents=True, exist_ok=True)
    docs = b"# Upgrading\n\nStop the workers, run the migrations, start the new workers.\n"
    spec = private_dir / "oracle" / "sim_oracle_spec.md"
    w.text(spec, "SIMULATION oracle spec: a job's completion is written only by the claim that holds it (from the "
                 "simulated application's documentation).\n")
    oracle_code = sim_dir / "oracle" / "sim_oracle.json"
    # the oracle states a property over all of the application's sources: it must never name the fix's file
    # (attempt 0 did, and evaluator_sandbox refused the package: the fix path is label material)
    w.json(oracle_code, {"scan": "app/", "required": "claim_guard(", "property": "a completion is written only "
                         "under its claim's guard"})
    for i in range(n_cases):
        cid = f"sim-case-{i:02d}"
        a, b = raw / f"{cid}-a.tar", raw / f"{cid}-b.tar"
        w.check(a)
        _archive(a, fenced=False, docs=docs)
        _archive(b, fenced=True, docs=docs)
        env = {"schema": intake_v2.SCHEMA, "curator": "curator-sim",
               "submission": {"case_id": cid, "application": "simulation-app",
                              "repository": "https://example.invalid/simulation-app.git", "split": "confirmatory",
                              "revisions": [{"archive": str(a), "commit": secrets.token_hex(20)},
                                            {"archive": str(b), "commit": secrets.token_hex(20)}],
                              "buggy": 0, "fix_paths": ["app/queue.py"], "defect_summary": "unfenced completion",
                              "keywords": ["unfenced", "overwrite"], "contract": {"kind": "single-version",
                                                                  "citation": "docs/upgrade.md"},
                              "oracle_spec_sha256": ci.sha(spec),
                              "exposure": {"curator": ["fix diff"], "public": []}},
               "selection": {"selected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "method": "SIMULATION: generated"},
               "environment": {"image_digest": "sha256:" + "0" * 64, "lock_sha256": "1" * 64, "postgres_major": "16"},
               "pinned_sources": [{"tree_sha256": intake_v2.tree_digest(a)}, {"tree_sha256": intake_v2.tree_digest(b)}],
               "contract_evidence": {"path": "docs/upgrade.md", "sha256": hashlib.sha256(docs).hexdigest()}}
        envelope = raw / f"{cid}.envelope.json"
        w.json(envelope, env)
        env_clean = json.loads(envelope.read_text())
        for k in (SIM, "banner"):
            env_clean.pop(k)
        envelope.write_text(json.dumps(env_clean))
        intake_v2.submit(envelope, cases_root, private_dir / "reveals" / f"{cid}.json", intake_ledger)
        ci.register(cases_root / cid, registry)
    ci.freeze(registry)
    case_dirs = sorted(p for p in cases_root.iterdir() if p.is_dir())
    summary["registry_head"] = json.loads(registry.read_text().splitlines()[-1])["hash"]
    # 2. independent oracle (mock roles): spec, review, corrected-version validation (curator side), freeze
    oracle_ledger = sim_dir / "oracle" / "ORACLE_LEDGER.jsonl"
    results_ledger = sim_dir / "results" / "RESULTS_LEDGER.jsonl"

    def corrected(archive: Path, code: Path) -> bool:  # the oracle holds on the fixed revision
        return not _violable(archive, json.loads(code.read_text()))
    for c in case_dirs:
        orc.add_spec(oracle_ledger, c, spec, author="oracle-author-sim", simulation=True,
                     results_ledger=results_ledger)
        orc.add_review(oracle_ledger, c.name, spec_sha256=ci.sha(spec), oracle_code=oracle_code,
                       reviewer="oracle-reviewer-sim", verdict="approve", simulation=True,
                       results_ledger=results_ledger)
        orc.validate_corrected(oracle_ledger, c, private_dir / "reveals" / f"{c.name}.json", oracle_code, corrected,
                               simulation=True)
    orc.freeze(oracle_ledger, simulation=True)
    allow = sim_dir / "oracle" / "APPROVED_ORACLES.json"
    w.json(allow, orc.allowlist(oracle_ledger))  # evaluator_sandbox reads it by file name; extra keys are inert
    # 3. mock approvals; budgets locked from the plan; a complete policy
    mock = ap.mock_approvals()
    if ap.mock_problems(mock):
        raise Refused(f"refused: {ap.mock_problems(mock)}")
    w.json(sim_dir / "approvals" / "MOCK_APPROVALS.json", mock)
    lock = lock_budgets(w, sim_dir / "BUDGET_LOCK.json")
    w.json(sim_dir / "POLICY.json", policy_from_plan())
    if policy_problems(policy_from_plan()):
        raise Refused("refused: the policy isn't the plan's")
    summary["real_mode_would_refuse"] = real_refusals(
        registry=registry, cases_root=cases_root, oracle_ledger=oracle_ledger, registration=None,
        policy=sim_dir / "POLICY.json", results_dir=sim_dir / "results")
    # 4. evaluator: only the permitted package, reduced environment, the simulated executor
    pkg = sim_dir / "evaluator" / "package"
    w.check(pkg)
    launch = es.launch(mode="simulated", registry=registry, cases=case_dirs, out=pkg,
                       cmd=[sys.executable, "-I", str(HERE / "sim_executor.py"), json.dumps(lock["budgets"]),
                            ",".join(ARMS), scenario],
                       oracles=[oracle_code], allowlist=allow, reveal_dirs=[private_dir / "reveals"],
                       sensitive=sorted((private_dir / "reveals").iterdir()))
    summary["launch"] = {k: launch[k] for k in ("mode", "label", "exit", "env_keys", "approved_oracles")}
    if launch["exit"] != 0:
        raise Refused(f"refused: the evaluator exited {launch['exit']}: {launch['stderr_tail']}")
    runs = [json.loads(x) for x in (pkg / "RESULTS.jsonl").read_text().splitlines()]
    summary["budget_checks"] = budget_problems(runs, lock)
    if summary["budget_checks"]:
        raise Refused(f"refused: runs outside the locked budgets: {summary['budget_checks']}")
    # 5. immutable result ledger (blinded: slots only), then the authorized reveal
    for r in runs:
        _append_result(w, results_ledger, r, oracle_sha=ci.sha(oracle_code), lock=lock)
    auth = _authorize(w, results_ledger, authorizer="SIM-owner", oracle_sha=ci.sha(oracle_code), lock=lock)
    # 6. independent scoring with the reveals, then the paired analysis
    rows, voided, triage = score(results_ledger, case_dirs, private_dir / "reveals", oracle_code,
                                 lock_path=sim_dir / "BUDGET_LOCK.json")
    w.json(sim_dir / "results" / "TRIAGE_QUEUE.json", {"note": "fixed-version violations for blinded two-reviewer "
                                                               "triage (#8); each voids its case for that arm",
                                                       "items": triage})
    w.check(sim_dir / "results" / "SCORED_RUNS.jsonl").write_text("".join(json.dumps(r | {SIM: True}, sort_keys=True)
                                                                    + "\n" for r in rows))
    analysis = analyse(rows, voided, lock)
    w.json(sim_dir / "results" / "ANALYSIS.json", analysis)
    summary.update(runs=len(runs), authorization=auth["hash"], voided=voided, analysis_verdicts={
        k: v["verdict"] for k, v in analysis["comparisons"].items()})
    w.json(sim_dir / "SUMMARY.json", summary)
    return summary


def budget_problems(runs: list[dict[str, Any]], lock: dict[str, Any]) -> list[str]:
    """The locked budgets, checked on what the arms actually used: schedules per run, the cost cap per case and arm
    and per evaluation, and a zero model budget for the deterministic arms (#16). Usage is counted once per
    replicate (it is repeated on both slots' records)."""
    b, p = lock["budgets"], []
    per: dict[tuple[str, str], float] = {}
    seen = set()
    for r in runs:
        if r["schedules_used"] > b["B"] // b["replicates"]:
            p.append(f"{r['case_id']}/{r['arm']}/r{r['replicate']}: more schedules than B/replicates")
        key = (r["case_id"], r["arm"], r["replicate"])
        if key in seen:
            continue
        seen.add(key)
        if r["arm"] in ("RA-DET", "RA-DET-C") and (r["tokens_in"] or r["tokens_out"] or r["est_usd"]):
            p.append(f"{r['case_id']}/{r['arm']}: a deterministic arm used the model (#16)")
        per[(r["case_id"], r["arm"])] = per.get((r["case_id"], r["arm"]), 0.0) + r["est_usd"]
    p += [f"{c}/{a}: ${v:.4f} exceeds ${b['C_usd_per_case_arm']} per case and arm" for (c, a), v in per.items()
          if v > b["C_usd_per_case_arm"]]
    if sum(per.values()) > b["C_usd_per_evaluation"]:
        p.append(f"${sum(per.values()):.4f} exceeds ${b['C_usd_per_evaluation']} per evaluation")
    return p


def _chain(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _h(e: dict[str, Any]) -> str:
    return hashlib.sha256(ci.canonical({k: v for k, v in e.items() if k != "hash"})).hexdigest()


def verify_results(p: Path) -> list[str]:
    out, prev = [], "0" * 64
    for i, e in enumerate(_chain(p)):
        if e.get("seq") != i or e.get("prev") != prev or e.get("hash") != _h(e):
            out.append(f"results entry {i}: the chain is broken")
        prev = e.get("hash", "")
    return out


def _append_result(w: Writer, ledger: Path, run: dict[str, Any], *, oracle_sha: str, lock: dict[str, Any]) -> None:
    entries = _chain(ledger)
    if verify_results(ledger) or any(e["kind"] == "authorization" for e in entries):
        raise Refused("refused: the results ledger is broken or already authorized for reveal")
    if run["schedules_used"] > lock["budgets"]["B"] // lock["budgets"]["replicates"]:
        raise Refused("refused: a run used more schedules than its locked budget")
    e = {"seq": len(entries), "kind": "result", "run": run, "oracle_sha256": oracle_sha,
         "plan_sha256": lock["plan_sha256"], "prev": entries[-1]["hash"] if entries else "0" * 64,
         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **({SIM: True} if w.simulation else {})}
    e["hash"] = _h(e)
    w.check(ledger).parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")


def _authorize(w: Writer, ledger: Path, *, authorizer: str, oracle_sha: str, lock: dict[str, Any]) -> dict[str, Any]:
    entries = _chain(ledger)
    if verify_results(ledger) or not entries or entries[-1]["kind"] != "result":
        raise Refused("refused: results must be committed and intact before a reveal is authorized")
    e = {"seq": len(entries), "kind": "authorization", "authorizer": authorizer, "results_head": entries[-1]["hash"],
         "oracle_sha256": oracle_sha, "plan_sha256": lock["plan_sha256"], "prev": entries[-1]["hash"],
         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **({SIM: True} if w.simulation else {})}
    e["hash"] = _h(e)
    with w.check(ledger).open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def score(ledger: Path, case_dirs: list[Path], reveals: Path, oracle_code: Path, *, lock_path: Path
          ) -> tuple[list[dict[str, Any]], list[list[str]], list[dict[str, Any]]]:
    """Independent scoring: only after an intact, authorized ledger, with the frozen oracle and the locked budgets.
    Returns paired_eval rows, the voided (case, arm) cells, and the triage queue."""
    entries = _chain(ledger)
    if verify_results(ledger):
        raise Refused("refused: the results ledger is broken")
    auth = [e for e in entries if e["kind"] == "authorization"]
    if not auth or auth[-1]["results_head"] != entries[-2]["hash"] or entries[-1]["kind"] != "authorization":
        raise Refused("refused: no reveal authorization bound to the final results")
    if ci.sha(oracle_code) != auth[-1]["oracle_sha256"]:
        raise Refused("refused: the oracle changed after the results were committed")
    lock = check_lock(lock_path)
    roles = {c.name: ci.score(c, reveals / f"{c.name}.json")["revision_roles"] for c in case_dirs}
    by: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for e in entries:
        if e["kind"] == "result":
            r = e["run"]
            by.setdefault((r["case_id"], r["arm"], r["replicate"]), {})[roles[r["case_id"]][r["slot"]]] = r
    rows, triage = [], []
    for (cid, arm, rep), pair in sorted(by.items()):
        bug, fix = pair["buggy"], pair["fixed"]
        detected = bug["verdict"] == "VIOLATED" and bug["corroboration"]["ok"] and \
            bug["corroboration"]["params_mismatched"] == 0
        fp = fix["verdict"] == "VIOLATED"
        if fp:
            triage.append({"case": cid, "arm_hidden_from_reviewers": True, "replicate": rep, "reason":
                           "a violation on the corrected revision", "_arm": arm})
        rows.append({"case": cid, "arm": arm, "run_id": f"{cid}/{arm}/r{rep}", "seq": rep,
                     "schedules_used": bug["schedules_used"], "detected": detected,
                     "first_detection_schedule": bug["first_violation_schedule"] if detected else None,
                     "wall_seconds": bug["wall_seconds"] + fix["wall_seconds"],
                     "invalid_proposals": bug["invalid_proposals"], "refused": bug["verdict"] == "REFUSED",
                     "fixed_version_violation": fp, "tokens_in": bug["tokens_in"], "tokens_out": bug["tokens_out"],
                     "est_usd": bug["est_usd"],
                     "schedule_budget": lock["budgets"]["B"]})
    voided = sorted({(t["case"], t["_arm"]) for t in triage})
    for t in triage:
        t.pop("_arm")
    return rows, [list(v) for v in voided], triage


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = running
    return adj


def analyse(rows: list[dict[str, Any]], voided: list[list[str]], lock: dict[str, Any]) -> dict[str, Any]:
    """The predeclared analysis (plan §6–7). Every run stays in the accounting; voided cells leave only the paired
    detection comparison of their arm, and are listed."""
    void = {tuple(v) for v in voided}
    rows = [pe.validate(dict(r), f"scored run {i}") | {"_order": i} for i, r in enumerate(rows)]
    accounting = {}
    for arm in ARMS:
        rs = [r for r in rows if r["arm"] == arm]
        accounting[arm] = {"runs": len(rs), "refused_runs": sum(r["refused"] for r in rs),
                           "detecting_runs": sum(r["detected"] for r in rs),
                           "unsuccessful_runs": sum(not r["detected"] and not r["refused"] for r in rs),
                           "schedules_used": sum(r["schedules_used"] for r in rs),
                           "fixed_version_false_positives": sum(r["fixed_version_violation"] for r in rs),
                           "invalid_proposals": sum(r["invalid_proposals"] for r in rs),
                           "tokens_in": sum(r["tokens_in"] for r in rs), "tokens_out": sum(r["tokens_out"] for r in rs),
                           "est_usd": round(sum(r["est_usd"] for r in rs), 6),
                           "voided_cases": sorted(c for c, a in void if a == arm)}
    kept = [r for r in rows if (r["case"], r["arm"]) not in void]
    comps = {}

    def comp(x: str, y: str, label: str) -> dict[str, Any]:
        sub = [r for r in kept if r["arm"] in (x, y)]
        table, excl = pe.paired_table(sub, x, y)
        c = pe.compare(table, x, y, excl)
        cells = {k[0] for k in table}
        voided_here = sorted(cv for cv, a in void if a in (x, y))
        c["censored_nondetections"] = {a: sum(1 for (cs, aa), v in table.items() if aa == a and v["censored"])
                                       for a in (x, y)}
        c["voided_cases_excluded"] = voided_here
        c["label"] = label
        c["paired_case_ids"] = sorted(cells)
        return c
    comps[f"{PRIMARY[0]} vs {PRIMARY[1]}"] = comp(*PRIMARY, "PREREGISTERED PRIMARY (plan §7)")
    sec = [comp(x, y, "PREREGISTERED SECONDARY (plan §7), Holm-corrected") for x, y in SECONDARY]
    adj = holm([c["mcnemar"]["p_exact_two_sided"] for c in sec])
    for (x, y), c, a in zip(SECONDARY, sec, adj, strict=True):
        c["holm_adjusted_p"] = a
        comps[f"{x} vs {y}"] = c
    for x, y in (("RA-MS", "RA-DET"), ("RA-DET-C", "RA-DET")):
        comps[f"{x} vs {y}"] = comp(x, y, "POST-HOC (exploratory; not a preregistered finding)")
    primary = comps[f"{PRIMARY[0]} vs {PRIMARY[1]}"]
    return {"budgets": lock["budgets"], "plan_sha256": lock["plan_sha256"], "accounting": accounting,
            "comparisons": comps,
            "reading": ("a null or negative result for the Muse arms is a valid result" if "RA-MDS better" not in
                        primary["verdict"] else "the primary comparison favours RA-MDS under the frozen analysis")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("real")
    for k in ("registry", "cases-root", "oracle-ledger", "registration", "policy", "results-dir"):
        r.add_argument(f"--{k}", type=Path)
    s = sub.add_parser("simulate")
    s.add_argument("--sim-dir", type=Path, required=True)
    s.add_argument("--private-dir", type=Path, required=True)
    s.add_argument("--scenario", choices=("difference", "null"), default="difference")
    s.add_argument("--cases", type=int, default=12)
    a = p.parse_args(argv)
    if a.cmd == "real":
        why = real_refusals(registry=a.registry, cases_root=a.cases_root, oracle_ledger=a.oracle_ledger,
                            registration=a.registration, policy=a.policy, results_dir=a.results_dir)
        print(json.dumps({"mode": "real", "refused": bool(why), "reasons": why}, indent=1))
        if why:
            return 3
        RealExecutor()()
        return 3
    out = simulate(a.sim_dir, a.private_dir, scenario=a.scenario, n_cases=a.cases)
    print(json.dumps({k: out[k] for k in ("scenario", "cases", "runs", "voided", "analysis_verdicts")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
