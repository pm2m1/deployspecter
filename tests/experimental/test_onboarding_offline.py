"""EXPERIMENTAL, offline: the onboarding measurement framework (research_tools/onboarding), in a temporary git repo
with synthetic sessions. No participant data."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "onboarding"))
import baseline as bl  # noqa: E402
import onboard as ob  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "adapters").mkdir(parents=True)
    git(r, "init", "-q")
    git(r, "config", "user.email", "synthetic@example.invalid")
    git(r, "config", "user.name", "synthetic")
    (r / "adapters" / "existing.py").write_text("x = 1\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "base")
    return r


def session_run(tmp_path, repo, arm, participant, *, t0=1000.0, developer=False, corrected=True, hand_lines=3):
    s = tmp_path / f"s_{arm}_{participant}"
    ob.start(s, app="synthapp", arm=arm, participant=participant, repo=repo, paths=["adapters"], developer=developer,
             now=t0)
    (repo / "adapters" / f"hand_{participant}.py").write_text("".join(f"line{i}\n" for i in range(hand_lines)))
    ob.append(s, "intervention", now=t0 + 10, detail="looked up the pool seam")
    ob.append(s, "attempt", now=t0 + 20, ok=False, detail="wrong DSN")
    if arm == "muse":
        gen = repo / "adapters" / f"gen_{participant}.py"
        gen.write_text("a = 1\nb = 2\nc = 3\n")
        ob.record_generated(s, f"adapters/gen_{participant}.py", now=t0 + 30, tool="muse")
        ob.append(s, "model_call", now=t0 + 31, tokens_in=100, tokens_out=900, usd=0.004)
        if corrected:
            gen.write_text("a = 1\nb = 20\nc = 3\n")  # one line corrected after generation
    ob.append(s, "unsupported", now=t0 + 35, detail="LISTEN/NOTIFY")
    ob.append(s, "attempt", now=t0 + 40, ok=True)
    ob.append(s, "first_corroborated_run", now=t0 + 50, evidence="results/x/report.json")
    ob.append(s, "end", now=t0 + 60)
    return s


def test_measure_counts_everything(tmp_path, repo):
    m = ob.measure(session_run(tmp_path, repo, "muse", "p1"))
    assert m["wall_clock_setup_seconds"] == 60 and m["seconds_to_first_corroborated_run"] == 50
    assert m["manual_interventions"] == 1 and m["failed_attempts"] == 1 and m["attempts"] == 2
    assert m["generated_files"] == 1 and m["generated_files_corrected"] == 1
    assert m["generated_lines_corrected"] == 2  # ndiff: one removed, one added
    assert m["handwritten_by_file"] == {"adapters/hand_p1.py": 3}  # the generated file is not handwritten
    assert m["handwritten_lines"] == 3 + 2
    assert m["tokens_out"] == 900 and m["est_usd"] == 0.004 and m["unsupported_features"] == ["LISTEN/NOTIFY"]


def test_committed_handwritten_lines_count_against_the_base(tmp_path, repo):
    s = session_run(tmp_path, repo, "deterministic", "p2", hand_lines=4)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "work")
    (repo / "adapters" / "existing.py").write_text("x = 1\ny = 2\n")
    m = ob.measure(s)
    assert m["handwritten_by_file"] == {"adapters/existing.py": 1, "adapters/hand_p2.py": 4}


def test_log_rules(tmp_path, repo):
    s = tmp_path / "s"
    with pytest.raises(ob.Refused, match="start the session first"):
        ob.append(s, "intervention")
    ob.start(s, app="a", arm="muse", participant="p", repo=repo, paths=["adapters"], now=10)
    with pytest.raises(ob.Refused, match="already started"):
        ob.start(s, app="a", arm="muse", participant="p", repo=repo, paths=["adapters"], now=11)
    with pytest.raises(ob.Refused, match="unknown event kind"):
        ob.append(s, "coffee", now=12)
    with pytest.raises(ob.Refused, match="backwards"):
        ob.append(s, "intervention", now=5)
    with pytest.raises(ob.Refused, match="evidence"):
        ob.append(s, "first_corroborated_run", now=13)
    ob.append(s, "first_corroborated_run", now=14, evidence="e")
    with pytest.raises(ob.Refused, match="already recorded"):
        ob.append(s, "first_corroborated_run", now=15, evidence="e")
    ob.append(s, "end", now=16)
    with pytest.raises(ob.Refused, match="ended"):
        ob.append(s, "intervention", now=17)
    with pytest.raises(ob.Refused, match="arm"):
        ob.start(tmp_path / "s2", app="a", arm="magic", participant="p", repo=repo, paths=["adapters"])


def test_a_tampered_log_is_refused(tmp_path, repo):
    s = session_run(tmp_path, repo, "deterministic", "p3")
    lines = (s / "events.jsonl").read_text().splitlines()
    e = json.loads(lines[1])
    e["detail"] = "nothing happened"
    lines[1] = json.dumps(e, sort_keys=True)
    (s / "events.jsonl").write_text("\n".join(lines) + "\n")
    assert ob.verify(s)
    with pytest.raises(ob.Refused, match="broken"):
        ob.measure(s)


def test_compare_needs_independent_participants(tmp_path, repo):
    ms = [ob.measure(session_run(tmp_path, repo, "muse", f"m{i}", t0=1000.0 + i)) for i in range(2)]
    ms += [ob.measure(session_run(tmp_path, repo, "deterministic", f"d{i}", t0=2000.0 + i)) for i in range(2)]
    ms.append(ob.measure(session_run(tmp_path, repo, "deterministic", "dev", developer=True, t0=3000.0)))
    r = ob.compare(ms)
    assert r["verdict"].startswith("INSUFFICIENT SAMPLE (2")
    assert r["arms"]["deterministic"]["developer_baseline_sessions"] == 1
    assert r["arms"]["deterministic"]["independent_completed"] == 2       # the developer session doesn't count
    assert r["arms"]["muse"]["generated_lines_corrected"]["median"] == 2
    many = [ob.measure(session_run(tmp_path, repo, arm, f"{arm}{i}", t0=4000.0 + i))
            for arm in ob.ARMS for i in range(ob.MIN_PARTICIPANTS)]
    assert ob.compare(many)["verdict"].startswith("DESCRIPTIVE")


def test_cli_exit_codes(tmp_path, repo):
    s = tmp_path / "cli"
    assert ob.main(["start", "--session", str(s), "--app", "a", "--arm", "muse", "--participant", "p", "--repo",
                    str(repo), "--paths", "adapters"]) == 0
    assert ob.main(["event", "--session", str(s), "attempt"]) == 2          # neither --ok nor --failed
    assert ob.main(["event", "--session", str(s), "attempt", "--failed"]) == 0
    assert ob.main(["end", "--session", str(s)]) == 0
    assert ob.main(["measure", "--session", str(s)]) == 0
    assert json.loads((s / "measure.json").read_text())["failed_attempts"] == 1


def test_the_developer_baseline_is_labelled_and_has_no_participant_fields():
    b = {k: bl.app_baseline(v) for k, v in bl.APPS.items()}
    for app in ("chancy", "pgqueuer", "procrastinate"):
        assert b[app]["commits"] >= 1 and b[app]["current_lines"] > 0
        assert b[app]["manual_interventions"] is None and b[app]["seconds_to_first_corroborated_run"] is None
    assert not any("requirements" in p for v in bl.APPS.values() for p in v)  # generated lock files excluded
