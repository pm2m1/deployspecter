"""EXPERIMENTAL, offline: the confirmatory arm executor (ds-confirm-exec/1) with a fake, SIMULATION-marked
integration. These tests cover:
* results committed run by run to the hash-chained ledger;
* the locked budgets: schedules, T, R and cost, and no model use for deterministic arms;
* no retried runs;
* the same seeds for every arm;
* interruption and resume without duplicates;
* the preflight refusals;
* the reveal authorization's checks.
The real Chancy integration is tested live in ``test_confirmatory_chancy_sv_live`` and the rehearsal."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "confirmatory"))
sys.path.insert(0, str(ROOT / "research_tools"))
import real_executor as rx  # noqa: E402
import run  # noqa: E402
from integrations import chancy_sv  # noqa: E402

LOCK = {"plan_sha256": "f" * 64, "budgets": {"B": 12, "replicates": 3, "T_seconds": 900, "R": 2,
                                             "C_usd_per_case_arm": 1.0, "C_usd_per_evaluation": 10.0}}


class Fake:
    """SIMULATION integration: an archive containing BUG violates at schedule 2; anything else holds."""
    SIMULATION = True

    def __init__(self, *, refuse_at=None, interrupt_after=None):
        self.calls, self.refuse_at, self.interrupt_after, self.seeds = [], refuse_at, interrupt_after, {}

    def validate(self, w):
        return [] if isinstance(w, dict) and w.get("n") in (0, 1, 2) else ["bad workload"]

    def workloads(self, arm):
        return [{"n": 0}, {"n": 1}]

    def prepare(self, archive, sha256, workdir):
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == sha256
        return {"bug": b"BUG" in archive.read_bytes()}

    def run(self, handle, workload, seed, out, *, oracle_code, oracle_sha256):
        if self.interrupt_after is not None and len(self.calls) >= self.interrupt_after:
            raise KeyboardInterrupt
        self.calls.append(seed)
        n = len(self.seeds.setdefault(str(out).split("__")[1], []))
        self.seeds[str(out).split("__")[1]].append(seed)
        if self.refuse_at is not None and n + 1 == self.refuse_at:
            return {"verdict": "REFUSED", "seed": seed, "corroboration": {"ok": False}}
        i = int(str(out)[-2:])
        oracle = "VIOLATED" if handle["bug"] and i == 2 else "HOLDS"
        return {"verdict": "SOUND", "oracle": oracle, "seed": seed,
                "corroboration": {"ok": True, "params_mismatched": 0, "params_unchecked": 0}}


def package(tmp: Path, application="fake") -> tuple[Path, dict]:
    pkg = tmp / "pkg"
    d = pkg / "cases" / "sim-c1"
    d.mkdir(parents=True)
    (d / "R1.tar").write_bytes(b"SIMULATION fixed")
    (d / "R2.tar").write_bytes(b"SIMULATION BUG")
    (d / "case.json").write_text(json.dumps({"case_id": "sim-c1", "application": application, "revisions": {
        s: {"sha256": hashlib.sha256((d / f"{s}.tar").read_bytes()).hexdigest()} for s in ("R1", "R2")}}))
    code = tmp / "oracle.py"
    code.write_text("# SIMULATION oracle\ndef check(final_state, raw_results):\n    return 'HOLDS'\n")
    return pkg, {"sim-c1": (code, hashlib.sha256(code.read_bytes()).hexdigest())}


def executor(tmp: Path, integ, *, arms=("RA-DET",), lock=LOCK, model=None, manifest=None, application="fake"):
    pkg, oracles = package(tmp, application)
    w = run.Writer([tmp], simulation=True)
    ledger = tmp / "RESULTS_LEDGER.jsonl"
    ex = rx.Executor(package=pkg, lock=lock, arms=list(arms), oracles=oracles, integrations={"fake": integ},
                     append=lambda r: run._append_result(w, ledger, r, oracle_sha="o" * 64, lock=lock),
                     committed=lambda: run._chain(ledger), workdir=tmp / "work", write_json=w.json, model=model,
                     model_manifest=manifest, simulation=True)
    return ex, ledger


def test_runs_are_committed_one_by_one_and_detection_ends_a_run(tmp_path):
    ex, ledger = executor(tmp_path, Fake())
    out = ex.run()
    assert out["complete"] and out["runs_committed"] == 6 and run.verify_results(ledger) == []
    runs = {(e["run"]["replicate"], e["run"]["slot"]): e["run"] for e in run._chain(ledger)}
    assert all(runs[(r, "R2")]["verdict"] == "VIOLATED" and runs[(r, "R2")]["first_violation_schedule"] == 2
               and runs[(r, "R2")]["schedules_used"] == 2 for r in range(3))
    assert all(runs[(r, "R1")]["verdict"] == "HOLDS" and runs[(r, "R1")]["schedules_used"] == 4 for r in range(3))
    assert all(e["run"]["SIMULATION"] for e in run._chain(ledger))


def test_the_same_seeds_for_every_arm(tmp_path):
    f = Fake()
    ex, _ = executor(tmp_path, f, arms=("RA-DET", "RA-DET-C"))
    ex.run()
    assert f.seeds["RA-DET"] == f.seeds["RA-DET-C"]


def test_a_non_sound_schedule_is_final_and_never_retried(tmp_path):
    f = Fake(refuse_at=1)
    ex, ledger = executor(tmp_path, f)
    ex.run()
    runs = [e["run"] for e in run._chain(ledger)]
    assert runs[0]["verdict"] == "REFUSED" and runs[0]["schedules_used"] == 1
    assert len(f.calls) == sum(r["schedules_used"] for r in runs)  # one call per used schedule: nothing retried


def test_the_time_cap_censors(tmp_path):
    ex, ledger = executor(tmp_path, Fake(), lock={**LOCK, "budgets": {**LOCK["budgets"], "T_seconds": 0}})
    ex.run()
    assert all(e["run"]["schedules_used"] == 0 and e["run"]["censored_by"] == "T" for e in run._chain(ledger))


def test_interruption_keeps_committed_runs_and_resume_adds_no_duplicates(tmp_path):
    ex, ledger = executor(tmp_path, Fake(interrupt_after=5))
    with pytest.raises(KeyboardInterrupt):
        ex.run()
    committed = run._chain(ledger)
    marker = json.loads((tmp_path / "work" / "INTERRUPTED.json").read_text())
    assert run.verify_results(ledger) == [] and len(committed) == len(marker["committed"]) >= 1
    ex2, _ = executor(tmp_path / "x", Fake())  # a fresh object, the same ledger and work dir
    ex2.workdir, ex2.append, ex2.committed = ex.workdir, ex.append, ex.committed
    ex2.package = ex.package
    out = ex2.run()
    keys = [rx.key(e["run"]) for e in run._chain(ledger)]
    assert out["complete"] and len(keys) == len(set(keys)) == 6 and run.verify_results(ledger) == []


def test_preflight_refusals(tmp_path):
    for app, why in (("unknown-app", "no reviewed integration"),):
        ex, _ = executor(tmp_path / app, Fake(), application=app)
        with pytest.raises(SystemExit, match=why):
            ex.run()
    ex, _ = executor(tmp_path / "hash", Fake())
    (ex.package / "cases" / "sim-c1" / "R1.tar").write_bytes(b"swapped")
    with pytest.raises(SystemExit, match="isn't the frozen manifest's"):
        ex.run()
    ex, _ = executor(tmp_path / "oracle", Fake())
    ex.oracles["sim-c1"][0].write_text("def check(*a):\n    return 'VIOLATED'\n")
    with pytest.raises(SystemExit, match="isn't the approved one"):
        ex.run()
    ex, _ = executor(tmp_path / "muse", Fake(), arms=("RA-MD",))
    with pytest.raises(SystemExit, match="model and prompt manifest"):
        ex.run()


def stub_model(cost=0.01, calls=None, transport_failures=0):
    state = {"n": 0}

    def model(case, arm, i):
        state["n"] += 1
        if calls is not None:
            calls.append(i)
        if state["n"] <= transport_failures:
            raise ConnectionError("transport")
        return json.dumps([{"n": 2}, {"n": 9}]), {"tokens_in": 10, "tokens_out": 20, "est_usd": cost}
    return model


MANIFEST = {"SIMULATION": True, "frozen_at_adoption": "SIMULATION stub"}


def test_muse_arms_are_held_to_r_journaled_and_not_asked_again_on_resume(tmp_path):
    calls = []
    ex, ledger = executor(tmp_path, Fake(), arms=("RA-MD",), model=stub_model(calls=calls), manifest=MANIFEST)
    ex.run()
    assert calls == [0, 1]  # R = 2 requests
    r = run._chain(ledger)[0]["run"]
    assert r["invalid_proposals"] == 2 and r["tokens_in"] == 20 and r["est_usd"] == pytest.approx(0.02)
    journal = tmp_path / "work" / "proposals" / "sim-c1__RA-MD.json"
    assert json.loads(journal.read_text())["valid"] == [{"n": 2}, {"n": 2}]
    calls.clear()
    ex.run()  # everything committed: nothing reruns, the model isn't asked again
    assert calls == []


def test_transport_failures_count_against_r_and_the_cost_caps_hold(tmp_path):
    calls = []
    ex, ledger = executor(tmp_path, Fake(), arms=("RA-MD",), model=stub_model(calls=calls, transport_failures=1),
                          manifest=MANIFEST)
    ex.run()
    assert len(calls) == 2  # one transport failure plus one answer: R = 2 in all
    ex, _ = executor(tmp_path / "cost", Fake(), arms=("RA-MD",), model=stub_model(cost=5.0), manifest=MANIFEST)
    with pytest.raises(SystemExit, match="per case and arm cap"):
        ex.run()


def test_reveal_authorization_checks(tmp_path):
    ex, ledger = executor(tmp_path, Fake(interrupt_after=2))
    with pytest.raises(KeyboardInterrupt):
        ex.run()
    rec = tmp_path / "auth.json"
    rec.write_text(json.dumps({"role": "owner", "authorizer": "Jane Owner", "date": "2026-11-07"}))
    owner = [{"name": "Jane Owner"}]
    with pytest.raises(SystemExit, match="incomplete"):
        rx.authorize_real(run._chain(ledger), ex.expected(), rec, owner)
    ex2, _ = executor(tmp_path / "y", Fake())
    ex2.workdir, ex2.append, ex2.committed, ex2.package = ex.workdir, ex.append, ex.committed, ex.package
    ex2.run()
    entries = run._chain(ledger)
    assert rx.authorize_real(entries, ex.expected(), rec, owner)["authorizer"] == "Jane Owner"
    with pytest.raises(SystemExit, match="isn't the owner"):
        rx.authorize_real(entries, ex.expected(), rec, [{"name": "Someone Else"}])
    rec.write_text(json.dumps({"role": "owner", "authorizer": "Jane Owner", "SIMULATION": True}))
    with pytest.raises(SystemExit, match="SIMULATION"):
        rx.authorize_real(entries, ex.expected(), rec, owner)
    run._authorize(run.Writer([tmp_path], simulation=True), ledger, authorizer="SIM", oracle_sha="o" * 64,
                   lock=LOCK)
    with pytest.raises(SystemExit, match="already authorized"):
        ex2.append({"case_id": "sim-c1", "arm": "RA-DET", "replicate": 0, "slot": "R1", "schedules_used": 1})


# ------------------------------------------------------------------------------------------------ chancy-sv/1

def test_chancy_sv_workload_schema_and_spec():
    for arm in ("RA-DET", "RA-DET-C"):
        assert all(chancy_sv.validate(w) == [] for w in chancy_sv.workloads(arm))
    for bad in ({"jobs": [], "max_attempts": 1}, {"jobs": [{"n": 0, "cancel": True}], "max_attempts": 3},
                {"jobs": [{"n": 0, "cancel": "yes"}], "max_attempts": 1}, {"jobs": [{"n": 1, "cancel": True}] * 2,
                                                                           "max_attempts": 1},
                {"jobs": [{"n": 0, "cancel": True, "sql": "x"}], "max_attempts": 1}, "DROP TABLE"):
        assert chancy_sv.validate(bad), bad
    s = chancy_sv.spec({"jobs": [{"n": 0, "cancel": False}, {"n": 1, "cancel": True}], "max_attempts": 2})
    assert s["covers"] == [0, 1] and s["processes"]["A"][1]["args"]["cancel"] == [1]
    assert any(g.get("match") == "SET state = 'failed'" for g in s["gates"])


def test_chancy_sv_refuses_an_archive_that_isnt_the_manifests(tmp_path):
    a = tmp_path / "R1.tar"
    a.write_bytes(b"not the archive")
    with pytest.raises(ValueError, match="isn't the frozen manifest's"):
        chancy_sv.prepare(a, "0" * 64, tmp_path / "w")


def test_chancy_sv_reads_the_schema_version_from_the_archive():
    sd = Path("/private/tmp/claude-501/-Users-kiyo-Desktop-deployspecter/b1c95b43-b64d-4432-a288-8cf30f7b145b/"
              "scratchpad/sdists")
    for name, v in (("chancy-git-80f570f.tar", "7"), ("chancy-git-2895c4a.tar", "8")):
        if (sd / name).exists():
            assert chancy_sv.schema_version(sd / name) == v
