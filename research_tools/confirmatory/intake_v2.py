"""EXPERIMENTAL: the external curator submission path (``ds-intake-envelope/1``), wrapping ``corpus_intake.py``
without changing it.

The curator's **envelope** adds to ``corpus_intake``'s submission what a confirmatory case also needs:
* ``selection``: ``selected_at`` (UTC, not in the future) and how the case was found;
* ``environment``: a reproducible environment, i.e. the runner image digest (``sha256:``), the lock-file sha256 and
  the PostgreSQL major version;
* ``pinned_sources``: the curator's tree digest of each revision archive (sha256 over the sorted file names and
  contents), recomputed here before anything is written;
* ``contract_evidence``: the documentation file that the ``contract.citation`` relies on, with its sha256. It must
  be present, byte-identical, in at least one revision.
* ``curator``: a role identifier (the person is named only on the signed form).

**What ``submit`` does:**
1. validates the envelope;
2. calls ``corpus_intake.submit``, which seals the labels and shuffles the revisions into R1/R2;
3. leak-scans the public envelope against the sealed labels;
4. writes ``<case_id>.envelope.json`` beside the case directory (the case directory itself stays exactly
   case.json, R1.tar, R2.tar);
5. appends a hash-chained ``INTAKE_LEDGER.jsonl`` entry binding the manifest's and the envelope's sha256.
A leak, or any validation failure, removes what was written.

``evaluator_view`` is the only part of an envelope the evaluator may see: the environment descriptor and the
contract kind. Selection notes, curator identity and timestamps stay on the curator's side.

**Limits (stated, not solved):**
- Real role separation needs the curator and the evaluator on independent accounts or machines; on one machine and
  one account it is procedural only.
- The two revisions' contents, a public repository's history and any model's pretraining can still reveal a
  historical defect, so this process is blind by procedure, not absolutely.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import corpus_intake as ci  # noqa: E402

SCHEMA = "ds-intake-envelope/1"
KEYS = {"schema", "submission", "selection", "environment", "pinned_sources", "contract_evidence", "curator"}
CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,40}")
PERMITTED = ("environment", "contract_kind")


class IntakeRefused(SystemExit):
    pass


def tree_digest(archive: Path) -> str:
    h = hashlib.sha256()
    with tarfile.open(archive) as t:
        for m in sorted((m for m in t.getmembers() if m.isfile()), key=lambda m: m.name):
            f = t.extractfile(m)
            h.update(m.name.encode() + b"\0" + hashlib.sha256(f.read() if f else b"").hexdigest().encode() + b"\n")
    return h.hexdigest()


def _member_sha(archive: Path, path: str) -> str | None:
    with tarfile.open(archive) as t:
        try:
            f = t.extractfile(path)
        except KeyError:
            return None
        return hashlib.sha256(f.read()).hexdigest() if f else None


def validate(env: Any, *, now: float | None = None) -> list[str]:
    if not isinstance(env, dict) or set(env) != KEYS or env.get("schema") != SCHEMA:
        return [f"envelope: exactly {sorted(KEYS)} with schema {SCHEMA}"]
    p = list(ci.validate_submission(env["submission"]))
    s = env["submission"]
    if isinstance(s, dict) and not CASE_ID.fullmatch(str(s.get("case_id"))):
        p.append("case_id: lowercase letters, digits and '-', 3..41 characters (an immutable identifier)")
    sel = env["selection"]
    try:
        t = calendar.timegm(time.strptime(sel["selected_at"], "%Y-%m-%dT%H:%M:%SZ"))
        if t > (now or time.time()) + 60:
            p.append("selection.selected_at is in the future")
    except (KeyError, TypeError, ValueError):
        p.append("selection.selected_at: UTC 'YYYY-MM-DDTHH:MM:SSZ'")
    if not (isinstance(sel, dict) and isinstance(sel.get("method"), str) and sel["method"]):
        p.append("selection.method: how the case was found")
    e = env["environment"]
    if not (isinstance(e, dict) and re.fullmatch(r"sha256:[0-9a-f]{64}", str(e.get("image_digest")))
            and re.fullmatch(r"[0-9a-f]{64}", str(e.get("lock_sha256")))
            and re.fullmatch(r"\d{2}", str(e.get("postgres_major")))):
        p.append("environment: {image_digest: sha256:<64 hex>, lock_sha256: <64 hex>, postgres_major: '16'}")
    if not (isinstance(env["curator"], str) and re.fullmatch(r"curator-[a-z0-9]{1,12}", env["curator"])):
        p.append("curator: a role identifier such as 'curator-1' (the person is named on the signed form)")
    if p:
        return p
    archives = [Path(r["archive"]) for r in s["revisions"]]
    ps = env["pinned_sources"]
    if not (isinstance(ps, list) and len(ps) == 2):
        return p + ["pinned_sources: one {tree_sha256} per revision, in submission order"]
    for i, (a, want) in enumerate(zip(archives, ps, strict=True)):
        if not a.exists():
            p.append(f"revision {i}: archive missing")
        elif tree_digest(a) != want.get("tree_sha256"):
            p.append(f"revision {i}: the archive's tree digest isn't the curator's pinned one")
    ce = env["contract_evidence"]
    if not (isinstance(ce, dict) and ce.get("path") and re.fullmatch(r"[0-9a-f]{64}", str(ce.get("sha256")))):
        p.append("contract_evidence: {path, sha256} of the documentation the citation relies on")
    elif not any(a.exists() and _member_sha(a, ce["path"]) == ce["sha256"] for a in archives):
        p.append("contract_evidence: the cited document isn't in either revision with that sha256")
    return p


def public_envelope(env: dict[str, Any], manifest_sha: str) -> dict[str, Any]:
    """What is recorded beside the case: no archive paths, nothing indexed by revision role."""
    return {"schema": SCHEMA, "case_id": env["submission"]["case_id"], "manifest_sha256": manifest_sha,
            "selection": env["selection"], "environment": env["environment"], "curator": env["curator"],
            "pinned_tree_sha256": sorted(x["tree_sha256"] for x in env["pinned_sources"]),
            "contract_evidence": env["contract_evidence"], "contract_kind": env["submission"]["contract"]["kind"]}


def _chain(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _h(e: dict[str, Any]) -> str:
    return hashlib.sha256(ci.canonical({k: v for k, v in e.items() if k != "hash"})).hexdigest()


def verify_ledger(path: Path) -> list[str]:
    p, prev = [], "0" * 64
    for i, e in enumerate(_chain(path)):
        if e.get("seq") != i or e.get("prev") != prev or e.get("hash") != _h(e):
            p.append(f"intake ledger entry {i}: the chain is broken")
        prev = e.get("hash", "")
    return p


def submit(envelope: Path, cases_root: Path, reveal: Path, ledger: Path) -> dict[str, Any]:
    env = json.loads(envelope.read_text())
    problems = validate(env)
    if problems:
        raise IntakeRefused(f"refused: {problems}")
    if verify_ledger(ledger):
        raise IntakeRefused(f"refused: {verify_ledger(ledger)}")
    case_id = env["submission"]["case_id"]
    case_dir = cases_root / case_id
    sub = envelope.with_name(envelope.stem + ".submission.json")
    sub.write_text(json.dumps(env["submission"]))
    try:
        manifest = ci.submit(sub, case_dir, reveal)
    finally:
        sub.unlink()
    mshash = ci.sha(case_dir / "case.json")
    pub = public_envelope(env, mshash)
    labels = json.loads(reveal.read_text())["labels"]
    leaks = ci.leak_scan(pub | {"case_id": "", "repository": ""}, labels)
    if leaks:
        shutil.rmtree(case_dir)
        reveal.unlink()
        raise IntakeRefused(f"refused: the public envelope would contain label material: {leaks}")
    out = cases_root / f"{case_id}.envelope.json"
    out.write_text(json.dumps(pub, indent=1, sort_keys=True) + "\n")
    entries = _chain(ledger)
    e = {"seq": len(entries), "case_id": case_id, "manifest_sha256": mshash, "envelope_sha256": ci.sha(out),
         "selected_at": env["selection"]["selected_at"], "submitted_at": manifest["submitted_at"],
         "prev": entries[-1]["hash"] if entries else "0" * 64}
    e["hash"] = _h(e)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return {"case_dir": str(case_dir), "envelope": str(out), "ledger_entry": e}


def evaluator_view(envelope_public: dict[str, Any]) -> dict[str, Any]:
    """The only envelope fields an evaluator may receive."""
    return {k: envelope_public[k] for k in PERMITTED}
