"""EXPERIMENTAL, offline: Track C — approvals and dashboard, intake v2, oracle review, the confirmatory runner's real
mode gate, the SIMULATION pipeline, and the evaluation-quality rules.

The "all prerequisites hold" world below is a **test fixture in a temporary directory**: copies of the protocol files
with fixture signatures, a fixture registration and a fixture case. It exists only to show that the gate opens when
everything holds and closes when any single prerequisite is removed. Nothing is written to the repository.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "confirmatory"))
sys.path.insert(0, str(ROOT / "research_tools"))
import approvals as ap  # noqa: E402
import corpus_intake as ci  # noqa: E402
import dashboard  # noqa: E402
import intake_v2  # noqa: E402
import oracle_review as orc  # noqa: E402
import run  # noqa: E402

PROTO = ROOT / "docs/experimental_mvp/protocol"
NAMES = {"owner": "Fixture Owner", "curator": "Fixture Curator", "oracle_author": "Fixture Author",
         "oracle_reviewer": "Fixture Reviewer", "triage_reviewer_1": "Fixture Triage One",
         "triage_reviewer_2": "Fixture Triage Two", "adjudicator": "Fixture Adjudicator"}


# ------------------------------------------------------------------------------------------------ C1

def test_the_repository_has_24_proposed_decisions_and_nothing_accepted():
    ds, fs = ap.decisions(), ap.forms()
    assert len(ds) == 24 and len({d.id for d in ds}) == 24
    assert {ap.status(d, fs)["status"] for d in ds} == {"PROPOSED"}
    roles = {d.id: d.roles for d in ds}
    assert roles["6"] == ("owner", "curator") and roles["7"] == ("owner", "oracle_author", "oracle_reviewer")
    assert roles["8"] == ("owner", "triage_reviewer_1", "triage_reviewer_2", "adjudicator")
    assert roles["11"] == ("owner", "oracle_reviewer") and roles["9"] == ("owner",)
    assert [d.candidate_line for d in ds] == sorted(d.candidate_line for d in ds)


def test_the_dashboard_is_generated_from_the_repository_and_up_to_date():
    text = dashboard.render()
    assert text.count("**PROPOSED**") >= 48 and "ACCEPTED**" not in text
    assert dashboard.main(["--check"]) == 0, "regenerate docs/experimental_mvp/protocol/APPROVAL_DASHBOARD.md"


def sign(protocol: Path, *, names: dict[str, str] | None = None, skip: set[str] = frozenset(),
         tick: str = "approve") -> None:
    """Fixture signatures on copies of the forms (temporary directory only)."""
    names = {**NAMES, **(names or {})}
    rows = {"OWNER_APPROVAL.md": [("Project owner", "owner")], "CURATOR_ACCEPTANCE.md": [("Label curator", "curator")],
            "ORACLE_REVIEWER_ACCEPTANCE.md": [("Oracle author", "oracle_author"),
                                              ("Oracle reviewer", "oracle_reviewer")],
            "TRIAGE_REVIEWERS_ACCEPTANCE.md": [("Reviewer 1", "triage_reviewer_1"),
                                               ("Reviewer 2", "triage_reviewer_2"), ("Adjudicator", "adjudicator")]}
    for form, rs in rows.items():
        p = protocol / "approval" / form
        lines = p.read_text().splitlines()
        out, in_sig = [], False
        for line in lines:
            if line.startswith("| #"):
                line = line.replace(f"[ ] {tick}", f"[x] {tick}", 1)
            if line.startswith("| Role |"):
                in_sig = True
                out.append(line)
                continue
            if in_sig and line.startswith("|---"):
                out.append(line)
                for label, role in rs:
                    who = "" if role in skip else names[role]
                    out.append(f"| {label} | {who} | {'/s/ ' + who if who else ''} | {'2026-11-06' if who else ''} |")
                continue
            if in_sig and line.startswith("|"):
                continue  # the blank template rows, replaced above
            in_sig = in_sig and line.startswith("|")
            out.append(line)
        p.write_text("\n".join(out) + "\n")


def proto_copy(tmp: Path) -> Path:
    d = tmp / "protocol"
    (d / "approval").mkdir(parents=True)
    for f in ("AMENDMENT_003_ADOPTION_CANDIDATE.md", "DECISIONS_003_v3.md", "DECISIONS_003_v2.md"):
        shutil.copyfile(PROTO / f, d / f)
    for f in (PROTO / "approval").iterdir():
        shutil.copyfile(f, d / "approval" / f.name)
    return d


def test_the_owner_cannot_approve_for_independent_roles(tmp_path):
    d = proto_copy(tmp_path)
    sign(d, skip=set(ap.INDEPENDENT))
    fs, ds = ap.forms(d), ap.decisions(d)
    st = {x.id: ap.status(x, fs)["status"] for x in ds}
    assert st["3"] == "ACCEPTED" and st["9"] == "ACCEPTED"  # owner-only decisions
    assert all(st[i] == "PROPOSED" for i in ("1", "2", "6", "7", "8", "11", "19"))
    d2 = proto_copy(tmp_path / "b")
    sign(d2, names={"curator": NAMES["owner"]})  # the owner's name on the curator's own form
    assert any("signed by the owner" in p for p in ap.real_problems(d2))


def test_signed_fixture_forms_accept_and_real_problems_catch_role_abuse(tmp_path):
    d = proto_copy(tmp_path)
    sign(d)
    assert all(ap.status(x, ap.forms(d))["status"] == "ACCEPTED" for x in ap.decisions(d))
    assert ap.real_problems(d) == []
    dev = sorted(ap.developers())[0]
    for names, why in (({"curator": dev}, "commit author"), ({"oracle_reviewer": NAMES["oracle_author"]},
                                                              "two independent roles"),
                       ({"adjudicator": "SIMULATION adjudicator"}, "SIMULATION")):
        e = proto_copy(tmp_path / why.replace(" ", "_"))
        sign(e, names=names)
        assert any(why in p for p in ap.real_problems(e)), (why, ap.real_problems(e))
    f = proto_copy(tmp_path / "amend")
    sign(f, tick="amend")
    assert {ap.status(x, ap.forms(f))["status"] for x in ap.decisions(f)} == {
        "AMEND OR REJECT REQUESTED (a new candidate is needed)"}


def test_mock_approvals_are_complete_marked_and_never_real():
    m = ap.mock_approvals()
    assert m["SIMULATION"] is True and ap.mock_problems(m) == []
    assert ap.mock_problems({k: v for k, v in m.items() if k != "SIMULATION"})


# ------------------------------------------------------------------------------------------------ C2

DOCS = b"# Upgrading\n\nStop the workers, migrate, start the new workers.\n"


def archive(path: Path, guarded: bool) -> None:
    buf = io.BytesIO()
    files = {"app/queue.py": b"def finish(j):\n    claim_guard(j)\n" if guarded else b"def finish(j):\n    pass\n",
             "docs/upgrade.md": DOCS}
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for n in sorted(files):
            i = tarfile.TarInfo(n)
            i.size = len(files[n])
            t.addfile(i, io.BytesIO(files[n]))
    path.write_bytes(buf.getvalue())


def envelope(tmp: Path, cid: str = "fx-case-01", app: str = "fixture-app", **over) -> Path:
    raw = tmp / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    a, b = raw / f"{cid}-a.tar", raw / f"{cid}-b.tar"
    archive(a, False)
    archive(b, True)
    spec = raw / "spec.md"
    spec.write_text("property: a completion is written only under its claim's guard\n")
    env = {"schema": intake_v2.SCHEMA, "curator": "curator-1",
           "submission": {"case_id": cid, "application": app, "repository": "https://example.invalid/x.git",
                          "split": "confirmatory",
                          "revisions": [{"archive": str(a), "commit": "a" * 40},
                                        {"archive": str(b), "commit": "b" * 40}],
                          "buggy": 0, "fix_paths": ["app/queue.py"], "defect_summary": "an unguarded completion",
                          "keywords": ["unguarded"],
                          "contract": {"kind": "single-version", "citation": "docs/upgrade.md"},
                          "oracle_spec_sha256": ci.sha(spec), "exposure": {"curator": ["fix"], "public": []}},
           "selection": {"selected_at": "2026-11-01T00:00:00Z", "method": "a changelog search"},
           "environment": {"image_digest": "sha256:" + "c" * 64, "lock_sha256": "d" * 64, "postgres_major": "16"},
           "pinned_sources": [{"tree_sha256": intake_v2.tree_digest(a)}, {"tree_sha256": intake_v2.tree_digest(b)}],
           "contract_evidence": {"path": "docs/upgrade.md", "sha256": hashlib.sha256(DOCS).hexdigest()}}
    for k, v in over.items():
        node, *path = k.split(".")
        target = env[node]
        for x in path[:-1]:
            target = target[x]
        if path:
            target[path[-1]] = v
        else:
            env[node] = v
    p = raw / f"{cid}.envelope.json"
    p.write_text(json.dumps(env))
    return p


def test_intake_v2_accepts_a_complete_envelope_and_keeps_the_case_directory_pure(tmp_path):
    cases = tmp_path / "cases"
    r = intake_v2.submit(envelope(tmp_path), cases, tmp_path / "priv" / "fx.json", cases / "INTAKE_LEDGER.jsonl")
    assert sorted(p.name for p in (cases / "fx-case-01").iterdir()) == ["R1.tar", "R2.tar", "case.json"]
    assert ci.verify_case(cases / "fx-case-01") == [] and intake_v2.verify_ledger(cases / "INTAKE_LEDGER.jsonl") == []
    pub = json.loads(Path(r["envelope"]).read_text())
    assert set(intake_v2.evaluator_view(pub)) == {"environment", "contract_kind"}
    assert "archive" not in json.dumps(pub) and "app/queue.py" not in json.dumps(pub)


@pytest.mark.parametrize("over,why", [
    ({"selection.selected_at": "2099-01-01T00:00:00Z"}, "future"),
    ({"environment.image_digest": "latest"}, "environment"),
    ({"curator": "Jane Doe"}, "role identifier"),
    ({"contract_evidence.sha256": "e" * 64}, "cited document"),
    ({"pinned_sources": [{"tree_sha256": "0" * 64}, {"tree_sha256": "1" * 64}]}, "tree digest"),
    ({"submission.application": "procrastinate"}, "never confirmatory"),
    ({"submission.case_id": "Bad Id"}, "immutable identifier")])
def test_intake_v2_refusals(tmp_path, over, why):
    with pytest.raises(SystemExit, match=why):
        intake_v2.submit(envelope(tmp_path, **over), tmp_path / "cases", tmp_path / "priv" / "x.json",
                         tmp_path / "cases" / "INTAKE_LEDGER.jsonl")


def test_a_leaky_envelope_is_refused_and_nothing_is_left(tmp_path):
    p = envelope(tmp_path, **{"selection.method": "found by reading app/queue.py's fix"})
    with pytest.raises(SystemExit, match="label material"):
        intake_v2.submit(p, tmp_path / "cases", tmp_path / "priv" / "x.json", tmp_path / "cases" / "L.jsonl")
    assert not (tmp_path / "cases" / "fx-case-01").exists() and not (tmp_path / "priv" / "x.json").exists()


def test_a_tampered_intake_ledger_is_detected(tmp_path):
    cases = tmp_path / "cases"
    intake_v2.submit(envelope(tmp_path), cases, tmp_path / "priv" / "fx.json", cases / "INTAKE_LEDGER.jsonl")
    e = json.loads((cases / "INTAKE_LEDGER.jsonl").read_text())
    e["selected_at"] = "2026-10-01T00:00:00Z"
    (cases / "INTAKE_LEDGER.jsonl").write_text(json.dumps(e) + "\n")
    assert intake_v2.verify_ledger(cases / "INTAKE_LEDGER.jsonl")


# ------------------------------------------------------------------------------------------------ C3

def oracle_code(tmp: Path) -> Path:
    p = tmp / "oracle.json"
    p.write_text(json.dumps({"scan": "app/", "required": "claim_guard(", "property": "guarded completion"}))
    return p


def corrected(arc: Path, code: Path) -> bool:
    return not run._violable(arc, json.loads(code.read_text()))


def case_with_reveal(tmp: Path) -> tuple[Path, Path, Path]:
    cases = tmp / "cases"
    intake_v2.submit(envelope(tmp), cases, tmp / "priv" / "fx.json", cases / "INTAKE_LEDGER.jsonl")
    return cases / "fx-case-01", tmp / "priv" / "fx.json", tmp / "raw" / "spec.md"


def test_the_oracle_review_chain_and_corrected_version_validation(tmp_path):
    case, reveal, spec = case_with_reveal(tmp_path)
    led, code = tmp_path / "ORACLE_LEDGER.jsonl", oracle_code(tmp_path)
    orc.add_spec(led, case, spec, author="oracle-author-1")
    with pytest.raises(SystemExit, match="not be the spec's author"):
        orc.add_review(led, case.name, spec_sha256=ci.sha(spec), oracle_code=code, reviewer="oracle-author-1",
                       verdict="approve")
    orc.add_review(led, case.name, spec_sha256=ci.sha(spec), oracle_code=code, reviewer="oracle-reviewer-1",
                   verdict="approve")
    seen = []

    def executor(arc: Path, c: Path) -> bool:
        seen.append(intake_v2.tree_digest(arc))
        return corrected(arc, c)
    v = orc.validate_corrected(led, case, reveal, code, executor)
    fixed_tree = intake_v2.tree_digest(tmp_path / "raw" / "fx-case-01-b.tar")
    assert seen == [fixed_tree] and v["result"] == "holds" and "R1" not in json.dumps(v) and "R2" not in json.dumps(v)
    assert orc.approved(led)[case.name]["problems"] == ["the oracle set isn't frozen"]
    orc.freeze(led)
    assert orc.allowlist(led) == {"oracle.json": ci.sha(code)}
    with pytest.raises(SystemExit, match="frozen"):
        orc.add_spec(led, case, spec, author="oracle-author-2")
    assert orc.verify(led) == []


def test_post_outcome_oracle_edits_and_wrong_specs_are_refused(tmp_path):
    case, reveal, spec = case_with_reveal(tmp_path)
    led, results = tmp_path / "O.jsonl", tmp_path / "RESULTS_LEDGER.jsonl"
    other = tmp_path / "other.md"
    other.write_text("a different spec")
    with pytest.raises(SystemExit, match="sealed in the case manifest"):
        orc.add_spec(led, case, other, author="a")
    results.write_text("{}\n")
    with pytest.raises(SystemExit, match="after outcomes"):
        orc.add_spec(led, case, spec, author="a", results_ledger=results)


def test_a_failed_corrected_version_validation_blocks_the_oracle(tmp_path):
    case, reveal, spec = case_with_reveal(tmp_path)
    led, code = tmp_path / "O.jsonl", oracle_code(tmp_path)
    orc.add_spec(led, case, spec, author="a")
    orc.add_review(led, case.name, spec_sha256=ci.sha(spec), oracle_code=code, reviewer="r", verdict="approve")
    orc.validate_corrected(led, case, reveal, code, lambda a, c: False)
    orc.freeze(led)
    assert any("doesn't hold" in p for p in orc.approved(led)[case.name]["problems"]) and orc.allowlist(led) == {}


# ------------------------------------------------------------------------------------------------ C4: the real gate

@pytest.fixture
def world(tmp_path, monkeypatch):
    """Every prerequisite satisfied, in a temporary directory (test fixture only)."""
    protocol = proto_copy(tmp_path)
    sign(protocol)
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "EVALUATION_PROTOCOL_AMENDMENT_003.md").write_bytes(
        (protocol / "AMENDMENT_003_ADOPTION_CANDIDATE.md").read_bytes())
    corpus = tmp_path / "corpus"
    cases = corpus / "cases"
    intake_v2.submit(envelope(tmp_path), cases, tmp_path / "priv" / "fx.json", cases / "INTAKE_LEDGER.jsonl")
    registry = corpus / "REGISTRY_confirmatory.jsonl"
    ci.register(cases / "fx-case-01", registry)
    ci.freeze(registry)
    led, code = tmp_path / "ORACLE_LEDGER.jsonl", oracle_code(tmp_path)
    orc.add_spec(led, cases / "fx-case-01", tmp_path / "raw" / "spec.md", author="oracle-author-1")
    orc.add_review(led, "fx-case-01", spec_sha256=ci.sha(tmp_path / "raw" / "spec.md"), oracle_code=code,
                   reviewer="oracle-reviewer-1", verdict="approve")
    orc.validate_corrected(led, cases / "fx-case-01", tmp_path / "priv" / "fx.json", code, corrected)
    orc.freeze(led)
    reg = tmp_path / "registration"
    reg.mkdir()
    (reg / "token.ots").write_bytes(b"fixture timestamp token")
    (reg / "REGISTRATION.json").write_text(json.dumps({
        "registration_id": "fixture-registration", "registered_at": "2026-11-05T00:00:00Z",
        "candidate_sha256": ci.sha(protocol / "AMENDMENT_003_ADOPTION_CANDIDATE.md"),
        "adopted_file_sha256": ci.sha(frozen / "EVALUATION_PROTOCOL_AMENDMENT_003.md"),
        "timestamp_token": "token.ots", "timestamp_token_sha256": ci.sha(reg / "token.ots")}))
    policy = tmp_path / "POLICY.json"
    policy.write_text(json.dumps(run.policy_from_plan()))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setattr(run, "EVIDENCE", evidence)
    kw = {"registry": registry, "cases_root": cases, "oracle_ledger": led, "registration": reg / "REGISTRATION.json",
          "policy": policy, "results_dir": tmp_path / "results", "protocol": protocol, "frozen": frozen}
    return {"kw": kw, "tmp": tmp_path, "evidence": evidence}


def test_the_gate_opens_only_when_everything_holds(world):
    assert run.real_refusals(**world["kw"]) == []
    # past the seven gates, the executor's own preflight can only refuse more: the fixture's synthetic application
    # has no reviewed integration, so nothing runs and no result is committed
    import argparse
    kw = world["kw"]
    a = argparse.Namespace(**{k: kw[k] for k in ("registry", "cases_root", "oracle_ledger", "registration", "policy",
                                                 "results_dir")}, model_manifest=None)
    with pytest.raises(SystemExit, match="no reviewed integration"):
        run.real_execute(a, gate=lambda **k: run.real_refusals(**k, protocol=kw["protocol"], frozen=kw["frozen"]))
    assert not (kw["results_dir"] / "RESULTS_LEDGER.jsonl").exists()


def reasons(world, **change):
    return " | ".join(run.real_refusals(**(world["kw"] | change)))


def test_each_missing_prerequisite_refuses(world):
    kw, tmp = world["kw"], world["tmp"]
    # approvals
    e = proto_copy(tmp / "unsigned")
    shutil.copyfile(kw["protocol"] / "approval" / "OWNER_APPROVAL.md", e / "approval" / "OWNER_APPROVAL.md")
    assert "approvals: #1 is PROPOSED" in reasons(world, protocol=e)
    # protocol adoption
    (kw["frozen"] / "EVALUATION_PROTOCOL_AMENDMENT_003.md").rename(tmp / "moved.md")
    assert "protocol: Amendment 003 is not adopted" in reasons(world)
    (tmp / "moved.md").rename(kw["frozen"] / "EVALUATION_PROTOCOL_AMENDMENT_003.md")
    # registration
    assert "registration: no prospective registration record" in reasons(world, registration=tmp / "none.json")
    rec = json.loads(kw["registration"].read_text())
    for change, why in (({"registered_at": "2099-01-01T00:00:00Z"}, "in the future"),
                        ({"SIMULATION": True}, "is a SIMULATION"),
                        ({"timestamp_token_sha256": "0" * 64}, "timestamp token"),
                        ({"candidate_sha256": "0" * 64}, "candidate's sha256")):
        alt = kw["registration"].with_name("ALT.json")
        alt.write_text(json.dumps(rec | change))
        assert why in reasons(world, registration=alt), why
    res = kw["results_dir"]
    res.mkdir()
    (res / "RESULTS_LEDGER.jsonl").write_text(json.dumps({"at": "2026-11-04T00:00:00Z"}) + "\n")
    assert "results exist from before the registration" in reasons(world)
    shutil.rmtree(res)
    # corpus
    assert "corpus: no confirmatory registry" in reasons(world, registry=tmp / "none.jsonl")
    env = kw["cases_root"] / "fx-case-01.envelope.json"
    env.rename(tmp / "env.json")
    assert "has no curator envelope" in reasons(world)
    (tmp / "env.json").rename(env)
    # oracles
    assert "oracles: no oracle ledger" in reasons(world, oracle_ledger=tmp / "none.jsonl")
    # policy
    assert "policy: no resource and retry policy" in reasons(world, policy=tmp / "none.json")
    pol = json.loads(kw["policy"].read_text())
    pol["budgets"]["B"] = 24
    alt = tmp / "POL2.json"
    alt.write_text(json.dumps(pol))
    assert "policy.budgets isn't the frozen plan's" in reasons(world, policy=alt)


def test_an_unfrozen_registry_or_a_simulation_case_refuses(tmp_path, world):
    kw = world["kw"]
    reg = Path(kw["registry"])
    lines = reg.read_text().splitlines()
    unfrozen = reg.with_name("REGISTRY_confirmatory.jsonl.unfrozen")
    d = tmp_path / "u" / "REGISTRY_confirmatory.jsonl"
    d.parent.mkdir()
    d.write_text("\n".join(lines[:-1]) + "\n")
    assert "isn't frozen" in reasons(world, registry=d)
    assert not unfrozen.exists()
    t2 = tmp_path / "sim"
    cases = t2 / "cases"
    intake_v2.submit(envelope(t2, cid="sim-case-99"), cases, t2 / "priv" / "s.json", cases / "INTAKE_LEDGER.jsonl")
    r2 = t2 / "REGISTRY_confirmatory.jsonl"
    ci.register(cases / "sim-case-99", r2)
    ci.freeze(r2)
    assert "sim-case-99 is a SIMULATION case" in reasons(world, registry=r2, cases_root=cases)


def test_oracle_problems_refuse(tmp_path, world):
    kw = world["kw"]
    led = tmp_path / "O2.jsonl"
    spec = world["tmp"] / "raw" / "spec.md"
    orc.add_spec(led, kw["cases_root"] / "fx-case-01", spec, author="a", simulation=True)
    orc.add_review(led, "fx-case-01", spec_sha256=ci.sha(spec), oracle_code=oracle_code(tmp_path), reviewer="r",
                   verdict="approve", simulation=True)
    orc.validate_corrected(led, kw["cases_root"] / "fx-case-01", world["tmp"] / "priv" / "fx.json",
                           oracle_code(tmp_path), lambda a, c: False)
    text = reasons(world, oracle_ledger=led)
    assert "oracle is a SIMULATION" in text and "doesn't hold" in text and "isn't frozen" in text


def test_leakage_into_development_data_refuses(world):
    kw = world["kw"]
    e = json.loads(Path(kw["registry"]).read_text().splitlines()[0])
    dev = Path(kw["registry"]).with_name("REGISTRY_development.jsonl")
    dev.write_text(json.dumps({"kind": "case", "case_id": "dev-x", "archives": e["archives"][:1]}) + "\n")
    assert "also in the development registry" in reasons(world)
    dev.unlink()
    (world["evidence"] / "notes.md").write_text(f"we looked at {e['archives'][1]} earlier\n")
    assert "appears in development evidence" in reasons(world)


def test_the_current_repository_refuses_a_real_run():
    why = run.real_refusals(registry=ROOT / "docs/experimental_mvp/corpus/REGISTRY_confirmatory.jsonl",
                            cases_root=None, oracle_ledger=None, registration=None, policy=None, results_dir=None)
    cats = {w.split(":")[0] for w in why}
    assert {"approvals", "protocol", "registration", "corpus", "oracles", "policy"} <= cats
    assert run.main(["real"]) == 3


# ------------------------------------------------------------------------------------------------ C4/C5: simulation

@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    base = tmp_path_factory.mktemp("c")
    s, p = base / "rehearsal_simulation", base / "private_simulation"
    out = run.simulate(s, p, scenario="null", n_cases=3)
    return {"dir": s, "private": p, "summary": out}


def test_the_simulation_runs_end_to_end_and_stamps_every_artifact(sim):
    s = sim["dir"]
    assert sim["summary"]["runs"] == 3 * len(run.ARMS) * 3 * 2
    for f in s.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix == ".tar":
            with tarfile.open(f) as t:
                assert "SIMULATION.txt" in t.getnames(), f
        else:
            assert re.search(r"simulation|sim-|SIM", f.read_text(errors="ignore")), f
    assert any("is a SIMULATION case" in x for x in sim["summary"]["real_mode_would_refuse"])
    assert sim["summary"]["launch"]["label"].startswith("SIMULATED")


def test_a_null_result_is_reported_as_valid(sim):
    a = json.loads((sim["dir"] / "results" / "ANALYSIS.json").read_text())
    prim = a["comparisons"]["RA-MDS vs RA-DET"]
    assert prim["label"].startswith("PREREGISTERED PRIMARY") and "better" not in prim["verdict"]
    assert "null or negative result" in a["reading"]
    assert a["comparisons"]["RA-MS vs RA-DET"]["label"].startswith("POST-HOC")
    assert {"refused_runs", "unsuccessful_runs", "fixed_version_false_positives"} <= set(a["accounting"]["RA-DET"])
    assert "censored_nondetections" in prim


def test_scoring_refuses_tampering_late_results_and_changed_oracles(sim, tmp_path):
    s = sim["dir"]
    led = s / "results" / "RESULTS_LEDGER.jsonl"
    cases = sorted(p for p in (s / "corpus" / "cases").iterdir() if p.is_dir())
    code = s / "oracle" / "sim_oracle.json"
    w = run.Writer([tmp_path], simulation=True)
    with pytest.raises(SystemExit, match="outside the simulation"):
        w.json(s.parent / "elsewhere.json", {})
    bad = tmp_path / "L.jsonl"
    lines = led.read_text().splitlines()
    first = json.loads(lines[0])
    first["run"]["schedules_used"] += 1  # always a change to the committed record
    bad.write_text("\n".join([json.dumps(first, sort_keys=True)] + lines[1:]) + "\n")
    with pytest.raises(SystemExit, match="broken"):
        run.score(bad, cases, sim["private"] / "reveals", code, lock_path=s / "BUDGET_LOCK.json")
    with pytest.raises(SystemExit, match="already authorized"):
        run._append_result(run.Writer([s], True), led, json.loads(lines[0])["run"], oracle_sha="x",
                           lock=json.loads((s / "BUDGET_LOCK.json").read_text()))
    alt = tmp_path / "sim_oracle.json"
    alt.write_text(code.read_text().replace("claim_guard(", "anything("))
    with pytest.raises(SystemExit, match="oracle changed"):
        run.score(led, cases, sim["private"] / "reveals", alt, lock_path=s / "BUDGET_LOCK.json")


def test_budgets_are_locked_from_the_frozen_plan(sim, tmp_path):
    lock = sim["dir"] / "BUDGET_LOCK.json"
    assert run.check_lock(lock)["budgets"] == run.plan_budgets()["budgets"]
    with pytest.raises(SystemExit, match="post-hoc"):
        run.check_lock(lock, budget_override=24)
    plan = tmp_path / "PLAN.md"
    plan.write_text(run.PLAN.read_text().replace("| 12: the forced", "| 24: the forced"))
    with pytest.raises(SystemExit, match="changed after"):
        run.check_lock(lock, plan=plan)
    b = run.plan_budgets()["budgets"]
    rows = [{"case_id": "c", "arm": "RA-DET", "replicate": 0, "schedules_used": 1, "tokens_in": 5, "tokens_out": 0,
             "est_usd": 0.0}, {"case_id": "c", "arm": "RA-MD", "replicate": 0, "schedules_used": b["B"],
                              "tokens_in": 1, "tokens_out": 1, "est_usd": 2.0}]
    probs = run.budget_problems(rows, {"budgets": b})
    assert any("deterministic arm used the model" in x for x in probs)
    assert any("more schedules" in x for x in probs) and any("per case and arm" in x for x in probs)


def test_simulation_refuses_unnamed_repo_or_nonempty_directories(tmp_path):
    with pytest.raises(SystemExit, match="named as such"):
        run.simulate(tmp_path / "out", tmp_path / "private_simulation")
    with pytest.raises(SystemExit, match="outside the repository"):
        run.simulate(tmp_path / "x_simulation", ROOT / "docs" / "private_simulation")
    (tmp_path / "y_simulation").mkdir()
    (tmp_path / "y_simulation" / "f").write_text("x")
    with pytest.raises(SystemExit, match="isn't empty"):
        run.simulate(tmp_path / "y_simulation", tmp_path / "private_simulation")


def test_holm_and_plan_parsing():
    assert run.holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
    b = run.plan_budgets()["budgets"]
    assert (b["B"], b["T_seconds"], b["replicates"], b["R"], b["P"], b["C_usd_per_case_arm"]) == (12, 900, 3, 2, 6,
                                                                                                  1.0)
    assert time.time() > 0
